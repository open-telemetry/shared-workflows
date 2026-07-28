import crypto from "node:crypto";

import { getStore } from "@netlify/blobs";

import { REFRESH_QUEUE_STORE, refreshQueueKey } from "../lib/refresh-queue.mjs";

const MAX_WEBHOOK_BYTES = 1024 * 1024;
const OWNER = "open-telemetry";
const DASHBOARD_APP_SLUG = "opentelemetry-pr-dashboard";

const ALLOWED_ACTIONS = {
  check_suite: new Set(["completed", "requested", "rerequested"]),
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
  const action = payload.action;
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

  // Record the event instead of dispatching. The scheduled flush function
  // collapses each burst into one workflow run per pull request.
  const store = getStore(REFRESH_QUEUE_STORE);
  const key = refreshQueueKey(repository.name, prNumber);
  const queued = (await store.get(key, { type: "json" })) || {};
  await store.setJSON(key, {
    ...queued,
    lastEventAt: Date.now(),
    triggerEvent: eventName,
  });

  return response(202, {
    status: "queued",
    repository: repository.fullName,
    pr_number: prNumber,
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
  const config = {
    webhookSecret: process.env.GITHUB_WEBHOOK_SECRET,
  };

  const missing = Object.entries(config)
    .filter(([, value]) => !value)
    .map(([key]) => key);
  if (missing.length > 0) {
    throw httpError(500, "missing required configuration", `missing required configuration: ${missing.join(", ")}`);
  }

  return config;
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

function extractPullRequestNumber(eventName, payload) {
  if (eventName === "issue_comment") {
    if (!payload.issue || !payload.issue.pull_request) {
      return undefined;
    }
    return payload.issue.number;
  }

  const checkPullRequestNumber = extractPullRequestNumberFromCheckSuitePullRequests(
    payload.check_suite && payload.check_suite.pull_requests,
    payload.repository,
  );
  if (checkPullRequestNumber) {
    return checkPullRequestNumber;
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

function extractPullRequestNumberFromCheckSuitePullRequests(pullRequests, repository) {
  if (!Array.isArray(pullRequests)) {
    return undefined;
  }
  for (const pullRequest of pullRequests) {
    if (
      pullRequest &&
      Number.isInteger(pullRequest.number) &&
      checkSuitePullRequestBelongsToRepository(pullRequest, repository)
    ) {
      return pullRequest.number;
    }
  }
  return undefined;
}

function checkSuitePullRequestBelongsToRepository(pullRequest, repository) {
  const repositoryUrl = repository && repository.url;
  if (!repositoryUrl) {
    return false;
  }
  // check_suite.pull_requests are commit/ref associations and can point at a
  // fork PR whose head is this repository. Only dispatch when the associated PR
  // itself belongs to the repository that emitted this webhook event; the
  // workflow dispatch passes repository + pr_number, and PR numbers are
  // repository-scoped.
  const pullRequestRepositoryUrl = repositoryUrlFromPullRequestApiUrl(pullRequest.url);
  const baseRepositoryUrl = pullRequest.base && pullRequest.base.repo && pullRequest.base.repo.url;
  return pullRequestRepositoryUrl === repositoryUrl || baseRepositoryUrl === repositoryUrl;
}

function repositoryUrlFromPullRequestApiUrl(url) {
  if (typeof url !== "string") {
    return "";
  }
  const match = url.match(/^(https:\/\/api\.github\.com\/repos\/[^/]+\/[^/]+)\/pulls\/\d+$/);
  return match ? match[1] : "";
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

function response(status, body) {
  return Response.json(body, { status });
}

function httpError(statusCode, publicMessage, message) {
  const error = new Error(message);
  error.statusCode = statusCode;
  error.publicMessage = publicMessage;
  return error;
}
