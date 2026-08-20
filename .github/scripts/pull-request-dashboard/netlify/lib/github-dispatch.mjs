import crypto from "node:crypto";

const GITHUB_API_VERSION = "2022-11-28";
const OWNER = "open-telemetry";
const WORKFLOW_REPOSITORY = "shared-workflows";
const WORKFLOW_REF = "main";
const DASHBOARD_WORKFLOW_ID = "pull-request-dashboard.yml";
const DRAIN_WORKFLOW_ID = "pull-request-dashboard-drain.yml";

export function loadDispatcherConfig() {
  const config = {
    clientId: process.env.OTELBOT_SHARED_WORKFLOWS_CLIENT_ID,
    privateKey: normalizePrivateKey(
      process.env.OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY,
      process.env.OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY_BASE64,
    ),
  };
  const missing = Object.entries(config)
    .filter(([, value]) => !value)
    .map(([key]) => key);
  if (missing.length > 0) {
    throw dispatchError(
      500,
      "missing required configuration",
      `missing dispatcher configuration: ${missing.join(", ")}`,
    );
  }
  return config;
}

export async function dispatchDashboardRefresh(inputs, config = loadDispatcherConfig()) {
  return dispatchWorkflow(DASHBOARD_WORKFLOW_ID, inputs, config);
}

export async function dispatchQueueDrain(
  dispatcherGeneration,
  config = loadDispatcherConfig(),
) {
  if (!Number.isInteger(dispatcherGeneration) || dispatcherGeneration < 1) {
    throw new Error("dispatcherGeneration must be a positive integer");
  }
  return dispatchWorkflow(
    DRAIN_WORKFLOW_ID,
    { dispatcher_generation: String(dispatcherGeneration) },
    config,
  );
}

async function dispatchWorkflow(workflowId, inputs, config) {
  const appJwt = createAppJwt(config);
  const installation = await githubJson(
    `https://api.github.com/repos/${OWNER}/${WORKFLOW_REPOSITORY}/installation`,
    appJwt,
  );
  if (!installation || !installation.id) {
    throw dispatchError(
      502,
      "GitHub installation lookup failed",
      "GitHub installation response did not include an id",
    );
  }
  const installationToken = await githubJson(
    `https://api.github.com/app/installations/${installation.id}/access_tokens`,
    appJwt,
    { method: "POST" },
  );
  if (!installationToken || !installationToken.token) {
    throw dispatchError(
      502,
      "GitHub token request failed",
      "GitHub installation token response did not include a token",
    );
  }
  await githubFetch(
    `https://api.github.com/repos/${OWNER}/${WORKFLOW_REPOSITORY}/actions/workflows/${encodeURIComponent(workflowId)}/dispatches`,
    installationToken.token,
    {
      method: "POST",
      body: JSON.stringify({
        ref: WORKFLOW_REF,
        inputs,
      }),
    },
  );
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
    throw dispatchError(
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
    iss: config.clientId,
  });
  const signature = crypto.sign(
    "RSA-SHA256",
    Buffer.from(`${header}.${payload}`),
    config.privateKey,
  );
  return `${header}.${payload}.${signature.toString("base64url")}`;
}

function base64UrlJson(value) {
  return Buffer.from(JSON.stringify(value)).toString("base64url");
}

function normalizePrivateKey(value, base64Value) {
  const rawValue = base64Value
    ? Buffer.from(base64Value, "base64").toString("utf8")
    : value;
  if (!rawValue) {
    return "";
  }
  // The key travels either as a base64 blob or as a single-line environment
  // value, so surrounding quotes and escaped newlines both have to survive the
  // round trip.
  return rawValue.trim().replace(/^['"]|['"]$/g, "").replace(/\\n/g, "\n");
}

function dispatchError(statusCode, publicMessage, message) {
  const error = new Error(message);
  error.statusCode = statusCode;
  error.publicMessage = publicMessage;
  return error;
}
