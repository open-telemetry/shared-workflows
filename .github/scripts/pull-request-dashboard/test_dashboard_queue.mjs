import assert from "node:assert/strict";
import test from "node:test";

import {
  DashboardQueue,
  DEFAULT_SHARD_CONCURRENCY,
  queueItemKey,
  queueShardKey,
} from "./netlify/lib/dashboard-queue.mjs";

class MemoryStore {
  constructor() {
    this.entries = new Map();
    this.version = 0;
    this.beforeSet = null;
    this.beforeGet = null;
    this.dropEtags = false;
  }

  async get(key) {
    if (this.beforeGet) {
      await this.beforeGet(key);
    }
    const entry = this.entries.get(key);
    if (!entry) {
      return null;
    }
    const copy = structuredClone(entry);
    return this.dropEtags ? { ...copy, etag: undefined } : copy;
  }

  async set(key, value, condition) {
    if (this.beforeSet) {
      await this.beforeSet(key, value, condition);
    }
    const existing = this.entries.get(key);
    if (condition.onlyIfNew && existing) {
      return { modified: false };
    }
    if (condition.onlyIfMatch && (!existing || existing.etag !== condition.onlyIfMatch)) {
      return { modified: false };
    }
    this.version += 1;
    const etag = `"${this.version}"`;
    this.entries.set(key, {
      etag,
      value: structuredClone(value),
    });
    return { modified: true, etag };
  }
}

function fixture(lane = "live") {
  let now = Date.parse("2026-08-19T20:00:00Z");
  let sequence = 0;
  const store = new MemoryStore();
  const createQueue = (queueLane) => new DashboardQueue({
    store,
    lane: queueLane,
    now: () => now,
    randomId: () => `id-${++sequence}`,
    itemLeaseMs: 1_000,
    dispatcherRequestLeaseMs: 1_000,
    dispatcherActiveLeaseMs: 1_000,
  });
  const queue = createQueue(lane);
  return {
    queue,
    store,
    createQueue,
    advance(milliseconds) {
      now += milliseconds;
    },
  };
}

test("live refreshes run while an older maintenance batch owns its dispatcher", async () => {
  const { queue: live, createQueue, advance } = fixture();
  const maintenance = createQueue("maintenance");
  const backfill = await maintenance.enqueue({ repository: "example", kind: "backfill" });
  const hourly = await maintenance.requestDispatcher();
  await maintenance.activateDispatcher({ generation: hourly.generation, workerId: "hourly" });
  const [hourlyClaim] = await maintenance.claimWave({ generation: hourly.generation, workerId: "hourly" });
  advance(100);
  await live.enqueue({ repository: "example", prNumber: 1 });
  const event = await live.requestDispatcher();
  assert.equal(event.acquired, true);
  await live.activateDispatcher({ generation: event.generation, workerId: "live" });
  const [liveClaim] = await live.claimWave({ generation: event.generation, workerId: "live" });
  assert.equal(liveClaim.prNumber, 1);
  await live.acknowledge({
    ...liveClaim, generation: event.generation, workerId: "live", outcome: "success",
  });
  assert.equal((await maintenance.itemStatus(backfill)).status, "inflight");
  await assert.rejects(live.acknowledge({
    ...hourlyClaim, generation: event.generation, workerId: "live", outcome: "success",
  }), /another lane/);
  assert.equal((await live.finishDispatcher({
    generation: event.generation, workerId: "live",
  })).requested, false);
  assert.equal((await maintenance.stats()).inflight, 1);
  assert.equal((await live.stats()).inflight, 0);
});

test("a runnerless maintenance dispatcher does not block live ownership or recovery", async () => {
  const { queue: live, createQueue, advance } = fixture();
  const maintenance = createQueue("maintenance");
  await maintenance.enqueue({ repository: "example", kind: "backfill" });
  const stranded = await maintenance.requestDispatcher();
  advance(500);
  await live.enqueue({ repository: "example", prNumber: 1 });
  const event = await live.requestDispatcher();
  await live.activateDispatcher({ generation: event.generation, workerId: "live" });
  await live.claimWave({ generation: event.generation, workerId: "live" });
  advance(501);
  const recovery = await maintenance.recoverExpiredLeases();
  assert.equal(recovery.requested, true);
  assert.ok(recovery.generation > stranded.generation);
  assert.equal((await live.stats()).dispatcher.leaseOwner, "live");
  assert.equal((await live.stats()).inflight, 1);
  assert.equal((await live.heartbeat({ generation: event.generation, workerId: "live" })).items, 1);
});

test("lanes can claim existing shard records without changing their request identities", async () => {
  const { queue: live, createQueue, store } = fixture();
  const maintenance = createQueue("maintenance");
  const request = await maintenance.enqueue({ repository: "example", kind: "backfill" });
  const before = structuredClone(store.entries.get(request.shardKey).value.items[request.itemKey]);
  const liveDispatcher = await live.requestDispatcher();
  await live.activateDispatcher({ generation: liveDispatcher.generation, workerId: "live" });
  assert.deepEqual(await live.claimWave({ generation: liveDispatcher.generation, workerId: "live" }), []);
  assert.deepEqual(store.entries.get(request.shardKey).value.items[request.itemKey], before);
  const hourly = await maintenance.requestDispatcher();
  await maintenance.activateDispatcher({ generation: hourly.generation, workerId: "hourly" });
  await maintenance.claimWave({ generation: hourly.generation, workerId: "hourly" });
  assert.equal((await maintenance.itemStatus(request)).requestId, request.requestId);
});

