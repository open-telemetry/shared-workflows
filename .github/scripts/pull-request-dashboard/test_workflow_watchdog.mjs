import assert from "node:assert/strict";
import test from "node:test";

import {
  cancelStalledDashboardRuns,
  WATCHED_DASHBOARD_WORKFLOWS,
} from "./netlify/lib/workflow-watchdog.mjs";

const NOW = Date.parse("2026-09-10T12:00:00Z");
const WORKFLOW = Object.freeze({ workflowId: "dashboard.yml" });
const ACTIVE_RUN_STATUSES = ["in_progress", "queued", "waiting", "pending"];
const PUBLISHER_WORKFLOW = WATCHED_DASHBOARD_WORKFLOWS.find((workflow) =>
  workflow.groupByPublisher);

function fixture({
  runs, jobs = {}, forceErrors = {},
  getRunErrors = {}, jobErrors = {}, onForceCancel,
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

function publisherJob(
  name = "run-targeted-dashboard-stable / publish-dashboard",
  startedAt = "2026-09-10T11:00:00Z",
  status = "queued",
) {
  return { ...unassignedJob(startedAt, status), name };
}

test("matches stalled publishers across PRs and scheduled or manual backfills", async () => {
  for (const [event, title, name] of [
    ["workflow_dispatch", "pull-request-dashboard-repo-a-2-refresh",
      "run-targeted-dashboard-stable / publish-dashboard"],
    ["workflow_dispatch", "pull-request-dashboard-repo-a-backfill-manual",
      "run-repo-dashboard-stable (repo-a) / publish-dashboard"],
    ["schedule", "pull-request-dashboard-all-repositories-backfill-refresh",
      "run-repo-dashboard-stable (repo-a) / publish-dashboard"],
    ["workflow_dispatch", "pull-request-dashboard-all-repositories-backfill-manual",
      "run-repo-dashboard-stable (repo-a) / publish-dashboard"],
    ["workflow_dispatch", `pull-request-dashboard-repo-a-${"a".repeat(40)}-refresh`,
      "run-head-sha-dashboard-stable (2) / publish-dashboard"],
    ["workflow_dispatch", "pull-request-dashboard-Repo-A-2-refresh",
      "run-targeted-dashboard-canary / publish-dashboard"],
  ]) {
    const { actions, calls } = fixture({
      runs: [
        run(2, "pending", "2026-09-10T11:50:00Z", event, title),
        run(1, "queued", "2026-09-10T10:00:00Z", "workflow_dispatch",
          "pull-request-dashboard-repo-a-1-refresh"),
      ],
      jobs: {
        1: [completedJob(), publisherJob()],
        2: [publisherJob(name, "2026-09-10T11:50:00Z", "pending")],
      },
    });
    const result = await cancelStalledDashboardRuns({
      actions, now: () => NOW, watchedWorkflows: [PUBLISHER_WORKFLOW],
    });
    assert.deepEqual(result.requested.map(({ runId, newerRunId }) =>
      ({ runId, newerRunId })), [{ runId: 1, newerRunId: 2 }]);
    assert.equal(calls.filter(([action, id]) =>
      action === "list-jobs" && id === 1).length, 2);
    assert.equal(calls.filter(([action, id]) =>
      action === "list-jobs" && id === 2).length, 2);
  }
});

test("does not match publishers for unrelated repositories or state update jobs", async () => {
  for (const [title, job] of [
    ["pull-request-dashboard-repo-b-2-refresh", publisherJob(
      undefined, "2026-09-10T11:50:00Z", "pending")],
    ["pull-request-dashboard-all-repositories-backfill-refresh", publisherJob(
      "run-repo-dashboard-stable (repo-b) / publish-dashboard",
      "2026-09-10T11:50:00Z", "pending")],
    ["pull-request-dashboard-repo-a-2-refresh", {
      ...publisherJob(undefined, "2026-09-10T11:50:00Z", "pending"),
      name: "run-targeted-dashboard-stable / update-dashboard",
    }],
    ["Pull request dashboard", publisherJob(
      undefined, "2026-09-10T11:50:00Z", "pending")],
    ["pull-request-dashboard-all-repositories-backfill-refresh", publisherJob(
      undefined, "2026-09-10T11:50:00Z", "pending")],
  ]) {
    const { actions, calls } = fixture({
      runs: [
        run(2, "pending", "2026-09-10T11:50:00Z", "workflow_dispatch", title),
        run(1, "queued", "2026-09-10T10:00:00Z", "workflow_dispatch",
          "pull-request-dashboard-repo-a-1-refresh"),
      ],
      jobs: { 1: [completedJob(), publisherJob()], 2: [job] },
    });
    const result = await cancelStalledDashboardRuns({
      actions, now: () => NOW, watchedWorkflows: [PUBLISHER_WORKFLOW],
    });
    assert.deepEqual(result.requested, []);
    assert.equal(calls.some(([action]) => action === "force-cancel"), false);
  }
});

test("matches newer publisher requests from an earlier backfill still processing other repositories", async () => {
  for (const suffix of ["refresh", "manual"]) {
    const { actions } = fixture({
      runs: [
        run(2, "in_progress", "2026-09-10T09:00:00Z", "workflow_dispatch",
          `pull-request-dashboard-all-repositories-backfill-${suffix}`),
        run(1, "queued", "2026-09-10T10:00:00Z", "workflow_dispatch",
          "pull-request-dashboard-repo-a-1-refresh"),
      ],
      jobs: {
        1: [completedJob(), publisherJob()],
        2: [
          publisherJob("run-repo-dashboard-stable (repo-a) / publish-dashboard",
            "2026-09-10T11:50:00Z", "pending"),
          { ...unassignedJob("2026-09-10T11:50:00Z"), runner_id: 123 },
        ],
      },
    });
    const result = await cancelStalledDashboardRuns({
      actions, now: () => NOW, watchedWorkflows: [PUBLISHER_WORKFLOW],
    });
    assert.deepEqual(result.requested.map(({ runId, newerRunId }) =>
      ({ runId, newerRunId })), [{ runId: 1, newerRunId: 2 }]);
  }
});

test("requires a newer publisher request with a known enqueue time", async () => {
  for (const createdAt of ["2026-09-10T10:00:00Z", null, "invalid"]) {
    const { actions, calls } = fixture({
      runs: [
        run(2, "pending", "2026-09-10T11:50:00Z", "workflow_dispatch",
          "pull-request-dashboard-repo-a-2-refresh"),
        run(1, "queued", "2026-09-10T10:00:00Z", "workflow_dispatch",
          "pull-request-dashboard-repo-a-1-refresh"),
      ],
      jobs: {
        1: [completedJob(), publisherJob()],
        2: [{
          ...publisherJob(undefined, "2026-09-10T11:50:00Z", "pending"),
          created_at: createdAt,
          started_at: null,
        }],
      },
    });
    const result = await cancelStalledDashboardRuns({
      actions, now: () => NOW, watchedWorkflows: [PUBLISHER_WORKFLOW],
    });
    assert.deepEqual(result.requested, []);
    assert.equal(calls.some(([action]) => action === "force-cancel"), false);
  }
});

test("does not treat a publisher in the same run as a replacement", async () => {
  const { actions, calls } = fixture({
    runs: [run(1, "queued", "2026-09-10T10:00:00Z", "workflow_dispatch",
      "pull-request-dashboard-repo-a-1-refresh")],
    jobs: {
      1: [
        completedJob(),
        publisherJob(),
        publisherJob(undefined, "2026-09-10T11:00:00Z", "pending"),
      ],
    },
  });
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [PUBLISHER_WORKFLOW],
  });
  assert.deepEqual(result.requested, []);
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
});

