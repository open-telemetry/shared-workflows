import crypto from "node:crypto";

const GITHUB_API_VERSION = "2022-11-28";
const OWNER = "open-telemetry";

export async function createInstallationToken({
  clientId,
  privateKey,
  repositories,
  fetchImpl = fetch,
  now = () => Math.floor(Date.now() / 1000),
}) {
  if (!clientId || !privateKey) {
    throw new Error("GitHub App credentials are missing");
  }
  if (!Array.isArray(repositories) || repositories.length === 0) {
    throw new Error("cannot create a GitHub App token without repositories");
  }
  const appJwt = createAppJwt(clientId, privateKey, now());
  const installation = await githubJson(
    `https://api.github.com/orgs/${OWNER}/installation`,
    appJwt,
    {},
    fetchImpl,
  );
  if (!Number.isInteger(installation?.id)) {
    throw new Error("GitHub App installation response did not include an id");
  }
  const access = await githubJson(
    `https://api.github.com/app/installations/${installation.id}/access_tokens`,
    appJwt,
    {
      method: "POST",
      body: JSON.stringify({
        repositories,
        permissions: {
          checks: "read",
          contents: "read",
          issues: "write",
          members: "read",
          pull_requests: "write",
          statuses: "read",
        },
      }),
    },
    fetchImpl,
  );
  if (typeof access?.token !== "string" || !access.token) {
    throw new Error("GitHub App installation token response did not include a token");
  }
  return access.token;
}

export async function revokeInstallationToken(token, fetchImpl = fetch) {
  if (!token) {
    throw new Error("GitHub App installation token is missing");
  }
  await githubJson(
    "https://api.github.com/installation/token",
    token,
    { method: "DELETE" },
    fetchImpl,
  );
}

function createAppJwt(clientId, privateKey, now) {
  const header = Buffer.from(JSON.stringify({ alg: "RS256", typ: "JWT" })).toString(
    "base64url",
  );
  const payload = Buffer.from(JSON.stringify({
    iat: now - 60,
    exp: now + 10 * 60,
    iss: clientId,
  })).toString("base64url");
  const normalizedKey = privateKey
    .trim()
    .replace(/^['"]|['"]$/g, "")
    .replace(/\\n/g, "\n");
  const signature = crypto.sign(
    "RSA-SHA256",
    Buffer.from(`${header}.${payload}`),
    normalizedKey,
  );
  return `${header}.${payload}.${signature.toString("base64url")}`;
}

async function githubJson(url, token, options, fetchImpl) {
  const response = await fetchImpl(url, {
    ...options,
    headers: {
      accept: "application/vnd.github+json",
      "content-type": "application/json",
      "user-agent": "pull-request-dashboard-queue-drain",
      "x-github-api-version": GITHUB_API_VERSION,
      authorization: `Bearer ${token}`,
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(
      `GitHub API request failed: ${response.status} ${response.statusText}: ${body}`,
    );
  }
  return response.status === 204 ? null : response.json();
}

async function readInput() {
  let input = "";
  for await (const chunk of process.stdin) {
    input += chunk;
  }
  return JSON.parse(input);
}

async function main() {
  const input = await readInput();
  if (input.operation === "mint") {
    const token = await createInstallationToken(input);
    process.stdout.write(`${JSON.stringify({ token })}\n`);
    return;
  }
  if (input.operation === "revoke") {
    await revokeInstallationToken(input.token);
    process.stdout.write('{"revoked":true}\n');
    return;
  }
  throw new Error("operation must be mint or revoke");
}

if (process.argv[1] && import.meta.url === new URL(process.argv[1], "file:").href) {
  main().catch((error) => {
    console.error(error.stack || error);
    process.exitCode = 1;
  });
}