test("repository delivery ownership is shared across lanes and fenced by its generation", async () => {
  const { queue: live, createQueue } = fixture();
  const maintenance = createQueue("maintenance");
  const hourly = await maintenance.requestDispatcher();
  const event = await live.requestDispatcher();
  await maintenance.activateDispatcher({ generation: hourly.generation, workerId: "hourly" });
  await live.activateDispatcher({ generation: event.generation, workerId: "live" });
  const hourlyOwner = { repository: "example", generation: hourly.generation, workerId: "hourly" };
  const liveOwner = { repository: "example", generation: event.generation, workerId: "live" };
  const lease = await maintenance.acquireDeliveryLease(hourlyOwner);
  assert.equal(lease.acquired, true);
  assert.deepEqual(await maintenance.acquireDeliveryLease(hourlyOwner), lease);
  assert.deepEqual(await live.acquireDeliveryLease(liveOwner), { acquired: false });
  assert.equal((await live.acquireDeliveryLease({ ...liveOwner, repository: "other" })).acquired, true);
  assert.equal((await maintenance.releaseDeliveryLease({
    ...hourlyOwner, leaseGeneration: lease.leaseGeneration,
  })).released, true);
  assert.equal((await maintenance.releaseDeliveryLease({
    ...hourlyOwner, leaseGeneration: lease.leaseGeneration,
  })).released, true);
  const next = await live.acquireDeliveryLease(liveOwner);
  assert.equal(next.acquired, true);
  assert.ok(next.leaseGeneration > lease.leaseGeneration);
  assert.equal((await maintenance.releaseDeliveryLease({
    ...hourlyOwner, leaseGeneration: lease.leaseGeneration,
  })).released, false);
});

test("heartbeats renew delivery ownership and abandonment allows another lane to acquire it", async () => {
  const { queue: live, createQueue, advance } = fixture();
  const maintenance = createQueue("maintenance");
  const event = await live.requestDispatcher();
  const hourly = await maintenance.requestDispatcher();
  await live.activateDispatcher({ generation: event.generation, workerId: "live" });
  await maintenance.activateDispatcher({ generation: hourly.generation, workerId: "hourly" });
  const liveOwner = { repository: "example", generation: event.generation, workerId: "live" };
  const hourlyOwner = { repository: "example", generation: hourly.generation, workerId: "hourly" };
  const lease = await live.acquireDeliveryLease(liveOwner);
  advance(800);
  await live.heartbeat(liveOwner);
  await maintenance.heartbeat(hourlyOwner);
  advance(400);
  assert.deepEqual(await maintenance.acquireDeliveryLease(hourlyOwner), { acquired: false });
  advance(500);
  await maintenance.heartbeat(hourlyOwner);
  advance(200);
  const replacement = await maintenance.acquireDeliveryLease(hourlyOwner);
  assert.equal(replacement.acquired, true);
  assert.equal((await live.releaseDeliveryLease({
    ...liveOwner, leaseGeneration: lease.leaseGeneration,
  })).released, false);
  await assert.rejects(live.acquireDeliveryLease(liveOwner), /active dispatcher/);
});

test("a stranded request cannot prevent a replacement generation from running", async () => {
  const { queue, advance } = fixture();
  await queue.enqueue({ repository: "example", prNumber: 1 });
  const stranded = await queue.requestDispatcher("no-runner");
  advance(1_001);
  const recovery = await queue.recoverExpiredLeases();
  assert.equal(recovery.requested, true);
  assert.equal(await queue.activateDispatcher({ generation: recovery.generation, workerId: "replacement" }), true);
  assert.equal(await queue.activateDispatcher({ generation: stranded.generation, workerId: "stranded" }), false);
  assert.equal(await queue.activateDispatcher({ generation: recovery.generation, workerId: "duplicate" }), false);
  const [claim] = await queue.claimWave({ generation: recovery.generation, workerId: "replacement" });
  assert.equal(claim.prNumber, 1);
  await assert.rejects(queue.claimWave({ generation: stranded.generation, workerId: "stranded" }), /active dispatcher/);
});

test("recovery can dispatch a replacement before a runnerless request lease expires", async () => {
  const { queue, advance } = fixture();
  queue.dispatcherRequestLeaseMs = 60 * 60 * 1_000;
  await queue.enqueue({ repository: "example", prNumber: 1 });
  const original = await queue.requestDispatcher("runnerless");
  advance(15 * 60 * 1_000);
  const recovery = await queue.recoverExpiredLeases({ redispatchRequested: true });
  assert.equal(recovery.recoveredDispatcher, false);
  assert.equal(recovery.requested, true);
  assert.equal(recovery.replayed, true);
  assert.equal(recovery.generation, original.generation);
  assert.equal(await queue.activateDispatcher({ generation: recovery.generation, workerId: "replacement" }), true);
  assert.equal(await queue.activateDispatcher({ generation: original.generation, workerId: "runnerless" }), false);
  assert.equal((await queue.claimWave({ generation: recovery.generation, workerId: "replacement" })).length, 1);
});

