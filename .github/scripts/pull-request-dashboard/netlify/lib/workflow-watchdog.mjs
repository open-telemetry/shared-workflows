export const DEFAULT_STALE_RUN_MS = 30 * 60 * 1000;

const DASHBOARD_RUN_NAME_PREFIX = "pull-request-dashboard-";
const MAX_CANDIDATES_PER_WORKFLOW = 4;
const MAX_CANDIDATES_PER_INVOCATION = 8;
const MAX_CONCURRENT_JOB_LOOKUPS = 4;
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
    workflowId: "pull-request-dashboard.yml",
    groupByPublisher: true,
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
  const requested = [];
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
    const jobsByRun = new Map();
    if (workflow.groupByPublisher && matchingRuns.some((run) =>
      BLOCKING_RUN_STATUSES.has(run.status) &&
      runAttemptStart(run) <= staleBefore &&
      !run.display_title.endsWith("-manual")
    )) {
      await mapWithConcurrency(matchingRuns, async (run) => {
        jobsByRun.set(run.id, await getJobsIfFound(actions, run.id));
      });
    }

    const candidates = matchingRuns
      .filter((run) => {
        const createdAt = runAttemptStart(run);
        return !requested.some((request) => request.runId === run.id) &&
          BLOCKING_RUN_STATUSES.has(run.status) &&
          (!workflow.groupByPublisher || !run.display_title.endsWith("-manual")) &&
          Number.isFinite(createdAt) &&
          createdAt <= staleBefore &&
          findNewerRun(run, matchingRuns, workflow, jobsByRun, requested);
      })
      .sort((left, right) =>
        Date.parse(left.created_at) - Date.parse(right.created_at)
      );
    // Keep each candidate slice for a full workflow rotation so every workflow
    // can receive budget before its slice advances.
    const offset = candidates.length > MAX_CANDIDATES_PER_WORKFLOW
      ? Math.floor(checkedAt / (WATCHDOG_INTERVAL_MS * workflows.length)) *
        MAX_CANDIDATES_PER_WORKFLOW % candidates.length
      : 0;
    const selected = [...candidates.slice(offset), ...candidates.slice(0, offset)]
      .slice(0, Math.min(MAX_CANDIDATES_PER_WORKFLOW, remainingCandidates));
    remainingCandidates -= selected.length;

    for (const run of selected) {
      const newerRun = findNewerRun(run, matchingRuns, workflow, jobsByRun, requested);
      if (!newerRun) {
        continue;
      }
      const createdAt = runAttemptStart(run);
      const details = {
        workflowId: workflow.workflowId,
        runId: run.id,
        newerRunId: newerRun.id,
        ageMinutes: Math.floor(
          (checkedAt - createdAt) / (60 * 1000),
        ),
      };
      const jobs = workflow.groupByPublisher
        ? jobsByRun.get(run.id)
        : await getJobsIfFound(actions, run.id);
      if (jobs === null || !canCancelStalledRun(jobs, staleBefore)) {
        continue;
      }
      const publisher = workflow.groupByPublisher;
      const replacementRuns = publisher
        ? findPublisherReplacements(run, matchingRuns, jobsByRun, requested)
        : [newerRun];
      const [current, ...currentReplacements] = await Promise.all(
        [run, ...replacementRuns].map((candidate) =>
          getRunIfFound(actions, candidate.id)
        ),
      );
      if (!current) {
        continue;
      }
      let currentJobs;
      let currentNewer;
      let stillReplaced;
      if (publisher) {
        const live = currentReplacements.filter(Boolean);
        await mapWithConcurrency([current, ...live], async (candidate) => {
          jobsByRun.set(candidate.id, await getJobsIfFound(actions, candidate.id));
        });
        currentJobs = jobsByRun.get(run.id);
        stillReplaced = findPublisherReplacements(
          current,
          live.filter((candidate) => matchesWorkflow(candidate, workflow)),
          jobsByRun,
          requested,
        ).length > 0;
        currentNewer = stillReplaced;
      } else {
        currentJobs = await getJobsIfFound(actions, run.id);
        currentNewer = currentReplacements[0];
      }
      const currentNewerStart = !publisher && currentNewer
        ? runAttemptStart(currentNewer)
        : NaN;
      if (
        !BLOCKING_RUN_STATUSES.has(current.status) ||
        current.run_attempt !== run.run_attempt ||
        !Number.isFinite(runAttemptStart(current)) ||
        runAttemptStart(current) > now() - staleRunMs ||
        !matchesWorkflow(current, workflow) ||
        !currentNewer ||
        (!publisher && (
          requested.some((request) => request.runId === currentNewer.id) ||
          !WAITING_RUN_STATUSES.has(currentNewer.status) ||
          !matchesWorkflow(currentNewer, workflow) ||
          !Number.isFinite(currentNewerStart) ||
          currentNewerStart <= runAttemptStart(current) ||
          !sameConcurrencyGroup(current, currentNewer, workflow, jobsByRun)
        )) ||
        currentJobs === null ||
        !canCancelStalledRun(currentJobs, now() - staleRunMs)
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
          conflicts.push(details);
        }
        continue;
      }
      requested.push(details);
      const afterRequest = await getRunIfFound(actions, run.id);
      finishIfStopped(details, afterRequest);
    }
  }

  return {
    checkedWorkflows: watchedWorkflows.length,
    requested,
    confirmed,
    unconfirmed,
    conflicts,
  };
}

