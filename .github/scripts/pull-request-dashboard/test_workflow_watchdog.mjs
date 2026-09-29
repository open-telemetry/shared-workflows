import assert from "node:assert/strict";
import test from "node:test";

import { cancelStalledDashboardRuns } from "./netlify/lib/workflow-watchdog.mjs";

const NOW = Date.parse("2026-09-10T12:00:00Z");
const WORKFLOW = Object.freeze({ workflowId: "dashboard.yml" });
const ACTIVE_RUN_STATUSES = ["in_progress", "queued", "waiting", "pending"];
const NORMAL_FORCE_RUN_MS = 24 * 60 * 60 * 1000;

function fixture({
  runs, jobs = {}, cancellationErrors = {}, forceErrors = {},
  getRunErrors = {}, jobErrors = {}, onCancel, onForceCancel,
}) {
  const calls = [];
  const actions = {
    async listWorkflowRuns(workflowId, options) {
      calls.push(["list-runs", workflowId, options]);
      return runs.filter((candidate) =>
        options.statuses.includes(candidate.status) &&
        (!options.event || candidate.event === options.event));
    },
    async getWorkflowRun(runId) {
      calls.push(["get-run", Number(runId)]);
      if (getRunErrors[runId]) {
        throw getRunErrors[runId];
      }
      return runs.find((candidate) => candidate.id === Number(runId));
    },
    async listRunJobs(runId) {
      calls.push(["list-jobs", runId]);
      if (jobErrors[runId]) {
        throw jobErrors[runId];
      }
      return jobs[runId] || [];
    },
    async cancelWorkflowRun(runId) {
      calls.push(["cancel", runId]);
      if (cancellationErrors[runId]) {
        throw cancellationErrors[runId];
      }
      onCancel?.(runId, runs);
    },
    async forceCancelWorkflowRun(runId) {
      calls.push(["force-cancel", runId]);
      if (forceErrors[runId]) {
        throw forceErrors[runId];
      }
      onForceCancel?.(runId, runs);
    },
  };
  return { actions, calls, runs, jobs, getRunErrors, jobErrors };
}

function run(
  id,
  status,
  createdAt,
  event = "workflow_dispatch",
  displayTitle = "Pull request dashboard",
) {
  return {
    id,
    status,
    created_at: createdAt,
    event,
    display_title: displayTitle,
  };
}

function unassignedJob(startedAt, status = "in_progress") {
  return {
    status,
    created_at: startedAt,
    started_at: status === "queued" ? null : startedAt,
    runner_id: 0,
    runner_name: "",
    steps: [{
      name: "Run job",
      status: "queued",
      conclusion: null,
      number: 1,
      started_at: null,
      completed_at: null,
    }],
  };
}

function completedJob(startedAt = "2026-09-10T11:00:00Z") {
  return {
    ...unassignedJob(startedAt, "completed"),
    runner_id: 123,
    runner_name: "GitHub Actions 123",
    steps: [{ started_at: startedAt }],
  };
}

test("cancels an unassigned stale run blocking a newer run", async () => {
  const { actions, calls } = fixture({
    runs: [
      run(2, "queued", "2026-09-10T11:45:00Z"),
      run(1, "in_progress", "2026-09-10T11:00:00Z"),
    ],
    jobs: {
      1: [unassignedJob("2026-09-10T11:00:00Z")],
    },
  });

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    forceRunMs: NORMAL_FORCE_RUN_MS,
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result, {
    checkedWorkflows: 1,
    requested: [{
      workflowId: "dashboard.yml",
      runId: 1,
      newerRunId: 2,
      ageMinutes: 60,
    }],
    forceRequested: [],
    confirmed: [],
    unconfirmed: [],
    conflicts: [],
  });
  assert.deepEqual(calls, [
    ["list-runs", "dashboard.yml", {
      event: undefined,
      statuses: ACTIVE_RUN_STATUSES,
    }],
    ["list-jobs", 1],
    ["get-run", 1],
    ["cancel", 1],
    ["get-run", 1],
  ]);
});

test("cancels a stale run before GitHub creates job records", async () => {
  const { actions, calls } = fixture({
    runs: [
      run(2, "queued", "2026-09-10T11:45:00Z"),
      run(1, "in_progress", "2026-09-10T11:00:00Z"),
    ],
  });

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    forceRunMs: NORMAL_FORCE_RUN_MS,
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested, [{
    workflowId: "dashboard.yml",
    runId: 1,
    newerRunId: 2,
    ageMinutes: 60,
  }]);
  assert.deepEqual(calls, [
    ["list-runs", "dashboard.yml", {
      event: undefined,
      statuses: ACTIVE_RUN_STATUSES,
    }],
    ["list-jobs", 1],
    ["get-run", 1],
    ["cancel", 1],
    ["get-run", 1],
  ]);
});

test("cancels a stale run blocked by a newer pending run", async () => {
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:45:00Z"),
      run(1, "in_progress", "2026-09-10T11:00:00Z"),
    ],
    jobs: {
      1: [unassignedJob("2026-09-10T11:00:00Z")],
    },
  });

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    forceRunMs: NORMAL_FORCE_RUN_MS,
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested, [{
    workflowId: "dashboard.yml",
    runId: 1,
    newerRunId: 2,
    ageMinutes: 60,
  }]);
  assert.deepEqual(calls, [
    ["list-runs", "dashboard.yml", {
      event: undefined,
      statuses: ACTIVE_RUN_STATUSES,
    }],
    ["list-jobs", 1],
    ["get-run", 1],
    ["cancel", 1],
    ["get-run", 1],
  ]);
});