test("dispatcher expiry fences acknowledgments even before an item lease expires", async () => {
  const { queue, advance } = fixture();
  queue.itemLeaseMs = 10_000;
  await queue.enqueue({ repository: "example", prNumber: 1 });
  const dispatcher = await queue.requestDispatcher();
  await queue.activateDispatcher({ generation: dispatcher.generation, workerId: "worker" });
  const [claim] = await queue.claimWave({ generation: dispatcher.generation, workerId: "worker" });
  advance(1_001);
  assert.deepEqual(await queue.heartbeat({ generation: dispatcher.generation, workerId: "worker" }), { dispatcher: false, items: 0 });
  await assert.rejects(queue.acknowledge({
    generation: dispatcher.generation, workerId: "worker", itemKey: claim.itemKey,
    claimGeneration: claim.claimGeneration, outcome: "success",
  }), /active dispatcher/);
});

test("a reused worker identity cannot heartbeat or acknowledge an older generation's items", async () => {
  const { queue, advance } = fixture();
  queue.itemLeaseMs = 10_000;
  await queue.enqueue({ repository: "example", prNumber: 1 });
  const first = await queue.requestDispatcher();
  await queue.activateDispatcher({ generation: first.generation, workerId: "worker" });
  const [claim] = await queue.claimWave({ generation: first.generation, workerId: "worker" });
  advance(1_001);
  const replacement = await queue.requestDispatcher();
  await queue.activateDispatcher({ generation: replacement.generation, workerId: "worker" });
  assert.equal((await queue.heartbeat({ generation: replacement.generation, workerId: "worker" })).items, 0);
  await assert.rejects(queue.acknowledge({
    ...claim, generation: replacement.generation, workerId: "worker", outcome: "success",
  }), /stale or unauthorized/);
  assert.equal((await queue.stats()).inflight, 1);
});

test("acknowledgments require dispatcher generation even with a valid item lease", async () => {
  const { queue } = fixture();
  const request = await queue.enqueue({ repository: "example", prNumber: 1 });
  const dispatcher = await queue.requestDispatcher();
  await queue.activateDispatcher({ generation: dispatcher.generation, workerId: "worker" });
  const [claim] = await queue.claimWave({ generation: dispatcher.generation, workerId: "worker" });
  await assert.rejects(queue.acknowledge({
    ...claim, workerId: "worker", outcome: "success",
  }), /active dispatcher/);
  assert.equal((await queue.itemStatus(request)).status, "inflight");
  await queue.acknowledge({
    ...claim, generation: dispatcher.generation, workerId: "worker", outcome: "success",
    operationId: "completed",
  });
  assert.equal((await queue.itemStatus(request)).status, "completed");
});

test("stored refresh records without dispatcher fencing recover before being claimed again", async () => {
  const { queue, store, advance } = fixture();
  const request = await queue.enqueue({ repository: "example", prNumber: 1 });
  const dispatcher = await queue.requestDispatcher();
  await queue.activateDispatcher({ generation: dispatcher.generation, workerId: "worker" });
  const [claim] = await queue.claimWave({ generation: dispatcher.generation, workerId: "worker" });
  delete store.entries.get(request.shardKey).value.items[request.itemKey].dispatcherGeneration;
  assert.equal((await queue.heartbeat({ generation: dispatcher.generation, workerId: "worker" })).items, 0);
  await assert.rejects(queue.acknowledge({
    ...claim, generation: dispatcher.generation, workerId: "worker", outcome: "success",
  }), /stale or unauthorized/);
  advance(1_001);
  const recovery = await queue.recoverExpiredLeases();
  await queue.activateDispatcher({ generation: recovery.generation, workerId: "replacement" });
  const [replacement] = await queue.claimWave({ generation: recovery.generation, workerId: "replacement" });
  assert.equal(replacement.kind, undefined);
  assert.equal((await queue.itemStatus(request)).requestId, request.requestId);
  await queue.acknowledge({
    ...replacement, generation: recovery.generation, workerId: "replacement", outcome: "success",
    operationId: "completed",
  });
  assert.equal((await queue.itemStatus(request)).status, "completed");
});

test("stored completion receipts remain readable without dispatcher generation", async () => {
  const { queue, store } = fixture();
  const request = await queue.enqueue({ repository: "example", prNumber: 1 });
  const dispatcher = await queue.requestDispatcher();
  await queue.activateDispatcher({ generation: dispatcher.generation, workerId: "worker" });
  const [claim] = await queue.claimWave({ generation: dispatcher.generation, workerId: "worker" });
  const acknowledgment = {
    ...claim, generation: dispatcher.generation, workerId: "worker", outcome: "success",
    operationId: "completed",
  };
  await queue.acknowledge(acknowledgment);
  const [receipt] = Object.values(store.entries.get(request.shardKey).value.acknowledgments);
  delete receipt.dispatcherGeneration;
  assert.equal((await queue.itemStatus(request)).status, "completed");
  await assert.rejects(queue.acknowledge(acknowledgment), /stale acknowledgment receipt/);
});

