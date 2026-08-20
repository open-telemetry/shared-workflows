import crypto from "node:crypto";

import { getStore } from "@netlify/blobs";

export const QUEUE_STORE_NAME = "pr-dashboard-queue";
export const DEFAULT_SHARD_COUNT = 64;
export const DEFAULT_ITEM_LEASE_MS = 15 * 60 * 1000;
export const DEFAULT_DISPATCHER_REQUEST_LEASE_MS = 2 * 60 * 60 * 1000;
export const DEFAULT_DISPATCHER_ACTIVE_LEASE_MS = 15 * 60 * 1000;
export const DEFAULT_CAS_ATTEMPTS = 8;
// An item whose worker dies before it can acknowledge is recovered instead of
// acknowledged, so recovery needs its own ceiling to stop a poison item from
// re-dispatching a drain forever.
export const DEFAULT_MAX_RECOVERY_ATTEMPTS = 5;
export const DEFAULT_ACKNOWLEDGMENT_RECEIPT_LIMIT = 100;
export const DEFAULT_FINISH_RECEIPT_LIMIT = 20;
// Every full scan touches each shard once, so the round trips have to overlap
// to stay inside the Netlify function execution budget.
export const DEFAULT_SHARD_CONCURRENCY = 8;

const SCHEMA_VERSION = 1;
const DISPATCHER_KEY = "dispatcher";
const REPOSITORY_PATTERN = /^[A-Za-z0-9_.-]+$/;
const SHA_PATTERN = /^[0-9a-f]{40}$/;

export function openQueueStore(name = QUEUE_STORE_NAME) {
  const store = getStore({
    name,
    consistency: "strong",
  });
  return {
    async get(key) {
      const entry = await store.getWithMetadata(key, {
        consistency: "strong",
        type: "json",
      });
      if (entry === null) {
        return null;
      }
      return {
        etag: entry.etag,
        value: entry.data,
      };
    },
    async set(key, value, condition) {
      return store.setJSON(key, value, condition);
    },
  };
}

export function queueItemKey({ repository, prNumber, headSha }) {
  validateRepository(repository);
  const hasPrNumber = Number.isInteger(prNumber) && prNumber > 0;
  const hasHeadSha = typeof headSha === "string" && headSha.length > 0;
  if (hasPrNumber === hasHeadSha) {
    throw new Error("queue item requires exactly one of prNumber or headSha");
  }
  if (hasHeadSha && !SHA_PATTERN.test(headSha)) {
    throw new Error("queue item headSha must be a 40-character lowercase hexadecimal SHA");
  }
  return hasPrNumber
    ? `${repository}#pr:${prNumber}`
    : `${repository}#head:${headSha}`;
}

export function queueShardKey(itemKey, shardCount = DEFAULT_SHARD_COUNT) {
  if (!Number.isInteger(shardCount) || shardCount < 1 || shardCount > 256) {
    throw new Error("shardCount must be an integer between 1 and 256");
  }
  const digest = crypto.createHash("sha256").update(itemKey).digest();
  const shard = digest.readUInt32BE(0) % shardCount;
  const width = Math.max(2, Math.ceil(Math.log2(shardCount) / 4));
  return `shards/${shard.toString(16).padStart(width, "0")}`;
}

export class DashboardQueue {
  constructor({
    store = openQueueStore(),
    now = () => Date.now(),
    randomId = () => crypto.randomUUID(),
    shardCount = DEFAULT_SHARD_COUNT,
    itemLeaseMs = DEFAULT_ITEM_LEASE_MS,
    dispatcherRequestLeaseMs = DEFAULT_DISPATCHER_REQUEST_LEASE_MS,
    dispatcherActiveLeaseMs = DEFAULT_DISPATCHER_ACTIVE_LEASE_MS,
    casAttempts = DEFAULT_CAS_ATTEMPTS,
    maxRecoveryAttempts = DEFAULT_MAX_RECOVERY_ATTEMPTS,
    shardConcurrency = DEFAULT_SHARD_CONCURRENCY,
    acknowledgmentReceiptLimit = DEFAULT_ACKNOWLEDGMENT_RECEIPT_LIMIT,
    finishReceiptLimit = DEFAULT_FINISH_RECEIPT_LIMIT,
  } = {}) {
    this.store = store;
    this.now = now;
    this.randomId = randomId;
    this.shardCount = shardCount;
    this.itemLeaseMs = itemLeaseMs;
    this.dispatcherRequestLeaseMs = dispatcherRequestLeaseMs;
    this.dispatcherActiveLeaseMs = dispatcherActiveLeaseMs;
    this.casAttempts = casAttempts;
    this.maxRecoveryAttempts = maxRecoveryAttempts;
    this.shardConcurrency = shardConcurrency;
    this.acknowledgmentReceiptLimit = acknowledgmentReceiptLimit;
    this.finishReceiptLimit = finishReceiptLimit;
  }