test("cancels a stale run blocked by a newer waiting run", async () => {
  const { actions, calls } = fixture({
    runs: [
      run(2, "waiting", "2026-09-10T11:45:00Z"),
      run(1, "in_progress", "2026-09-10T11:00:00Z"),
    ],
    jobs: {
      1: [unassignedJob("2026-09-10T11:00:00Z")],
    },
  });

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    forceRunMs: NORMAL_FORCE_RUN_MS,
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested, [{
    workflowId: "dashboard.yml",
    runId: 1,
    newerRunId: 2,
    ageMinutes: 60,
  }]);
  assert.deepEqual(calls, [
    ["list-runs", "dashboard.yml", {
      event: undefined,
      statuses: ACTIVE_RUN_STATUSES,
    }],
    ["list-jobs", 1],
    ["get-run", 1],
    ["cancel", 1],
    ["get-run", 1],
  ]);
});

test("cancels a stale waiting run blocking a newer pending run", async () => {
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:45:00Z"),
      run(1, "waiting", "2026-09-10T11:00:00Z"),
    ],
    jobs: {
      1: [unassignedJob("2026-09-10T11:00:00Z", "waiting")],
    },
  });

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    forceRunMs: NORMAL_FORCE_RUN_MS,
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested, [{
    workflowId: "dashboard.yml",
    runId: 1,
    newerRunId: 2,
    ageMinutes: 60,
  }]);
  assert.deepEqual(calls, [
    ["list-runs", "dashboard.yml", {
      event: undefined,
      statuses: ACTIVE_RUN_STATUSES,
    }],
    ["list-jobs", 1],
    ["get-run", 1],
    ["cancel", 1],
    ["get-run", 1],
  ]);
});

test("matches targeted dispatches by their exposed concurrency group", async () => {
  const targetedWorkflow = {
    workflowId: "dashboard.yml",
    event: "workflow_dispatch",
    groupByRunName: true,
    runNamePrefix: "pull-request-dashboard-",
  };
  const { actions, calls } = fixture({
    runs: [
      run(
        3,
        "pending",
        "2026-09-10T11:50:00Z",
        "workflow_dispatch",
        "pull-request-dashboard-repo-a-1-refresh",
      ),
      run(
        2,
        "pending",
        "2026-09-10T11:40:00Z",
        "workflow_dispatch",
        "pull-request-dashboard-repo-b-2-refresh",
      ),
      run(
        1,
        "waiting",
        "2026-09-10T11:00:00Z",
        "workflow_dispatch",
        "pull-request-dashboard-Repo-A-1-refresh",
      ),
    ],
    jobs: {
      1: [unassignedJob("2026-09-10T11:00:00Z", "waiting")],
    },
  });

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    forceRunMs: NORMAL_FORCE_RUN_MS,
    watchedWorkflows: [targetedWorkflow],
  });

  assert.deepEqual(result.requested, [{
    workflowId: "dashboard.yml",
    runId: 1,
    newerRunId: 3,
    ageMinutes: 60,
  }]);
  assert.deepEqual(calls, [
    ["list-runs", "dashboard.yml", {
      event: "workflow_dispatch",
      statuses: ACTIVE_RUN_STATUSES,
    }],
    ["list-jobs", 1],
    ["get-run", 1],
    ["cancel", 1],
    ["get-run", 1],
  ]);
});

test("ignores targeted dispatches without an exposed concurrency group", async () => {
  const targetedWorkflow = {
    workflowId: "dashboard.yml",
    event: "workflow_dispatch",
    groupByRunName: true,
    runNamePrefix: "pull-request-dashboard-",
  };
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:45:00Z"),
      run(1, "waiting", "2026-09-10T11:00:00Z"),
    ],
  });

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    watchedWorkflows: [targetedWorkflow],
  });

  assert.deepEqual(result.requested, []);
  assert.deepEqual(calls, [
    ["list-runs", "dashboard.yml", {
      event: "workflow_dispatch",
      statuses: ACTIVE_RUN_STATUSES,
    }],
  ]);
});

test("ignores manually dispatched dashboard runs", async () => {
  const targetedWorkflow = {
    workflowId: "dashboard.yml",
    event: "workflow_dispatch",
    groupByRunName: true,
    runNamePrefix: "pull-request-dashboard-",
    runNameSuffix: "-refresh",
  };
  const { actions, calls } = fixture({
    runs: [
      run(
        2,
        "pending",
        "2026-09-10T11:45:00Z",
        "workflow_dispatch",
        "pull-request-dashboard-repo-a-1-manual",
      ),
      run(
        1,
        "waiting",
        "2026-09-10T10:00:00Z",
        "workflow_dispatch",
        "pull-request-dashboard-repo-a-1-manual",
      ),
    ],
  });

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    watchedWorkflows: [targetedWorkflow],
  });

  assert.deepEqual(result.forceRequested, []);
  assert.deepEqual(calls, [
    ["list-runs", "dashboard.yml", {
      event: "workflow_dispatch",
      statuses: ACTIVE_RUN_STATUSES,
    }],
  ]);
});

test("continues when a run completes during cancellation", async () => {
  const conflict = Object.assign(new Error("Conflict"), {
    githubStatusCode: 409,
  });
  const { actions, calls, runs } = fixture({
    runs: [
      run(3, "pending", "2026-09-10T11:45:00Z"),
      run(2, "queued", "2026-09-10T11:00:00Z"),
      run(1, "in_progress", "2026-09-10T10:30:00Z"),
    ],
    jobs: {
      1: [unassignedJob("2026-09-10T10:30:00Z")],
      2: [unassignedJob("2026-09-10T11:00:00Z")],
    },
    cancellationErrors: { 1: conflict },
  });
  const cancel = actions.cancelWorkflowRun;
  actions.cancelWorkflowRun = (runId) => {
    if (runId === 1) {
      runs[2].status = "completed";
      runs[2].conclusion = "success";
    }
    return cancel(runId);
  };

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    forceRunMs: NORMAL_FORCE_RUN_MS,
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested, [{
    workflowId: "dashboard.yml",
    runId: 2,
    newerRunId: 3,
    ageMinutes: 60,
  }]);
  assert.deepEqual(result.unconfirmed.map(({ runId, reason }) =>
    ({ runId, reason })), [{ runId: 1, reason: "finished" }]);
  assert.deepEqual(result.conflicts, []);
  assert.deepEqual(calls, [
    ["list-runs", "dashboard.yml", {
      event: undefined,
      statuses: ACTIVE_RUN_STATUSES,
    }],
    ["list-jobs", 1],
    ["get-run", 1],
    ["cancel", 1],
    ["get-run", 1],
    ["list-jobs", 2],
    ["get-run", 2],
    ["cancel", 2],
    ["get-run", 2],
  ]);
});

