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

function fixture() {
  let now = Date.parse("2026-08-19T20:00:00Z");
  let sequence = 0;
  const store = new MemoryStore();
  const queue = new DashboardQueue({
    store,
    now: () => now,
    randomId: () => `id-${++sequence}`,
    itemLeaseMs: 1_000,
    dispatcherRequestLeaseMs: 1_000,
    dispatcherActiveLeaseMs: 1_000,
  });
  return {
    queue,
    store,
    advance(milliseconds) {
      now += milliseconds;
    },
  };
}

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
    itemKey: claim.itemKey,
    claimGeneration: claim.claimGeneration,
    workerId: "worker",
    outcome: "success",
  }), { status: "removed" });
  assert.equal((await queue.stats()).queued, 0);
  assert.equal((await queue.stats()).inflight, 0);
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
      itemKey: claim.itemKey,
      claimGeneration: claim.claimGeneration,
      workerId: "other-worker",
      outcome: "success",
    }),
    /stale or unauthorized/,
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
