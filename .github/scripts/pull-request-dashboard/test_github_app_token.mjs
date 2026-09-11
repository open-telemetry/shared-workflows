import assert from "node:assert/strict";
import crypto from "node:crypto";
import test from "node:test";

import {
  createInstallationToken,
  revokeInstallationToken,
} from "./github_app_token.mjs";

test("limits each installation token to the wave repositories and permissions", async () => {
  const { privateKey } = crypto.generateKeyPairSync("rsa", {
    modulusLength: 2048,
    privateKeyEncoding: { type: "pkcs8", format: "pem" },
    publicKeyEncoding: { type: "spki", format: "pem" },
  });
  const requests = [];
  const responses = [
    Response.json({ id: 123 }),
    Response.json({ token: "wave-token" }),
    new Response(null, { status: 204 }),
  ];
  const fetchImpl = async (url, options) => {
    requests.push({ url, options });
    return responses.shift();
  };

  const token = await createInstallationToken({
    clientId: "client-id",
    privateKey,
    repositories: ["repo-a", "repo-b"],
    fetchImpl,
    now: () => 1_000,
  });
  await revokeInstallationToken(token, fetchImpl);

  assert.equal(token, "wave-token");
  assert.deepEqual(JSON.parse(requests[1].options.body), {
    repositories: ["repo-a", "repo-b"],
    permissions: {
      checks: "read",
      contents: "read",
      issues: "write",
      members: "read",
      pull_requests: "write",
      statuses: "read",
    },
  });
  assert.equal(requests[2].url, "https://api.github.com/installation/token");
  assert.equal(requests[2].options.method, "DELETE");
});
