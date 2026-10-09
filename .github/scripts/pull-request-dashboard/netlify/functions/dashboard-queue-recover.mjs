import { DashboardQueue } from "../lib/dashboard-queue.mjs";
import { dispatchQueueDrain } from "../lib/github-dispatch.mjs";
import { executionMode } from "../lib/execution-mode.mjs";

export default async () => recoverQueueLanes();

export async function recoverQueueLanes({
  createQueue = (lane) => new DashboardQueue({ lane }),
  dispatchDrain = dispatchQueueDrain,
} = {}) {
  const results = await Promise.allSettled(["live", "maintenance"].map(async (lane) => {
    const queue = createQueue(lane);
    const recovery = await queue.recoverExpiredLeases({
      requestSuccessor: executionMode() !== "paused",
      redispatchRequested: executionMode() === "owned",
    });
    if (recovery.requested && executionMode() !== "paused") {
      try {
        await dispatchDrain(recovery.generation, lane);
      } catch (error) {
        if (!recovery.replayed) {
          await queue.releaseRequestedDispatcher({
            generation: recovery.generation,
            requestOwner: recovery.requestOwner,
          });
        }
        throw error;
      }
    }
    console.log(JSON.stringify({
      event: "dashboard_queue_recovery",
      lane,
      ...recovery,
      health: await queue.stats(),
    }));
  }));
  const errors = results.filter((result) => result.status === "rejected").map((result) => result.reason);
  if (errors.length) {
    for (const error of errors) console.error(error);
    throw new AggregateError(errors, "dashboard queue recovery failed");
  }
}
