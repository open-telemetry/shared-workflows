export const DEFAULT_STALE_RUN_MS = 30 * 60 * 1000;
export const DEFAULT_CANCEL_GRACE_MS = 30 * 60 * 1000;

const DASHBOARD_RUN_NAME_PREFIX = "pull-request-dashboard-";
const MAX_CANDIDATES_PER_WORKFLOW = 4;
const MAX_CANDIDATES_PER_INVOCATION = 8;
const MAX_FORCE_ATTEMPTS = 2;
const MAX_NORMAL_CONFLICTS = 2;
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
  store,
  now = () => Date.now(),
  staleRunMs = DEFAULT_STALE_RUN_MS,
  cancelGraceMs = DEFAULT_CANCEL_GRACE_MS,
  watchedWorkflows = WATCHED_DASHBOARD_WORKFLOWS,
}) {
  if (!actions || !store) {
    throw new Error("actions client and watchdog store are required");
  }
  if (!Number.isFinite(staleRunMs) || staleRunMs <= 0 ||
      !Number.isFinite(cancelGraceMs) || cancelGraceMs <= 0) {
    throw new Error("staleRunMs and cancelGraceMs must be positive");
  }

  const checkedAt = now();
  const staleBefore = checkedAt - staleRunMs;
  const requested = [];
  const forceRequested = [];
  const confirmed = [];
  const unconfirmed = [];
  const conflicts = [];
  const unresponsive = [];
  let remainingCandidates = MAX_CANDIDATES_PER_INVOCATION;
  const workflowOffset = watchedWorkflows.length
    ? Math.floor(checkedAt / WATCHDOG_INTERVAL_MS) % watchedWorkflows.length
    : 0;
  const workflows = [
    ...watchedWorkflows.slice(workflowOffset),
    ...watchedWorkflows.slice(0, workflowOffset),
  ];

  for (const workflow of workflows) {
    const key = `runs/${workflow.workflowId}/${workflow.event || "all"}`;
    let entry = await store.get(key);
    if (entry && (typeof entry.etag !== "string" || !entry.etag)) {
      throw new Error(`watchdog state for ${key} has no ETag`);
    }
    const state = entry?.value ?? { records: {} };
    if (!state.records || typeof state.records !== "object" ||
        Array.isArray(state.records)) {
      throw new Error(`invalid watchdog state for ${key}`);
    }
    async function saveState() {
      const write = await store.set(
        key,
        state,
        entry ? { onlyIfMatch: entry.etag } : { onlyIfNew: true },
      );
      if (!write.modified || !write.etag) {
        throw new Error(`watchdog state changed while processing ${key}`);
      }
      entry = { etag: write.etag };
    }
    async function finishIfStopped(runId, details, current) {
      if (current && current.status !== "completed") {
        return false;
      }
      if (state.records[runId]) {
        delete state.records[runId];
        await saveState();
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

    const missingRecords = Object.entries(state.records)
      .filter(([runId]) =>
        !matchingRuns.some((run) => String(run.id) === runId));
    const confirmationOffset = missingRecords.length > MAX_CANDIDATES_PER_WORKFLOW
      ? Math.floor(checkedAt / WATCHDOG_INTERVAL_MS) % missingRecords.length
      : 0;
    for (const [runId, record] of [
      ...missingRecords.slice(confirmationOffset),
      ...missingRecords.slice(0, confirmationOffset),
    ].slice(0, MAX_CANDIDATES_PER_WORKFLOW)) {
      const current = await getRunIfFound(actions, runId);
      await finishIfStopped(runId, record.details, current);
    }

    const candidates = matchingRuns
      .filter((run) => {
        const createdAt = Date.parse(run.created_at);
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
      ? Math.floor(checkedAt / WATCHDOG_INTERVAL_MS) % candidates.length
      : 0;
    const selected = [...candidates.slice(offset), ...candidates.slice(0, offset)]
      .slice(0, Math.min(MAX_CANDIDATES_PER_WORKFLOW, remainingCandidates));
    remainingCandidates -= selected.length;

    for (const run of selected) {
      const newerRun = findNewerRun(run, matchingRuns, workflow);
      const details = {
        workflowId: workflow.workflowId,
        runId: run.id,
        newerRunId: newerRun.id,
        ageMinutes: Math.floor(
          (checkedAt - Date.parse(run.created_at)) / (60 * 1000),
        ),
      };
      const record = state.records[run.id];
      const normalAccepted = Number.isFinite(record?.normalRequestedAt);
      if (record) {
        if (
          (!normalAccepted &&
            (!Number.isFinite(record.normalConflictAt) ||
              !Number.isSafeInteger(record.normalConflictAttempts) ||
              record.normalConflictAttempts < 1)) ||
          !Number.isSafeInteger(record.forceAttempts ?? 0) ||
          (record.forceAttempts ?? 0) < 0
        ) {
          throw new Error(`invalid watchdog receipt for run ${run.id}`);
        }
        const lastRequestAt = record.forceRequestedAt ??
          (normalAccepted ? record.normalRequestedAt : record.normalConflictAt);
        if (!Number.isFinite(lastRequestAt)) {
          throw new Error(`invalid watchdog receipt for run ${run.id}`);
        }
        if (checkedAt - lastRequestAt < cancelGraceMs) {
          continue;
        }
        const [current, currentNewer, jobs] = await Promise.all([
          getRunIfFound(actions, run.id),
          getRunIfFound(actions, newerRun.id),
          getJobsIfFound(actions, run.id),
        ]);
        if (await finishIfStopped(run.id, record.details, current)) {
          continue;
        }
        if (jobs === null) {
          await finishIfStopped(run.id, record.details, null);
          continue;
        }
        if (
          !BLOCKING_RUN_STATUSES.has(current.status) ||
          !Number.isFinite(Date.parse(current.created_at)) ||
          Date.parse(current.created_at) > now() - staleRunMs ||
          !matchesWorkflow(current, workflow) ||
          !currentNewer ||
          !WAITING_RUN_STATUSES.has(currentNewer.status) ||
          !matchesWorkflow(currentNewer, workflow) ||
          Date.parse(currentNewer.created_at) <= Date.parse(current.created_at) ||
          !sameConcurrencyGroup(current, currentNewer, workflow) ||
          !canCancelStalledRun(jobs, now() - staleRunMs)
        ) {
          continue;
        }
        if (!normalAccepted &&
            record.normalConflictAttempts >= MAX_NORMAL_CONFLICTS) {
          unresponsive.push({ ...record.details, reason: "normal_conflicts" });
          continue;
        }
        if (normalAccepted) {
          if ((record.forceAttempts || 0) >= MAX_FORCE_ATTEMPTS) {
            unresponsive.push({ ...record.details, reason: "force_attempts" });
            continue;
          }
          try {
            await actions.forceCancelWorkflowRun(run.id);
          } catch (error) {
            if (error.githubStatusCode !== 409) {
              throw error;
            }
            const afterConflict = await getRunIfFound(actions, run.id);
            if (await finishIfStopped(run.id, record.details, afterConflict)) {
              continue;
            }
            record.forceRequestedAt = now();
            record.forceAttempts = (record.forceAttempts || 0) + 1;
            await saveState();
            conflicts.push({ ...details, stage: "force" });
            continue;
          }
          record.forceRequestedAt = now();
          record.forceAttempts = (record.forceAttempts || 0) + 1;
          await saveState();
          forceRequested.push(details);
          const afterRequest = await getRunIfFound(actions, run.id);
          await finishIfStopped(run.id, details, afterRequest);
          continue;
        }
      } else {
        const jobs = await actions.listRunJobs(run.id);
        if (!canCancelStalledRun(jobs, staleBefore)) {
          continue;
        }
      }
      try {
        await actions.cancelWorkflowRun(run.id);
      } catch (error) {
        if (error.githubStatusCode !== 409) {
          throw error;
        }
        const afterConflict = await getRunIfFound(actions, run.id);
        if (await finishIfStopped(run.id, record?.details || details, afterConflict)) {
          continue;
        }
        state.records[run.id] = {
          normalConflictAt: now(),
          normalConflictAttempts: (record?.normalConflictAttempts || 0) + 1,
          details,
        };
        await saveState();
        conflicts.push({ ...details, stage: "normal" });
        continue;
      }
      state.records[run.id] = {
        normalRequestedAt: now(),
        details,
      };
      await saveState();
      requested.push(details);

      const current = await getRunIfFound(actions, run.id);
      await finishIfStopped(run.id, details, current);
    }
  }

  return {
    checkedWorkflows: watchedWorkflows.length,
    requested,
    forceRequested,
    confirmed,
    unconfirmed,
    conflicts,
    unresponsive,
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
        run.display_title.startsWith(workflow.runNamePrefix)));
}

function findNewerRun(run, runs, workflow) {
  return runs
    .filter((candidate) =>
      WAITING_RUN_STATUSES.has(candidate.status) &&
      Date.parse(candidate.created_at) > Date.parse(run.created_at) &&
      sameConcurrencyGroup(run, candidate, workflow)
    )
    .sort((left, right) =>
      Date.parse(left.created_at) - Date.parse(right.created_at)
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