async function mapWithConcurrency(items, fn) {
  let next = 0;
  const workers = Array.from(
    { length: Math.min(MAX_CONCURRENT_JOB_LOOKUPS, items.length) },
    async () => {
      while (next < items.length) {
        await fn(items[next++]);
      }
    },
  );
  await Promise.all(workers);
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

function findNewerRun(run, runs, workflow, jobsByRun, requested) {
  if (workflow.groupByPublisher) {
    return findPublisherReplacements(run, runs, jobsByRun, requested)[0];
  }
  return runs
    .filter((candidate) => {
      const candidateStart = runAttemptStart(candidate);
      return candidate.id !== run.id &&
        !requested.some((request) => request.runId === candidate.id) &&
        WAITING_RUN_STATUSES.has(candidate.status) &&
        Number.isFinite(candidateStart) &&
        candidateStart > runAttemptStart(run) &&
        sameConcurrencyGroup(run, candidate, workflow, jobsByRun);
    })
    .sort((left, right) =>
      runAttemptStart(left) - runAttemptStart(right)
    )[0];
}

function sameConcurrencyGroup(left, right, workflow) {
  return !workflow.groupByRunName ||
    left.display_title.toLowerCase() === right.display_title.toLowerCase();
}

// Every unfinished job of the run must be a publisher request, and each must be
// covered by a newer request in another run. Returns the covering runs, or an
// empty list when any request is uncovered or any job cannot be mapped.
function findPublisherReplacements(run, runs, jobsByRun, requested) {
  const jobs = jobsByRun.get(run.id);
  const unfinished = (jobs || []).filter((job) => job.status !== "completed");
  const older = publisherRequests(run, unfinished);
  if (!older.length || older.length !== unfinished.length) {
    return [];
  }
  const candidates = runs
    .filter((candidate) =>
      candidate.id !== run.id &&
      !requested.some((request) => request.runId === candidate.id) &&
      ACTIVE_RUN_STATUSES.includes(candidate.status) &&
      Number.isFinite(runAttemptStart(candidate))
    )
    .map((candidate) => ({
      run: candidate,
      requests: publisherRequests(candidate, jobsByRun.get(candidate.id)),
    }));
  const covering = new Set();
  for (const request of older) {
    const covers = candidates
      .filter(({ requests }) => requests.some((newer) => replaces(newer, request)))
      .sort((left, right) =>
        runAttemptStart(left.run) - runAttemptStart(right.run)
      );
    if (!covers.length) {
      return [];
    }
    covers.forEach(({ run: candidate }) => covering.add(candidate));
  }
  return [...covering].sort((left, right) =>
    runAttemptStart(left) - runAttemptStart(right)
  );
}

// A targeted publisher only delivers its own pull request, while a repository
// publisher delivers every pending pull request.
function replaces(newer, older) {
  return newer.repository === older.repository &&
    newer.createdAt > older.createdAt &&
    (newer.scope === "all" || newer.scope === older.scope);
}

function publisherRequests(run, jobs) {
  const match = run.display_title?.match(
    /^pull-request-dashboard-(.+)-([1-9][0-9]*|[0-9a-f]{40}|backfill)-(?:refresh|manual)$/i,
  );
  const target = match?.[1];
  return (jobs || []).flatMap((job) => {
    const createdAt = Date.parse(job.created_at || job.started_at);
    if (!WAITING_RUN_STATUSES.has(job.status) || wasAssigned(job) ||
        !Number.isFinite(createdAt)) {
      return [];
    }
    const matrixRepository = job.name?.match(
      /^run-repo-dashboard-(?:canary|stable) \(([^()]+)\) \/ publish-dashboard$/,
    )?.[1];
    if (matrixRepository) {
      return [{ repository: matrixRepository.toLowerCase(), scope: "all", createdAt }];
    }
    const targeted = job.name?.match(
      /^run-(?:targeted|head-sha)-dashboard-(?:canary|stable)(?: \(([^()]+)\))? \/ publish-dashboard$/,
    );
    if (target && target.toLowerCase() !== "all-repositories" && targeted) {
      return [{
        repository: target.toLowerCase(),
        scope: (targeted[1] || match[2]).toLowerCase(),
        createdAt,
      }];
    }
    return [];
  });
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
