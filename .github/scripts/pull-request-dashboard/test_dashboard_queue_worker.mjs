import assert from "node:assert/strict";
import test from "node:test";

import {
  handleQueueWorkerRequest,
} from "./netlify/functions/dashboard-queue-worker.mjs";

function request(body, token = "test-token") {
  return new Request("https://example.test/.netlify/functions/dashboard-queue-worker", {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  });
}

function fixture() {
  const calls = [];
  const queue = {
    async activateDispatcher(input) {
      calls.push(["activate", input]);
      return true;
    },
    async claimWave(input) {
      calls.push(["claim", input]);
      return [{ itemKey: "example#pr:1" }];
    },
    async heartbeat(input) {
      calls.push(["heartbeat", input]);
      return { dispatcher: true, items: 1 };
    },
    async acknowledge(input) {
      calls.push(["acknowledge", input]);
      return { status: "removed" };
    },
    async finishDispatcher(input) {
      calls.push(["finish", input]);
      return {
        requested: true,
        generation: 8,
        requestOwner: "successor",
      };
    },
    async claimFinishDispatch(input) {
      calls.push(["claim-finish-dispatch", input]);
      return "claimed";
    },
    async completeFinishDispatch(input) {
      calls.push(["complete-finish-dispatch", input]);
      return true;
    },
    async failFinishDispatchWithRelease(finish, lease) {
      calls.push(["fail-finish-dispatch-with-release", finish, lease]);
      return true;
    },
    async releaseRequestedDispatcher(input) {
      calls.push(["release", input]);
      return true;
    },
    async stats() {
      calls.push(["stats"]);
      return { queued: 1 };
    },
  };
  return { calls, queue };
}

const verifyRequest = async () => ({ repository: "open-telemetry/shared-workflows" });

test("manual requests confirm acceptance rather than completion", async () => {
  const previous = process.env.PR_DASHBOARD_EXECUTION_MODE;
  process.env.PR_DASHBOARD_EXECUTION_MODE = "owned";
  try {
    const { queue } = fixture();
    const calls = [];
    queue.enqueue = async (item) => {
      calls.push(item);
      return { itemKey: "shared-workflows#backfill", generation: 1, requestId: "request", status: "queued" };
    };
    queue.requestDispatcher = async () => ({ acquired: true, generation: 7 });
    const response = await handleQueueWorkerRequest(request({
      action: "enqueue", repository: "shared-workflows", kind: "backfill",
      triggerEvent: "schedule",
    }), { queue, verifyRequest, dispatchDrain: async (generation) => calls.push(generation) });
    const result = await response.json();
    assert.equal(result.accepted, true);
    assert.equal(result.completed, false);
    assert.equal(result.itemKey, "shared-workflows#backfill");
    assert.equal(result.requestId, "request");
    assert.equal(calls[0].kind, "backfill");
    assert.equal(calls[1], 7);
  } finally {
    if (previous === undefined) delete process.env.PR_DASHBOARD_EXECUTION_MODE;
    else process.env.PR_DASHBOARD_EXECUTION_MODE = previous;
  }
});

test("status requires and forwards the request identity", async () => {
  const { calls, queue } = fixture();
  queue.itemStatus = async (input) => {
    calls.push(input);
    return { ...input, status: "unknown" };
  };
  const body = { action: "status", itemKey: "shared-workflows#pr:1", generation: 1 };
  await assert.rejects(
    handleQueueWorkerRequest(request(body), { queue, verifyRequest }),
    /requestId must be/,
  );
  assert.deepEqual(calls, []);
  const response = await handleQueueWorkerRequest(
    request({ ...body, requestId: "request" }), { queue, verifyRequest },
  );
  assert.deepEqual(await response.json(), {
    itemKey: body.itemKey, generation: 1, requestId: "request", status: "unknown",
  });
});

