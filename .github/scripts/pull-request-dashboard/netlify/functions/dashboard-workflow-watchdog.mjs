import { createGitHubActionsClient } from "../lib/github-dispatch.mjs";
import {
  cancelStalledDashboardRuns,
} from "../lib/workflow-watchdog.mjs";

export default async () => {
  try {
    const actions = await createGitHubActionsClient();
    const result = await cancelStalledDashboardRuns({ actions });
    console.log(JSON.stringify({
      event: "dashboard_workflow_watchdog",
      ...result,
    }));
    return Response.json(result, { status: 200 });
  } catch (error) {
    console.error(error);
    throw error;
  }
};