test("does not cancel a run that received a runner", async () => {
  const assigned = {
    ...unassignedJob("2026-09-10T11:00:00Z"),
    runner_id: 123,
    runner_name: "GitHub Actions 123",
  };
  const { actions, calls } = fixture({
    runs: [
      run(2, "queued", "2026-09-10T11:45:00Z"),
      run(1, "in_progress", "2026-09-10T11:00:00Z"),
    ],
    jobs: { 1: [assigned] },
  });

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested, []);
  assert.equal(calls.some(([action]) => action === "cancel"), false);
});

test("cancels a partially completed run when only stale unassigned waiting jobs remain", async () => {
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:45:00Z"),
      run(1, "waiting", "2026-09-10T10:30:00Z"),
    ],
    jobs: {
      1: [
        completedJob(),
        unassignedJob("2026-09-10T11:10:00Z", "waiting"),
      ],
    },
  });
  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    forceRunMs: NORMAL_FORCE_RUN_MS,
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested, [{
    workflowId: "dashboard.yml",
    runId: 1,
    newerRunId: 2,
    ageMinutes: 90,
  }]);
  assert.deepEqual(calls.map(([action]) => action),
    ["list-runs", "list-jobs", "get-run", "cancel", "get-run"]);
});

test("requests cancellation for old queued jobs after other jobs finish", async () => {
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T10:30:00Z"),
    ],
    jobs: {
      1: [
        completedJob(),
        { ...completedJob(), conclusion: "skipped" },
        unassignedJob("2026-09-10T11:00:00Z", "queued"),
      ],
    },
  });

  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, forceRunMs: NORMAL_FORCE_RUN_MS,
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested.map(({ runId }) => runId), [1]);
  assert.deepEqual(result.confirmed, []);
  assert.equal(calls.some(([action]) => action === "cancel"), true);
});

test("requests cancellation for a partially finished targeted run with a queued unassigned job", async () => {
  const title = "pull-request-dashboard-opentelemetry-ebpf-instrumentation-3555-refresh";
  const { actions, calls } = fixture({
    runs: [
      run(36165134906, "pending", "2026-09-25T11:00:00Z", "workflow_dispatch", title),
      run(36008669270, "queued", "2026-09-24T13:52:19Z", "workflow_dispatch", title),
    ],
    jobs: {
      36008669270: [
        ...Array.from({ length: 7 }, () => completedJob("2026-09-24T14:00:00Z")),
        { ...completedJob("2026-09-24T14:00:00Z"), conclusion: "skipped" },
        {
          name: "run-targeted-dashboard-stable / update-dashboard",
          status: "queued",
          created_at: "2026-09-24T13:52:19Z",
          started_at: "2026-09-24T13:52:19Z",
          runner_id: null,
          runner_name: null,
          steps: [],
        },
      ],
    },
  });
  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => Date.parse("2026-09-25T12:00:00Z"),
    forceRunMs: 48 * 60 * 60 * 1000,
    watchedWorkflows: [{
      workflowId: "dashboard.yml",
      event: "workflow_dispatch",
      groupByRunName: true,
      runNamePrefix: "pull-request-dashboard-",
    }],
  });
  assert.deepEqual(result.requested.map(({ runId, newerRunId }) =>
    ({ runId, newerRunId })), [{
    runId: 36008669270,
    newerRunId: 36165134906,
  }]);
  assert.equal(calls.some(([action, id]) => action === "cancel" && id === 36008669270), true);
});

test("protects recently queued, assigned and started jobs in a partial run", async () => {
  for (const job of [
    unassignedJob("2026-09-10T11:45:00Z", "queued"),
    { ...unassignedJob("2026-09-10T11:00:00Z", "queued"), runner_id: 23 },
    {
      ...unassignedJob("2026-09-10T11:00:00Z", "queued"),
      steps: [{ started_at: "2026-09-10T11:30:00Z" }],
    },
    { ...unassignedJob("2026-09-10T11:00:00Z", "queued"), started_at: "2026-09-10T11:45:00Z" },
  ]) {
    const { actions, calls } = fixture({
      runs: [
        run(2, "pending", "2026-09-10T11:50:00Z"),
        run(1, "waiting", "2026-09-10T10:30:00Z"),
      ],
      jobs: { 1: [completedJob(), job] },
    });
    const result = await cancelStalledDashboardRuns({
      actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
    });
    assert.deepEqual(result.requested, []);
    assert.equal(calls.some(([action]) => action === "cancel"), false);
  }
});

test("does not cancel a newly queued or unknown job in an old run", async () => {
  for (const job of [
    unassignedJob("2026-09-10T11:45:00Z", "queued"),
    unassignedJob("2026-09-10T11:00:00Z", "unknown"),
  ]) {
    const { actions, calls } = fixture({
      runs: [
        run(2, "pending", "2026-09-10T11:50:00Z"),
        run(1, "waiting", "2026-09-10T10:00:00Z"),
      ],
      jobs: { 1: [job] },
    });
    const result = await cancelStalledDashboardRuns({
      actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
    });
    assert.deepEqual(result.requested, []);
    assert.equal(calls.some(([action]) => action === "cancel"), false);
  }
});

