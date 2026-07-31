import crypto from "node:crypto";

const GITHUB_API_VERSION = "2022-11-28";
const WORKFLOW_REF = "main";

export const OWNER = "open-telemetry";
export const WORKFLOW_REPOSITORY = "shared-workflows";

// The dispatcher app is installed only on this repository, which keeps the
// credentials reachable from Netlify unable to touch the monitored repositories.
export function loadDispatcherCredentials() {
  const credentials = {
    appId: process.env.OTELBOT_SHARED_WORKFLOWS_APP_ID,
    privateKey: normalizePrivateKey(
      process.env.OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY,
      process.env.OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY_BASE64,
    ),
  };
  requireConfig(credentials);
  return credentials;
}

export function requireConfig(config) {
  const missing = Object.entries(config)
    .filter(([, value]) => !value)
    .map(([key]) => key);
  if (missing.length > 0) {
    throw httpError(
      500,
      "missing required configuration",
      `missing required configuration: ${missing.join(", ")}`,
    );
  }
}

export async function createDispatcherToken(credentials) {
  const jwt = createAppJwt(credentials);
  const installationId = await findRepositoryInstallationId(jwt, `${OWNER}/${WORKFLOW_REPOSITORY}`);
  return createInstallationToken(jwt, installationId);
}

export async function dispatchWorkflow(token, workflowId, inputs) {
  const encodedWorkflowId = encodeURIComponent(workflowId);
  await githubFetch(
    `https://api.github.com/repos/${OWNER}/${WORKFLOW_REPOSITORY}/actions/workflows/${encodedWorkflowId}/dispatches`,
    token,
    {
      method: "POST",
      body: JSON.stringify({ ref: WORKFLOW_REF, inputs }),
    },
  );
}

export function normalizePrivateKey(value, base64Value) {
  const rawValue = base64Value ? Buffer.from(base64Value, "base64").toString("utf8") : value;
  return rawValue && rawValue.trim().replace(/^['"]|['"]$/g, "").replace(/\\n/g, "\n");
}

export function jsonResponse(status, body) {
  return Response.json(body, { status });
}

export function httpError(statusCode, publicMessage, message) {
  const error = new Error(message);
  error.statusCode = statusCode;
  error.publicMessage = publicMessage;
  return error;
}

async function findRepositoryInstallationId(jwt, repository) {
  const body = await githubJson(
    `https://api.github.com/repos/${encodeRepository(repository)}/installation`,
    jwt,
  );
  if (!body || !body.id) {
    throw httpError(
      502,
      "GitHub installation lookup failed",
      `GitHub installation response did not include id for ${repository}`,
    );
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
    throw httpError(
      502,
      "GitHub token request failed",
      "GitHub installation token response did not include a token",
    );
  }
  return body.token;
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

function createAppJwt(credentials) {
  const now = Math.floor(Date.now() / 1000);
  const header = base64UrlJson({ alg: "RS256", typ: "JWT" });
  const payload = base64UrlJson({
    iat: now - 60,
    exp: now + 10 * 60,
    iss: credentials.appId,
  });
  const unsignedToken = `${header}.${payload}`;
  const signature = crypto.sign("RSA-SHA256", Buffer.from(unsignedToken), credentials.privateKey);

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
