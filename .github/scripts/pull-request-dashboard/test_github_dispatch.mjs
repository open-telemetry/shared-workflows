import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";
import test from "node:test";

import { createGitHubActionsClient, dispatchQueueDrain } from "./netlify/lib/github-dispatch.mjs";
import { cancelStalledDashboardRuns } from "./netlify/lib/workflow-watchdog.mjs";

function mockClient(t, jobPages, onRequest) {
  const { privateKey } = generateKeyPairSync("rsa", {
    modulusLength: 2048,
    privateKeyEncoding: { type: "pkcs8", format: "pem" },
    publicKeyEncoding: { type: "spki", format: "pem" },
  });
  const requestedPages = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    const request = new URL(url);
    if (request.pathname.endsWith("/installation")) {
      return Response.json({ id: 1 });
    }
    if (request.pathname.endsWith("/access_tokens")) {
      return Response.json({ token: "test-token" });
    }
    if (request.pathname.endsWith("/actions/runs/42/jobs")) {
      const page = Number(request.searchParams.get("page"));
      requestedPages.push(page);
      return Response.json(jobPages[page - 1]);
    }
    if (onRequest) {
      return onRequest(request.pathname, options);
    }
    throw new Error(`unexpected GitHub API path: ${request.pathname}`);
  });
  const config = { clientId: "test", privateKey };
  return {
    config,
    client: createGitHubActionsClient(config),
    requestedPages,
  };
}

test("drain dispatches carry the independently owned lane", async (t) => {
  const dispatched = [];
  const { config, client } = mockClient(t, [], (path, options) => {
    assert.equal(path, "/repos/open-telemetry/shared-workflows/actions/workflows/pull-request-dashboard-drain.yml/dispatches");
    dispatched.push(JSON.parse(options.body));
    return new Response(null, { status: 204 });
  });
  await client;
  await dispatchQueueDrain(7, "live", config);
  await dispatchQueueDrain(11, "maintenance", config);
  assert.deepEqual(dispatched, [
    { ref: "main", inputs: { dispatcher_generation: "7", queue_lane: "live" } },
    { ref: "main", inputs: { dispatcher_generation: "11", queue_lane: "maintenance" } },
  ]);
});

test("does not cancel when an active job is on a later page", async (t) => {
  const firstPage = Array.from({ length: 100 }, (_, index) => ({
    id: index + 1,
    status: "completed",
  }));
  firstPage[99] = { id: 100, status: "waiting", started_at: "2026-09-10T11:00:00Z" };
  const { client, requestedPages } = mockClient(t, [
    { total_count: 101, jobs: firstPage },
    { total_count: 101, jobs: [{ id: 101, status: "in_progress" }] },
  ]);
  const actions = await client;
  actions.listWorkflowRuns = async () => [
    { id: 42, status: "waiting", created_at: "2026-09-10T11:01:00Z" },
    { id: 43, status: "pending", created_at: "2026-09-10T11:45:00Z" },
  ];
  actions.forceCancelWorkflowRun = () => {
    throw new Error("must not force-cancel while a job is active");
  };

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => Date.parse("2026-09-10T12:00:00Z"),
    watchedWorkflows: [{ workflowId: "dashboard.yml" }],
  });

  assert.deepEqual(result.requested, []);
  assert.deepEqual(requestedPages, [1, 2]);
});

test("rejects a changing job count instead of checking an incomplete run", async (t) => {
  const firstPage = Array.from({ length: 100 }, (_, index) => ({ id: index + 1 }));
  const { client } = mockClient(t, [
    { total_count: 101, jobs: firstPage },
    { total_count: 102, jobs: [{ id: 101 }] },
  ]);

  await assert.rejects((await client).listRunJobs(42), /changed or were incomplete/);
});

test("reads workflow run state and uses the force-cancel endpoint", async (t) => {
  const requests = [];
  const { client } = mockClient(t, [], (path, options) => {
    requests.push([path, options?.method || "GET"]);
    if (path.endsWith("/actions/runs/42")) {
      return Response.json({ id: 42, status: "waiting" });
    }
    if (path.endsWith("/actions/runs/42/force-cancel")) {
      return new Response(null, { status: 202 });
    }
    throw new Error(`unexpected GitHub API path: ${path}`);
  });
  const actions = await client;
  assert.equal((await actions.getWorkflowRun(42)).status, "waiting");
  await actions.forceCancelWorkflowRun(42);
  assert.deepEqual(requests, [
    ["/repos/open-telemetry/shared-workflows/actions/runs/42", "GET"],
    ["/repos/open-telemetry/shared-workflows/actions/runs/42/force-cancel", "POST"],
  ]);
});

test("propagates a force-cancel 409 for the watchdog to handle", async (t) => {
  const { client } = mockClient(t, [], () =>
    new Response("Conflict", { status: 409 }));
  await assert.rejects(
    (await client).forceCancelWorkflowRun(42),
    (error) => error.githubStatusCode === 409,
  );
});
