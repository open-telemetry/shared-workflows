import assert from "node:assert/strict";
import test from "node:test";

import {
  cancelStalledDashboardRuns,
} from "./netlify/lib/workflow-watchdog.mjs";

const NOW = Date.parse("2026-09-10T12:00:00Z");
const WORKFLOW = Object.freeze({ workflowId: "dashboard.yml" });

function fixture({ runs, jobs = {} }) {
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
    },
  };
  return { actions, calls };
}

function run(id, status, createdAt, event = "workflow_dispatch") {
  return {
    id,
    status,
    created_at: createdAt,
    event,
  };
}

function unassignedJob(startedAt, status = "in_progress") {
  return {
    status,
    created_at: startedAt,
    started_at: startedAt,
    runner_id: 0,
    runner_name: "",
    steps: [],
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
    ["list-runs", "dashboard.yml", { event: undefined }],
    ["list-jobs", 1],
    ["cancel", 1],
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
    ["list-runs", "dashboard.yml", { event: undefined }],
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
    ["list-runs", "dashboard.yml", { event: undefined }],
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
    ["list-runs", "dashboard.yml", { event: "schedule" }],
    ["list-jobs", 1],
    ["cancel", 1],
  ]);
});