test("does not cancel manual runs through publisher matching", async () => {
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z", "workflow_dispatch",
        "pull-request-dashboard-repo-a-2-refresh"),
      run(1, "queued", "2026-09-10T10:00:00Z", "workflow_dispatch",
        "pull-request-dashboard-repo-a-backfill-manual"),
    ],
    jobs: {
      1: [completedJob(), publisherJob(
        "run-repo-dashboard-stable (repo-a) / publish-dashboard")],
      2: [publisherJob(undefined, "2026-09-10T11:50:00Z", "pending")],
    },
  });
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [PUBLISHER_WORKFLOW],
  });
  assert.deepEqual(result.requested, []);
  assert.equal(calls.some(([action]) => action === "list-jobs"), false);
});

test("protects fresh, assigned or started publisher jobs and other active work", async () => {
  for (const unfinished of [
    [publisherJob(undefined, "2026-09-10T11:30:00.001Z")],
    [{ ...publisherJob(), runner_id: 123 }],
    [{ ...publisherJob(), steps: [{ started_at: "2026-09-10T11:01:00Z" }] }],
    [publisherJob(), unassignedJob("2026-09-10T11:00:00Z", "in_progress")],
  ]) {
    const { actions, calls } = fixture({
      runs: [
        run(2, "pending", "2026-09-10T11:50:00Z", "workflow_dispatch",
          "pull-request-dashboard-repo-a-2-refresh"),
        run(1, "queued", "2026-09-10T10:00:00Z", "workflow_dispatch",
          "pull-request-dashboard-repo-a-1-refresh"),
      ],
      jobs: {
        1: [completedJob(), ...unfinished],
        2: [publisherJob(undefined, "2026-09-10T11:50:00Z", "pending")],
      },
    });
    const result = await cancelStalledDashboardRuns({
      actions, now: () => NOW, watchedWorkflows: [PUBLISHER_WORKFLOW],
    });
    assert.deepEqual(result.requested, []);
    assert.equal(calls.some(([action]) => action === "force-cancel"), false);
  }
});