  async enqueue({ repository, prNumber, headSha, triggerEvent }) {
    const itemKey = queueItemKey({ repository, prNumber, headSha });
    const shardKey = queueShardKey(itemKey, this.shardCount);
    const observedAt = this.#isoNow();
    const event = normalizeTriggerEvent(triggerEvent);
    const mutation = await this.#mutate(shardKey, emptyShard, (shard) => {
      validateShard(shard);
      const existing = shard.items[itemKey];
      if (!existing) {
        shard.items[itemKey] = {
          repository,
          prNumber: prNumber || null,
          headSha: headSha || "",
          phase: "queued",
          generation: 1,
          dirty: false,
          attempts: 0,
          notBefore: null,
          leaseOwner: null,
          leaseExpiresAt: null,
          claimedGeneration: null,
          firstSeenAt: observedAt,
          lastSeenAt: observedAt,
          triggerEvents: event ? [event] : [],
        };
        return {
          changed: true,
          result: { status: "queued", generation: 1 },
        };
      }
      validateItem(itemKey, existing);
      if (existing.phase === "queued") {
        return {
          changed: false,
          result: { status: "coalesced", generation: existing.generation },
        };
      }
      if (existing.phase === "inflight" && existing.dirty) {
        return {
          changed: false,
          result: { status: "coalesced", generation: existing.generation },
        };
      }
      if (existing.phase !== "inflight") {
        throw new Error(`queue item ${itemKey} has unsupported phase ${existing.phase}`);
      }
      existing.dirty = true;
      existing.generation += 1;
      existing.lastSeenAt = observedAt;
      existing.triggerEvents = mergeTriggerEvents(existing.triggerEvents, event);
      return {
        changed: true,
        result: { status: "follow_up", generation: existing.generation },
      };
    });
    return {
      ...mutation.result,
      itemKey,
      shardKey,
    };
  }

  async requestDispatcher(requestOwner = this.randomId(), { replayExisting = false } = {}) {
    const requestedAt = this.#isoNow();
    const expiresAt = this.#isoAfter(this.dispatcherRequestLeaseMs);
    const mutation = await this.#mutate(DISPATCHER_KEY, emptyDispatcher, (dispatcher) => {
      validateDispatcher(dispatcher);
      if (
        replayExisting &&
        dispatcher.phase === "requested" &&
        dispatcher.leaseOwner === requestOwner &&
        !isExpired(dispatcher.leaseExpiresAt, this.now())
      ) {
        return {
          changed: false,
          result: {
            acquired: true,
            generation: dispatcher.generation,
            phase: dispatcher.phase,
            requestOwner,
          },
        };
      }
      if (
        dispatcher.phase !== "idle" &&
        !isExpired(dispatcher.leaseExpiresAt, this.now())
      ) {
        return {
          changed: false,
          result: {
            acquired: false,
            generation: dispatcher.generation,
            phase: dispatcher.phase,
          },
        };
      }
      dispatcher.phase = "requested";
      dispatcher.generation += 1;
      dispatcher.leaseOwner = requestOwner;
      dispatcher.leaseExpiresAt = expiresAt;
      dispatcher.updatedAt = requestedAt;
      return {
        changed: true,
        result: {
          acquired: true,
          generation: dispatcher.generation,
          phase: dispatcher.phase,
          requestOwner,
        },
      };
    });
    return mutation.result;
  }

  async releaseRequestedDispatcher({ generation, requestOwner }) {
    const mutation = await this.#mutate(DISPATCHER_KEY, emptyDispatcher, (dispatcher) => {
      validateDispatcher(dispatcher);
      if (
        dispatcher.phase !== "requested" ||
        dispatcher.generation !== generation ||
        dispatcher.leaseOwner !== requestOwner
      ) {
        return { changed: false, result: false };
      }
      setDispatcherIdle(dispatcher, this.#isoNow());
      return { changed: true, result: true };
    });
    return mutation.result;
  }

  async activateDispatcher({ generation, workerId }) {
    validateWorkerId(workerId);
    const mutation = await this.#mutate(DISPATCHER_KEY, emptyDispatcher, (dispatcher) => {
      validateDispatcher(dispatcher);
      if (
        dispatcher.phase !== "requested" ||
        dispatcher.generation !== generation ||
        isExpired(dispatcher.leaseExpiresAt, this.now())
      ) {
        return { changed: false, result: false };
      }
      dispatcher.phase = "active";
      dispatcher.leaseOwner = workerId;
      dispatcher.leaseExpiresAt = this.#isoAfter(this.dispatcherActiveLeaseMs);
      dispatcher.updatedAt = this.#isoNow();
      return { changed: true, result: true };
    });
    return mutation.result;
  }

  async heartbeat({ generation, workerId }) {
    validateWorkerId(workerId);
    const dispatcher = await this.#mutate(DISPATCHER_KEY, emptyDispatcher, (state) => {
      validateDispatcher(state);
      if (
        state.phase !== "active" ||
        state.generation !== generation ||
        state.leaseOwner !== workerId ||
        isExpired(state.leaseExpiresAt, this.now())
      ) {
        return { changed: false, result: false };
      }
      state.leaseExpiresAt = this.#isoAfter(this.dispatcherActiveLeaseMs);
      state.updatedAt = this.#isoNow();
      return { changed: true, result: true };
    });
    if (!dispatcher.result) {
      return { dispatcher: false, items: 0 };
    }

    let itemCount = 0;
    const counts = await this.#mapShards(async (shardKey) => {
      const result = await this.#mutate(shardKey, emptyShard, (shard) => {
        validateShard(shard);
        let changed = false;
        let count = 0;
        for (const [itemKey, item] of Object.entries(shard.items)) {
          validateItem(itemKey, item);
          if (item.phase === "inflight" && item.leaseOwner === workerId) {
            if (isExpired(item.leaseExpiresAt, this.now())) {
              throw new Error(`queue item lease expired for ${itemKey}`);
            }
            item.leaseExpiresAt = this.#isoAfter(this.itemLeaseMs);
            changed = true;
            count += 1;
          }
        }
        return { changed, result: count };
      });
      return result.result;
    });
    for (const count of counts) {
      itemCount += count;
    }
    return { dispatcher: true, items: itemCount };
  }

  async claimWave({ generation, workerId, limit = 4 }) {
    validateWorkerId(workerId);
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
      throw new Error("claim limit must be an integer between 1 and 100");
    }
    const dispatcher = await this.#readDispatcher();
    if (
      dispatcher.phase !== "active" ||
      dispatcher.generation !== generation ||
      dispatcher.leaseOwner !== workerId ||
      isExpired(dispatcher.leaseExpiresAt, this.now())
    ) {
      throw new Error("worker does not own the active dispatcher");
    }

    const candidates = [];
    const perShard = await this.#mapShards(async (shardKey) => {
      const entry = await this.store.get(shardKey);
      if (!entry) {
        return [];
      }
      validateShard(entry.value);
      const found = [];
      for (const [itemKey, item] of Object.entries(entry.value.items)) {
        validateItem(itemKey, item);
        if (
          item.phase === "queued" &&
          (!item.notBefore || Date.parse(item.notBefore) <= this.now())
        ) {
          found.push({
            itemKey,
            shardKey,
            firstSeenAt: item.firstSeenAt,
          });
        }
      }
      return found;
    });
    for (const found of perShard) {
      candidates.push(...found);
    }
    candidates.sort((left, right) => (
      left.firstSeenAt.localeCompare(right.firstSeenAt) ||
      left.itemKey.localeCompare(right.itemKey)
    ));

    const selectedByShard = new Map();
    for (const candidate of candidates.slice(0, limit)) {
      const selected = selectedByShard.get(candidate.shardKey) || new Set();
      selected.add(candidate.itemKey);
      selectedByShard.set(candidate.shardKey, selected);
    }

    const claims = [];
    for (const [shardKey, selected] of selectedByShard) {
      const mutation = await this.#mutate(shardKey, emptyShard, (shard) => {
        validateShard(shard);
        const claimed = [];
        for (const itemKey of selected) {
          const item = shard.items[itemKey];
          if (
            !item ||
            item.phase !== "queued" ||
            (item.notBefore && Date.parse(item.notBefore) > this.now())
          ) {
            continue;
          }
          item.phase = "inflight";
          item.dirty = false;
          item.leaseOwner = workerId;
          item.leaseExpiresAt = this.#isoAfter(this.itemLeaseMs);
          item.claimedGeneration = item.generation;
          claimed.push({
            itemKey,
            shardKey,
            claimGeneration: item.claimedGeneration,
            repository: item.repository,
            prNumber: item.prNumber,
            headSha: item.headSha,
            triggerEvents: [...item.triggerEvents],
            attempts: item.attempts,
          });
        }
        return {
          changed: claimed.length > 0,
          result: claimed,
        };
      });
      claims.push(...mutation.result);
    }
    return claims;
  }

  async acknowledge({
    itemKey,
    claimGeneration,
    workerId,
    outcome,
    error = "",
    retryAfterMs = 0,
    operationId = "",
  }) {
    validateWorkerId(workerId);
    if (!["success", "retry", "dead"].includes(outcome)) {
      throw new Error(`unsupported acknowledgment outcome ${outcome}`);
    }
    const shardKey = queueShardKey(itemKey, this.shardCount);
    const receiptKey = operationId ? operationKey([operationId]) : "";
    const signature = JSON.stringify([
      itemKey,
      claimGeneration,
      workerId,
      outcome,
      error,
      retryAfterMs,
    ]);
    const mutation = await this.#mutate(shardKey, emptyShard, (shard) => {
      validateShard(shard);
      const acknowledgments = shard.acknowledgments || {};
      const receipt = receiptKey ? acknowledgments[receiptKey] : null;
      if (receipt) {
        if (receipt.signature !== signature) {
          throw new Error(`conflicting acknowledgment retry for ${itemKey}`);
        }
        return { changed: false, result: receipt.result };
      }
      const item = shard.items[itemKey];
      if (!item) {
        throw new Error(`queue item ${itemKey} does not exist`);
      }
      validateItem(itemKey, item);
      if (
        item.phase !== "inflight" ||
        item.leaseOwner !== workerId ||
        item.claimedGeneration !== claimGeneration ||
        isExpired(item.leaseExpiresAt, this.now())
      ) {
        throw new Error(`stale or unauthorized acknowledgment for ${itemKey}`);
      }
      if (outcome === "success" && !item.dirty && item.generation === claimGeneration) {
        delete shard.items[itemKey];
        const result = { status: "removed" };
        if (receiptKey) {
          recordAcknowledgment(
            shard,
            receiptKey,
            signature,
            result,
            this.#isoNow(),
            this.acknowledgmentReceiptLimit,
          );
        }
        return { changed: true, result };
      }
      if (
        outcome === "dead" &&
        !item.dirty &&
        item.generation === claimGeneration
      ) {
        const deadKey = `${itemKey}@${item.generation}`;
        shard.deadLetters[deadKey] = {
          ...publicItem(item),
          error: normalizeError(error),
          failedAt: this.#isoNow(),
        };
        trimDeadLetters(shard.deadLetters);
        delete shard.items[itemKey];
        const result = { status: "dead", deadKey };
        if (receiptKey) {
          recordAcknowledgment(
            shard,
            receiptKey,
            signature,
            result,
            this.#isoNow(),
            this.acknowledgmentReceiptLimit,
          );
        }
        return { changed: true, result };
      }

      const hasFollowUp = item.dirty || item.generation !== claimGeneration;
      item.phase = "queued";
      item.dirty = false;
      item.leaseOwner = null;
      item.leaseExpiresAt = null;
      item.claimedGeneration = null;
      item.attempts = outcome === "retry" && !hasFollowUp ? item.attempts + 1 : 0;
      item.notBefore = outcome === "retry" && !hasFollowUp && retryAfterMs > 0
        ? this.#isoAfter(retryAfterMs)
        : null;
      const result = {
        status: outcome === "retry" && !hasFollowUp ? "retry" : "follow_up",
        attempts: item.attempts,
      };
      if (receiptKey) {
        recordAcknowledgment(
          shard,
          receiptKey,
          signature,
          result,
          this.#isoNow(),
          this.acknowledgmentReceiptLimit,
        );
      }
      return { changed: true, result };
    });
    return mutation.result;
  }

  async finishDispatcher({ generation, workerId }) {
    validateWorkerId(workerId);
    const receiptKey = operationKey([generation, workerId]);
    const released = await this.#mutate(DISPATCHER_KEY, emptyDispatcher, (dispatcher) => {
      validateDispatcher(dispatcher);
      const receipt = dispatcher.finishes?.[receiptKey];
      if (receipt) {
        if (receipt.dispatchState === "failed") {
          receipt.result = null;
          receipt.dispatchState = "none";
          receipt.completedAt = this.#isoNow();
          return { changed: true, result: receipt };
        }
        return { changed: false, result: receipt };
      }
      if (
        dispatcher.phase !== "active" ||
        dispatcher.generation !== generation ||
        dispatcher.leaseOwner !== workerId ||
        isExpired(dispatcher.leaseExpiresAt, this.now())
      ) {
        return { changed: false, result: false };
      }
      setDispatcherIdle(dispatcher, this.#isoNow());
      dispatcher.finishes ||= {};
      dispatcher.finishes[receiptKey] = {
        generation,
        workerId,
        result: null,
        dispatchState: "none",
        completedAt: this.#isoNow(),
      };
      trimReceipts(dispatcher.finishes, this.finishReceiptLimit);
      return { changed: true, result: dispatcher.finishes[receiptKey] };
    });
    if (!released.result) {
      throw new Error("worker does not own the active dispatcher");
    }
    if (released.result.result) {
      return released.result.result;
    }
    let result = { requested: false };
    if (await this.hasRunnableItems()) {
      const request = await this.requestDispatcher(
        `successor:${generation}:${workerId}`,
        { replayExisting: true },
      );
      result = {
        requested: request.acquired,
        generation: request.generation,
        requestOwner: request.requestOwner,
      };
    }
    const completed = await this.#mutate(DISPATCHER_KEY, emptyDispatcher, (dispatcher) => {
      validateDispatcher(dispatcher);
      const receipt = dispatcher.finishes?.[receiptKey];
      if (!receipt) {
        throw new Error("dispatcher finish receipt disappeared");
      }
      if (receipt.result) {
        return { changed: false, result: receipt.result };
      }
      receipt.result = result;
      receipt.dispatchState = result.requested ? "pending" : "none";
      receipt.completedAt = this.#isoNow();
      return { changed: true, result };
    });
    return completed.result;
  }

  async claimFinishDispatch({ generation, workerId }) {
    const receiptKey = operationKey([generation, workerId]);
    const mutation = await this.#mutate(DISPATCHER_KEY, emptyDispatcher, (dispatcher) => {
      validateDispatcher(dispatcher);
      const receipt = dispatcher.finishes?.[receiptKey];
      if (receipt?.dispatchState === "dispatched") {
        return { changed: false, result: "completed" };
      }
      if (receipt?.dispatchState === "dispatching") {
        return { changed: false, result: "in_progress" };
      }
      if (
        !receipt?.result?.requested ||
        receipt.dispatchState !== "pending" ||
        dispatcher.phase !== "requested" ||
        dispatcher.generation !== receipt.result.generation ||
        dispatcher.leaseOwner !== receipt.result.requestOwner ||
        isExpired(dispatcher.leaseExpiresAt, this.now())
      ) {
        return { changed: false, result: "unavailable" };
      }
      receipt.dispatchState = "dispatching";
      receipt.completedAt = this.#isoNow();
      return { changed: true, result: "claimed" };
    });
    return mutation.result;
  }

  async completeFinishDispatch({ generation, workerId }) {
    return this.#setFinishDispatchState(generation, workerId, "dispatching", "dispatched");
  }

  async failFinishDispatch({ generation, workerId }) {
    return this.#setFinishDispatchState(generation, workerId, "dispatching", "failed");
  }

  async failFinishDispatchWithRelease(
    { generation: finishGeneration, workerId },
    { generation: dispatchGeneration, requestOwner },
  ) {
    const receiptKey = operationKey([finishGeneration, workerId]);
    const mutation = await this.#mutate(DISPATCHER_KEY, emptyDispatcher, (dispatcher) => {
      validateDispatcher(dispatcher);
      const receipt = dispatcher.finishes?.[receiptKey];
      if (!receipt || receipt.dispatchState !== "dispatching") {
        return { changed: false, result: false };
      }
      if (
        dispatcher.phase === "requested" &&
        dispatcher.generation === dispatchGeneration &&
        dispatcher.leaseOwner === requestOwner
      ) {
        setDispatcherIdle(dispatcher, this.#isoNow());
      }
      receipt.dispatchState = "failed";
      receipt.completedAt = this.#isoNow();
      return { changed: true, result: true };
    });
    return mutation.result;
  }

  async hasRunnableItems() {
    const runnable = await this.#mapShards(async (shardKey) => {
      const entry = await this.store.get(shardKey);
      if (!entry) {
        return false;
      }
      validateShard(entry.value);
      return Object.values(entry.value.items).some(
        (item) => item.phase === "queued" &&
          (!item.notBefore || Date.parse(item.notBefore) <= this.now()),
      );
    });
    return runnable.some(Boolean);
  }

  async recoverExpiredLeases() {
    let recoveredItems = 0;
    let abandonedItems = 0;
    const recoveries = await this.#mapShards(async (shardKey) => {
      const mutation = await this.#mutate(shardKey, emptyShard, (shard) => {
        validateShard(shard);
        let recovered = 0;
        let abandoned = 0;
        for (const [itemKey, item] of Object.entries(shard.items)) {
          validateItem(itemKey, item);
          if (
            item.phase !== "inflight" ||
            !isExpired(item.leaseExpiresAt, this.now())
          ) {
            continue;
          }
          if (item.dirty) {
            item.attempts = 0;
          } else {
            item.attempts += 1;
          }
          if (!item.dirty && item.attempts >= this.maxRecoveryAttempts) {
            shard.deadLetters[`${itemKey}@${item.generation}`] = {
              ...publicItem(item),
              error: `queue item lease expired ${item.attempts} times without an acknowledgment`,
              failedAt: this.#isoNow(),
            };
            trimDeadLetters(shard.deadLetters);
            delete shard.items[itemKey];
            abandoned += 1;
            continue;
          }
          item.phase = "queued";
          item.dirty = false;
          item.leaseOwner = null;
          item.leaseExpiresAt = null;
          item.claimedGeneration = null;
          item.notBefore = null;
          recovered += 1;
        }
        return {
          changed: recovered + abandoned > 0,
          result: { recovered, abandoned },
        };
      });
      return mutation.result;
    });
    for (const { recovered, abandoned } of recoveries) {
      recoveredItems += recovered;
      abandonedItems += abandoned;
    }

    const dispatcher = await this.#mutate(DISPATCHER_KEY, emptyDispatcher, (state) => {
      validateDispatcher(state);
      if (state.phase === "idle" || !isExpired(state.leaseExpiresAt, this.now())) {
        return { changed: false, result: false };
      }
      setDispatcherIdle(state, this.#isoNow());
      return { changed: true, result: true };
    });
    const request = await this.hasRunnableItems()
      ? await this.requestDispatcher("lease-recovery")
      : { acquired: false };
    return {
      recoveredItems,
      abandonedItems,
      recoveredDispatcher: dispatcher.result,
      requested: request.acquired,
      generation: request.generation,
      requestOwner: request.requestOwner,
    };
  }

  async stats() {
    const counts = {
      queued: 0,
      inflight: 0,
      dirty: 0,
      deadLetters: 0,
      oldestQueuedAt: null,
    };
    const shardCounts = await this.#mapShards(async (shardKey) => {
      const entry = await this.store.get(shardKey);
      if (!entry) {
        return null;
      }
      validateShard(entry.value);
      const shard = {
        queued: 0,
        inflight: 0,
        dirty: 0,
        deadLetters: Object.keys(entry.value.deadLetters).length,
        oldestQueuedAt: null,
      };
      for (const [itemKey, item] of Object.entries(entry.value.items)) {
        validateItem(itemKey, item);
        shard[item.phase] += 1;
        if (item.dirty) {
          shard.dirty += 1;
        }
        if (
          item.phase === "queued" &&
          (!shard.oldestQueuedAt || item.firstSeenAt < shard.oldestQueuedAt)
        ) {
          shard.oldestQueuedAt = item.firstSeenAt;
        }
      }
      return shard;
    });
    for (const shard of shardCounts) {
      if (!shard) {
        continue;
      }
      counts.queued += shard.queued;
      counts.inflight += shard.inflight;
      counts.dirty += shard.dirty;
      counts.deadLetters += shard.deadLetters;
      if (
        shard.oldestQueuedAt &&
        (!counts.oldestQueuedAt || shard.oldestQueuedAt < counts.oldestQueuedAt)
      ) {
        counts.oldestQueuedAt = shard.oldestQueuedAt;
      }
    }
    const dispatcher = await this.#readDispatcher();
    return {
      ...counts,
      dispatcher,
    };
  }

  async #readDispatcher() {
    const entry = await this.store.get(DISPATCHER_KEY);
    const dispatcher = entry ? entry.value : emptyDispatcher();
    validateDispatcher(dispatcher);
    return dispatcher;
  }

  async #setFinishDispatchState(generation, workerId, expected, next) {
    const receiptKey = operationKey([generation, workerId]);
    const mutation = await this.#mutate(DISPATCHER_KEY, emptyDispatcher, (dispatcher) => {
      validateDispatcher(dispatcher);
      const receipt = dispatcher.finishes?.[receiptKey];
      if (!receipt || receipt.dispatchState !== expected) {
        return { changed: false, result: false };
      }
      receipt.dispatchState = next;
      receipt.completedAt = this.#isoNow();
      return { changed: true, result: true };
    });
    return mutation.result;
  }

  async #mutate(key, emptyValue, mutator) {
    for (let attempt = 0; attempt < this.casAttempts; attempt += 1) {
      const entry = await this.store.get(key);
      const value = structuredClone(entry ? entry.value : emptyValue());
      const mutation = mutator(value);
      if (!mutation.changed) {
        return mutation;
      }
      const condition = entry
        ? { onlyIfMatch: requiredEtag(key, entry.etag) }
        : { onlyIfNew: true };
      const write = await this.store.set(key, value, condition);
      if (write.modified) {
        return mutation;
      }
      if (attempt + 1 < this.casAttempts) {
        await sleep(Math.min(5 * (attempt + 1), 25));
      }
    }
    throw new Error(`queue compare-and-swap retries exhausted for ${key}`);
  }

  #shardKeys() {
    return Array.from(
      { length: this.shardCount },
      (_, index) => {
        const width = Math.max(2, Math.ceil(Math.log2(this.shardCount) / 4));
        return `shards/${index.toString(16).padStart(width, "0")}`;
      },
    );
  }

  // Results stay in shard order so callers that sort or aggregate them see the
  // same answer regardless of how the round trips interleave.
  async #mapShards(handler) {
    const keys = this.#shardKeys();
    const results = new Array(keys.length);
    let next = 0;
    const lanes = Math.max(1, Math.min(this.shardConcurrency, keys.length));
    await Promise.all(Array.from({ length: lanes }, async () => {
      while (next < keys.length) {
        const index = next;
        next += 1;
        results[index] = await handler(keys[index]);
      }
    }));
    return results;
  }

  #isoNow() {
    return new Date(this.now()).toISOString();
  }

  #isoAfter(durationMs) {
    return new Date(this.now() + durationMs).toISOString();
  }
}

