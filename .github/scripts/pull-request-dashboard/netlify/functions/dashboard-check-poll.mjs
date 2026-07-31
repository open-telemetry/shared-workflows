import {
  createDispatcherToken,
  dispatchWorkflow,
  loadDispatcherCredentials,
} from "../lib/github-app.mjs";

const WORKFLOW_ID = "pull-request-dashboard-check-poll.yml";

// Check and status webhooks are not subscribed. They arrive once per check
// suite, carry no pull request number on a fork head, and the dashboard's own
// runs fed that fallback back into itself. Polling the rollup asks the same
// question once per repository, keyed by pull request number.
//
// GitHub's own schedule trigger is best effort and does not hold a cadence
// below an hour, so the poll is driven from here instead.
export default async () => {
  const token = await createDispatcherToken(loadDispatcherCredentials());
  await dispatchWorkflow(token, WORKFLOW_ID, {});
  console.log(`dispatched ${WORKFLOW_ID}`);
};

export const config = {
  schedule: "*/10 * * * *",
};
