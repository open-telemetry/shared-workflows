import assert from "node:assert/strict";
import test from "node:test";

import {
  cancelStalledDashboardRuns as runWatchdog,
} from "./netlify/lib/workflow-watchdog.mjs";

const NOW = Date.parse("2026-09-10T12:00:00Z");
const WORKFLOW = Object.freeze({ workflowId: "dashboard.yml" });
const ACTIVE_RUN_STATUSES = ["in_progress", "queued", "waiting", "pending"];

function fixture({
  runs, jobs = {}, cancellationErrors = {}, forceErrors = {}, onCancel,
}) {
  const calls = [];
  const entries = new Map();
  let version = 0;
  const store = {
    async get(key) {
      return structuredClone(entries.get(key) || null);
    },
    async set(key, value, condition) {
      const previous = entries.get(key);
      if ((condition.onlyIfNew && previous) ||
          (condition.onlyIfMatch && previous?.etag !== condition.onlyIfMatch)) {
        return { modified: false };
      }
      const etag = `"${++version}"`;
      entries.set(key, { etag, value: structuredClone(value) });
      return { modified: true, etag };
    },
  };
  const actions = {
    store,
    async listWorkflowRuns(workflowId, options) {
      calls.push(["list-runs", workflowId, options]);
      return runs.filter((candidate) =>
        options.statuses.includes(candidate.status) &&
        (!options.event || candidate.event === options.event));
    },
    async getWorkflowRun(runId) {
      calls.push(["get-run", Number(runId)]);
      return runs.find((candidate) => candidate.id === Number(runId));
    },
    async listRunJobs(runId) {
      calls.push(["list-jobs", runId]);
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
    },
  };
  return { actions, calls, store, runs, jobs };
}

