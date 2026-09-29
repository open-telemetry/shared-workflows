export const DEFAULT_STALE_RUN_MS = 30 * 60 * 1000;
export const DEFAULT_FORCE_RUN_MS = 60 * 60 * 1000;

const DASHBOARD_RUN_NAME_PREFIX = "pull-request-dashboard-";
const MAX_CANDIDATES_PER_WORKFLOW = 4;
const MAX_CANDIDATES_PER_INVOCATION = 8;
const WATCHDOG_INTERVAL_MS = 15 * 60 * 1000;

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
    runNameSuffix: "-refresh",
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
  forceRunMs = DEFAULT_FORCE_RUN_MS,
  watchedWorkflows = WATCHED_DASHBOARD_WORKFLOWS,
}) {
  if (!actions) {
    throw new Error("actions client is required");
  }
  if (!Number.isFinite(staleRunMs) || staleRunMs <= 0 ||
      !Number.isFinite(forceRunMs) || forceRunMs <= staleRunMs) {
    throw new Error("forceRunMs must exceed a positive staleRunMs");
  }

  const checkedAt = now();
  const staleBefore = checkedAt - staleRunMs;
  const forceBefore = checkedAt - forceRunMs;
  const requested = [];
  const forceRequested = [];
  const confirmed = [];
  const unconfirmed = [];
  const conflicts = [];
  let remainingCandidates = MAX_CANDIDATES_PER_INVOCATION;
  const workflowOffset = watchedWorkflows.length
    ? Math.floor(checkedAt / WATCHDOG_INTERVAL_MS) % watchedWorkflows.length
    : 0;
  const workflows = [
    ...watchedWorkflows.slice(workflowOffset),
    ...watchedWorkflows.slice(0, workflowOffset),
  ];

  for (const workflow of workflows) {
    function finishIfStopped(details, current) {
      if (current && current.status !== "completed") {
        return false;
      }
      if (current?.conclusion === "cancelled") {
        confirmed.push(details);
      } else {
        unconfirmed.push({ ...details, reason: current ? "finished" : "not_found" });
      }
      return true;
    }

    const runs = await actions.listWorkflowRuns(workflow.workflowId, {
      event: workflow.event,
      statuses: ACTIVE_RUN_STATUSES,
    });
    const matchingRuns = runs.filter((run) => matchesWorkflow(run, workflow));

    const candidates = matchingRuns
      .filter((run) => {
        const createdAt = runAttemptStart(run);
        return BLOCKING_RUN_STATUSES.has(run.status) &&
          Number.isFinite(createdAt) &&
          createdAt <= staleBefore &&
          findNewerRun(run, matchingRuns, workflow);
      })
      .sort((left, right) =>
        Date.parse(left.created_at) - Date.parse(right.created_at)
      );
    // Rotate large backlogs so one run that ignores force-cancel cannot hide
    // later eligible runs behind the per-invocation API budget.
    const offset = candidates.length > MAX_CANDIDATES_PER_WORKFLOW
      ? Math.floor(checkedAt / WATCHDOG_INTERVAL_MS) *
        MAX_CANDIDATES_PER_WORKFLOW % candidates.length
      : 0;
    const selected = [...candidates.slice(offset), ...candidates.slice(0, offset)]
      .slice(0, Math.min(MAX_CANDIDATES_PER_WORKFLOW, remainingCandidates));
    remainingCandidates -= selected.length;

    for (const run of selected) {
      const newerRun = findNewerRun(run, matchingRuns, workflow);
      const createdAt = runAttemptStart(run);
      const details = {
        workflowId: workflow.workflowId,
        runId: run.id,
        newerRunId: newerRun.id,
        ageMinutes: Math.floor(
          (checkedAt - createdAt) / (60 * 1000),
        ),
      };
      if (createdAt <= forceBefore) {
        const jobs = await getJobsIfFound(actions, run.id);
        if (jobs === null) {
          continue;
        }
        const [current, currentNewer] = await Promise.all([
          getRunIfFound(actions, run.id),
          getRunIfFound(actions, newerRun.id),
        ]);
        if (!current) {
          continue;
        }
        const currentNewerStart = currentNewer
          ? runAttemptStart(currentNewer)
          : NaN;
        if (
          !BLOCKING_RUN_STATUSES.has(current.status) ||
          current.run_attempt !== run.run_attempt ||
          !Number.isFinite(runAttemptStart(current)) ||
          runAttemptStart(current) > now() - forceRunMs ||
          !matchesWorkflow(current, workflow) ||
          !currentNewer ||
          !WAITING_RUN_STATUSES.has(currentNewer.status) ||
          !matchesWorkflow(currentNewer, workflow) ||
          !Number.isFinite(currentNewerStart) ||
          currentNewerStart <= runAttemptStart(current) ||
          !sameConcurrencyGroup(current, currentNewer, workflow) ||
          !canCancelStalledRun(jobs, now() - staleRunMs)
        ) {
          continue;
        }
        try {
          await actions.forceCancelWorkflowRun(run.id);
        } catch (error) {
          if (error.githubStatusCode !== 409) {
            throw error;
          }
          const afterConflict = await getRunIfFound(actions, run.id);
          if (!finishIfStopped(details, afterConflict)) {
            conflicts.push({ ...details, stage: "force" });
          }
          continue;
        }
        forceRequested.push(details);
        const afterRequest = await getRunIfFound(actions, run.id);
        finishIfStopped(details, afterRequest);
      } else {
        const jobs = await actions.listRunJobs(run.id);
        if (!canCancelStalledRun(jobs, staleBefore)) {
          continue;
        }
        const current = await getRunIfFound(actions, run.id);
        if (
          !current ||
          !BLOCKING_RUN_STATUSES.has(current.status) ||
          current.run_attempt !== run.run_attempt ||
          !Number.isFinite(runAttemptStart(current)) ||
          runAttemptStart(current) > now() - staleRunMs ||
          !matchesWorkflow(current, workflow)
        ) {
          continue;
        }
        try {
          await actions.cancelWorkflowRun(run.id);
        } catch (error) {
          if (error.githubStatusCode !== 409) {
            throw error;
          }
          const afterConflict = await getRunIfFound(actions, run.id);
          if (!finishIfStopped(details, afterConflict)) {
            conflicts.push({ ...details, stage: "normal" });
          }
          continue;
        }
        requested.push(details);
        const afterRequest = await getRunIfFound(actions, run.id);
        finishIfStopped(details, afterRequest);
      }
    }
  }

  return {
    checkedWorkflows: watchedWorkflows.length,
    requested,
    forceRequested,
    confirmed,
    unconfirmed,
    conflicts,
  };
}

