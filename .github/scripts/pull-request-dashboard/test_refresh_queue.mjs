import assert from "node:assert/strict";
import test from "node:test";

import {
  isRefreshDue,
  isRefreshExpired,
  parseRefreshQueueKey,
  refreshQueueKey,
} from "./netlify/lib/refresh-queue.mjs";

const QUIET_MS = 45_000;
const EXPIRY_MS = 24 * 60 * 60 * 1000;
const NOW = 1_000_000_000;

test("round-trips a repository and pull request number", () => {
  const key = refreshQueueKey("opentelemetry-collector-contrib", 49832);
  assert.equal(key, "opentelemetry-collector-contrib/49832");
  assert.deepEqual(parseRefreshQueueKey(key), {
    repository: "opentelemetry-collector-contrib",
    prNumber: "49832",
  });
});

test("rejects keys without a usable pull request number", () => {
  assert.equal(parseRefreshQueueKey("opentelemetry-java"), undefined);
  assert.equal(parseRefreshQueueKey("/123"), undefined);
  assert.equal(parseRefreshQueueKey("weaver/0"), undefined);
  assert.equal(parseRefreshQueueKey("weaver/abc"), undefined);
  assert.equal(parseRefreshQueueKey(undefined), undefined);
});

test("holds a refresh while events are still arriving", () => {
  const queued = { lastEventAt: NOW - 10_000, lastDispatchAt: 0 };
  assert.equal(isRefreshDue(queued, NOW, QUIET_MS), false);
});

test("dispatches once the pull request has gone quiet", () => {
  const queued = { lastEventAt: NOW - QUIET_MS, lastDispatchAt: 0 };
  assert.equal(isRefreshDue(queued, NOW, QUIET_MS), true);
});

test("does not redispatch when nothing arrived since the last dispatch", () => {
  const queued = { lastEventAt: NOW - 100_000, lastDispatchAt: NOW - 100_000 };
  assert.equal(isRefreshDue(queued, NOW, QUIET_MS), false);
});

test("dispatches again for events that arrived after the last dispatch", () => {
  const queued = { lastEventAt: NOW - QUIET_MS, lastDispatchAt: NOW - 200_000 };
  assert.equal(isRefreshDue(queued, NOW, QUIET_MS), true);
});

test("treats records without a last event time as expired", () => {
  assert.equal(isRefreshDue({}, NOW, QUIET_MS), false);
  assert.equal(isRefreshExpired(undefined, NOW, EXPIRY_MS), true);
  assert.equal(isRefreshExpired({}, NOW, EXPIRY_MS), true);
});

test("expires records only after the retention window", () => {
  assert.equal(isRefreshExpired({ lastEventAt: NOW - EXPIRY_MS }, NOW, EXPIRY_MS), false);
  assert.equal(isRefreshExpired({ lastEventAt: NOW - EXPIRY_MS - 1 }, NOW, EXPIRY_MS), true);
});