test("cancels a repository publisher at the exact 30-minute job threshold", async () => {
  const { actions } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z", "workflow_dispatch",
        "pull-request-dashboard-repo-a-2-refresh"),
      run(1, "queued", "2026-09-10T10:00:00Z", "workflow_dispatch",
        "pull-request-dashboard-repo-a-1-refresh"),
    ],
    jobs: {
      1: [completedJob(), publisherJob(undefined, "2026-09-10T11:30:00Z")],
      2: [publisherJob(undefined, "2026-09-10T11:50:00Z", "pending")],
    },
  });
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [PUBLISHER_WORKFLOW],
  });
  assert.deepEqual(result.requested.map(({ runId }) => runId), [1]);
});

test("rechecks publisher jobs before force-cancelling across run groups", async () => {
  for (const change of [
    (jobs) => { jobs[1][1].runner_id = 123; },
    (jobs) => { jobs[1][1].steps = [{ started_at: "2026-09-10T11:59:00Z" }]; },
    (jobs) => { jobs[2][0].status = "completed"; },
    (jobs) => {
      jobs[2][0].name = "run-repo-dashboard-stable (repo-b) / publish-dashboard";
    },
  ]) {
    const { actions, calls, jobs } = fixture({
      runs: [
        run(2, "pending", "2026-09-10T11:50:00Z", "workflow_dispatch",
          "pull-request-dashboard-all-repositories-backfill-refresh"),
        run(1, "queued", "2026-09-10T10:00:00Z", "workflow_dispatch",
          "pull-request-dashboard-repo-a-1-refresh"),
      ],
      jobs: {
        1: [completedJob(), publisherJob()],
        2: [publisherJob("run-repo-dashboard-stable (repo-a) / publish-dashboard",
          "2026-09-10T11:50:00Z", "pending")],
      },
    });
    const listJobs = actions.listRunJobs;
    let lookups = 0;
    actions.listRunJobs = async (id) => {
      if (++lookups === 3) {
        change(jobs);
      }
      return listJobs(id);
    };
    const result = await cancelStalledDashboardRuns({
      actions, now: () => NOW, watchedWorkflows: [PUBLISHER_WORKFLOW],
    });
    assert.deepEqual(result.requested, []);
    assert.equal(calls.some(([action]) => action === "force-cancel"), false);
  }
});

test("skips remaining publisher candidates when their replacement disappears", async () => {
  const { actions, calls, jobs } = fixture({
    runs: [
      run(3, "pending", "2026-09-10T11:50:00Z", "workflow_dispatch",
        "pull-request-dashboard-all-repositories-backfill-refresh"),
      run(2, "queued", "2026-09-10T10:00:00Z", "workflow_dispatch",
        "pull-request-dashboard-repo-b-1-refresh"),
      run(1, "queued", "2026-09-10T09:00:00Z", "workflow_dispatch",
        "pull-request-dashboard-repo-a-1-refresh"),
    ],
    jobs: {
      1: [completedJob(), publisherJob()],
      2: [completedJob(), publisherJob()],
      3: [
        publisherJob("run-repo-dashboard-stable (repo-a) / publish-dashboard",
          "2026-09-10T11:50:00Z", "pending"),
        publisherJob("run-repo-dashboard-stable (repo-b) / publish-dashboard",
          "2026-09-10T11:50:00Z", "pending"),
      ],
    },
  });
  const listJobs = actions.listRunJobs;
  let lookups = 0;
  actions.listRunJobs = async (id) => {
    if (++lookups === 4) {
      jobs[3] = [];
    }
    return listJobs(id);
  };
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [PUBLISHER_WORKFLOW],
  });
  assert.deepEqual(result.requested, []);
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
});