test("backfill continuation survives deployment and completes without consuming retries", async () => {
  const { queue, store } = fixture("maintenance");
  const request = await queue.enqueue({ repository: "example", kind: "backfill", triggerEvent: "workflow_dispatch" });
  await queue.enqueue({ repository: "example", kind: "backfill", triggerEvent: "schedule" });
  const dispatcher = await queue.requestDispatcher();
  await queue.activateDispatcher({ generation: dispatcher.generation, workerId: "worker" });
  const [claim] = await queue.claimWave({ generation: dispatcher.generation, workerId: "worker" });
  assert.deepEqual(claim.triggerEvents, ["schedule", "workflow_dispatch"]);
  assert.equal(claim.kind, "backfill");
  assert.equal((await queue.acknowledge({
    ...claim, generation: dispatcher.generation, workerId: "worker", outcome: "continue",
  })).status, "continued");
  const redeployed = new DashboardQueue({ store, now: queue.now, lane: "maintenance" });
  const [resumed] = await redeployed.claimWave({ generation: dispatcher.generation, workerId: "worker" });
  assert.equal(resumed.attempts, 0);
  await redeployed.acknowledge({
    ...resumed, generation: dispatcher.generation, workerId: "worker",
    outcome: "success", operationId: "completed",
  });
  assert.equal((await redeployed.itemStatus(request)).status, "completed");
  const next = await redeployed.enqueue({ repository: "example", kind: "backfill" });
  assert.ok(next.generation > request.generation);
  assert.equal((await redeployed.itemStatus(next)).status, "queued");
});

test("pausing recovery preserves legacy refresh records without dispatching", async () => {
  const { queue, store, advance } = fixture();
  await queue.enqueue({ repository: "example", prNumber: 1 });
  const dispatcher = await queue.requestDispatcher();
  await queue.activateDispatcher({ generation: dispatcher.generation, workerId: "worker" });
  await queue.claimWave({ generation: dispatcher.generation, workerId: "worker" });
  advance(1_001);
  const recovery = await queue.recoverExpiredLeases({ requestSuccessor: false });
  assert.equal(recovery.requested, false);
  const rolledBack = new DashboardQueue({ store, now: queue.now });
  const replacement = await rolledBack.requestDispatcher();
  await rolledBack.activateDispatcher({ generation: replacement.generation, workerId: "legacy" });
  const [claim] = await rolledBack.claimWave({ generation: replacement.generation, workerId: "legacy" });
  assert.equal(claim.prNumber, 1);
  assert.equal(claim.kind, undefined);
});

test("receipt eviction cannot turn later work into completion of an earlier request", async () => {
  const { queue } = fixture();
  queue.acknowledgmentReceiptLimit = 0;
  const original = await queue.enqueue({ repository: "example", prNumber: 1 });
  const dispatcher = await queue.requestDispatcher();
  await queue.activateDispatcher({ generation: dispatcher.generation, workerId: "worker" });
  const [first] = await queue.claimWave({ generation: dispatcher.generation, workerId: "worker" });
  await queue.acknowledge({ ...first, generation: dispatcher.generation, workerId: "worker", outcome: "success", operationId: "first" });
  assert.equal((await queue.itemStatus(original)).status, "unknown");
  const next = await queue.enqueue({ repository: "example", prNumber: 1 });
  assert.equal(next.generation, original.generation);
  assert.notEqual(original.requestId, next.requestId);
  assert.equal((await queue.itemStatus(original)).status, "unknown");
  queue.acknowledgmentReceiptLimit = 100;
  const [second] = await queue.claimWave({ generation: dispatcher.generation, workerId: "worker" });
  await queue.acknowledge({ ...second, generation: dispatcher.generation, workerId: "worker", outcome: "success", operationId: "second" });
  assert.equal((await queue.itemStatus(next)).status, "completed");
  assert.equal((await queue.itemStatus(original)).status, "unknown");
});

test("unknown progress retains request identity even when its shard is absent", async () => {
  const { queue } = fixture();
  const request = { itemKey: "example#pr:1", generation: 1, requestId: "missing" };
  assert.deepEqual(await queue.itemStatus(request), { ...request, status: "unknown" });
});

test("builds stable PR and head queue keys", () => {
  assert.equal(
    queueItemKey({ repository: "example", prNumber: 123, headSha: "" }),
    "example#pr:123",
  );
  assert.equal(
    queueItemKey({
      repository: "example",
      prNumber: null,
      headSha: "a".repeat(40),
    }),
    `example#head:${"a".repeat(40)}`,
  );
  assert.equal(
    queueShardKey("example#pr:123"),
    queueShardKey("example#pr:123"),
  );
  assert.throws(
    () => queueItemKey({ repository: "example", prNumber: 1, headSha: "a".repeat(40) }),
    /exactly one/,
  );
});

test("coalesces repeated queued events", async () => {
  const { queue } = fixture();
  const first = await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });
  const second = await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "status",
  });

  assert.equal(first.status, "queued");
  assert.equal(second.status, "coalesced");
  assert.equal((await queue.stats()).queued, 1);
});

test("concurrent first writes converge to one item", async () => {
  const { queue } = fixture();
  const results = await Promise.all(
    Array.from({ length: 10 }, () => queue.enqueue({
      repository: "example",
      prNumber: 123,
      headSha: "",
      triggerEvent: "check_suite",
    })),
  );

  assert.equal(results.filter((result) => result.status === "queued").length, 1);
  assert.equal(results.filter((result) => result.status === "coalesced").length, 9);
  assert.equal((await queue.stats()).queued, 1);
});

