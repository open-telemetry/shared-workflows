import assert from "node:assert/strict";
import crypto from "node:crypto";
import test from "node:test";

import {
  extractHeadSha,
  extractPullRequestNumber,
  handleWebhookRequest,
  isAllowedAction,
  isDashboardSelfTriggeredCommentEvent,
  isDefaultBranchEvent,
} from "./netlify/functions/github-webhook.mjs";

const dashboardApp = { slug: "opentelemetry-pr-dashboard" };
const dashboardActor = { id: 1 };
const headSha = "65325a64c9b2e4b8a1d0f3c7e5a9b1d2c3e4f5a6";
const webhookSecret = "test-webhook-secret";

test("refreshes when the dashboard override label changes", () => {
  assert.equal(isAllowedAction("pull_request", "labeled"), true);
  assert.equal(isAllowedAction("pull_request", "unlabeled"), true);
});

test("refreshes when a review request is added or removed", () => {
  assert.equal(isAllowedAction("pull_request", "review_requested"), true);
  assert.equal(isAllowedAction("pull_request", "review_request_removed"), true);
});

test("refreshes only once a check suite completes", () => {
  assert.equal(isAllowedAction("check_suite", "completed"), true);
  assert.equal(isAllowedAction("check_suite", "requested"), false);
  assert.equal(isAllowedAction("check_suite", "rerequested"), false);
});

test("ignores check runs, which the app no longer subscribes to", () => {
  assert.equal(isAllowedAction("check_run", "completed"), false);
});

test("refreshes on commit statuses, which carry no action", () => {
  assert.equal(isAllowedAction("status", ""), true);
  assert.equal(isAllowedAction("pull_request", ""), false);
});

test("ignores check and status events on the default branch", () => {
  const repository = { default_branch: "main" };
  assert.equal(isDefaultBranchEvent("status", {
    repository,
    branches: [{ name: "main" }],
  }), true);
  assert.equal(isDefaultBranchEvent("status", {
    repository,
    branches: [{ name: "renovate/kotlin-plugin-updates" }],
  }), false);
  // A fork head is not a branch in the repository that emitted the event.
  assert.equal(isDefaultBranchEvent("status", { repository, branches: [] }), false);
  assert.equal(isDefaultBranchEvent("check_suite", {
    repository,
    check_suite: { head_branch: "main" },
  }), true);
  assert.equal(isDefaultBranchEvent("check_suite", {
    repository,
    check_suite: { head_branch: "peschinskiy/host-id-definition" },
  }), false);
  // Code scanning reports no head branch, and publishes only on pull request
  // heads, so leaving it dispatchable refreshes a pull request, not a push.
  assert.equal(isDefaultBranchEvent("check_suite", {
    repository,
    check_suite: { head_branch: null },
  }), false);
});

test("reports the head commit when a check event has no pull request", () => {
  // Check suites on a fork head name the fork's branch and list no pull
  // requests, which is why the head commit has to carry the association.
  const payload = {
    repository: { url: "https://api.github.com/repos/open-telemetry/example", default_branch: "main" },
    check_suite: { head_branch: "kangyi/bump-logsagentexporter", head_sha: headSha, pull_requests: [] },
  };
  assert.equal(extractPullRequestNumber("check_suite", payload), undefined);
  assert.equal(extractHeadSha("check_suite", payload), headSha);
});

test("prefers the pull request a check event names", () => {
  const payload = {
    repository: { url: "https://api.github.com/repos/open-telemetry/example" },
    check_suite: {
      head_sha: headSha,
      pull_requests: [
        { number: 19286, url: "https://api.github.com/repos/open-telemetry/example/pulls/19286" },
      ],
    },
  };
  assert.equal(extractPullRequestNumber("check_suite", payload), 19286);
});

test("reports the head commit of a status event", () => {
  assert.equal(extractHeadSha("status", { sha: headSha }), headSha);
  assert.equal(extractHeadSha("status", { sha: "not-a-sha" }), "");
  assert.equal(extractHeadSha("pull_request", { pull_request: { number: 1 } }), "");
});

test("recognizes comments performed by the dashboard app", () => {
  assert.equal(isDashboardSelfTriggeredCommentEvent("issue_comment", {
    comment: {
      body: "ordinary comment without a dashboard marker",
      performed_via_github_app: dashboardApp,
      user: dashboardActor,
    },
    sender: dashboardActor,
  }), true);
});

test("rejects comments not performed by the dashboard app", () => {
  assert.equal(isDashboardSelfTriggeredCommentEvent("issue_comment", {
    comment: {
      body: "<!-- pull-request-dashboard-status --> spoofed",
      user: dashboardActor,
    },
    sender: dashboardActor,
  }), false);
  assert.equal(isDashboardSelfTriggeredCommentEvent("issue_comment", {
    comment: {
      performed_via_github_app: { slug: "other-app" },
      user: dashboardActor,
    },
    sender: dashboardActor,
  }), false);
});

test("does not filter non-comment events from the dashboard app", () => {
  assert.equal(isDashboardSelfTriggeredCommentEvent("pull_request", {
    comment: {
      performed_via_github_app: dashboardApp,
      user: dashboardActor,
    },
    sender: dashboardActor,
  }), false);
});