test("does not request cancellation twice when run and publisher groups both match", async () => {
  const title = "pull-request-dashboard-repo-a-1-refresh";
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:50:00Z", "workflow_dispatch", title),
      run(1, "queued", "2026-09-10T10:00:00Z", "workflow_dispatch", title),
    ],
    jobs: {
      1: [completedJob(), publisherJob()],
      2: [publisherJob(undefined, "2026-09-10T11:50:00Z", "pending")],
    },
  });
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW,
    watchedWorkflows: [
      {
        workflowId: "dashboard.yml",
        groupByRunName: true,
        runNamePrefix: "pull-request-dashboard-",
      },
      PUBLISHER_WORKFLOW,
    ],
  });
  assert.deepEqual(result.requested.map(({ runId }) => runId), [1]);
  assert.equal(calls.filter(([action]) => action === "force-cancel").length, 1);
});

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
    ["get-run", 2],
    ["force-cancel", 1],
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
    ["get-run", 1],
    ["get-run", 2],
    ["force-cancel", 1],
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
    ["get-run", 1],
    ["get-run", 2],
    ["force-cancel", 1],
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
    ["get-run", 1],
    ["get-run", 2],
    ["force-cancel", 1],
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
    ["get-run", 1],
    ["get-run", 2],
    ["force-cancel", 1],
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
    ["get-run", 1],
    ["get-run", 3],
    ["force-cancel", 1],
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
    forceErrors: { 1: conflict },
  });
  const cancel = actions.forceCancelWorkflowRun;
  actions.forceCancelWorkflowRun = (runId) => {
    if (runId === 1) {
      runs[2].status = "completed";
      runs[2].conclusion = "success";
    }
    return cancel(runId);
  };

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
    ["get-run", 2],
    ["force-cancel", 1],
    ["get-run", 1],
    ["list-jobs", 2],
    ["get-run", 2],
    ["get-run", 3],
    ["force-cancel", 2],
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
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
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
  assert.deepEqual(calls.map(([action]) => action),
    ["list-runs", "list-jobs", "get-run", "get-run", "force-cancel", "get-run"]);
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
    actions, now: () => NOW,
    watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested.map(({ runId }) => runId), [1]);
  assert.deepEqual(result.confirmed, []);
  assert.equal(calls.some(([action]) => action === "force-cancel"), true);
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
  assert.equal(calls.some(([action, id]) => action === "force-cancel" && id === 36008669270), true);
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
    assert.equal(calls.some(([action]) => action === "force-cancel"), false);
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
    assert.equal(calls.some(([action]) => action === "force-cancel"), false);
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
      actions, now: () => NOW,
      watchedWorkflows: [WORKFLOW],
    });
    assert.equal(result.requested.length, Number(shouldRequest));
  }
});

test("force-cancels at the exact 30-minute stale threshold", async () => {
  for (const [ageMs, shouldRequest] of [
    [30 * 60 * 1000 - 1, false],
    [30 * 60 * 1000, true],
    [30 * 60 * 1000 + 1, true],
    [60 * 60 * 1000, true],
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
      action === "force-cancel").map(([action]) => action),
    shouldRequest ? ["force-cancel"] : []);
    assert.equal(result.requested.length, Number(shouldRequest));
  }
});

test("force-cancels a queued publisher at 30 minutes on the first invocation", async () => {
  const { actions, calls } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:55:00Z"),
      run(1, "queued", "2026-09-10T11:30:00Z"),
    ],
    jobs: { 1: [completedJob(), unassignedJob("2026-09-10T11:30:00Z", "queued")] },
  });
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  });

  assert.deepEqual(result.requested.map(({ runId, ageMinutes }) =>
    ({ runId, ageMinutes })), [{ runId: 1, ageMinutes: 30 }]);
  assert.deepEqual(calls.map(([action]) => action),
    ["list-runs", "list-jobs", "get-run", "get-run", "force-cancel", "get-run"]);
});

