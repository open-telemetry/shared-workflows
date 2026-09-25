import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";
import test from "node:test";

import { createGitHubActionsClient } from "./netlify/lib/github-dispatch.mjs";
import { cancelStalledDashboardRuns } from "./netlify/lib/workflow-watchdog.mjs";

function mockClient(t, jobPages) {
  const { privateKey } = generateKeyPairSync("rsa", {
    modulusLength: 2048,
    privateKeyEncoding: { type: "pkcs8", format: "pem" },
    publicKeyEncoding: { type: "spki", format: "pem" },
  });
  const requestedPages = [];
  t.mock.method(globalThis, "fetch", async (url) => {
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
    throw new Error(`unexpected GitHub API path: ${request.pathname}`);
  });
  return {
    client: createGitHubActionsClient({ clientId: "test", privateKey }),
    requestedPages,
  };
}

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
    { id: 42, status: "waiting", created_at: "2026-09-10T11:00:00Z" },
    { id: 43, status: "pending", created_at: "2026-09-10T11:45:00Z" },
  ];
  actions.cancelWorkflowRun = () => {
    throw new Error("must not cancel while a job is active");
  };

  const result = await cancelStalledDashboardRuns({
    actions,
    now: () => Date.parse("2026-09-10T12:00:00Z"),
    watchedWorkflows: [{ workflowId: "dashboard.yml" }],
  });

  assert.deepEqual(result.cancelled, []);
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