test("paused execution accepts controls but rejects activation", async () => {
  const previous = process.env.PR_DASHBOARD_EXECUTION_MODE;
  process.env.PR_DASHBOARD_EXECUTION_MODE = "paused";
  try {
    const { calls, queue } = fixture();
    queue.enqueue = async () => ({ itemKey: "shared-workflows#reminders", generation: 1, status: "queued" });
    const response = await handleQueueWorkerRequest(request({
      action: "enqueue", repository: "shared-workflows", kind: "reminders",
    }), { queue, verifyRequest });
    assert.equal((await response.json()).completed, false);
    const activation = await handleQueueWorkerRequest(request({
      action: "activate", generation: 1, workerId: "worker",
    }), { queue, verifyRequest });
    assert.equal((await activation.json()).activated, false);
    assert.deepEqual(calls, []);
  } finally {
    if (previous === undefined) delete process.env.PR_DASHBOARD_EXECUTION_MODE;
    else process.env.PR_DASHBOARD_EXECUTION_MODE = previous;
  }
});

test("claims a wave after authentication", async () => {
  const { calls, queue } = fixture();
  const response = await handleQueueWorkerRequest(request({
    action: "claim",
    generation: 7,
    workerId: "worker",
    limit: 4,
    excludeItemKeys: ["example#pr:2"],
  }), { queue, verifyRequest });

  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    claims: [{ itemKey: "example#pr:1" }],
  });
  assert.deepEqual(calls, [[
    "claim",
    {
      generation: 7,
      workerId: "worker",
      limit: 4,
      excludeItemKeys: ["example#pr:2"],
    },
  ]]);
});

test("dispatches a successor when finish leaves runnable work", async () => {
  const { calls, queue } = fixture();
  const dispatches = [];
  const response = await handleQueueWorkerRequest(request({
    action: "finish",
    generation: 7,
    workerId: "worker",
  }), {
    queue,
    verifyRequest,
    dispatchDrain: async (generation) => dispatches.push(generation),
  });

  assert.equal(response.status, 200);
  assert.deepEqual(dispatches, [8]);
  assert.deepEqual(calls, [[
    "finish",
    { generation: 7, workerId: "worker" },
  ], [
    "claim-finish-dispatch",
    { generation: 7, workerId: "worker" },
  ], [
    "complete-finish-dispatch",
    { generation: 7, workerId: "worker" },
  ]]);
});

test("atomically releases a successor lease after a rejected dispatch", async () => {
  const { calls, queue } = fixture();
  const dispatchError = Object.assign(new Error("dispatch failed"), { statusCode: 503 });
  await assert.rejects(
    handleQueueWorkerRequest(request({
      action: "finish",
      generation: 7,
      workerId: "worker",
    }), {
      queue,
      verifyRequest,
      dispatchDrain: async () => {
        throw dispatchError;
      },
    }),
    /dispatch failed/,
  );
  assert.deepEqual(calls, [[
    "finish",
    { generation: 7, workerId: "worker" },
  ], [
    "claim-finish-dispatch",
    { generation: 7, workerId: "worker" },
  ], [
    "fail-finish-dispatch-with-release",
    { generation: 7, workerId: "worker" },
    { generation: 8, requestOwner: "successor" },
  ]]);
});

test("preserves the successor lease after an ambiguous transport failure", async () => {
  const { calls, queue } = fixture();
  await assert.rejects(
    handleQueueWorkerRequest(request({
      action: "finish",
      generation: 7,
      workerId: "worker",
    }), {
      queue,
      verifyRequest,
      dispatchDrain: async () => {
        throw new Error("connection reset");
      },
    }),
    /connection reset/,
  );
  assert.deepEqual(calls, [[
    "finish",
    { generation: 7, workerId: "worker" },
  ], [
    "claim-finish-dispatch",
    { generation: 7, workerId: "worker" },
  ]]);
});

