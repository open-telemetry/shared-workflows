import crypto from "node:crypto";
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const GITHUB_API_VERSION = "2022-11-28";
const OWNER = "open-telemetry";
const WAVE_LIMIT = 16;
const MAX_ATTEMPTS = 3;
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));

export async function drainQueue({
  initialClaims,
  processingDeadlineEpochSeconds,
  claimWave,
  processWave,
  now = () => Date.now() / 1000,
  minimumWaveSeconds = 10 * 60,
  waveDurationMultiplier = 1.5,
  maximumExclusions = 500,
}) {
  let claims = initialClaims;
  let waves = 0;
  let claimCount = 0;
  let deadLetters = 0;
  let maximumWaveSeconds = 0;
  const retryItemKeys = new Set();

  while (claims.length > 0) {
    const startedAt = now();
    const waveResult = await processWave(claims, waves + 1);
    const finishedAt = now();
    waves += 1;
    claimCount += claims.length;
    deadLetters += waveResult?.deadLetters || 0;
    for (const itemKey of waveResult?.retryItemKeys || []) {
      retryItemKeys.add(itemKey);
    }
    if (retryItemKeys.size >= maximumExclusions) {
      return {
        claims: claimCount,
        deadLetters,
        reason: "exclusion_limit",
        waves,
      };
    }

    maximumWaveSeconds = Math.max(maximumWaveSeconds, finishedAt - startedAt);
    const nextWaveSeconds = Math.max(
      minimumWaveSeconds,
      maximumWaveSeconds * waveDurationMultiplier,
    );
    if (finishedAt + nextWaveSeconds >= processingDeadlineEpochSeconds) {
      return { claims: claimCount, deadLetters, reason: "deadline", waves };
    }
    claims = await claimWave(waves + 1, [...retryItemKeys]);
  }
  return { claims: claimCount, deadLetters, reason: "queue_empty", waves };
}

export async function processClaimWave({
  claims,
  claimsPath,
  resultsPath,
  generation,
  workerId,
  queueEndpoint,
  clientId,
  privateKey,
  childEnv = childProcessEnvironment(),
  runCommand = defaultRunCommand,
  createToken = createInstallationToken,
  reportWarning = (message) => console.log(`::warning::${message}`),
}) {
  const repositories = [...new Set(claims.map((claim) => claim.repository))].sort();
  let installationToken;
  try {
    installationToken = await createToken({
      clientId,
      privateKey,
      repositories,
    });
    console.log(`::add-mask::${installationToken.token}`);
    await runCommand(
      "python3",
      [
        path.join(SCRIPT_DIR, "process_queue_batch.py"),
        "--claims",
        claimsPath,
        "--results",
        resultsPath,
        "--max-repositories",
        "4",
        "--queue-endpoint",
        queueEndpoint,
        "--dispatcher-generation",
        String(generation),
        "--worker-id",
        workerId,
        "--continue-after-dead-letters",
      ],
      {
        ...childEnv,
        GH_TOKEN: installationToken.token,
        PR_DASHBOARD_TOKEN: installationToken.token,
      },
    );
  } catch (error) {
    let results = [];
    try {
      results = JSON.parse(await fs.readFile(resultsPath, "utf8"));
    } catch (readError) {
      if (readError.code !== "ENOENT") {
        throw new AggregateError(
          [error, readError],
          "queue wave failed and its partial results could not be read",
        );
      }
    }
    const unresolved = unresolvedAcknowledgments(
      claims,
      results,
      error,
      { allowDeadLetters: installationToken !== undefined },
    );
    if (unresolved.length > 0) {
      await fs.writeFile(resultsPath, `${JSON.stringify(unresolved, null, 2)}\n`);
      try {
        await acknowledgeResults({
          resultsPath,
          generation,
          workerId,
          queueEndpoint,
          childEnv,
          runCommand,
        });
      } catch (acknowledgmentError) {
        throw new AggregateError(
          [error, acknowledgmentError],
          "queue wave failed and unresolved claims could not be acknowledged",
        );
      }
    }
    throw error;
  } finally {
    if (installationToken) {
      try {
        await runCommand(
          "python3",
          [path.join(SCRIPT_DIR, "report_rate_limits.py")],
          { ...childEnv, GH_TOKEN: installationToken.token },
        );
      } catch (error) {
        reportWarning(`GitHub App rate-limit reporting failed: ${error.message}`);
      }
      try {
        await installationToken.revoke();
      } catch (error) {
        reportWarning(`GitHub App token revocation failed: ${error.message}`);
      }
    }
  }
  const results = JSON.parse(await fs.readFile(resultsPath, "utf8"));
  return {
    deadLetters: results.filter((result) => result.outcome === "dead").length,
    retryItemKeys: results
      .filter((result) => result.outcome === "retry")
      .map((result) => result.itemKey),
  };
}

