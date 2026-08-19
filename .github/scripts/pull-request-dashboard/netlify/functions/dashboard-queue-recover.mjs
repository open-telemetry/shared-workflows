import { DashboardQueue } from "../lib/dashboard-queue.mjs";
import { dispatchQueueDrain } from "../lib/github-dispatch.mjs";

export default async () => {
  try {
    const queue = new DashboardQueue();
    const recovery = await queue.recoverExpiredLeases();
    if (recovery.requested) {
      try {
        await dispatchQueueDrain(recovery.generation);
      } catch (error) {
        await queue.releaseRequestedDispatcher({
          generation: recovery.generation,
          requestOwner: recovery.requestOwner,
        });
        throw error;
      }
    }
    console.log(JSON.stringify({
      event: "dashboard_queue_recovery",
      ...recovery,
    }));
  } catch (error) {
    console.error(error);
    throw error;
  }
};
