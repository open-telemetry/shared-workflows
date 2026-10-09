import assert from "node:assert/strict";
import test from "node:test";

import { recoverQueueLanes } from "./netlify/functions/dashboard-queue-recover.mjs";

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
