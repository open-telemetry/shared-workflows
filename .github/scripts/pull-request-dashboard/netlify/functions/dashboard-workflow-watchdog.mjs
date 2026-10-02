import { createGitHubActionsClient } from "../lib/github-dispatch.mjs";
import {
  cancelStalledDashboardRuns,
  WATCHED_DASHBOARD_WORKFLOWS,
} from "../lib/workflow-watchdog.mjs";
import { executionMode } from "../lib/execution-mode.mjs";

export default async () => {
  try {
    const actions = await createGitHubActionsClient();
    const watchedWorkflows = executionMode() !== "owned"
      ? WATCHED_DASHBOARD_WORKFLOWS
      : WATCHED_DASHBOARD_WORKFLOWS.filter(
        (workflow) => workflow.workflowId === "pull-request-dashboard-deploy-webhook.yml",
      );
    const result = await cancelStalledDashboardRuns({ actions, watchedWorkflows });
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
