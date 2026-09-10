import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  createInstallationToken,
  drainQueue,
  processClaimWave,
  unresolvedAcknowledgments,
} from "./drain_queue.mjs";

function claim(itemKey, attempts = 0) {
  return {
    itemKey,
    claimGeneration: 1,
    repository: "example",
    prNumber: 1,
    attempts,
  };
}

test("stops without claiming another wave when the queue is empty", async () => {
  let claimed = false;
  let processed = false;
  const result = await drainQueue({
    initialClaims: [],
    processingDeadlineEpochSeconds: 100,
    claimWave: async () => {
      claimed = true;
      return [];
    },
    processWave: async () => {
      processed = true;
    },
    now: () => 0,
  });

  assert.deepEqual(
    result,
    { claims: 0, deadLetters: 0, reason: "queue_empty", waves: 0 },
  );
  assert.equal(claimed, false);
  assert.equal(processed, false);
});

test("continues through bounded waves until a claim is empty", async () => {
  const processed = [];
  const queued = [[claim("example#pr:2")], []];
  const result = await drainQueue({
    initialClaims: [claim("example#pr:1")],
    processingDeadlineEpochSeconds: 1_000,
    claimWave: async () => queued.shift(),
    processWave: async (claims) => {
      processed.push(claims.map((item) => item.itemKey));
    },
    now: () => 0,
  });

  assert.deepEqual(processed, [["example#pr:1"], ["example#pr:2"]]);
  assert.deepEqual(
    result,
    { claims: 2, deadLetters: 0, reason: "queue_empty", waves: 2 },
  );
});

test("does not claim after the safe deadline", async () => {
  let claimCalls = 0;
  const result = await drainQueue({
    initialClaims: [claim("example#pr:1")],
    processingDeadlineEpochSeconds: 100,
    claimWave: async () => {
      claimCalls += 1;
      return [claim("example#pr:2")];
    },
    processWave: async () => {},
    now: () => 100,
  });

  assert.equal(claimCalls, 0);
  assert.deepEqual(
    result,
    { claims: 1, deadLetters: 0, reason: "deadline", waves: 1 },
  );
});

test("continues after dead letters and excludes retries from later waves", async () => {
  const exclusions = [];
  const queued = [[claim("example#pr:3")], []];
  const result = await drainQueue({
    initialClaims: [claim("example#pr:1"), claim("example#pr:2")],
    processingDeadlineEpochSeconds: 1_000,
    claimWave: async (_wave, excludeItemKeys) => {
      exclusions.push(excludeItemKeys);
      return queued.shift();
    },
    processWave: async (_claims, wave) => (
      wave === 1
        ? { deadLetters: 1, retryItemKeys: ["example#pr:2"] }
        : { deadLetters: 0, retryItemKeys: [] }
    ),
    now: () => 0,
  });

  assert.deepEqual(exclusions, [
    ["example#pr:2"],
    ["example#pr:2"],
  ]);
  assert.deepEqual(
    result,
    { claims: 3, deadLetters: 1, reason: "queue_empty", waves: 2 },
  );
});

test("stops before the retry exclusion request becomes too large", async () => {
  let claimCalls = 0;
  const result = await drainQueue({
    initialClaims: [claim("example#pr:1"), claim("example#pr:2")],
    processingDeadlineEpochSeconds: 1_000,
    maximumExclusions: 2,
    claimWave: async () => {
      claimCalls += 1;
      return [];
    },
    processWave: async () => ({
      deadLetters: 0,
      retryItemKeys: ["example#pr:1", "example#pr:2"],
    }),
    now: () => 0,
  });

  assert.equal(claimCalls, 0);
  assert.deepEqual(
    result,
    { claims: 2, deadLetters: 0, reason: "exclusion_limit", waves: 1 },
  );
});

test("retries only claims left undecided by a failed wave", async () => {
  const claims = [
    claim("example#pr:1"),
    claim("example#pr:2"),
    claim("example#pr:3", 2),
  ];
  const results = [{
    itemKey: "example#pr:1",
    claimGeneration: 1,
    outcome: "success",
  }];

  assert.deepEqual(
    unresolvedAcknowledgments(claims, results, new Error("processor stopped")),
    [
      {
        itemKey: "example#pr:2",
        claimGeneration: 1,
        outcome: "retry",
        error: "processor stopped",
        retryAfterMs: 0,
      },
      {
        itemKey: "example#pr:3",
        claimGeneration: 1,
        outcome: "dead",
        error: "processor stopped",
        retryAfterMs: 0,
      },
    ],
  );
});

test("acknowledges unprocessed claims when token creation fails", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "drain-queue-test-"));
  try {
    const claims = [claim("example#pr:1", 2)];
    const claimsPath = path.join(directory, "claims.json");
    const resultsPath = path.join(directory, "results.json");
    await fs.writeFile(claimsPath, JSON.stringify(claims));
    let acknowledged;

    await assert.rejects(
      processClaimWave({
        claims,
        claimsPath,
        resultsPath,
        generation: 1,
        workerId: "worker",
        queueEndpoint: "https://example.test/queue",
        clientId: "client",
        privateKey: "key",
        createToken: async () => {
          throw new Error("token failed");
        },
        runCommand: async (_command, args) => {
          assert.equal(args.includes("acknowledge"), true);
          acknowledged = JSON.parse(await fs.readFile(resultsPath, "utf8"));
        },
      }),
      /token failed/,
    );

    assert.equal(acknowledged[0].outcome, "retry");
    assert.equal(acknowledged[0].error, "token failed");
  } finally {
    await fs.rm(directory, { recursive: true });
  }
});

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
  const token = await createInstallationToken({
    clientId: "client-id",
    privateKey,
    repositories: ["repo-a", "repo-b"],
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return responses.shift();
    },
    now: () => 1_000,
  });

  assert.equal(token.token, "wave-token");
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
  await token.revoke();
  assert.equal(requests[2].url, "https://api.github.com/installation/token");
  assert.equal(requests[2].options.method, "DELETE");
});