function emptyShard() {
  return {
    schema: SCHEMA_VERSION,
    items: {},
    deadLetters: {},
    acknowledgments: {},
  };
}

function emptyDispatcher() {
  return {
    schema: SCHEMA_VERSION,
    phase: "idle",
    generation: 0,
    leaseOwner: null,
    leaseExpiresAt: null,
    updatedAt: null,
    finishes: {},
  };
}

function validateRepository(repository) {
  if (typeof repository !== "string" || !REPOSITORY_PATTERN.test(repository)) {
    throw new Error("queue item repository is invalid");
  }
}

function validateWorkerId(workerId) {
  if (
    typeof workerId !== "string" ||
    workerId.length < 1 ||
    workerId.length > 200
  ) {
    throw new Error("workerId must be a non-empty string no longer than 200 characters");
  }
}

function validateShard(shard) {
  if (
    !shard ||
    shard.schema !== SCHEMA_VERSION ||
    !isPlainObject(shard.items) ||
    !isPlainObject(shard.deadLetters) ||
    (shard.acknowledgments !== undefined && !isPlainObject(shard.acknowledgments))
  ) {
    throw new Error("queue shard has an unsupported schema");
  }
}

function validateDispatcher(dispatcher) {
  if (
    !dispatcher ||
    dispatcher.schema !== SCHEMA_VERSION ||
    !["idle", "requested", "active"].includes(dispatcher.phase) ||
    !Number.isInteger(dispatcher.generation) ||
    dispatcher.generation < 0 ||
    (dispatcher.finishes !== undefined && !isPlainObject(dispatcher.finishes))
  ) {
    throw new Error("dispatcher has an unsupported schema");
  }
}

