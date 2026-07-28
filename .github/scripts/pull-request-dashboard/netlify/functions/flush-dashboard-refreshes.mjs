import { getStore } from "@netlify/blobs";

import { createWorkflowDispatcher } from "../lib/github-dispatch.mjs";
import {
  REFRESH_QUEUE_STORE,
  isRefreshDue,
  isRefreshExpired,
  parseRefreshQueueKey,
} from "../lib/refresh-queue.mjs";

// A burst of webhooks for one pull request collapses into a single refresh once
// the pull request has been quiet this long.
const QUIET_MS = 45_000;
const EXPIRY_MS = 24 * 60 * 60 * 1000;

export const config = { schedule: "* * * * *" };

export default async () => {
  const store = getStore(REFRESH_QUEUE_STORE);
  const { blobs } = await store.list();
  const now = Date.now();
  const due = [];

  for (const { key } of blobs) {
    const queued = await store.get(key, { type: "json" });
    if (isRefreshExpired(queued, now, EXPIRY_MS)) {
      await store.delete(key);
      continue;
    }
    const target = parseRefreshQueueKey(key);
    if (target && isRefreshDue(queued, now, QUIET_MS)) {
      due.push({ key, queued, target });
    }
  }

  if (due.length === 0) {
    return;
  }

  const dispatch = await createWorkflowDispatcher();
  for (const { key, queued, target } of due) {
    try {
      await dispatch({
        repository: target.repository,
        pr_number: target.prNumber,
        trigger_event: queued.triggerEvent,
      });
      await store.setJSON(key, { ...queued, lastDispatchAt: Date.now() });
    } catch (error) {
      // Leave lastDispatchAt untouched so the next sweep retries this entry.
      console.error(`failed to dispatch dashboard refresh for ${key}`, error);
    }
  }
};