function cancelStalledDashboardRuns({ actions, ...options }) {
  return runWatchdog({ actions, store: actions.store, ...options });
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
    unresponsive: [],
  });
  assert.deepEqual(calls, [
    ["list-runs", "dashboard.yml", {
      event: undefined,
      statuses: ACTIVE_RUN_STATUSES,
    }],
    ["list-jobs", 1],
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

test("continues when a run completes during cancellation", async () => {
  const conflict = Object.assign(new Error("Conflict"), {
    githubStatusCode: 409,
  });
  const { actions, calls } = fixture({
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

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => NOW,
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested, [{
    workflowId: "dashboard.yml",
    runId: 2,
    newerRunId: 3,
    ageMinutes: 60,
  }]);
  assert.deepEqual(calls, [
    ["list-runs", "dashboard.yml", {
      event: undefined,
      statuses: ACTIVE_RUN_STATUSES,
    }],
    ["list-jobs", 1],
    ["cancel", 1],
    ["list-jobs", 2],
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
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested, [{
    workflowId: "dashboard.yml",
    runId: 1,
    newerRunId: 2,
    ageMinutes: 90,
  }]);
  assert.deepEqual(calls.map(([action]) => action), ["list-runs", "list-jobs", "cancel", "get-run"]);
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
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested.map(({ runId }) => runId), [1]);
  assert.deepEqual(result.confirmed, []);
  assert.equal(calls.some(([action]) => action === "cancel"), true);
});

test("protects recently queued, assigned and started jobs in a partial run", async () => {
  for (const job of [
    unassignedJob("2026-09-10T11:45:00Z", "queued"),
    { ...unassignedJob("2026-09-10T11:00:00Z", "queued"), runner_id: 23 },
    {
      ...unassignedJob("2026-09-10T11:00:00Z", "queued"),
      steps: [{ started_at: "2026-09-10T11:30:00Z" }],
    },
    { ...unassignedJob("2026-09-10T11:00:00Z", "queued"), started_at: "2026-09-10T11:00:00Z" },
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

test("waits for normal cancellation before forcing and confirms the outcome", async () => {
  let clock = NOW;
  const { actions, calls, runs } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:45:00Z"),
      run(1, "waiting", "2026-09-10T10:00:00Z"),
    ],
    jobs: { 1: [unassignedJob("2026-09-10T10:00:00Z", "waiting")] },
  });
  const options = { actions, now: () => clock, watchedWorkflows: [WORKFLOW] };
  const first = await cancelStalledDashboardRuns(options);
  assert.deepEqual(first.requested.map(({ runId }) => runId), [1]);
  assert.deepEqual(first.confirmed, []);
  assert.deepEqual(first.forceRequested, []);

  clock += 15 * 60 * 1000;
  const second = await cancelStalledDashboardRuns(options);
  assert.deepEqual(second.requested, []);
  assert.deepEqual(second.forceRequested, []);
  assert.equal(calls.filter(([action]) => action === "cancel").length, 1);

  clock += 15 * 60 * 1000;
  const third = await cancelStalledDashboardRuns(options);
  assert.deepEqual(third.forceRequested.map(({ runId }) => runId), [1]);
  assert.deepEqual(third.confirmed, []);
  assert.equal(calls.filter(([action]) => action === "force-cancel").length, 1);

  runs[1].status = "completed";
  runs[1].conclusion = "cancelled";
  clock += 15 * 60 * 1000;
  const fourth = await cancelStalledDashboardRuns(options);
  assert.deepEqual(fourth.confirmed.map(({ runId }) => runId), [1]);
  assert.deepEqual(fourth.forceRequested, []);
});

test("confirms an asynchronous normal cancellation without force", async () => {
  let clock = NOW;
  const { actions, runs, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T10:00:00Z"),
    ],
  });
  const options = { actions, now: () => clock, watchedWorkflows: [WORKFLOW] };
  await cancelStalledDashboardRuns(options);
  runs[1].status = "completed";
  runs[1].conclusion = "cancelled";
  clock += 15 * 60 * 1000;
  const result = await cancelStalledDashboardRuns(options);
  assert.deepEqual(result.confirmed.map(({ runId }) => runId), [1]);
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
});

test("does not force a run that gains a runner or started step", async () => {
  for (const change of [
    (job) => { job.runner_id = 99; },
    (job) => { job.steps[0].started_at = "2026-09-10T12:01:00Z"; },
  ]) {
    let clock = NOW;
    const job = unassignedJob("2026-09-10T10:00:00Z", "waiting");
    const { actions, calls } = fixture({
      runs: [
        run(2, "pending", "2026-09-10T11:50:00Z"),
        run(1, "waiting", "2026-09-10T10:00:00Z"),
      ],
      jobs: { 1: [job] },
    });
    const options = { actions, now: () => clock, watchedWorkflows: [WORKFLOW] };
    await cancelStalledDashboardRuns(options);
    change(job);
    clock += 30 * 60 * 1000;
    const result = await cancelStalledDashboardRuns(options);
    assert.deepEqual(result.forceRequested, []);
    assert.equal(calls.some(([action]) => action === "force-cancel"), false);
  }
});

test("does not force after the newer same-group request disappears", async () => {
  let clock = NOW;
  const { actions, runs, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T10:00:00Z"),
    ],
  });
  const options = { actions, now: () => clock, watchedWorkflows: [WORKFLOW] };
  await cancelStalledDashboardRuns(options);
  runs[0].status = "completed";
  clock += 30 * 60 * 1000;
  const result = await cancelStalledDashboardRuns(options);
  assert.deepEqual(result.forceRequested, []);
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
});

test("does not force across different targeted PR groups", async () => {
  let clock = NOW;
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
  const options = { actions, now: () => clock, watchedWorkflows: [workflow] };
  await cancelStalledDashboardRuns(options);
  runs[0].display_title = "pull-request-dashboard-a-2-refresh";
  clock += 30 * 60 * 1000;
  const result = await cancelStalledDashboardRuns(options);
  assert.deepEqual(result.forceRequested, []);
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
});