test("only one webhook acquires the dispatcher", async () => {
  const { queue } = fixture();
  const requests = await Promise.all(
    Array.from({ length: 10 }, (_, index) => queue.requestDispatcher(`request-${index}`)),
  );

  assert.equal(requests.filter((request) => request.acquired).length, 1);
  assert.equal((await queue.stats()).dispatcher.phase, "requested");
});

test("event during processing requests exactly one follow-up", async () => {
  const { queue } = fixture();
  const enqueued = await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });
  const request = await queue.requestDispatcher("request");
  assert.equal(await queue.activateDispatcher({
    generation: request.generation,
    workerId: "worker",
  }), true);
  const [claim] = await queue.claimWave({
    generation: request.generation,
    workerId: "worker",
    limit: 4,
  });

  assert.equal(claim.itemKey, enqueued.itemKey);
  assert.equal((await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "status",
  })).status, "follow_up");
  assert.equal((await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "check_suite",
  })).status, "coalesced");

  const acknowledged = await queue.acknowledge({
    generation: request.generation,
    itemKey: claim.itemKey,
    claimGeneration: claim.claimGeneration,
    workerId: "worker",
    outcome: "success",
  });
  assert.equal(acknowledged.status, "follow_up");
  assert.equal((await queue.stats()).queued, 1);
});

test("clean success removes the claimed item", async () => {
  const { queue } = fixture();
  await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });
  const request = await queue.requestDispatcher("request");
  await queue.activateDispatcher({
    generation: request.generation,
    workerId: "worker",
  });
  const [claim] = await queue.claimWave({
    generation: request.generation,
    workerId: "worker",
  });

  assert.deepEqual(await queue.acknowledge({
    generation: request.generation,
    itemKey: claim.itemKey,
    claimGeneration: claim.claimGeneration,
    workerId: "worker",
    outcome: "success",
  }), { status: "removed" });
  assert.equal((await queue.stats()).queued, 0);
  assert.equal((await queue.stats()).inflight, 0);
});

test("claim wave skips item keys retried by the same worker run", async () => {
  const { queue } = fixture();
  for (const prNumber of [123, 456]) {
    await queue.enqueue({
      repository: "example",
      prNumber,
      headSha: "",
      triggerEvent: "pull_request",
    });
  }
  const request = await queue.requestDispatcher("request");
  await queue.activateDispatcher({
    generation: request.generation,
    workerId: "worker",
  });

  const claims = await queue.claimWave({
    generation: request.generation,
    workerId: "worker",
    limit: 2,
    excludeItemKeys: ["example#pr:123"],
  });

  assert.deepEqual(claims.map((claim) => claim.itemKey), ["example#pr:456"]);
});

test("claim wave can skip active repositories and cap distinct repositories", async () => {
  const { queue, advance } = fixture();
  for (const [repository, prNumber] of [
    ["a", 1], ["b", 1], ["a", 2], ["c", 1], ["d", 1], ["b", 2],
  ]) {
    await queue.enqueue({ repository, prNumber, headSha: "", triggerEvent: "pull_request" });
    advance(1);
  }
  const request = await queue.requestDispatcher("request");
  await queue.activateDispatcher({ generation: request.generation, workerId: "worker" });
  const claim = (options) => queue.claimWave({
    generation: request.generation,
    workerId: "worker",
    ...options,
  });

  const filtered = await claim({ limit: 10, excludeRepositories: ["a"], maxRepositories: 2 });
  assert.deepEqual(
    filtered.map((item) => item.itemKey).sort(),
    ["b#pr:1", "b#pr:2", "c#pr:1"],
  );

  const bounded = await claim({ limit: 1, maxRepositories: 4 });
  assert.deepEqual(bounded.map((item) => item.itemKey), ["a#pr:1"]);

  const legacy = await claim({ limit: 2 });
  assert.deepEqual(legacy.map((item) => item.itemKey).sort(), ["a#pr:2", "d#pr:1"]);
});

test("claim wave keeps existing semantics without repository filters", async () => {
  const { queue, advance } = fixture();
  for (const [repository, prNumber] of [["a", 1], ["a", 2], ["b", 1]]) {
    await queue.enqueue({ repository, prNumber, headSha: "", triggerEvent: "pull_request" });
    advance(1);
  }
  const request = await queue.requestDispatcher("request");
  await queue.activateDispatcher({ generation: request.generation, workerId: "worker" });

  const claims = await queue.claimWave({
    generation: request.generation,
    workerId: "worker",
    limit: 2,
  });

  assert.deepEqual(claims.map((item) => item.itemKey), ["a#pr:1", "a#pr:2"]);
});

test("claim wave rejects invalid repository filters", async () => {
  const { queue } = fixture();
  for (const options of [
    { maxRepositories: 0 },
    { maxRepositories: 101 },
    { maxRepositories: 1.5 },
    { maxRepositories: "2" },
  ]) {
    await assert.rejects(
      queue.claimWave({ generation: 1, workerId: "worker", ...options }),
      /claim repository limit must be an integer between 1 and 100/,
    );
  }
  for (const excludeRepositories of [
    "a",
    [""],
    [1],
    ["x".repeat(101)],
    Array.from({ length: 101 }, (_, index) => `repo-${index}`),
  ]) {
    await assert.rejects(
      queue.claimWave({ generation: 1, workerId: "worker", excludeRepositories }),
      /excluded repositories are invalid/,
    );
  }
});