test("uses one configurable stale threshold for selection and revalidation", async () => {
  for (const [ageMs, shouldRequest] of [
    [45 * 60 * 1000 - 1, false],
    [45 * 60 * 1000, true],
  ]) {
    const { actions } = fixture({
      runs: [
        run(2, "pending", "2026-09-10T11:55:00Z"),
        run(1, "waiting", new Date(NOW - ageMs).toISOString()),
      ],
    });
    const result = await cancelStalledDashboardRuns({
      actions, now: () => NOW, staleRunMs: 45 * 60 * 1000,
      watchedWorkflows: [WORKFLOW],
    });
    assert.equal(result.requested.length, Number(shouldRequest));
  }
});

test("rejects invalid stale thresholds before looking up runs", async () => {
  for (const staleRunMs of [0, -1, NaN, Infinity]) {
    const { actions, calls } = fixture({ runs: [] });
    await assert.rejects(cancelStalledDashboardRuns({
      actions, staleRunMs, watchedWorkflows: [WORKFLOW],
    }), /staleRunMs must be positive/);
    assert.deepEqual(calls, []);
  }
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
  assert.deepEqual(calls.map(([action]) => action), ["list-runs"]);

  clock += 20 * 60 * 1000;
  const stale = await cancelStalledDashboardRuns(options);

  assert.deepEqual(stale.requested.map(({ runId, ageMinutes }) =>
    ({ runId, ageMinutes })), [{ runId: 1, ageMinutes: 30 }]);
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
  assert.deepEqual(result.requested, []);
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
  assert.deepEqual(result.requested, []);
  assert.deepEqual(calls.map(([action]) => action),
    ["list-runs", "list-jobs", "get-run", "get-run"]);
});

test("does not force a 40-minute-old run rerun during the job lookup", async () => {
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
    ["list-runs", "list-jobs", "get-run", "get-run"]);
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
  assert.deepEqual(result.requested.map(({ runId }) => runId), [1]);
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
  assert.deepEqual(result.requested, []);
  assert.deepEqual(calls.map(([action]) => action), ["list-runs"]);
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
  assert.deepEqual(result.requested, []);
  assert.equal(calls.filter(([action]) => action === "force-cancel").length, 1);
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
  assert.deepEqual(result.requested, []);
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
  assert.deepEqual(result.requested, []);
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
  assert.deepEqual(result.requested, []);
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
    assert.deepEqual(result.requested, []);
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
  assert.deepEqual(result.requested, []);
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
  assert.deepEqual(result.requested, []);
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
  assert.deepEqual(result.requested, []);
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
    assert.ok(result.requested.length <= 4);
    for (const { runId } of result.requested) {
      seen.add(runId);
    }
    clock += 15 * 60 * 1000;
  }
  assert.equal(seen.size, 8);
  assert.ok(calls.some(([action, runId]) => action === "force-cancel" && runId === 8));
});

test("rotates every backlog when workflows compete for the invocation budget", async () => {
  let clock = NOW;
  const watchedWorkflows = Array.from({ length: 4 }, (_, index) => ({
    workflowId: `dashboard-${index}.yml`,
  }));
  const runsByWorkflow = new Map(watchedWorkflows.map((workflow, workflowIndex) => [
    workflow.workflowId,
    [
      run(workflowIndex * 100 + 99, "pending", "2026-09-10T11:50:00Z"),
      ...Array.from({ length: 16 }, (_, index) =>
        run(
          workflowIndex * 100 + index + 1,
          "waiting",
          new Date(NOW - (120 + index) * 60 * 1000).toISOString(),
        )),
    ],
  ]));
  const runs = [...runsByWorkflow.values()].flat();
  const { actions } = fixture({ runs });
  actions.listWorkflowRuns = async (workflowId) => runsByWorkflow.get(workflowId);
  const seen = new Set();
  for (let tick = 0; tick < 16; tick += 1) {
    const result = await cancelStalledDashboardRuns({
      actions, now: () => clock, watchedWorkflows,
    });
    assert.equal(result.requested.length, 8);
    for (const { workflowId } of watchedWorkflows) {
      assert.ok(result.requested.filter((request) =>
        request.workflowId === workflowId).length <= 4);
    }
    for (const { runId } of result.requested) {
      seen.add(runId);
    }
    clock += 15 * 60 * 1000;
  }
  assert.deepEqual(seen, new Set(runs
    .filter(({ status }) => status === "waiting")
    .map(({ id }) => id)));
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
  assert.deepEqual(result.requested, []);
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
    assert.deepEqual(result.requested, []);
    assert.deepEqual(result.confirmed, []);
    assert.deepEqual(result.conflicts.map(({ runId }) => runId), [1]);
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
  assert.deepEqual(result.requested, []);
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
    assert.deepEqual(result.requested.map(({ runId }) => runId), [1]);
    assert.deepEqual(result.confirmed, []);
  }
  assert.equal(calls.filter(([action]) => action === "force-cancel").length, 3);
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
  assert.deepEqual(result.requested.map(({ runId }) => runId), [1]);
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
  assert.deepEqual(result.requested.map(({ runId }) => runId), [1]);
  assert.deepEqual(result.unconfirmed.map(({ runId, reason }) =>
    ({ runId, reason })), [{ runId: 1, reason: "not_found" }]);
  assert.deepEqual(result.confirmed, []);
  assert.deepEqual(calls.slice(-2), [["force-cancel", 1], ["get-run", 1]]);
});

