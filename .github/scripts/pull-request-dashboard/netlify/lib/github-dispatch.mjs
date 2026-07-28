import crypto from "node:crypto";

const GITHUB_API_VERSION = "2022-11-28";
const OWNER = "open-telemetry";
const WORKFLOW_REPOSITORY = "shared-workflows";
const WORKFLOW_ID = "pull-request-dashboard.yml";
const WORKFLOW_REF = "main";

// Mints one installation token and reuses it for every dispatch in a sweep.
export async function createWorkflowDispatcher() {
  const appId = process.env.OTELBOT_SHARED_WORKFLOWS_APP_ID;
  const privateKey = normalizePrivateKey(
    process.env.OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY,
    process.env.OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY_BASE64,
  );
  if (!appId || !privateKey) {
    throw new Error(
      "missing required configuration: OTELBOT_SHARED_WORKFLOWS_APP_ID, OTELBOT_SHARED_WORKFLOWS_PRIVATE_KEY",
    );
  }

  const jwt = createAppJwt({ appId, privateKey });
  const installationId = await findRepositoryInstallationId(jwt, `${OWNER}/${WORKFLOW_REPOSITORY}`);
  const token = await createInstallationToken(jwt, installationId);

  return (inputs) => dispatchWorkflow(token, inputs);
}

async function findRepositoryInstallationId(jwt, repository) {
  const body = await githubJson(
    `https://api.github.com/repos/${encodeRepository(repository)}/installation`,
    jwt,
  );
  if (!body || !body.id) {
    throw new Error(`GitHub installation response did not include id for ${repository}`);
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
    throw new Error("GitHub installation token response did not include a token");
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
    throw new Error(`GitHub API request failed: ${response.status} ${response.statusText}: ${body}`);
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