export function unresolvedAcknowledgments(
  claims,
  results,
  error,
  { allowDeadLetters = true } = {},
) {
  const resolvedKeys = new Set(
    Array.isArray(results)
      ? results
        .filter((result) => result && typeof result.itemKey === "string")
        .map((result) => result.itemKey)
      : [],
  );
  return claims
    .filter((claim) => !resolvedKeys.has(claim.itemKey))
    .map((claim) => ({
      itemKey: claim.itemKey,
      claimGeneration: claim.claimGeneration,
      outcome: allowDeadLetters && (claim.attempts || 0) + 1 >= MAX_ATTEMPTS
        ? "dead"
        : "retry",
      error: String(error.message || error).slice(0, 1000),
      retryAfterMs: 0,
    }));
}

async function acknowledgeResults({
  resultsPath,
  generation,
  workerId,
  queueEndpoint,
  childEnv,
  runCommand,
}) {
  await runCommand("python3", [
    path.join(SCRIPT_DIR, "queue_worker_client.py"),
    "--endpoint",
    queueEndpoint,
    "--generation",
    String(generation),
    "--worker-id",
    workerId,
    "acknowledge",
    "--results",
    resultsPath,
  ], childEnv);
}

async function claimWave({
  claimsPath,
  generation,
  workerId,
  queueEndpoint,
  excludeItemKeys,
  childEnv,
  runCommand = defaultRunCommand,
}) {
  const args = [
    path.join(SCRIPT_DIR, "queue_worker_client.py"),
    "--endpoint",
    queueEndpoint,
    "--generation",
    String(generation),
    "--worker-id",
    workerId,
    "claim",
    "--limit",
    String(WAVE_LIMIT),
    "--output",
    claimsPath,
  ];
  for (const itemKey of excludeItemKeys) {
    args.push("--exclude-item-key", itemKey);
  }
  await runCommand("python3", args, childEnv);
  return JSON.parse(await fs.readFile(claimsPath, "utf8"));
}

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
  if (repositories.length === 0) {
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
  return {
    token: access.token,
    revoke: async () => {
      await githubJson(
        "https://api.github.com/installation/token",
        access.token,
        { method: "DELETE" },
        fetchImpl,
      );
    },
  };
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

function childProcessEnvironment() {
  const {
    PR_DASHBOARD_CLIENT_ID: _clientId,
    PR_DASHBOARD_PRIVATE_KEY: _privateKey,
    ...childEnv
  } = process.env;
  return childEnv;
}

function defaultRunCommand(command, args, env = childProcessEnvironment()) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { env, stdio: "inherit" });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(
          signal
            ? `${command} terminated by ${signal}`
            : `${command} exited with code ${code}`,
        ));
      }
    });
  });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const initialClaims = JSON.parse(await fs.readFile(args.claims, "utf8"));
  const clientId = process.env.PR_DASHBOARD_CLIENT_ID;
  const privateKey = process.env.PR_DASHBOARD_PRIVATE_KEY;
  const childEnv = childProcessEnvironment();
  const temporaryDirectory = await fs.mkdtemp(path.join(os.tmpdir(), "dashboard-waves-"));
  try {
    const result = await drainQueue({
      initialClaims,
      processingDeadlineEpochSeconds: Number(args.deadline),
      claimWave: async (wave, excludeItemKeys) => {
        const claimsPath = path.join(temporaryDirectory, `claims-${wave}.json`);
        return claimWave({
          claimsPath,
          generation: args.generation,
          workerId: args.worker,
          queueEndpoint: args.endpoint,
          excludeItemKeys,
          childEnv,
        });
      },
      processWave: async (claims, wave) => {
        const claimsPath = wave === 1
          ? args.claims
          : path.join(temporaryDirectory, `claims-${wave}.json`);
        return processClaimWave({
          claims,
          claimsPath,
          resultsPath: path.join(temporaryDirectory, `results-${wave}.json`),
          generation: args.generation,
          workerId: args.worker,
          queueEndpoint: args.endpoint,
          clientId,
          privateKey,
          childEnv,
        });
      },
    });
    console.log(JSON.stringify(result));
    if (result.deadLetters > 0) {
      process.exitCode = 1;
    }
  } finally {
    await fs.rm(temporaryDirectory, { recursive: true });
  }
}

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 2) {
    const name = argv[index];
    const value = argv[index + 1];
    if (!name?.startsWith("--") || value === undefined) {
      throw new Error(`invalid argument: ${name || ""}`);
    }
    args[name.slice(2)] = value;
  }
  for (const name of ["claims", "deadline", "generation", "worker", "endpoint"]) {
    if (!args[name]) {
      throw new Error(`--${name} is required`);
    }
  }
  if (!Number.isInteger(Number(args.deadline)) || Number(args.deadline) < 1) {
    throw new Error("--deadline must be a positive integer");
  }
  return args;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(`::error::${error.stack || error}`);
    process.exitCode = 1;
  });
}