test("replays an identical acknowledgment after its response is lost", async () => {
  const { queue } = fixture();
  await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });
  const request = await queue.requestDispatcher("request");
  await queue.activateDispatcher({
    generation: request.generation,
    workerId: "worker",
  });
  const [claim] = await queue.claimWave({
    generation: request.generation,
    workerId: "worker",
  });
  const acknowledgment = {
    generation: request.generation,
    itemKey: claim.itemKey,
    claimGeneration: claim.claimGeneration,
    workerId: "worker",
    outcome: "success",
    operationId: "acknowledgment-1",
  };

  assert.deepEqual(await queue.acknowledge(acknowledgment), { status: "removed" });
  assert.deepEqual(await queue.acknowledge(acknowledgment), { status: "removed" });
  await assert.rejects(
    queue.acknowledge({ ...acknowledgment, outcome: "retry" }),
    /conflicting acknowledgment retry/,
  );
});

test("replays a committed finish without reclaiming its dispatch", async () => {
  const { queue } = fixture();
  await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });
  const request = await queue.requestDispatcher("request");
  await queue.activateDispatcher({
    generation: request.generation,
    workerId: "worker",
  });
  const finish = {
    generation: request.generation,
    workerId: "worker",
  };

  const first = await queue.finishDispatcher(finish);
  assert.equal(first.requested, true);
  assert.equal(await queue.claimFinishDispatch(finish), "claimed");
  assert.equal(await queue.completeFinishDispatch(finish), true);

  assert.deepEqual(await queue.finishDispatcher(finish), first);
  assert.equal(await queue.claimFinishDispatch(finish), "completed");
});

test("a failed finish dispatch can request a new successor", async () => {
  const { queue } = fixture();
  await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });
  const request = await queue.requestDispatcher("request");
  await queue.activateDispatcher({
    generation: request.generation,
    workerId: "worker",
  });
  const finish = {
    generation: request.generation,
    workerId: "worker",
  };

  const first = await queue.finishDispatcher(finish);
  assert.equal(await queue.claimFinishDispatch(finish), "claimed");
  assert.equal(await queue.failFinishDispatchWithRelease(finish, {
    generation: first.generation,
    requestOwner: first.requestOwner,
  }), true);

  const retried = await queue.finishDispatcher(finish);
  assert.equal(retried.requested, true);
  assert.notEqual(retried.generation, first.generation);
  assert.equal(await queue.claimFinishDispatch(finish), "claimed");
});

test("an expired successor lease is rejected by claimFinishDispatch", async () => {
  const { queue, advance } = fixture();
  await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });
  const request = await queue.requestDispatcher("request");
  await queue.activateDispatcher({
    generation: request.generation,
    workerId: "worker",
  });
  const finish = {
    generation: request.generation,
    workerId: "worker",
  };

  const first = await queue.finishDispatcher(finish);
  assert.equal(first.requested, true);
  advance(1_001);

  assert.equal(await queue.claimFinishDispatch(finish), "unavailable");
});

test("stale worker acknowledgment is rejected", async () => {
  const { queue } = fixture();
  await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });
  const request = await queue.requestDispatcher("request");
  await queue.activateDispatcher({
    generation: request.generation,
    workerId: "worker",
  });
  const [claim] = await queue.claimWave({
    generation: request.generation,
    workerId: "worker",
  });

  await assert.rejects(
    queue.acknowledge({
      generation: request.generation,
      itemKey: claim.itemKey,
      claimGeneration: claim.claimGeneration,
      workerId: "other-worker",
      outcome: "success",
    }),
    /active dispatcher/,
  );
});

test("expired dispatcher ownership cannot claim more work", async () => {
  const { queue, advance } = fixture();
  await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });
  const request = await queue.requestDispatcher("request");
  await queue.activateDispatcher({
    generation: request.generation,
    workerId: "worker",
  });
  advance(1_001);

  await assert.rejects(
    queue.claimWave({
      generation: request.generation,
      workerId: "worker",
    }),
    /does not own/,
  );
});

test("expired item leases reject acknowledgments", async () => {
  const { queue, advance } = fixture();
  queue.dispatcherActiveLeaseMs = 10_000;
  await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });
  const request = await queue.requestDispatcher("request");
  await queue.activateDispatcher({
    generation: request.generation,
    workerId: "worker",
  });
  const [claim] = await queue.claimWave({
    generation: request.generation,
    workerId: "worker",
  });
  advance(1_001);

  await assert.rejects(
    queue.acknowledge({
      generation: request.generation,
      itemKey: claim.itemKey,
      claimGeneration: claim.claimGeneration,
      workerId: "worker",
      outcome: "success",
    }),
    /stale or unauthorized/,
  );
});

test("expired leases are recovered and dispatch is requested", async () => {
  const { queue, advance } = fixture();
  await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });
  const request = await queue.requestDispatcher("request");
  await queue.activateDispatcher({
    generation: request.generation,
    workerId: "worker",
  });
  await queue.claimWave({
    generation: request.generation,
    workerId: "worker",
  });
  advance(1_001);

  const recovery = await queue.recoverExpiredLeases();
  assert.equal(recovery.recoveredItems, 1);
  assert.equal(recovery.recoveredDispatcher, true);
  assert.equal(recovery.requested, true);
  assert.equal((await queue.stats()).queued, 1);
});