test("rechecks the newer group immediately before force-cancel", async () => {
  let clock = NOW;
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
  const options = { actions, now: () => clock, watchedWorkflows: [workflow] };
  await cancelStalledDashboardRuns(options);
  const originalGet = actions.getWorkflowRun;
  actions.getWorkflowRun = async (id) => {
    const current = await originalGet(id);
    return id === 2
      ? { ...current, display_title: "pull-request-dashboard-a-2-refresh" }
      : current;
  };
  clock += 30 * 60 * 1000;
  const result = await cancelStalledDashboardRuns(options);
  assert.deepEqual(result.forceRequested, []);
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
});

test("one unresponsive run does not starve other candidates", async () => {
  let clock = NOW;
  const runs = [
    run(9, "pending", "2026-09-10T11:50:00Z"),
    ...Array.from({ length: 8 }, (_, index) =>
      run(index + 1, "waiting", `2026-09-10T0${index}:00:00Z`)),
  ];
  const { actions, calls } = fixture({ runs });
  const options = { actions, now: () => clock, watchedWorkflows: [WORKFLOW] };
  const seen = new Set();
  for (let tick = 0; tick < 8; tick += 1) {
    const result = await cancelStalledDashboardRuns(options);
    assert.ok(result.requested.length <= 4);
    for (const { runId } of result.requested) {
      seen.add(runId);
    }
    clock += 15 * 60 * 1000;
  }
  assert.equal(seen.size, 8);
  assert.ok(calls.some(([action, runId]) => action === "cancel" && runId === 8));
});

test("a recovered run is never force-cancelled", async () => {
  let clock = NOW;
  const { actions, runs, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T10:00:00Z"),
    ],
  });
  const options = { actions, now: () => clock, watchedWorkflows: [WORKFLOW] };
  await cancelStalledDashboardRuns(options);
  runs[1].status = "in_progress";
  const originalGet = actions.getWorkflowRun;
  actions.getWorkflowRun = async (runId) =>
    runId === 1 ? { ...await originalGet(runId), status: "completed", conclusion: "success" } :
      originalGet(runId);
  clock += 30 * 60 * 1000;
  const result = await cancelStalledDashboardRuns(options);
  assert.deepEqual(result.forceRequested, []);
  assert.deepEqual(result.confirmed, []);
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
});

test("handles a 409 race at force-cancel without claiming confirmation", async () => {
  let clock = NOW;
  const conflict = Object.assign(new Error("Conflict"), { githubStatusCode: 409 });
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T10:00:00Z"),
    ],
    forceErrors: { 1: conflict },
  });
  const options = { actions, now: () => clock, watchedWorkflows: [WORKFLOW] };
  await cancelStalledDashboardRuns(options);
  clock += 30 * 60 * 1000;
  const result = await cancelStalledDashboardRuns(options);
  assert.deepEqual(result.forceRequested, []);
  assert.deepEqual(result.confirmed, []);
  assert.equal(calls.filter(([action]) => action === "force-cancel").length, 1);
});

test("caps force attempts and reports a still-blocked run", async () => {
  let clock = NOW;
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T10:00:00Z"),
    ],
  });
  const options = { actions, now: () => clock, watchedWorkflows: [WORKFLOW] };
  await cancelStalledDashboardRuns(options);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    clock += 30 * 60 * 1000;
    const result = await cancelStalledDashboardRuns(options);
    assert.deepEqual(result.forceRequested.map(({ runId }) => runId), [1]);
  }
  clock += 30 * 60 * 1000;
  const result = await cancelStalledDashboardRuns(options);
  assert.deepEqual(result.unresponsive.map(({ runId }) => runId), [1]);
  assert.equal(calls.filter(([action]) => action === "force-cancel").length, 2);
});

test("confirms cancellation only when GitHub reports a cancelled conclusion", async () => {
  const { actions } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z"),
      run(1, "waiting", "2026-09-10T10:00:00Z"),
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
    watchedWorkflows: [scheduledWorkflow],
  });

  assert.deepEqual(calls, [
    ["list-runs", "dashboard.yml", {
      event: "schedule",
      statuses: ACTIVE_RUN_STATUSES,
    }],
    ["list-jobs", 1],
    ["cancel", 1],
    ["get-run", 1],
  ]);
});