test("surfaces forbidden immediate confirmation lookups", async () => {
  const forbidden = Object.assign(new Error("Forbidden"), { githubStatusCode: 403 });
  const { actions, getRunErrors } = fixture({
    runs: [
      run(2, "pending", "2026-09-10T11:55:00Z"),
      run(1, "waiting", "2026-09-10T11:20:00Z"),
    ],
  });
  const request = actions.forceCancelWorkflowRun;
  actions.forceCancelWorkflowRun = async (id) => {
    await request(id);
    getRunErrors[id] = forbidden;
  };
  await assert.rejects(cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [WORKFLOW],
  }), (error) => error === forbidden);
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
  assert.deepEqual(result.requested, []);
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
  assert.equal(calls.some(([action]) => action === "force-cancel"), false);
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
    assert.equal(calls.some(([action]) => action === "force-cancel"), false);
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
    ["get-run", 1],
    ["get-run", 2],
    ["force-cancel", 1],
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
    ["get-run", 1],
    ["get-run", 2],
    ["force-cancel", 1],
    ["get-run", 1],
  ]);
});

test("bounds concurrent publisher job lookups", async () => {
  const runs = [run(1, "queued", "2026-09-10T10:00:00Z", "workflow_dispatch",
    "pull-request-dashboard-repo-a-1-refresh")];
  const jobs = { 1: [completedJob(), publisherJob()] };
  for (let id = 2; id < 12; id++) {
    runs.push(run(id, "pending", "2026-09-10T11:50:00Z", "workflow_dispatch",
      "pull-request-dashboard-repo-b-2-refresh"));
    jobs[id] = [publisherJob(undefined, "2026-09-10T11:50:00Z", "pending")];
  }
  const { actions } = fixture({ runs, jobs });
  let inFlight = 0;
  let maxInFlight = 0;
  const listRunJobs = actions.listRunJobs;
  actions.listRunJobs = async (runId) => {
    inFlight++;
    maxInFlight = Math.max(maxInFlight, inFlight);
    await new Promise((resolve) => setTimeout(resolve, 1));
    try {
      return await listRunJobs(runId);
    } finally {
      inFlight--;
    }
  };
  await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [PUBLISHER_WORKFLOW],
  });
  assert.ok(maxInFlight > 1 && maxInFlight <= 4);
});

test("does not reuse a cancellation-requested run as a replacement", async () => {
  const runs = [
    run(1, "queued", "2026-09-10T10:00:00Z", "schedule",
      "pull-request-dashboard-all-repositories-backfill-refresh"),
    run(2, "queued", "2026-09-10T10:05:00Z", "workflow_dispatch",
      "pull-request-dashboard-repo-b-2-refresh"),
    run(3, "queued", "2026-09-10T10:10:00Z", "workflow_dispatch",
      "pull-request-dashboard-repo-a-3-refresh"),
  ];
  const jobs = {
    1: [
      publisherJob("run-repo-dashboard-stable (repo-a) / publish-dashboard",
        "2026-09-10T10:00:00Z"),
      publisherJob("run-repo-dashboard-stable (repo-b) / publish-dashboard",
        "2026-09-10T10:08:00Z"),
    ],
    2: [publisherJob(undefined, "2026-09-10T10:05:00Z")],
    3: [publisherJob(undefined, "2026-09-10T10:10:00Z")],
  };
  const { actions } = fixture({ runs, jobs });
  const result = await cancelStalledDashboardRuns({
    actions, now: () => NOW, watchedWorkflows: [PUBLISHER_WORKFLOW],
  });
  assert.deepEqual(result.requested.map((r) => r.runId), [1]);
});