test("uses queued job age, not started_at as assignment evidence", async () => {
  for (const [startedAt, shouldRequest] of [
    ["2026-09-10T11:00:00Z", true],
    ["2026-09-10T11:45:00Z", false],
  ]) {
    const { actions } = fixture({
      runs: [
        run(2, "pending", "2026-09-10T11:50:00Z"),
        run(1, "queued", "2026-09-10T10:00:00Z"),
      ],
      jobs: {
        1: [{
          status: "queued",
          created_at: "2026-09-10T11:00:00Z",
          started_at: startedAt,
          runner_id: null,
          runner_name: null,
          steps: [],
        }],
      },
    });
    const result = await cancelStalledDashboardRuns({
      actions, now: () => NOW, forceRunMs: NORMAL_FORCE_RUN_MS,
      watchedWorkflows: [WORKFLOW],
    });
    assert.equal(result.requested.length, Number(shouldRequest));
  }
});

test("uses exact 30 and 60 minute run-age thresholds", async () => {
  for (const [ageMs, expectedCall] of [
    [30 * 60 * 1000 - 1, null],
    [30 * 60 * 1000, "cancel"],
    [60 * 60 * 1000 - 1, "cancel"],
    [60 * 60 * 1000, "force-cancel"],
  ]) {
    const { actions, calls } = fixture({
      runs: [
        run(2, "pending", "2026-09-10T11:55:00Z"),
        run(1, "waiting", new Date(NOW - ageMs).toISOString()),
      ],
      jobs: { 1: [unassignedJob("2026-09-10T11:00:00Z", "waiting")] },
    });
    const result = await cancelStalledDashboardRuns({
      actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
    });
    assert.deepEqual(calls.filter(([action]) =>
      action === "cancel" || action === "force-cancel").map(([action]) => action),
    expectedCall ? [expectedCall] : []);
    assert.equal(result.requested.length, Number(expectedCall === "cancel"));
    assert.equal(result.forceRequested.length, Number(expectedCall === "force-cancel"));
  }
});

test("forces a 60-minute-old run on the first invocation", async () => {
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:55:00Z"),
      run(1, "waiting", "2026-09-10T11:00:00Z"),
    ],
    jobs: { 1: [unassignedJob("2026-09-10T11:00:00Z", "waiting")] },
  });
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.requested, []);
  assert.deepEqual(result.forceRequested.map(({ runId, ageMinutes }) =>
    ({ runId, ageMinutes })), [{ runId: 1, ageMinutes: 60 }]);
  assert.deepEqual(calls.map(([action]) => action),
    ["list-runs", "list-jobs", "get-run", "get-run", "force-cancel", "get-run"]);
});

test("does not force a fresh rerun with an old run ID and no jobs", async () => {
  let clock = NOW;
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:55:00Z"),
      {
        ...run(1, "waiting", "2026-09-10T10:00:00Z"),
        run_attempt: 2,
        run_started_at: "2026-09-10T11:50:00Z",
      },
    ],
  });
  const options = { actions, now: () => clock, watchedWorkflows: [WORKFLOW] };
  const fresh = await cancelStalledDashboardRuns(options);
  assert.deepEqual(fresh.requested, []);
  assert.deepEqual(fresh.forceRequested, []);
  assert.deepEqual(calls.map(([action]) => action), ["list-runs"]);

  clock += 50 * 60 * 1000;
  const stale = await cancelStalledDashboardRuns(options);
  assert.deepEqual(stale.requested, []);
  assert.deepEqual(stale.forceRequested.map(({ runId, ageMinutes }) =>
    ({ runId, ageMinutes })), [{ runId: 1, ageMinutes: 60 }]);
  assert.equal(calls.some(([action]) => action === "list-jobs"), true);
});

test("does not infer a rerun age or newer request from the original run", async () => {
  for (const [startedAt, newerCreatedAt] of [
    [null, "2026-09-10T11:55:00Z"],
    ["2026-09-10T10:30:00Z", "2026-09-10T10:20:00Z"],
  ]) {
    const { actions, calls } = fixture({
      runs: [
        run(2, "pending", newerCreatedAt),
        {
          ...run(1, "waiting", "2026-09-10T09:00:00Z"),
          run_attempt: 2,
          run_started_at: startedAt,
        },
      ],
    });
    const result = await cancelStalledDashboardRuns({
      actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
    });
    assert.deepEqual(result.forceRequested, []);
    assert.deepEqual(result.requested, []);
    assert.deepEqual(calls.map(([action]) => action), ["list-runs"]);
  }
});

test("does not force if the run is rerun between listing and revalidation", async () => {
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:55:00Z"),
      { ...run(1, "waiting", "2026-09-10T10:00:00Z"), run_attempt: 1 },
    ],
  });
  const getRun = actions.getWorkflowRun;
  actions.getWorkflowRun = async (id) => {
    const current = await getRun(id);
    return id === 1
      ? { ...current, run_attempt: 2, run_started_at: "2026-09-10T11:50:00Z" }
      : current;
  };
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.forceRequested, []);
  assert.deepEqual(result.confirmed, []);
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
});

test("does not force if the run is rerun during the job lookup", async () => {
  const { actions, calls, runs } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:55:00Z"),
      { ...run(1, "waiting", "2026-09-10T10:00:00Z"), run_attempt: 1 },
    ],
  });
  const listJobs = actions.listRunJobs;
  actions.listRunJobs = async (id) => {
    const jobs = await listJobs(id);
    runs[1] = {
      ...runs[1],
      run_attempt: 2,
      run_started_at: "2026-09-10T11:50:00Z",
    };
    return jobs;
  };
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.forceRequested, []);
  assert.deepEqual(calls.map(([action]) => action),
    ["list-runs", "list-jobs", "get-run", "get-run"]);
});

test("does not normally cancel if the run is rerun during the job lookup", async () => {
  const { actions, calls, runs } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:55:00Z"),
      { ...run(1, "waiting", "2026-09-10T11:20:00Z"), run_attempt: 1 },
    ],
  });
  const listJobs = actions.listRunJobs;
  actions.listRunJobs = async (id) => {
    const jobs = await listJobs(id);
    runs[1] = {
      ...runs[1],
      run_attempt: 2,
      run_started_at: "2026-09-10T11:50:00Z",
    };
    return jobs;
  };
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.requested, []);
  assert.deepEqual(calls.map(([action]) => action),
    ["list-runs", "list-jobs", "get-run"]);
});

