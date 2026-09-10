export const DEFAULT_STALE_RUN_MS = 30 * 60 * 1000;

export const WATCHED_DASHBOARD_WORKFLOWS = Object.freeze([
  Object.freeze({ workflowId: "pull-request-dashboard-drain.yml" }),
  Object.freeze({
    workflowId: "pull-request-dashboard.yml",
    event: "schedule",
  }),
  Object.freeze({
    workflowId: "pull-request-dashboard-deploy-webhook.yml",
  }),
]);

const BLOCKING_RUN_STATUSES = new Set(["in_progress", "queued"]);
// GitHub reports a run held by its concurrency group as "pending" and a run
// admitted to the group but waiting for a runner as "queued".
const WAITING_RUN_STATUSES = new Set(["queued", "pending"]);

export async function cancelStalledDashboardRuns({
  actions,
  now = () => Date.now(),
  staleRunMs = DEFAULT_STALE_RUN_MS,
  watchedWorkflows = WATCHED_DASHBOARD_WORKFLOWS,
}) {
  if (!actions) {
    throw new Error("actions client is required");
  }
  if (!Number.isFinite(staleRunMs) || staleRunMs <= 0) {
    throw new Error("staleRunMs must be positive");
  }

  const checkedAt = now();
  const staleBefore = checkedAt - staleRunMs;
  const cancelled = [];

  for (const workflow of watchedWorkflows) {
    const runs = await actions.listWorkflowRuns(workflow.workflowId, {
      event: workflow.event,
    });
    const matchingRuns = workflow.event
      ? runs.filter((run) => run.event === workflow.event)
      : runs;
    const candidates = matchingRuns
      .filter((run) => {
        const createdAt = Date.parse(run.created_at);
        return BLOCKING_RUN_STATUSES.has(run.status) &&
          Number.isFinite(createdAt) &&
          createdAt <= staleBefore &&
          matchingRuns.some((newer) =>
            WAITING_RUN_STATUSES.has(newer.status) &&
            Date.parse(newer.created_at) > createdAt
          );
      })
      .sort((left, right) =>
        Date.parse(left.created_at) - Date.parse(right.created_at)
      );

    for (const run of candidates) {
      const jobs = await actions.listRunJobs(run.id);
      if (!wasNeverAssigned(jobs, staleBefore)) {
        continue;
      }
      const newerRun = matchingRuns
        .filter((candidate) =>
          WAITING_RUN_STATUSES.has(candidate.status) &&
          Date.parse(candidate.created_at) > Date.parse(run.created_at)
        )
        .sort((left, right) =>
          Date.parse(left.created_at) - Date.parse(right.created_at)
        )[0];
      await actions.cancelWorkflowRun(run.id);
      cancelled.push({
        workflowId: workflow.workflowId,
        runId: run.id,
        newerRunId: newerRun.id,
        ageMinutes: Math.floor(
          (checkedAt - Date.parse(run.created_at)) / (60 * 1000),
        ),
      });
      break;
    }
  }

  return {
    checkedWorkflows: watchedWorkflows.length,
    cancelled,
  };
}

function wasNeverAssigned(jobs, staleBefore) {
  if (
    jobs.some((job) =>
      (Number.isInteger(job.runner_id) && job.runner_id !== 0) ||
      Boolean(job.runner_name) ||
      (Array.isArray(job.steps) &&
        job.steps.some((step) => Boolean(step.started_at)))
    )
  ) {
    return false;
  }
  return jobs.some((job) => {
    const startedAt = Date.parse(job.started_at || job.created_at);
    return BLOCKING_RUN_STATUSES.has(job.status) &&
      Number.isFinite(startedAt) &&
      startedAt <= staleBefore;
  });
}
