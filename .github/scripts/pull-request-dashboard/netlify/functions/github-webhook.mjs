import crypto from "node:crypto";

import {
  OWNER,
  createDispatcherToken,
  dispatchWorkflow,
  httpError,
  jsonResponse as response,
  loadDispatcherCredentials,
} from "../lib/github-app.mjs";

const MAX_WEBHOOK_BYTES = 1024 * 1024;
const WORKFLOW_ID = "pull-request-dashboard.yml";
const DASHBOARD_APP_SLUG = "opentelemetry-pr-dashboard";

// Check suite, check run, and status events are deliberately absent. They
// arrive about ten times per push, report no pull request number when the head
// branch lives in a fork, and the dashboard's own runs reached the head commit
// fallback and dispatched each other. The scheduled poll in
// `pull-request-dashboard-check-poll.yml` reads the same rollup once per
// repository, keyed by pull request number.
const ALLOWED_ACTIONS = {
  pull_request: new Set([
    "assigned",
    "closed",
    "converted_to_draft",
    "edited",
    "labeled",
    "opened",
    "ready_for_review",
    "reopened",
    "review_request_removed",
    "review_requested",
    "synchronize",
    "unassigned",
    "unlabeled",
  ]),
  issue_comment: new Set(["created", "edited", "deleted"]),
  pull_request_review: new Set(["submitted", "edited", "dismissed"]),
  pull_request_review_comment: new Set(["created", "edited", "deleted"]),
  pull_request_review_thread: new Set(["resolved", "unresolved"]),
};

export default async (request) => {
  try {
    return await handle(request);
  } catch (error) {
    console.error(error);
    return response(error.statusCode || 500, { error: error.publicMessage || "internal server error" });
  }
};

async function handle(request) {
  if (request.method !== "POST") {
    return response(405, { error: "method not allowed" });
  }

  const config = loadConfig();
  const rawBody = Buffer.from(await request.arrayBuffer());

  if (rawBody.length > MAX_WEBHOOK_BYTES) {
    return response(413, { error: "payload too large" });
  }

  if (!verifySignature(rawBody, request.headers.get("x-hub-signature-256"), config.webhookSecret)) {
    return response(401, { error: "invalid signature" });
  }

  const eventName = request.headers.get("x-github-event");
  if (eventName === "ping") {
    return response(202, { status: "ignored", reason: "ping" });
  }
  if (!Object.prototype.hasOwnProperty.call(ALLOWED_ACTIONS, eventName)) {
    return response(202, { status: "ignored", reason: `unsupported event: ${eventName || "missing"}` });
  }

  const payload = parseJson(rawBody);
  const action = payload.action || "";
  if (!isAllowedAction(eventName, action)) {
    return response(202, { status: "ignored", reason: `unsupported action: ${eventName}.${action || "missing"}` });
  }
  if (isDashboardSelfTriggeredCommentEvent(eventName, payload)) {
    return response(202, { status: "ignored", reason: "dashboard-managed comment" });
  }

  const repository = readRepository(payload);
  if (!repository.fullName) {
    return response(202, { status: "ignored", reason: "missing repository" });
  }
  if (repository.owner !== OWNER) {
    return response(202, { status: "ignored", reason: `unsupported repository owner: ${repository.owner || "missing"}` });
  }

  const prNumber = extractPullRequestNumber(eventName, payload);
  if (!Number.isInteger(prNumber) || prNumber <= 0) {
    return response(202, { status: "ignored", reason: "no pull request number found" });
  }

  const installationToken = await createDispatcherToken(config.dispatcher);
  await dispatchWorkflow(installationToken, WORKFLOW_ID, {
    repository: repository.name,
    pr_number: String(prNumber),
    trigger_event: eventName,
  });

  return response(202, {
    status: "dispatched",
    repository: repository.fullName,
    pr_number: String(prNumber),
    trigger_event: eventName,
  });
}

export function isAllowedAction(eventName, action) {
  return Boolean(ALLOWED_ACTIONS[eventName] && ALLOWED_ACTIONS[eventName].has(action));
}

export function isDashboardSelfTriggeredCommentEvent(eventName, payload) {
  if (eventName !== "issue_comment") {
    return false;
  }
  const comment = payload.comment || {};
  const app = comment.performed_via_github_app || {};
  const commentAuthor = comment.user || {};
  const sender = payload.sender || {};
  return (
    app.slug === DASHBOARD_APP_SLUG &&
    Boolean(sender.id) &&
    sender.id === commentAuthor.id
  );
}

function loadConfig() {
  const webhookSecret = process.env.GITHUB_WEBHOOK_SECRET;
  if (!webhookSecret) {
    throw httpError(
      500,
      "missing required configuration",
      "missing required configuration: webhookSecret",
    );
  }
  return { dispatcher: loadDispatcherCredentials(), webhookSecret };
}

function readRepository(payload) {
  const repository = payload.repository || {};
  const fullName = repository.full_name || "";
  const [, name = ""] = fullName.split("/", 2);
  return {
    fullName,
    name,
    owner: repository.owner && repository.owner.login,
  };
}

function verifySignature(rawBody, signatureHeader, secret) {
  if (!signatureHeader || !signatureHeader.startsWith("sha256=")) {
    return false;
  }

  const expected = Buffer.from(signatureHeader.slice("sha256=".length), "hex");
  const actual = crypto.createHmac("sha256", secret).update(rawBody).digest();

  return expected.length === actual.length && crypto.timingSafeEqual(expected, actual);
}

function parseJson(rawBody) {
  try {
    return JSON.parse(rawBody.toString("utf8"));
  } catch (error) {
    throw httpError(400, "invalid JSON payload", `invalid JSON payload: ${error.message}`);
  }
}

export function extractPullRequestNumber(eventName, payload) {
  if (eventName === "issue_comment") {
    if (!payload.issue || !payload.issue.pull_request) {
      return undefined;
    }
    return payload.issue.number;
  }

  if (payload.pull_request && Number.isInteger(payload.pull_request.number)) {
    return payload.pull_request.number;
  }

  return extractPullRequestNumberFromUrls([
    payload.pull_request_url,
    payload.review_thread && payload.review_thread.pull_request_url,
    payload.thread && payload.thread.pull_request_url,
  ]);
}

function extractPullRequestNumberFromUrls(urls) {
  for (const url of urls) {
    if (typeof url !== "string") {
      continue;
    }
    const match = url.match(/\/pulls\/(\d+)(?:$|[/?#])/);
    if (match) {
      return Number.parseInt(match[1], 10);
    }
  }
  return undefined;
}
