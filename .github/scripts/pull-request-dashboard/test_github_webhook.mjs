import assert from "node:assert/strict";
import test from "node:test";

import {
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

test("refreshes only once a check suite completes", () => {
  assert.equal(isAllowedAction("check_suite", "completed"), true);
  assert.equal(isAllowedAction("check_suite", "requested"), false);
  assert.equal(isAllowedAction("check_suite", "rerequested"), false);
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
