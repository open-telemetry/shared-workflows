import assert from "node:assert/strict";
import test from "node:test";

import {
  extractHeadSha,
  extractPullRequestNumber,
  isAllowedAction,
  isDashboardSelfTriggeredCommentEvent,
  isDefaultBranchStatusEvent,
  isRedundantCheckRunEvent,
} from "./netlify/functions/github-webhook.mjs";

const dashboardApp = { slug: "opentelemetry-pr-dashboard" };
const dashboardActor = { id: 1 };
const codeScanningApp = { id: 57789 };
const headSha = "65325a64c9b2e4b8a1d0f3c7e5a9b1d2c3e4f5a6";

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

test("refreshes when a check run completes", () => {
  assert.equal(isAllowedAction("check_run", "completed"), true);
  assert.equal(isAllowedAction("check_run", "created"), false);
});

test("refreshes on commit statuses, which carry no action", () => {
  assert.equal(isAllowedAction("status", ""), true);
  assert.equal(isAllowedAction("pull_request", ""), false);
});

test("ignores check runs reported by apps other than code scanning", () => {
  assert.equal(isRedundantCheckRunEvent("check_run", {
    check_run: { app: codeScanningApp },
  }), false);
  assert.equal(isRedundantCheckRunEvent("check_run", {
    check_run: { app: { id: 15368 } },
  }), true);
  assert.equal(isRedundantCheckRunEvent("check_suite", {
    check_suite: { app: { id: 15368 } },
  }), false);
});

test("ignores statuses reported on the default branch", () => {
  const repository = { default_branch: "main" };
  assert.equal(isDefaultBranchStatusEvent("status", {
    repository,
    branches: [{ name: "main" }],
  }), true);
  assert.equal(isDefaultBranchStatusEvent("status", {
    repository,
    branches: [{ name: "renovate/kotlin-plugin-updates" }],
  }), false);
  // A fork head is not a branch in the repository that emitted the event.
  assert.equal(isDefaultBranchStatusEvent("status", { repository, branches: [] }), false);
});

test("reports the head commit when a check event has no pull request", () => {
  // Check suites on a fork head report head_branch: null and no pull requests.
  const payload = {
    repository: { url: "https://api.github.com/repos/open-telemetry/example", default_branch: "main" },
    check_suite: { head_branch: null, head_sha: headSha, pull_requests: [] },
  };
  assert.equal(extractPullRequestNumber("check_suite", payload), undefined);
  assert.equal(extractHeadSha("check_suite", payload), headSha);
});

test("prefers the pull request a check event names", () => {
  const payload = {
    repository: { url: "https://api.github.com/repos/open-telemetry/example" },
    check_run: {
      head_sha: headSha,
      pull_requests: [
        { number: 19286, url: "https://api.github.com/repos/open-telemetry/example/pulls/19286" },
      ],
    },
  };
  assert.equal(extractPullRequestNumber("check_run", payload), 19286);
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
