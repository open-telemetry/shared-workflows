import assert from "node:assert/strict";
import test from "node:test";

import {
  extractPullRequestNumber,
  isAllowedAction,
  isDashboardSelfTriggeredCommentEvent,
} from "./netlify/functions/github-webhook.mjs";

const dashboardApp = { slug: "opentelemetry-pr-dashboard" };
const dashboardActor = { id: 1 };

test("refreshes when the dashboard override label changes", () => {
  assert.equal(isAllowedAction("pull_request", "labeled"), true);
  assert.equal(isAllowedAction("pull_request", "unlabeled"), true);
});

test("refreshes when a review request is added or removed", () => {
  assert.equal(isAllowedAction("pull_request", "review_requested"), true);
  assert.equal(isAllowedAction("pull_request", "review_request_removed"), true);
});

// Check and status events are handled by the scheduled rollup poll instead, so
// the bridge only forwards events that name a pull request outright.
test("ignores check and status events", () => {
  assert.equal(isAllowedAction("check_suite", "completed"), false);
  assert.equal(isAllowedAction("check_run", "completed"), false);
  assert.equal(isAllowedAction("status", ""), false);
});

test("requires a known action", () => {
  assert.equal(isAllowedAction("pull_request", ""), false);
});

test("reads the pull request number an event names", () => {
  assert.equal(extractPullRequestNumber("pull_request", { pull_request: { number: 19286 } }), 19286);
  assert.equal(extractPullRequestNumber("issue_comment", { issue: { number: 19286, pull_request: {} } }), 19286);
  assert.equal(extractPullRequestNumber("issue_comment", { issue: { number: 19286 } }), undefined);
  assert.equal(
    extractPullRequestNumber("pull_request_review_thread", {
      review_thread: { pull_request_url: "https://api.github.com/repos/open-telemetry/example/pulls/19286" },
    }),
    19286,
  );
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
