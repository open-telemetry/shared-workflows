import crypto from "node:crypto";

import {
  DashboardQueue,
  openQueueStore,
} from "../lib/dashboard-queue.mjs";
import {
  dispatchDashboardRefresh,
  dispatchQueueDrain,
} from "../lib/github-dispatch.mjs";

const MAX_WEBHOOK_BYTES = 1024 * 1024;
const OWNER = "open-telemetry";
const DASHBOARD_APP_SLUG = "opentelemetry-pr-dashboard";
const QUEUE_MODES = new Set(["off", "shadow", "canary", "all"]);
const DISPATCHER_OWNER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;
const QUEUE_CANARY_REPOSITORIES = new Set([
  "opentelemetry-java-instrumentation",
  "shared-workflows",
]);

const ALLOWED_ACTIONS = {
  // GitHub only delivers `requested` and `rerequested` to apps with write-level
  // Checks access; this app has read-only, so `completed` is all that arrives.
  check_suite: new Set(["completed"]),
  // Commit statuses carry no action.
  status: new Set([""]),
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
    return await handleWebhookRequest(request);
  } catch (error) {
    console.error(error);
    return response(error.statusCode || 500, { error: error.publicMessage || "internal server error" });
  }
};

export async function handleWebhookRequest(
  request,
  {
    queue,
    shadowQueue,
    dispatchRefresh = dispatchDashboardRefresh,
    dispatchDrain = dispatchQueueDrain,
  } = {},
) {
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
  const headSha = extractHeadSha(eventName, payload);
  const dispatchPrNumber = Number.isInteger(prNumber) && prNumber > 0 ? String(prNumber) : "";
  const dispatchHeadSha = dispatchPrNumber ? "" : headSha;
  if (dispatchHeadSha && isDefaultBranchEvent(eventName, payload)) {
    return response(202, { status: "ignored", reason: "head commit is on the default branch" });
  }
  if (!dispatchPrNumber && !dispatchHeadSha) {
    return response(202, { status: "ignored", reason: "no pull request number or head commit found" });
  }

  const inputs = {
    repository: repository.name,
    pr_number: dispatchPrNumber,
    head_sha: dispatchHeadSha,
    trigger_event: eventName,
  };
  if (
    config.queueMode === "off" ||
    (
      config.queueMode === "canary" &&
      !QUEUE_CANARY_REPOSITORIES.has(repository.name)
    )
  ) {
    await dispatchRefresh(inputs);
    return response(202, {
      status: "dispatched",
      repository: repository.fullName,
      pr_number: dispatchPrNumber,
      head_sha: dispatchHeadSha,
      trigger_event: eventName,
      queue_mode: config.queueMode,
    });
  }

  const targetQueue = config.queueMode === "shadow"
    ? shadowQueue || new DashboardQueue({
      store: openQueueStore("pr-dashboard-queue-shadow"),
    })
    : queue || new DashboardQueue();
  if (config.queueMode === "shadow") {
    let queueStatus = "error";
    try {
      const queued = await targetQueue.enqueue({
        repository: repository.name,
        prNumber: dispatchPrNumber ? Number.parseInt(dispatchPrNumber, 10) : null,
        headSha: dispatchHeadSha,
        triggerEvent: eventName,
      });
      queueStatus = queued.status;
    } catch (error) {
      console.error("shadow queue observation failed", error);
    }
    await dispatchRefresh(inputs);
    return response(202, {
      status: "shadow_queued",
      queue_status: queueStatus,
      repository: repository.fullName,
      pr_number: dispatchPrNumber,
      head_sha: dispatchHeadSha,
      trigger_event: eventName,
      queue_mode: config.queueMode,
    });
  }

  const queued = await targetQueue.enqueue({
    repository: repository.name,
    prNumber: dispatchPrNumber ? Number.parseInt(dispatchPrNumber, 10) : null,
    headSha: dispatchHeadSha,
    triggerEvent: eventName,
  });
  const requestOwner = dispatcherRequestOwner(request);
  const dispatcher = await targetQueue.requestDispatcher(requestOwner);
  if (dispatcher.acquired) {
    try {
      await dispatchDrain(dispatcher.generation);
    } catch (error) {
      await targetQueue.releaseRequestedDispatcher({
        generation: dispatcher.generation,
        requestOwner,
      });
      throw error;
    }
  }

  return response(202, {
    status: dispatcher.acquired ? "queued_and_dispatched" : queued.status,
    repository: repository.fullName,
    pr_number: dispatchPrNumber,
    head_sha: dispatchHeadSha,
    trigger_event: eventName,
    queue_mode: config.queueMode,
  });
}

