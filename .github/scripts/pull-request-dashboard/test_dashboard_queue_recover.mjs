import assert from "node:assert/strict";
import test, { after, before } from "node:test";

import { recoverQueueLanes } from "./netlify/functions/dashboard-queue-recover.mjs";

const previousMode = process.env.PR_DASHBOARD_EXECUTION_MODE;
before(() => { process.env.PR_DASHBOARD_EXECUTION_MODE = "owned"; });
after(() => {
  if (previousMode === undefined) delete process.env.PR_DASHBOARD_EXECUTION_MODE;
  else process.env.PR_DASHBOARD_EXECUTION_MODE = previousMode;
});

test("paused recovery preserves both lanes without requesting or dispatching successors", async () => {
  process.env.PR_DASHBOARD_EXECUTION_MODE = "paused";
  try {
    const recovered = [];
    await recoverQueueLanes({
      createQueue: (lane) => ({
        async recoverExpiredLeases(options) {
          recovered.push([lane, options]);
          return { requested: false };
        },
        async stats() { return {}; },
      }),
      dispatchDrain: async () => { assert.fail("paused recovery must not dispatch"); },
    });
    assert.deepEqual(recovered, ["live", "maintenance"].map((lane) => [
      lane, { requestSuccessor: false, redispatchRequested: false },
    ]));
  } finally {
    process.env.PR_DASHBOARD_EXECUTION_MODE = "owned";
  }
});

test("recovery dispatches both lanes independently", async () => {
  const dispatched = [];
  const recovered = [];
  await recoverQueueLanes({
    createQueue: (lane) => ({
      async recoverExpiredLeases() {
        recovered.push(lane);
        return { requested: true, generation: lane === "live" ? 7 : 11 };
      },
      async stats() { return {}; },
    }),
    dispatchDrain: async (generation, lane) => dispatched.push([generation, lane]),
  });
  assert.deepEqual(recovered.sort(), ["live", "maintenance"]);
  assert.deepEqual(dispatched.sort((left, right) => left[0] - right[0]), [[7, "live"], [11, "maintenance"]]);
});

test("failure in one lane does not prevent recovery of the other", async () => {
  const dispatched = [];
  const released = [];
  await assert.rejects(recoverQueueLanes({
    createQueue: (lane) => ({
      async recoverExpiredLeases() {
        return { requested: true, generation: 1, requestOwner: lane };
      },
      async releaseRequestedDispatcher(lease) { released.push([lane, lease.requestOwner]); },
      async stats() { return {}; },
    }),
    dispatchDrain: async (_generation, lane) => {
      if (lane === "maintenance") throw new Error("maintenance dispatch unavailable");
      dispatched.push(lane);
    },
  }), /queue recovery failed/);
  assert.deepEqual(dispatched, ["live"]);
  assert.deepEqual(released, [["maintenance", "maintenance"]]);
});
