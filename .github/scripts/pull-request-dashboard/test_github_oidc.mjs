import assert from "node:assert/strict";
import test from "node:test";

import {
  createLocalJWKSet,
  exportJWK,
  generateKeyPair,
  SignJWT,
} from "jose";

import {
  EXPECTED_ENVIRONMENT,
  EXPECTED_REF,
  EXPECTED_REPOSITORY,
  EXPECTED_WORKFLOW_REF,
  ENQUEUE_WORKFLOW_REFS,
  GITHUB_OIDC_ISSUER,
  QUEUE_OIDC_AUDIENCE,
  verifyGitHubOidcToken,
} from "./netlify/lib/github-oidc.mjs";

async function tokenFixture(overrides = {}) {
  const { privateKey, publicKey } = await generateKeyPair("RS256");
  const kid = "test-key";
  const jwk = {
    ...await exportJWK(publicKey),
    alg: "RS256",
    kid,
    use: "sig",
  };
  const now = Math.floor(Date.now() / 1000);
  const claims = {
    repository: EXPECTED_REPOSITORY,
    ref: EXPECTED_REF,
    environment: EXPECTED_ENVIRONMENT,
    workflow_ref: EXPECTED_WORKFLOW_REF,
    ...overrides,
  };
  const token = await new SignJWT(claims)
    .setProtectedHeader({ alg: "RS256", kid })
    .setIssuer(GITHUB_OIDC_ISSUER)
    .setAudience(QUEUE_OIDC_AUDIENCE)
    .setIssuedAt(now)
    .setExpirationTime(now + 300)
    .sign(privateKey);
  return {
    token,
    keySet: createLocalJWKSet({ keys: [jwk] }),
  };
}

test("accepts the exact drain workflow identity", async () => {
  const { token, keySet } = await tokenFixture();
  const claims = await verifyGitHubOidcToken(token, { keySet });
  assert.equal(claims.repository, EXPECTED_REPOSITORY);
});

test("enqueue identity can read and accept work but cannot own a worker lease", async () => {
  const { token, keySet } = await tokenFixture({ workflow_ref: ENQUEUE_WORKFLOW_REFS[0] });
  await verifyGitHubOidcToken(token, {
    keySet, workflowRef: [EXPECTED_WORKFLOW_REF, ...ENQUEUE_WORKFLOW_REFS],
  });
  await assert.rejects(verifyGitHubOidcToken(token, { keySet }), /workflow_ref/);
});

for (const [claim, value] of [
  ["repository", "open-telemetry/other"],
  ["ref", "refs/heads/feature"],
  ["environment", "other"],
  ["workflow_ref", "open-telemetry/shared-workflows/.github/workflows/other.yml@refs/heads/main"],
]) {
  test(`rejects an unauthorized ${claim} claim`, async () => {
    const { token, keySet } = await tokenFixture({ [claim]: value });
    await assert.rejects(
      verifyGitHubOidcToken(token, { keySet }),
      new RegExp(claim),
    );
  });
}

test("rejects the wrong audience", async () => {
  const { token, keySet } = await tokenFixture();
  await assert.rejects(
    verifyGitHubOidcToken(token, {
      keySet,
      audience: "other-audience",
    }),
    /invalid GitHub OIDC token/,
  );
});
