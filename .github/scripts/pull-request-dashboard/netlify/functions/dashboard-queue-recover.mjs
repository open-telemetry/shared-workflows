import { DashboardQueue } from "../lib/dashboard-queue.mjs";
import { dispatchQueueDrain } from "../lib/github-dispatch.mjs";
import { executionMode } from "../lib/execution-mode.mjs";

export default async () => {
  try {
    const queue = new DashboardQueue();
    const recovery = await queue.recoverExpiredLeases({
      requestSuccessor: executionMode() !== "paused",
      redispatchRequested: executionMode() === "owned",
    });
    if (recovery.requested && executionMode() !== "paused") {
      try {
        await dispatchQueueDrain(recovery.generation);
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
      ...recovery,
      health: await queue.stats(),
    }));
  } catch (error) {
    console.error(error);
    throw error;
  }
};