test("uses the newer run's current attempt start", async () => {
  const { actions, calls } = fixture({
    runs: [
      {
        ...run(2, "pending", "2026-09-10T09:00:00Z"),
        run_attempt: 2,
        run_started_at: "2026-09-10T11:55:00Z",
      },
      run(1, "waiting", "2026-09-10T10:00:00Z"),
    ],
  });
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.forceRequested.map(({ runId }) => runId), [1]);
  assert.equal(calls.some(([action]) => action === "force-cancel"), true);
});

test("does not use a newer rerun with an unknown attempt start", async () => {
  const { actions, calls } = fixture({
    runs: [
      {
        ...run(2, "pending", "2026-09-10T11:55:00Z"),
        run_attempt: 2,
        run_started_at: null,
      },
      run(1, "waiting", "2026-09-10T10:00:00Z"),
    ],
  });
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.forceRequested, []);
  assert.deepEqual(calls.map(([action]) => action), ["list-runs"]);
});

test("repeats normal and force requests without retaining state", async () => {
  let clock = NOW;
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:55:00Z"),
      run(1, "queued", "2026-09-10T11:20:00Z"),
    ],
    jobs: {
      1: [
        completedJob("2026-09-10T11:00:00Z"),
        {
          status: "queued",
          created_at: "2026-09-10T11:00:00Z",
          started_at: "2026-09-10T11:00:00Z",
          runner_id: null,
          runner_name: null,
          steps: [],
        },
      ],
    },
  });
  const options = { actions, now: () => clock, watchedWorkflows: [WORKFLOW] };
  const first = await cancelStalledDashboardRuns(options);
  assert.deepEqual(first.requested.map(({ runId }) => runId), [1]);
  assert.deepEqual(first.confirmed, []);
  assert.deepEqual(first.forceRequested, []);

  const second = await cancelStalledDashboardRuns(options);
  assert.deepEqual(second.requested.map(({ runId }) => runId), [1]);
  assert.deepEqual(second.forceRequested, []);
  assert.equal(calls.filter(([action]) => action === "cancel").length, 2);

  clock += 20 * 60 * 1000;
  const third = await cancelStalledDashboardRuns(options);
  assert.deepEqual(third.forceRequested.map(({ runId }) => runId), [1]);
  assert.deepEqual(third.confirmed, []);
  assert.equal(calls.filter(([action]) => action === "force-cancel").length, 1);

  const fourth = await cancelStalledDashboardRuns(options);
  assert.deepEqual(fourth.confirmed, []);
  assert.deepEqual(fourth.forceRequested.map(({ runId }) => runId), [1]);
  assert.equal(calls.filter(([action]) => action === "force-cancel").length, 2);
});

test("confirms only the immediate post-request GET, not a later invocation", async () => {
  const { actions, runs, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T11:20:00Z"),
    ],
  });
  const options = { actions, now: () => NOW, watchedWorkflows: [WORKFLOW] };
  const first = await cancelStalledDashboardRuns(options);
  assert.deepEqual(first.confirmed, []);
  assert.deepEqual(first.requested.map(({ runId }) => runId), [1]);
  assert.equal(calls.at(-1)[0], "get-run");
  runs[1].status = "completed";
  runs[1].conclusion = "cancelled";
  const result = await cancelStalledDashboardRuns(options);
  assert.deepEqual(result.confirmed, []);
  assert.deepEqual(result.unconfirmed, []);
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
});

test("reports a deleted run on the immediate normal-cancellation GET", async () => {
  const missing = Object.assign(new Error("Not Found"), { githubStatusCode: 404 });
  const { actions, calls, getRunErrors } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T11:20:00Z"),
    ],
  });
  const cancel = actions.cancelWorkflowRun;
  actions.cancelWorkflowRun = async (id) => {
    await cancel(id);
    getRunErrors[id] = missing;
  };
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.requested.map(({ runId }) => runId), [1]);
  assert.deepEqual(result.confirmed, []);
  assert.deepEqual(result.unconfirmed.map(({ runId, reason }) =>
    ({ runId, reason })), [{ runId: 1, reason: "not_found" }]);
  assert.deepEqual(calls.map(([action]) => action),
    ["list-runs", "list-jobs", "get-run", "cancel", "get-run"]);
});

test("skips a deleted run during force revalidation without reporting a request", async () => {
  const missing = Object.assign(new Error("Not Found"), { githubStatusCode: 404 });
  const { actions, calls, getRunErrors } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T10:00:00Z"),
    ],
  });
  getRunErrors[1] = missing;
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.unconfirmed, []);
  assert.deepEqual(result.confirmed, []);
  assert.deepEqual(result.forceRequested, []);
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
});

test("skips force when job lookup races with run deletion", async () => {
  const missing = Object.assign(new Error("Not Found"), { githubStatusCode: 404 });
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T10:00:00Z"),
    ],
    jobErrors: { 1: missing },
  });
  const getRun = actions.getWorkflowRun;
  actions.getWorkflowRun = (id) => {
    if (Number(id) === 1) {
      throw missing;
    }
    return getRun(id);
  };
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.unconfirmed, []);
  assert.deepEqual(result.forceRequested, []);
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
});

test("does not force when the newer run disappears during revalidation", async () => {
  const missing = Object.assign(new Error("Not Found"), { githubStatusCode: 404 });
  const { actions, calls, getRunErrors } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T10:00:00Z"),
    ],
  });
  getRunErrors[2] = missing;
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.unconfirmed, []);
  assert.deepEqual(result.forceRequested, []);
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
});

