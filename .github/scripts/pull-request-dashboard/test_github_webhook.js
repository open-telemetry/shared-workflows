const assert = require("node:assert/strict");
const test = require("node:test");

const { isDashboardSelfTriggeredCommentEvent } = require("./netlify/functions/github-webhook");

const dashboardApp = { slug: "opentelemetry-pr-dashboard" };
const dashboardActor = { id: 1 };

test("recognizes current and legacy dashboard-managed comments", () => {
  for (const marker of [
    "<!-- pull-request-dashboard-status -->",
    "<!-- review-guidance -->",
    "<!-- copilot-review-guidance -->",
  ]) {
    assert.equal(isDashboardSelfTriggeredCommentEvent("issue_comment", {
      comment: {
        body: `${marker} body`,
        performed_via_github_app: dashboardApp,
        user: dashboardActor,
      },
      sender: dashboardActor,
    }), true);
  }
});

test("rejects spoofed markers and unrelated dashboard app comments", () => {
  assert.equal(isDashboardSelfTriggeredCommentEvent("issue_comment", {
    comment: {
      body: "<!-- pull-request-dashboard-status --> spoofed",
      user: dashboardActor,
    },
    sender: dashboardActor,
  }), false);
  assert.equal(isDashboardSelfTriggeredCommentEvent("issue_comment", {
    comment: {
      body: "ordinary comment",
      performed_via_github_app: dashboardApp,
      user: dashboardActor,
    },
    sender: dashboardActor,
  }), false);
  assert.equal(isDashboardSelfTriggeredCommentEvent("pull_request", {
    comment: {
      body: "<!-- pull-request-dashboard-status --> body",
      performed_via_github_app: dashboardApp,
      user: dashboardActor,
    },
    sender: dashboardActor,
  }), false);
});

test("allows dashboard-managed comment events triggered by another actor", () => {
  assert.equal(isDashboardSelfTriggeredCommentEvent("issue_comment", {
    comment: {
      body: "<!-- pull-request-dashboard-status --> body",
      performed_via_github_app: dashboardApp,
      user: dashboardActor,
    },
    sender: { id: 2 },
  }), false);
});