test("does not release a dispatched successor when recording completion fails", async () => {
  const { calls, queue } = fixture();
  queue.completeFinishDispatch = async (input) => {
    calls.push(["complete-finish-dispatch", input]);
    throw new Error("receipt failed");
  };

  await assert.rejects(
    handleQueueWorkerRequest(request({
      action: "finish",
      generation: 7,
      workerId: "worker",
    }), {
      queue,
      verifyRequest,
      dispatchDrain: async () => {},
    }),
    /receipt failed/,
  );

  assert.equal(calls.some(([action]) => action === "release"), false);
  assert.equal(calls.some(([action]) => action === "fail-finish-dispatch"), false);
});

test("does not dispatch a committed finish twice when its response is lost", async () => {
  const { queue } = fixture();
  let dispatchClaimed = false;
  queue.claimFinishDispatch = async () => {
    if (dispatchClaimed) {
      return "completed";
    }
    dispatchClaimed = true;
    return "claimed";
  };
  const dispatches = [];
  const input = {
    action: "finish",
    generation: 7,
    workerId: "worker",
  };

  for (let attempt = 0; attempt < 2; attempt += 1) {
    const response = await handleQueueWorkerRequest(request(input), {
      queue,
      verifyRequest,
      dispatchDrain: async (generation) => dispatches.push(generation),
    });
    assert.equal(response.status, 200);
  }

  assert.deepEqual(dispatches, [8]);
});

test("reports an in-progress finish dispatch as transient", async () => {
  const { queue } = fixture();
  queue.claimFinishDispatch = async () => "in_progress";

  const response = await handleQueueWorkerRequest(request({
    action: "finish",
    generation: 7,
    workerId: "worker",
  }), { queue, verifyRequest });

  assert.equal(response.status, 503);
});

test("rejects malformed actions before touching the queue", async () => {
  const { calls, queue } = fixture();
  await assert.rejects(
    handleQueueWorkerRequest(request({
      action: "claim",
      generation: 0,
      workerId: "worker",
    }), { queue, verifyRequest }),
    /generation must be a positive integer/,
  );
  assert.equal(calls.length, 0);
});

test("rejects unsupported acknowledgment outcomes as client errors", async () => {
  const { calls, queue } = fixture();
  await assert.rejects(
    handleQueueWorkerRequest(request({
      action: "acknowledge",
      generation: 1,
      itemKey: "example#pr:1",
      claimGeneration: 1,
      workerId: "worker",
      outcome: "ignored",
    }), { queue, verifyRequest }),
    (error) => {
      assert.equal(error.statusCode, 400);
      assert.match(error.message, /success, retry, dead/);
      return true;
    },
  );
  assert.equal(calls.length, 0);
});

test("rejects an unverified caller before reading the body", async () => {
  const { calls, queue } = fixture();
  let bodyRead = false;
  const unverified = request({ action: "stats", generation: 1, workerId: "worker" });
  const guarded = new Proxy(unverified, {
    get(target, property, receiver) {
      if (property === "text") {
        bodyRead = true;
      }
      return Reflect.get(target, property, target);
    },
  });

  await assert.rejects(
    handleQueueWorkerRequest(guarded, {
      queue,
      verifyRequest: async () => {
        const error = new Error("unauthorized");
        error.statusCode = 401;
        throw error;
      },
    }),
    /unauthorized/,
  );
  assert.equal(bodyRead, false);
  assert.equal(calls.length, 0);
});

test("rejects methods other than POST", async () => {
  const { calls, queue } = fixture();
  const response = await handleQueueWorkerRequest(
    new Request("https://example.test/.netlify/functions/dashboard-queue-worker"),
    { queue, verifyRequest },
  );

  assert.equal(response.status, 405);
  assert.equal(calls.length, 0);
});

test("rejects an unsupported action", async () => {
  const { calls, queue } = fixture();
  const response = await handleQueueWorkerRequest(request({
    action: "drop",
    generation: 1,
    workerId: "worker",
  }), { queue, verifyRequest });

  assert.equal(response.status, 400);
  assert.equal(calls.length, 0);
});