test("surfaces forbidden run and job lookups during force revalidation", async () => {
  for (const badLookup of ["older", "newer", "jobs"]) {
    const forbidden = Object.assign(new Error("Forbidden"), { githubStatusCode: 403 });
    const { actions } = fixture({
      runs: [
        run(2, "pending", "2026-09-10T11:50:00Z"),
        run(1, "waiting", "2026-09-10T10:00:00Z"),
      ],
      getRunErrors: badLookup === "jobs" ? {} : { [badLookup === "older" ? 1 : 2]: forbidden },
      jobErrors: badLookup === "jobs" ? { 1: forbidden } : {},
    });
    await assert.rejects(cancelStalledDashboardRuns({
      actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
    }), (error) => error === forbidden);
  }
});

test("does not force a run that gains a runner or started step", async () => {
  for (const change of [
    (job) => { job.runner_id = 99; },
    (job) => { job.steps[0].started_at = "2026-09-10T12:01:00Z"; },
  ]) {
    const job = unassignedJob("2026-09-10T10:00:00Z", "waiting");
    const { actions, calls } = fixture({
      runs: [
        run(2, "pending", "2026-09-10T11:50:00Z"),
        run(1, "waiting", "2026-09-10T10:00:00Z"),
      ],
      jobs: { 1: [job] },
    });
    change(job);
    const result = await cancelStalledDashboardRuns({
      actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
    });
    assert.deepEqual(result.forceRequested, []);
    assert.equal(calls.some(([action]) => action === "force-cancel"), false);
  }
});

test("does not force after the newer same-group request disappears", async () => {
  const { actions, runs, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T10:00:00Z"),
    ],
  });
  runs[0].status = "completed";
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.forceRequested, []);
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
});

test("does not force across different targeted PR groups", async () => {
  const workflow = {
    workflowId: "dashboard.yml",
    event: "workflow_dispatch",
    groupByRunName: true,
    runNamePrefix: "pull-request-dashboard-",
  };
  const { actions, runs, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z", "workflow_dispatch", "pull-request-dashboard-a-1-refresh"),
      run(1, "waiting", "2026-09-10T10:00:00Z", "workflow_dispatch", "pull-request-dashboard-a-1-refresh"),
    ],
  });
  runs[0].display_title = "pull-request-dashboard-a-2-refresh";
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [workflow],
  });
  assert.deepEqual(result.forceRequested, []);
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
});

test("rechecks the newer group immediately before force-cancel", async () => {
  const workflow = {
    workflowId: "dashboard.yml",
    event: "workflow_dispatch",
    groupByRunName: true,
    runNamePrefix: "pull-request-dashboard-",
  };
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z", "workflow_dispatch", "pull-request-dashboard-a-1-refresh"),
      run(1, "waiting", "2026-09-10T10:00:00Z", "workflow_dispatch", "pull-request-dashboard-a-1-refresh"),
    ],
  });
  const originalGet = actions.getWorkflowRun;
  actions.getWorkflowRun = async (id) => {
    const current = await originalGet(id);
    return id === 2
      ? { ...current, display_title: "pull-request-dashboard-a-2-refresh" }
      : current;
  };
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [workflow],
  });
  assert.deepEqual(result.forceRequested, []);
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
});

test("rotates a force-cancel backlog across bounded invocations", async () => {
  let clock = NOW;
  const runs = [
    run(9, "pending", "2026-09-10T11:50:00Z"),
    ...Array.from({ length: 8 }, (_, index) =>
      run(index + 1, "waiting", `2026-09-10T0${index}:00:00Z`)),
  ];
  const { actions, calls } = fixture({ runs });
  const options = { actions, now: () => clock, watchedWorkflows: [WORKFLOW] };
  const seen = new Set();
  for (let tick = 0; tick < 2; tick += 1) {
    const result = await cancelStalledDashboardRuns(options);
    assert.ok(result.forceRequested.length <= 4);
    for (const { runId } of result.forceRequested) {
      seen.add(runId);
    }
    clock += 15 * 60 * 1000;
  }
  assert.equal(seen.size, 8);
  assert.ok(calls.some(([action, runId]) => action === "force-cancel" && runId === 8));
});

test("a recovered run is never force-cancelled", async () => {
  const { actions, runs, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T10:00:00Z"),
    ],
  });
  runs[1].status = "in_progress";
  const originalGet = actions.getWorkflowRun;
  actions.getWorkflowRun = async (runId) =>
    runId === 1 ? { ...await originalGet(runId), status: "completed", conclusion: "success" } :
      originalGet(runId);
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.forceRequested, []);
  assert.deepEqual(result.confirmed, []);
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
});

test("rechecks a force-cancel 409 and retries on each invocation if still active", async () => {
  const conflict = Object.assign(new Error("Conflict"), { githubStatusCode: 409 });
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T10:00:00Z"),
    ],
    forceErrors: { 1: conflict },
  });
  const options = { actions, now: () => NOW, watchedWorkflows: [WORKFLOW] };
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const result = await cancelStalledDashboardRuns(options);
    assert.deepEqual(result.forceRequested, []);
    assert.deepEqual(result.confirmed, []);
    assert.deepEqual(result.conflicts.map(({ runId, stage }) =>
      ({ runId, stage })), [{ runId: 1, stage: "force" }]);
  }
  assert.equal(calls.filter(([action]) => action === "force-cancel").length, 3);
  assert.equal(calls.filter(([action, id]) => action === "get-run" && id === 1).length, 6);
});

test("confirms a completed run after a force-cancel 409 race", async () => {
  const conflict = Object.assign(new Error("Conflict"), { githubStatusCode: 409 });
  const { actions, runs } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T10:00:00Z"),
    ],
    forceErrors: { 1: conflict },
  });
  const force = actions.forceCancelWorkflowRun;
  actions.forceCancelWorkflowRun = async (runId) => {
    runs[1].status = "completed";
    runs[1].conclusion = "cancelled";
    await force(runId);
  };
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.confirmed.map(({ runId }) => runId), [1]);
  assert.deepEqual(result.conflicts, []);
  assert.deepEqual(result.forceRequested, []);
});