function dispatcherRequestOwner(request) {
  const delivery = request.headers.get("x-github-delivery");
  return delivery && DISPATCHER_OWNER_PATTERN.test(delivery)
    ? delivery
    : crypto.randomUUID();
}

export function isAllowedAction(eventName, action) {
  return Boolean(ALLOWED_ACTIONS[eventName] && ALLOWED_ACTIONS[eventName].has(action));
}

// A check or status event on the default branch reports a push, and the head
// SHA fallback would otherwise dispatch a refresh for it. The dashboard's own
// workflow runs are check suites on this repository's default branch, so each
// run would dispatch the next one and never stop.
//
// A fork pull request whose head branch is itself named `main` is
// indistinguishable here and falls back to the hourly backfill.
//
// Code scanning reports no head branch at all, and is deliberately left
// dispatchable. It publishes its check suite on pull request heads rather than
// on the default branch, so the head SHA fallback resolves to the pull request
// that owns the commit rather than to a push.
export function isDefaultBranchEvent(eventName, payload) {
  const defaultBranch = (payload.repository || {}).default_branch;
  if (!defaultBranch) {
    return false;
  }
  if (eventName === "status") {
    return (payload.branches || []).some(
      (branch) => branch && branch.name === defaultBranch,
    );
  }
  if (eventName === "check_suite") {
    return (payload.check_suite || {}).head_branch === defaultBranch;
  }
  return false;
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
    queueMode: process.env.PR_DASHBOARD_QUEUE_MODE || "off",
    webhookSecret: process.env.GITHUB_WEBHOOK_SECRET,
  };

  const missing = Object.entries(config)
    .filter(([, value]) => !value)
    .map(([key]) => key);
  if (missing.length > 0) {
    throw httpError(500, "missing required configuration", `missing required configuration: ${missing.join(", ")}`);
  }
  if (!QUEUE_MODES.has(config.queueMode)) {
    throw httpError(
      500,
      "invalid queue mode",
      `unsupported PR_DASHBOARD_QUEUE_MODE: ${config.queueMode}`,
    );
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

export function extractPullRequestNumber(eventName, payload) {
  if (eventName === "issue_comment") {
    if (!payload.issue || !payload.issue.pull_request) {
      return undefined;
    }
    return payload.issue.number;
  }

  // Check suites and check runs on a fork head report an empty association,
  // because GitHub only matches them to a pull request whose head branch lives
  // in this repository. Those events fall back to the head SHA.
  const checkPullRequestNumber = extractPullRequestNumberFromCheckPullRequests(
    checkPullRequests(payload),
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

export function extractHeadSha(eventName, payload) {
  const sha = eventName === "status" ? payload.sha : (payload.check_suite || {}).head_sha;
  return typeof sha === "string" && /^[0-9a-f]{40}$/.test(sha) ? sha : "";
}

function checkPullRequests(payload) {
  return (payload.check_suite || {}).pull_requests;
}

function extractPullRequestNumberFromCheckPullRequests(pullRequests, repository) {
  if (!Array.isArray(pullRequests)) {
    return undefined;
  }
  for (const pullRequest of pullRequests) {
    if (
      pullRequest &&
      Number.isInteger(pullRequest.number) &&
      checkPullRequestBelongsToRepository(pullRequest, repository)
    ) {
      return pullRequest.number;
    }
  }
  return undefined;
}

function checkPullRequestBelongsToRepository(pullRequest, repository) {
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