async function getRunIfFound(actions, runId) {
  try {
    const run = await actions.getWorkflowRun(runId);
    if (!run || typeof run !== "object") {
      throw new Error(`GitHub workflow run ${runId} lookup returned no run`);
    }
    return run;
  } catch (error) {
    if (error.githubStatusCode === 404) {
      return null;
    }
    throw error;
  }
}

async function getJobsIfFound(actions, runId) {
  try {
    return await actions.listRunJobs(runId);
  } catch (error) {
    if (error.githubStatusCode === 404 &&
        await getRunIfFound(actions, runId) === null) {
      return null;
    }
    throw error;
  }
}

function matchesWorkflow(run, workflow) {
  return (!workflow.event || run.event === workflow.event) &&
    (!workflow.runNamePrefix ||
      (typeof run.display_title === "string" &&
        run.display_title.startsWith(workflow.runNamePrefix))) &&
    (!workflow.runNameSuffix ||
      (typeof run.display_title === "string" &&
        run.display_title.endsWith(workflow.runNameSuffix)));
}

function runAttemptStart(run) {
  const createdAt = Date.parse(run.created_at);
  if (run.run_attempt > 1) {
    // A rerun keeps the original created_at; without its own start time, its age is unknown.
    const startedAt = Date.parse(run.run_started_at);
    return startedAt >= createdAt ? startedAt : NaN;
  }
  return createdAt;
}

function findNewerRun(run, runs, workflow) {
  return runs
    .filter((candidate) => {
      const candidateStart = runAttemptStart(candidate);
      return WAITING_RUN_STATUSES.has(candidate.status) &&
        Number.isFinite(candidateStart) &&
        candidateStart > runAttemptStart(run) &&
        sameConcurrencyGroup(run, candidate, workflow);
    })
    .sort((left, right) =>
      runAttemptStart(left) - runAttemptStart(right)
    )[0];
}

function sameConcurrencyGroup(left, right, workflow) {
  return !workflow.groupByRunName ||
    left.display_title.toLowerCase() === right.display_title.toLowerCase();
}

function canCancelStalledRun(jobs, staleBefore) {
  const unfinished = jobs.filter((job) => job.status !== "completed");
  if (unfinished.length !== jobs.length) {
    return unfinished.length > 0 && unfinished.every((job) => {
      const startedAt = Date.parse(
        job.status === "queued" ? job.started_at || job.created_at : job.started_at,
      );
      return (job.status === "waiting" || job.status === "queued") &&
        !wasAssigned(job) &&
        Number.isFinite(startedAt) &&
        startedAt <= staleBefore;
    });
  }
  return jobs.every((job) =>
    (job.status === "in_progress" ||
      job.status === "waiting" ||
      (job.status === "queued" &&
        Number.isFinite(Date.parse(job.started_at || job.created_at)) &&
        Date.parse(job.started_at || job.created_at) <= staleBefore)) &&
    !wasAssigned(job)
  );
}

function wasAssigned(job) {
  return (Number.isInteger(job.runner_id) && job.runner_id !== 0) ||
    Boolean(job.runner_name) ||
    (Array.isArray(job.steps) &&
      job.steps.some((step) => Boolean(step.started_at)));
}