test("retries normal 409 conflicts until age reaches the force threshold", async () => {
  let clock = NOW;
  const conflict = Object.assign(new Error("Conflict"), { githubStatusCode: 409 });
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T11:20:00Z"),
    ],
    cancellationErrors: { 1: conflict },
  });
  const options = { actions, now: () => clock, watchedWorkflows: [WORKFLOW] };
  const first = await cancelStalledDashboardRuns(options);
  assert.deepEqual(first.conflicts.map(({ runId, stage }) =>
    ({ runId, stage })), [{ runId: 1, stage: "normal" }]);
  assert.deepEqual(first.requested, []);
  clock += 10 * 60 * 1000;
  const retry = await cancelStalledDashboardRuns(options);
  assert.deepEqual(retry.conflicts.map(({ runId, stage }) =>
    ({ runId, stage })), [{ runId: 1, stage: "normal" }]);
  clock += 10 * 60 * 1000;
  const result = await cancelStalledDashboardRuns(options);
  assert.deepEqual(result.forceRequested.map(({ runId }) => runId), [1]);
  assert.deepEqual(result.confirmed, []);
  assert.equal(calls.filter(([action]) => action === "cancel").length, 2);
  assert.equal(calls.filter(([action]) => action === "force-cancel").length, 1);
});

test("force timing depends on run age, not normal cancellation acceptance", async () => {
  let clock = NOW;
  const conflict = Object.assign(new Error("Conflict"), { githubStatusCode: 409 });
  const cancellationErrors = { 1: conflict };
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T11:20:00Z"),
    ],
    cancellationErrors,
  });
  const options = { actions, now: () => clock, watchedWorkflows: [WORKFLOW] };
  assert.equal((await cancelStalledDashboardRuns(options)).conflicts.length, 1);
  delete cancellationErrors[1];
  clock += 10 * 60 * 1000;
  assert.equal((await cancelStalledDashboardRuns(options)).requested.length, 1);
  clock += 10 * 60 * 1000;
  assert.deepEqual((await cancelStalledDashboardRuns(options)).forceRequested
    .map(({ runId }) => runId), [1]);
  assert.equal(calls.filter(([action]) => action === "force-cancel").length, 1);
});

test("confirms a completed run after a normal-cancel 409 race", async () => {
  const conflict = Object.assign(new Error("Conflict"), { githubStatusCode: 409 });
  const { actions, runs } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T11:20:00Z"),
    ],
    cancellationErrors: { 1: conflict },
  });
  const cancel = actions.cancelWorkflowRun;
  actions.cancelWorkflowRun = async (runId) => {
    runs[1].status = "completed";
    runs[1].conclusion = "cancelled";
    await cancel(runId);
  };
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.confirmed.map(({ runId }) => runId), [1]);
  assert.deepEqual(result.conflicts, []);
});

test("continues forcing a still-blocked run without an attempt cap", async () => {
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T10:00:00Z"),
    ],
  });
  const options = { actions, now: () => NOW, watchedWorkflows: [WORKFLOW] };
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const result = await cancelStalledDashboardRuns(options);
    assert.deepEqual(result.forceRequested.map(({ runId }) => runId), [1]);
    assert.deepEqual(result.confirmed, []);
  }
  assert.equal(calls.filter(([action]) => action === "force-cancel").length, 3);
});

test("confirms cancellation only when GitHub reports a cancelled conclusion", async () => {
  const { actions } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T11:20:00Z"),
    ],
    onCancel(runId, runs) {
      const cancelled = runs.find((run) => run.id === runId);
      cancelled.status = "completed";
      cancelled.conclusion = "cancelled";
    },
  });
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.requested.map(({ runId }) => runId), [1]);
  assert.deepEqual(result.confirmed.map(({ runId }) => runId), [1]);
});

test("confirms force cancellation from the immediate post-request GET", async () => {
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T11:00:00Z"),
    ],
    onForceCancel(id, runs) {
      const cancelled = runs.find((candidate) => candidate.id === id);
      cancelled.status = "completed";
      cancelled.conclusion = "cancelled";
    },
  });
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.forceRequested.map(({ runId }) => runId), [1]);
  assert.deepEqual(result.confirmed.map(({ runId }) => runId), [1]);
  assert.deepEqual(result.unconfirmed, []);
  assert.deepEqual(calls.slice(-2), [["force-cancel", 1], ["get-run", 1]]);
});

test("reports a deleted run only when the force post-request GET returns 404", async () => {
  const missing = Object.assign(new Error("Not Found"), { githubStatusCode: 404 });
  const { actions, calls, getRunErrors } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T11:00:00Z"),
    ],
  });
  const force = actions.forceCancelWorkflowRun;
  actions.forceCancelWorkflowRun = async (id) => {
    await force(id);
    getRunErrors[id] = missing;
  };
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.forceRequested.map(({ runId }) => runId), [1]);
  assert.deepEqual(result.unconfirmed.map(({ runId, reason }) =>
    ({ runId, reason })), [{ runId: 1, reason: "not_found" }]);
  assert.deepEqual(result.confirmed, []);
  assert.deepEqual(calls.slice(-2), [["force-cancel", 1], ["get-run", 1]]);
});

test("surfaces forbidden immediate confirmation lookups for both cancellation methods", async () => {
  const forbidden = Object.assign(new Error("Forbidden"), { githubStatusCode: 403 });
  for (const age of [40, 60]) {
    const { actions, getRunErrors } = fixture({
      runs: [
        run(2, "pending", "2026-09-10T11:55:00Z"),
        run(1, "waiting", new Date(NOW - age * 60 * 1000).toISOString()),
      ],
    });
    const method = age === 40 ? "cancelWorkflowRun" : "forceCancelWorkflowRun";
    const request = actions[method];
    actions[method] = async (id) => {
      await request(id);
      getRunErrors[id] = forbidden;
    };
    await assert.rejects(cancelStalledDashboardRuns({
      actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
    }), (error) => error === forbidden);
  }
});

