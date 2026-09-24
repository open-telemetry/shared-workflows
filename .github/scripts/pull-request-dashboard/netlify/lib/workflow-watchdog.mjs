export const DEFAULT_STALE_RUN_MS = 30 * 60 * 1000;

const DASHBOARD_RUN_NAME_PREFIX = "pull-request-dashboard-";

export const WATCHED_DASHBOARD_WORKFLOWS = Object.freeze([
  Object.freeze({ workflowId: "pull-request-dashboard-drain.yml" }),
  Object.freeze({
    workflowId: "pull-request-dashboard.yml",
    event: "schedule",
  }),
  Object.freeze({
    workflowId: "pull-request-dashboard.yml",
    event: "workflow_dispatch",
    groupByRunName: true,
    runNamePrefix: DASHBOARD_RUN_NAME_PREFIX,
  }),
  Object.freeze({
    workflowId: "pull-request-dashboard-deploy-webhook.yml",
  }),
]);

const BLOCKING_RUN_STATUSES = new Set(["in_progress", "queued", "waiting"]);
// GitHub reports environment-gated runs as "waiting", concurrency-held runs as
// "pending", and runs waiting for a runner as "queued".
const WAITING_RUN_STATUSES = new Set(["queued", "pending", "waiting"]);
const ACTIVE_RUN_STATUSES = Object.freeze([
  ...new Set([...BLOCKING_RUN_STATUSES, ...WAITING_RUN_STATUSES]),
]);

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
      statuses: ACTIVE_RUN_STATUSES,
    });
    const matchingRuns = runs.filter((run) =>
      (!workflow.event || run.event === workflow.event) &&
      (!workflow.runNamePrefix ||
        (
          typeof run.display_title === "string" &&
          run.display_title.startsWith(workflow.runNamePrefix)
        ))
    );
    const candidates = matchingRuns
      .filter((run) => {
        const createdAt = Date.parse(run.created_at);
        return BLOCKING_RUN_STATUSES.has(run.status) &&
          Number.isFinite(createdAt) &&
          createdAt <= staleBefore &&
          matchingRuns.some((newer) =>
            WAITING_RUN_STATUSES.has(newer.status) &&
            Date.parse(newer.created_at) > createdAt &&
            sameConcurrencyGroup(run, newer, workflow)
          );
      })
      .sort((left, right) =>
        Date.parse(left.created_at) - Date.parse(right.created_at)
      );

    for (const run of candidates) {
      const jobs = await actions.listRunJobs(run.id);
      if (!canCancelStalledRun(jobs, staleBefore)) {
        continue;
      }
      const newerRun = matchingRuns
        .filter((candidate) =>
          WAITING_RUN_STATUSES.has(candidate.status) &&
          Date.parse(candidate.created_at) > Date.parse(run.created_at) &&
          sameConcurrencyGroup(run, candidate, workflow)
        )
        .sort((left, right) =>
          Date.parse(left.created_at) - Date.parse(right.created_at)
        )[0];
      try {
        await actions.cancelWorkflowRun(run.id);
      } catch (error) {
        if (error.githubStatusCode === 409) {
          continue;
        }
        throw error;
      }
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

function sameConcurrencyGroup(left, right, workflow) {
  return !workflow.groupByRunName ||
    left.display_title.toLowerCase() === right.display_title.toLowerCase();
}

function canCancelStalledRun(jobs, staleBefore) {
  const unfinished = jobs.filter((job) => job.status !== "completed");
  if (unfinished.length !== jobs.length) {
    return unfinished.length > 0 && unfinished.every((job) => {
      const startedAt = Date.parse(job.started_at);
      return job.status === "waiting" &&
        !wasAssigned(job) &&
        Number.isFinite(startedAt) &&
        startedAt <= staleBefore;
    });
  }
  return jobs.every((job) => !wasAssigned(job));
}

function wasAssigned(job) {
  return (Number.isInteger(job.runner_id) && job.runner_id !== 0) ||
    Boolean(job.runner_name) ||
    (Array.isArray(job.steps) &&
      job.steps.some((step) => Boolean(step.started_at)));
}