function validateItem(itemKey, item) {
  if (
    !item ||
    !["queued", "inflight"].includes(item.phase) ||
    !Number.isInteger(item.generation) ||
    item.generation < 1 ||
    typeof item.dirty !== "boolean" ||
    !Number.isInteger(item.attempts) ||
    item.attempts < 0
  ) {
    throw new Error(`queue item ${itemKey} has an unsupported schema`);
  }
  queueItemKey(item);
}

function normalizeTriggerEvent(triggerEvent) {
  if (!triggerEvent) {
    return "";
  }
  if (
    typeof triggerEvent !== "string" ||
    !/^[a-z_]{1,80}$/.test(triggerEvent)
  ) {
    throw new Error("triggerEvent is invalid");
  }
  return triggerEvent;
}

function mergeTriggerEvents(events, event) {
  const merged = new Set(Array.isArray(events) ? events : []);
  if (event) {
    merged.add(event);
  }
  return [...merged].sort().slice(-20);
}

function setDispatcherIdle(dispatcher, updatedAt) {
  dispatcher.phase = "idle";
  dispatcher.leaseOwner = null;
  dispatcher.leaseExpiresAt = null;
  dispatcher.updatedAt = updatedAt;
}

function isExpired(leaseExpiresAt, now) {
  return typeof leaseExpiresAt === "string" && Date.parse(leaseExpiresAt) <= now;
}