test("repeatedly abandoned items stop being recovered", async () => {
  const { queue, advance } = fixture();
  await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });

  let abandoned = 0;
  for (let round = 0; round < 6 && abandoned === 0; round += 1) {
    const request = await queue.requestDispatcher(`request-${round}`);
    await queue.activateDispatcher({
      generation: request.generation,
      workerId: `worker-${round}`,
    });
    await queue.claimWave({
      generation: request.generation,
      workerId: `worker-${round}`,
    });
    advance(1_001);
    abandoned = (await queue.recoverExpiredLeases()).abandonedItems;
  }

  assert.equal(abandoned, 1);
  const stats = await queue.stats();
  assert.equal(stats.queued, 0);
  assert.equal(stats.inflight, 0);
  assert.equal(stats.deadLetters, 1);
});

test("dead letters remove active work and retain bounded diagnostics", async () => {
  const { queue } = fixture();
  await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });
  const request = await queue.requestDispatcher("request");
  await queue.activateDispatcher({
    generation: request.generation,
    workerId: "worker",
  });
  const [claim] = await queue.claimWave({
    generation: request.generation,
    workerId: "worker",
  });

  const result = await queue.acknowledge({
    generation: request.generation,
    itemKey: claim.itemKey,
    claimGeneration: claim.claimGeneration,
    workerId: "worker",
    outcome: "dead",
    error: "invalid repository configuration",
  });
  assert.equal(result.status, "dead");
  const stats = await queue.stats();
  assert.equal(stats.deadLetters, 1);
  assert.equal(stats.inflight, 0);
});

test("a dead acknowledgment preserves a dirty follow-up generation", async () => {
  const { queue } = fixture();
  await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });
  const request = await queue.requestDispatcher("request");
  await queue.activateDispatcher({
    generation: request.generation,
    workerId: "worker",
  });
  const [claim] = await queue.claimWave({
    generation: request.generation,
    workerId: "worker",
  });
  await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "status",
  });

  const result = await queue.acknowledge({
    generation: request.generation,
    itemKey: claim.itemKey,
    claimGeneration: claim.claimGeneration,
    workerId: "worker",
    outcome: "dead",
    error: "final attempt failed",
  });

  assert.deepEqual(result, { status: "follow_up", attempts: 0 });
  const stats = await queue.stats();
  assert.equal(stats.queued, 1);
  assert.equal(stats.deadLetters, 0);
});

test("a retry acknowledgment gives a dirty generation a fresh attempt budget", async () => {
  const { queue } = fixture();
  await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });
  const request = await queue.requestDispatcher("request");
  await queue.activateDispatcher({
    generation: request.generation,
    workerId: "worker",
  });
  const [firstClaim] = await queue.claimWave({
    generation: request.generation,
    workerId: "worker",
  });
  assert.deepEqual(await queue.acknowledge({
    generation: request.generation,
    itemKey: firstClaim.itemKey,
    claimGeneration: firstClaim.claimGeneration,
    workerId: "worker",
    outcome: "retry",
  }), { status: "retry", attempts: 1 });

  const [secondClaim] = await queue.claimWave({
    generation: request.generation,
    workerId: "worker",
  });
  assert.equal(secondClaim.attempts, 1);
  await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "status",
  });

  assert.deepEqual(await queue.acknowledge({
    generation: request.generation,
    itemKey: secondClaim.itemKey,
    claimGeneration: secondClaim.claimGeneration,
    workerId: "worker",
    outcome: "retry",
    retryAfterMs: 10_000,
  }), { status: "follow_up", attempts: 0 });
  const [followUp] = await queue.claimWave({
    generation: request.generation,
    workerId: "worker",
  });
  assert.equal(followUp.attempts, 0);
});

test("requeue returns an unstarted item without consuming retries or completing it", async () => {
  const { queue, store } = fixture();
  const request = await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });
  const dispatcher = await queue.requestDispatcher("request");
  await queue.activateDispatcher({
    generation: dispatcher.generation,
    workerId: "worker",
  });

  for (const attempts of [1, 2]) {
    const [claimed] = await queue.claimWave({
      generation: dispatcher.generation,
      workerId: "worker",
    });
    assert.deepEqual(await queue.acknowledge({
      ...claimed,
      generation: dispatcher.generation,
      workerId: "worker",
      outcome: "retry",
    }), { status: "retry", attempts });
  }

  const [unstarted] = await queue.claimWave({
    generation: dispatcher.generation,
    workerId: "worker",
  });
  assert.equal(unstarted.attempts, 2);
  assert.deepEqual(await queue.acknowledge({
    ...unstarted,
    generation: dispatcher.generation,
    workerId: "worker",
    outcome: "requeue",
    operationId: "return-unstarted",
  }), { status: "requeued", attempts: 2 });

  assert.equal((await queue.itemStatus(request)).status, "queued");
  const shard = store.entries.get(request.shardKey).value;
  assert.equal(shard.items[request.itemKey].attempts, 2);
  assert.deepEqual(shard.deadLetters, {});
  const [available] = await queue.claimWave({
    generation: dispatcher.generation,
    workerId: "worker",
  });
  assert.equal(available.attempts, 2);
});