test("records a non-cancelled finish after a force 409 as unconfirmed", async () => {
  const conflict = Object.assign(new Error("Conflict"), { githubStatusCode: 409 });
  const { actions, runs, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T11:00:00Z"),
    ],
    forceErrors: { 1: conflict },
  });
  const force = actions.forceCancelWorkflowRun;
  actions.forceCancelWorkflowRun = async (id) => {
    runs.find((candidate) => candidate.id === id).status = "completed";
    runs.find((candidate) => candidate.id === id).conclusion = "success";
    await force(id);
  };
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });
  assert.deepEqual(result.unconfirmed.map(({ runId, reason }) =>
    ({ runId, reason })), [{ runId: 1, reason: "finished" }]);
  assert.deepEqual(result.confirmed, []);
  assert.deepEqual(result.forceRequested, []);
  assert.deepEqual(result.conflicts, []);
  assert.deepEqual(calls.slice(-2), [["force-cancel", 1], ["get-run", 1]]);
});

test("does not cancel a partially completed run with a newly waiting job", async () => {
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T10:30:00Z"),
    ],
    jobs: {
      1: [
        completedJob(),
        unassignedJob("2026-09-10T11:45:00Z", "waiting"),
      ],
    },
  });

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested, []);
  assert.equal(calls.some(([action]) => action === "cancel"), false);
});

test("does not cancel a run with skipped jobs and a newly waiting job", async () => {
  const { actions } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T10:30:00Z"),
    ],
    jobs: {
      1: [
        unassignedJob("2026-09-10T11:00:00Z", "completed"),
        unassignedJob("2026-09-10T11:45:00Z", "waiting"),
      ],
    },
  });

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested, []);
});

test("does not cancel a partially completed run with active or unknown work", async () => {
  for (const unfinished of [
    unassignedJob("2026-09-10T11:00:00Z", "in_progress"),
    unassignedJob("2026-09-10T11:00:00Z", "unknown"),
    { ...unassignedJob("2026-09-10T11:00:00Z", "waiting"), runner_id: 456 },
    { ...unassignedJob("2026-09-10T11:00:00Z", "waiting"), started_at: null },
  ]) {
    const { actions, calls } = fixture({
      runs: [
        run(2, "pending", "2026-09-10T11:45:00Z"),
        run(1, "waiting", "2026-09-10T10:30:00Z"),
      ],
      jobs: {
        1: [
          completedJob(),
          unassignedJob("2026-09-10T11:00:00Z", "waiting"),
          unfinished,
        ],
      },
    });

    const result = await cancelStalledDashboardRuns({
      actions,
      now: () => NOW,
      watchedWorkflows: [WORKFLOW],
    });

    assert.deepEqual(result.requested, []);
    assert.equal(calls.some(([action]) => action === "cancel"), false);
  }
});

test("does not cancel a completed run with no unfinished jobs", async () => {
  const { actions } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:45:00Z"),
      run(1, "waiting", "2026-09-10T10:30:00Z"),
    ],
    jobs: { 1: [completedJob()] },
  });

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested, []);
});

test("does not cancel without a newer queued run", async () => {
  const { actions, calls } = fixture({
    runs: [run(1, "in_progress", "2026-09-10T11:00:00Z")],
    jobs: {
      1: [unassignedJob("2026-09-10T11:00:00Z")],
    },
  });

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested, []);
  assert.deepEqual(calls, [
    ["list-runs", "dashboard.yml", {
      event: undefined,
      statuses: ACTIVE_RUN_STATUSES,
    }],
  ]);
});

test("does not cancel before the stale threshold", async () => {
  const { actions, calls } = fixture({
    runs: [
      run(2, "queued", "2026-09-10T11:50:00Z"),
      run(1, "in_progress", "2026-09-10T11:40:00Z"),
    ],
    jobs: {
      1: [unassignedJob("2026-09-10T11:40:00Z")],
    },
  });

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested, []);
  assert.deepEqual(calls, [
    ["list-runs", "dashboard.yml", {
      event: undefined,
      statuses: ACTIVE_RUN_STATUSES,
    }],
  ]);
});

test("uses the workflow run age instead of the job record age", async () => {
  const { actions, calls } = fixture({
    runs: [
      run(2, "queued", "2026-09-10T11:45:00Z"),
      run(1, "in_progress", "2026-09-10T11:00:00Z"),
    ],
    jobs: {
      1: [unassignedJob("2026-09-10T11:45:00Z")],
    },
  });

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    forceRunMs: NORMAL_FORCE_RUN_MS,
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested, [{
    workflowId: "dashboard.yml",
    runId: 1,
    newerRunId: 2,
    ageMinutes: 60,
  }]);
  assert.deepEqual(calls, [
    ["list-runs", "dashboard.yml", {
      event: undefined,
      statuses: ACTIVE_RUN_STATUSES,
    }],
    ["list-jobs", 1],
    ["get-run", 1],
    ["cancel", 1],
    ["get-run", 1],
  ]);
});

test("filters workflows whose concurrency group is event-specific", async () => {
  const scheduledWorkflow = {
    workflowId: "dashboard.yml",
    event: "schedule",
  };
  const { actions, calls } = fixture({
    runs: [
      run(3, "queued", "2026-09-10T11:45:00Z", "workflow_dispatch"),
      run(2, "queued", "2026-09-10T11:40:00Z", "schedule"),
      run(1, "in_progress", "2026-09-10T11:00:00Z", "schedule"),
    ],
    jobs: {
      1: [unassignedJob("2026-09-10T11:00:00Z")],
    },
  });

  await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    forceRunMs: NORMAL_FORCE_RUN_MS,
    watchedWorkflows: [scheduledWorkflow],
  });

  assert.deepEqual(calls, [
    ["list-runs", "dashboard.yml", {
      event: "schedule",
      statuses: ACTIVE_RUN_STATUSES,
    }],
    ["list-jobs", 1],
    ["get-run", 1],
    ["cancel", 1],
    ["get-run", 1],
  ]);
});
