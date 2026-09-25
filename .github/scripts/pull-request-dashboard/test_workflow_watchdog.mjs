import assert from "node:assert/strict";
import test from "node:test";

import {
  cancelStalledDashboardRuns,
} from "./netlify/lib/workflow-watchdog.mjs";

const NOW = Date.parse("2026-09-10T12:00:00Z");
const WORKFLOW = Object.freeze({ workflowId: "dashboard.yml" });
const ACTIVE_RUN_STATUSES = ["in_progress", "queued", "waiting", "pending"];

function fixture({ runs, jobs = {}, cancellationErrors = {} }) {
  const calls = [];
  const actions = {
    async listWorkflowRuns(workflowId, options) {
      calls.push(["list-runs", workflowId, options]);
      return runs;
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
    },
  };
  return { actions, calls };
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
    started_at: startedAt,
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
    cancelled: [{
      workflowId: "dashboard.yml",
      runId: 1,
      newerRunId: 2,
      ageMinutes: 60,
    }],
  });
  assert.deepEqual(calls, [
    ["list-runs", "dashboard.yml", {
      event: undefined,
      statuses: ACTIVE_RUN_STATUSES,
    }],
    ["list-jobs", 1],
    ["cancel", 1],
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

  assert.deepEqual(result.cancelled, [{
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

  assert.deepEqual(result.cancelled, [{
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

  assert.deepEqual(result.cancelled, [{
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

  assert.deepEqual(result.cancelled, [{
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

  assert.deepEqual(result.cancelled, [{
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

  assert.deepEqual(result.cancelled, []);
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

  assert.deepEqual(result.cancelled, [{
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

  assert.deepEqual(result.cancelled, []);
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

  assert.deepEqual(result.cancelled, [{
    workflowId: "dashboard.yml",
    runId: 1,
    newerRunId: 2,
    ageMinutes: 90,
  }]);
  assert.deepEqual(calls.map(([action]) => action), ["list-runs", "list-jobs", "cancel"]);
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

  assert.deepEqual(result.cancelled, []);
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

  assert.deepEqual(result.cancelled, []);
});

test("does not cancel a partially completed run with active or unknown work", async () => {
  for (const unfinished of [
    unassignedJob("2026-09-10T11:00:00Z", "in_progress"),
    unassignedJob("2026-09-10T11:00:00Z", "queued"),
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

    assert.deepEqual(result.cancelled, []);
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

  assert.deepEqual(result.cancelled, []);
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

  assert.deepEqual(result.cancelled, []);
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

  assert.deepEqual(result.cancelled, []);
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

  assert.deepEqual(result.cancelled, [{
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
  ]);
});