test("requeue gives a dirty follow-up generation a fresh retry budget", async () => {
  const { queue, store } = fixture();
  const request = await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });
  const dispatcher = await queue.requestDispatcher("request");
  await queue.activateDispatcher({
    generation: dispatcher.generation,
    workerId: "worker",
  });

  for (const attempts of [1, 2]) {
    const [claimed] = await queue.claimWave({
      generation: dispatcher.generation,
      workerId: "worker",
    });
    assert.deepEqual(await queue.acknowledge({
      ...claimed,
      generation: dispatcher.generation,
      workerId: "worker",
      outcome: "retry",
      error: "old generation failure",
    }), { status: "retry", attempts });
  }

  const [unstarted] = await queue.claimWave({
    generation: dispatcher.generation,
    workerId: "worker",
  });
  const followUp = await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "status",
  });
  assert.equal(followUp.status, "follow_up");

  assert.deepEqual(await queue.acknowledge({
    ...unstarted,
    generation: dispatcher.generation,
    workerId: "worker",
    outcome: "requeue",
    operationId: "return-dirty-unstarted",
  }), { status: "requeued", attempts: 0 });

  const shard = store.entries.get(request.shardKey).value;
  assert.equal(shard.items[request.itemKey].attempts, 0);
  assert.equal(shard.items[request.itemKey].lastError, "");
  assert.deepEqual(shard.deadLetters, {});
  assert.equal((await queue.itemStatus(request)).status, "queued");
  assert.ok(Object.values(shard.acknowledgments).every(
    (receipt) => receipt.result.status !== "removed",
  ));

  const [available] = await queue.claimWave({
    generation: dispatcher.generation,
    workerId: "worker",
  });
  assert.equal(available.claimGeneration, followUp.generation);
  assert.equal(available.attempts, 0);
  assert.deepEqual(await queue.acknowledge({
    ...available,
    generation: dispatcher.generation,
    workerId: "worker",
    outcome: "retry",
  }), { status: "retry", attempts: 1 });
});

test("requeue acknowledgment is fenced by the active item lease", async () => {
  const { queue, advance } = fixture();
  const request = await queue.enqueue({ repository: "example", prNumber: 1 });
  const dispatcher = await queue.requestDispatcher();
  await queue.activateDispatcher({
    generation: dispatcher.generation,
    workerId: "worker",
  });
  const [claim] = await queue.claimWave({
    generation: dispatcher.generation,
    workerId: "worker",
  });

  advance(1_001);
  await assert.rejects(queue.acknowledge({
    ...claim,
    generation: dispatcher.generation,
    workerId: "worker",
    outcome: "requeue",
  }), /worker does not own the active dispatcher/);

  const recovery = await queue.recoverExpiredLeases();
  await queue.activateDispatcher({
    generation: recovery.generation,
    workerId: "replacement",
  });
  const [replacement] = await queue.claimWave({
    generation: recovery.generation,
    workerId: "replacement",
  });
  assert.equal(replacement.attempts, 1);
  await queue.acknowledge({
    ...replacement,
    generation: recovery.generation,
    workerId: "replacement",
    outcome: "requeue",
  });
  assert.equal((await queue.itemStatus(request)).status, "queued");
});

test("expired recovery preserves a dirty generation at the attempt ceiling", async () => {
  const { queue, advance } = fixture();
  await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "pull_request",
  });

  for (let round = 0; round < 4; round += 1) {
    const request = await queue.requestDispatcher(`request-${round}`);
    await queue.activateDispatcher({
      generation: request.generation,
      workerId: `worker-${round}`,
    });
    await queue.claimWave({
      generation: request.generation,
      workerId: `worker-${round}`,
    });
    advance(1_001);
    await queue.recoverExpiredLeases();
  }

  const request = await queue.requestDispatcher("request-final");
  await queue.activateDispatcher({
    generation: request.generation,
    workerId: "worker-final",
  });
  await queue.claimWave({
    generation: request.generation,
    workerId: "worker-final",
  });
  await queue.enqueue({
    repository: "example",
    prNumber: 123,
    headSha: "",
    triggerEvent: "status",
  });
  advance(1_001);

  const recovery = await queue.recoverExpiredLeases();
  assert.equal(recovery.recoveredItems, 1);
  assert.equal(recovery.abandonedItems, 0);
  const stats = await queue.stats();
  assert.equal(stats.queued, 1);
  assert.equal(stats.deadLetters, 0);
});

test("a stored entry without an ETag never becomes a blind overwrite", async () => {
  const { queue, store } = fixture();
  const request = await queue.requestDispatcher("request");
  store.dropEtags = true;

  await assert.rejects(
    queue.activateDispatcher({
      generation: request.generation,
      workerId: "worker",
    }),
    /without an ETag/,
  );
});

test("full shard scans overlap their round trips", async () => {
  const { queue, store } = fixture();
  let active = 0;
  let peak = 0;
  store.beforeGet = async () => {
    active += 1;
    peak = Math.max(peak, active);
    await new Promise((resolve) => setTimeout(resolve, 1));
    active -= 1;
  };

  await queue.stats();
  assert.ok(peak > 1, `expected overlapping shard reads, saw ${peak}`);
  assert.ok(
    peak <= DEFAULT_SHARD_CONCURRENCY,
    `expected at most ${DEFAULT_SHARD_CONCURRENCY} overlapping reads, saw ${peak}`,
  );
});
