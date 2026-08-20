import {
  createRemoteJWKSet,
  jwtVerify,
} from "jose";

export const GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com";
export const QUEUE_OIDC_AUDIENCE = "otel-pr-dashboard-queue";
export const EXPECTED_REPOSITORY = "open-telemetry/shared-workflows";
export const EXPECTED_REF = "refs/heads/main";
export const EXPECTED_ENVIRONMENT = "protected";
export const EXPECTED_WORKFLOW_REF =
  "open-telemetry/shared-workflows/.github/workflows/pull-request-dashboard-drain.yml@refs/heads/main";

const githubKeys = createRemoteJWKSet(
  new URL(`${GITHUB_OIDC_ISSUER}/.well-known/jwks`),
);

export async function verifyGitHubOidcRequest(
  request,
  options = {},
) {
  const authorization = request.headers.get("authorization") || "";
  if (!authorization.startsWith("Bearer ")) {
    throw oidcError("missing bearer token");
  }
  return verifyGitHubOidcToken(authorization.slice("Bearer ".length), options);
}

export async function verifyGitHubOidcToken(
  token,
  {
    keySet = githubKeys,
    audience = process.env.PR_DASHBOARD_QUEUE_OIDC_AUDIENCE ||
      QUEUE_OIDC_AUDIENCE,
    repository = EXPECTED_REPOSITORY,
    ref = EXPECTED_REF,
    environment = EXPECTED_ENVIRONMENT,
    workflowRef = EXPECTED_WORKFLOW_REF,
  } = {},
) {
  let payload;
  try {
    ({ payload } = await jwtVerify(token, keySet, {
      issuer: GITHUB_OIDC_ISSUER,
      audience,
      algorithms: ["RS256"],
    }));
  } catch (error) {
    throw oidcError(`invalid GitHub OIDC token: ${error.code || error.message}`);
  }

  const expectedClaims = {
    repository,
    ref,
    environment,
    workflow_ref: workflowRef,
  };
  for (const [claim, expected] of Object.entries(expectedClaims)) {
    if (payload[claim] !== expected) {
      throw oidcError(`GitHub OIDC claim ${claim} is not authorized`);
    }
  }
  return payload;
}

function oidcError(message) {
  const error = new Error(message);
  error.statusCode = 401;
  error.publicMessage = "unauthorized";
  return error;
}