test("allows dashboard comment events triggered by another actor", () => {
  assert.equal(isDashboardSelfTriggeredCommentEvent("issue_comment", {
    comment: {
      performed_via_github_app: dashboardApp,
      user: dashboardActor,
    },
    sender: { id: 2 },
  }), false);
});

test("off mode retains direct workflow dispatch", async () => {
  const calls = [];
  const response = await withQueueMode("off", () => handleWebhookRequest(
    webhookRequest("example", 123),
    {
      queue: queueMock(calls),
      dispatchRefresh: async (inputs) => calls.push(["refresh", inputs]),
      dispatchDrain: async (generation) => calls.push(["drain", generation]),
    },
  ));

  assert.equal(response.status, 202);
  assert.equal((await response.json()).status, "dispatched");
  assert.equal(calls[0][0], "refresh");
  assert.equal(calls.some(([name]) => name === "enqueue"), false);
});

test("rejects the retired shadow mode", async () => {
  await assert.rejects(
    withQueueMode("shadow", () => handleWebhookRequest(webhookRequest("example", 123))),
    /unsupported PR_DASHBOARD_QUEUE_MODE: shadow/,
  );
});

test("canary mode queues only configured canary repositories", async () => {
  const stableCalls = [];
  await withQueueMode("canary", () => handleWebhookRequest(
    webhookRequest("example", 123),
    {
      queue: queueMock(stableCalls),
      dispatchRefresh: async (inputs) => stableCalls.push(["refresh", inputs]),
    },
  ));
  assert.deepEqual(stableCalls.map(([name]) => name), ["refresh"]);

  const canaryCalls = [];
  const response = await withQueueMode("canary", () => handleWebhookRequest(
    webhookRequest("shared-workflows", 456),
    {
      queue: queueMock(canaryCalls),
      dispatchRefresh: async (inputs) => canaryCalls.push(["refresh", inputs]),
      dispatchDrain: async (generation) => canaryCalls.push(["drain", generation]),
    },
  ));
  assert.equal((await response.json()).status, "queued_and_dispatched");
  assert.deepEqual(
    canaryCalls.map(([name]) => name),
    ["enqueue:live", "request", "drain"],
  );
});

test("queued mode uses an internal dispatcher owner", async () => {
  const calls = [];
  await withQueueMode("all", () => handleWebhookRequest(
    webhookRequest("example", 123),
    {
      queue: queueMock(calls),
      dispatchDrain: async (generation) => calls.push(["drain", generation]),
    },
  ));

  const requestOwner = calls.find(([name]) => name === "request")[1];
  assert.match(
    requestOwner,
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
});

test("queue dispatch failure releases the dispatcher lease", async () => {
  const calls = [];
  await assert.rejects(
    withQueueMode("all", () => handleWebhookRequest(
      webhookRequest("example", 123),
      {
        queue: queueMock(calls),
        dispatchDrain: async () => {
          throw new Error("dispatch failed");
        },
      },
    )),
    /dispatch failed/,
  );
  const requestOwner = calls.find(([name]) => name === "request")[1];
  assert.deepEqual(calls.at(-1), [
    "release",
    { generation: 7, requestOwner },
  ]);
});

function webhookRequest(repository, prNumber, delivery = "delivery-1") {
  const body = JSON.stringify({
    action: "opened",
    repository: {
      full_name: `open-telemetry/${repository}`,
      owner: { login: "open-telemetry" },
    },
    pull_request: { number: prNumber },
  });
  const signature = crypto
    .createHmac("sha256", webhookSecret)
    .update(body)
    .digest("hex");
  return new Request(
    "https://example.test/.netlify/functions/github-webhook",
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-github-delivery": delivery,
        "x-github-event": "pull_request",
        "x-hub-signature-256": `sha256=${signature}`,
      },
      body,
    },
  );
}

function queueMock(calls, name = "live") {
  return {
    async enqueue(item) {
      calls.push([`enqueue:${name}`, item]);
      return { status: "queued" };
    },
    async requestDispatcher(owner) {
      calls.push(["request", owner]);
      return { acquired: true, generation: 7, requestOwner: owner };
    },
    async releaseRequestedDispatcher(input) {
      calls.push(["release", input]);
      return true;
    },
  };
}

async function withQueueMode(queueMode, callback) {
  const previousSecret = process.env.GITHUB_WEBHOOK_SECRET;
  const previousMode = process.env.PR_DASHBOARD_QUEUE_MODE;
  process.env.GITHUB_WEBHOOK_SECRET = webhookSecret;
  process.env.PR_DASHBOARD_QUEUE_MODE = queueMode;
  try {
    return await callback();
  } finally {
    restoreEnvironment("GITHUB_WEBHOOK_SECRET", previousSecret);
    restoreEnvironment("PR_DASHBOARD_QUEUE_MODE", previousMode);
  }
}

function restoreEnvironment(name, value) {
  if (value === undefined) {
    delete process.env[name];
  } else {
    process.env[name] = value;
  }
}
