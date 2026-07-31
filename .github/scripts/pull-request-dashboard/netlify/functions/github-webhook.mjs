import crypto from "node:crypto";

const GITHUB_API_VERSION = "2022-11-28";
const MAX_WEBHOOK_BYTES = 1024 * 1024;
const OWNER = "open-telemetry";
const WORKFLOW_REPOSITORY = "shared-workflows";
const WORKFLOW_ID = "pull-request-dashboard.yml";
const WORKFLOW_REF = "main";
const DASHBOARD_APP_SLUG = "opentelemetry-pr-dashboard";
const CODE_SCANNING_APP_ID = 57789; // github-advanced-security

const ALLOWED_ACTIONS = {
  // GitHub only delivers `requested` and `rerequested` to apps with write-level
  // Checks access; this app has read-only, so `completed` is all that arrives.
  check_suite: new Set(["completed"]),
  check_run: new Set(["completed"]),
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
  if (isRedundantCheckRunEvent(eventName, payload)) {
    return response(202, { status: "ignored", reason: "check run covered by its check suite" });
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

  const dispatcherJwt = createAppJwt({ appId: config.dispatcherAppId, privateKey: config.dispatcherPrivateKey });
  const installationId = await findRepositoryInstallationId(dispatcherJwt, `${OWNER}/${WORKFLOW_REPOSITORY}`);
  const installationToken = await createInstallationToken(dispatcherJwt, installationId);
  await dispatchWorkflow(installationToken, {
    repository: repository.name,
    pr_number: dispatchPrNumber,
    head_sha: dispatchHeadSha,
    trigger_event: eventName,
  });

  return response(202, {
    status: "dispatched",
    repository: repository.fullName,
    pr_number: dispatchPrNumber,
    head_sha: dispatchHeadSha,
    trigger_event: eventName,
  });
}

export function isAllowedAction(eventName, action) {
  return Boolean(ALLOWED_ACTIONS[eventName] && ALLOWED_ACTIONS[eventName].has(action));
}

// Every other app reports its check runs under a check suite that completes
// with them, and one dispatch per job would multiply webhook volume. The code
// scanning app instead rewrites its already completed check run when the
// analysis arrives, which nothing else reports.
export function isRedundantCheckRunEvent(eventName, payload) {
  if (eventName !== "check_run") {
    return false;
  }
  const app = (payload.check_run || {}).app || {};
  return app.id !== CODE_SCANNING_APP_ID;
}

// A check or status event on the default branch reports a push, and the head
// SHA fallback would otherwise dispatch a refresh for it. The dashboard's own
// workflow runs are check suites on this repository's default branch, so each
// run would dispatch the next one and never stop.
//
// A fork pull request whose head branch is itself named `main` is
// indistinguishable here and falls back to the hourly backfill.
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
  if (eventName === "check_suite" || eventName === "check_run") {
    const checkSuite = payload.check_suite || (payload.check_run || {}).check_suite || {};
    return checkSuite.head_branch === defaultBranch;
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
    dispatcherAppId: process.env.OTELBOT_SHARED_WORKFLOWS_APP_ID,
    dispatcherPrivateKey: normalizePrivateKey(
      process.env.OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY,
      process.env.OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY_BASE64,
    ),
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
  const sha =
    eventName === "status"
      ? payload.sha
      : (payload.check_suite || payload.check_run || {}).head_sha;
  return typeof sha === "string" && /^[0-9a-f]{40}$/.test(sha) ? sha : "";
}

function checkPullRequests(payload) {
  const source = payload.check_suite || payload.check_run || {};
  return source.pull_requests;
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

async function findRepositoryInstallationId(jwt, repository) {
  const body = await githubJson(
    `https://api.github.com/repos/${encodeRepository(repository)}/installation`,
    jwt,
  );
  if (!body || !body.id) {
    throw httpError(502, "GitHub installation lookup failed", `GitHub installation response did not include id for ${repository}`);
  }
  return body.id;
}

async function createInstallationToken(jwt, installationId) {
  const body = await githubJson(
    `https://api.github.com/app/installations/${installationId}/access_tokens`,
    jwt,
    { method: "POST" },
  );
  if (!body || !body.token) {
    throw httpError(502, "GitHub token request failed", "GitHub installation token response did not include a token");
  }
  return body.token;
}

async function dispatchWorkflow(token, inputs) {
  const encodedWorkflowId = encodeURIComponent(WORKFLOW_ID);
  await githubFetch(
    `https://api.github.com/repos/${OWNER}/${WORKFLOW_REPOSITORY}/actions/workflows/${encodedWorkflowId}/dispatches`,
    token,
    {
      method: "POST",
      body: JSON.stringify({
        ref: WORKFLOW_REF,
        inputs,
      }),
    },
  );
}

function encodeRepository(repository) {
  return repository.split("/").map(encodeURIComponent).join("/");
}

async function githubJson(url, token, options = {}) {
  const response = await githubFetch(url, token, options);
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

async function githubFetch(url, token, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      accept: "application/vnd.github+json",
      "content-type": "application/json",
      "user-agent": "pull-request-dashboard-webhook",
      "x-github-api-version": GITHUB_API_VERSION,
      authorization: `Bearer ${token}`,
      ...(options.headers || {}),
    },
  });

  if (!response.ok) {
    const body = await response.text();
    throw httpError(
      502,
      "GitHub API request failed",
      `GitHub API request failed: ${response.status} ${response.statusText}: ${body}`,
    );
  }

  return response;
}

function createAppJwt(config) {
  const now = Math.floor(Date.now() / 1000);
  const header = base64UrlJson({ alg: "RS256", typ: "JWT" });
  const payload = base64UrlJson({
    iat: now - 60,
    exp: now + 10 * 60,
    iss: config.appId,
  });
  const unsignedToken = `${header}.${payload}`;
  const signature = crypto.sign("RSA-SHA256", Buffer.from(unsignedToken), config.privateKey);

  return `${unsignedToken}.${base64Url(signature)}`;
}

function base64UrlJson(value) {
  return base64Url(Buffer.from(JSON.stringify(value)));
}

function base64Url(buffer) {
  return buffer
    .toString("base64")
    .replace(/=/g, "")
    .replace(/\+/g, "-")
    .replace(/\//g, "_");
}

function normalizePrivateKey(value, base64Value) {
  const rawValue = base64Value ? Buffer.from(base64Value, "base64").toString("utf8") : value;
  return rawValue && rawValue.trim().replace(/^['"]|['"]$/g, "").replace(/\\n/g, "\n");
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