// A blank ETag makes `onlyIfMatch` a no-op condition, which turns a
// compare-and-swap into a blind overwrite of whatever another writer stored.
function requiredEtag(key, etag) {
  if (typeof etag !== "string" || etag.length === 0) {
    throw new Error(`queue entry ${key} was returned without an ETag`);
  }
  return etag;
}

function publicItem(item) {
  return {
    repository: item.repository,
    prNumber: item.prNumber,
    headSha: item.headSha,
    generation: item.generation,
    attempts: item.attempts,
    firstSeenAt: item.firstSeenAt,
    lastSeenAt: item.lastSeenAt,
    triggerEvents: [...item.triggerEvents],
  };
}

function trimDeadLetters(deadLetters, limit = 100) {
  const entries = Object.entries(deadLetters);
  if (entries.length <= limit) {
    return;
  }
  entries.sort((left, right) => left[1].failedAt.localeCompare(right[1].failedAt));
  for (const [key] of entries.slice(0, entries.length - limit)) {
    delete deadLetters[key];
  }
}

function recordAcknowledgment(
  shard,
  receiptKey,
  signature,
  result,
  completedAt,
  limit,
) {
  shard.acknowledgments ||= {};
  shard.acknowledgments[receiptKey] = {
    signature,
    result,
    completedAt,
  };
  trimReceipts(shard.acknowledgments, limit);
}

function trimReceipts(receipts, limit) {
  const entries = Object.entries(receipts);
  if (entries.length <= limit) {
    return;
  }
  entries.sort((left, right) => left[1].completedAt.localeCompare(right[1].completedAt));
  for (const [key] of entries.slice(0, entries.length - limit)) {
    delete receipts[key];
  }
}

function operationKey(parts) {
  return crypto.createHash("sha256").update(JSON.stringify(parts)).digest("hex");
}

function normalizeError(error) {
  return String(error || "unspecified failure").slice(0, 1000);
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function sleep(durationMs) {
  return new Promise((resolve) => setTimeout(resolve, durationMs));
}
