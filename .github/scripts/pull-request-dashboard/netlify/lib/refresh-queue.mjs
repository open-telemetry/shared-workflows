// The key format is shared with the scheduled flush function and must match
// exactly. A slash separates the parts because a hash is truncated as a URL
// fragment when the key is used in a Netlify Blobs request path.
export const REFRESH_QUEUE_STORE = "dashboard-refresh-queue";

export function refreshQueueKey(repository, prNumber) {
  return `${repository}/${prNumber}`;
}

export function parseRefreshQueueKey(key) {
  const separator = typeof key === "string" ? key.lastIndexOf("/") : -1;
  if (separator <= 0) {
    return undefined;
  }
  const repository = key.slice(0, separator);
  const prNumber = key.slice(separator + 1);
  if (!/^[1-9][0-9]*$/.test(prNumber)) {
    return undefined;
  }
  return { repository, prNumber };
}

// A refresh is owed when events have arrived since the last dispatch, and the
// pull request has since gone quiet long enough for the burst to have settled.
export function isRefreshDue(queued, now, quietMs) {
  if (!queued || !Number.isFinite(queued.lastEventAt)) {
    return false;
  }
  if ((queued.lastDispatchAt || 0) >= queued.lastEventAt) {
    return false;
  }
  return now - queued.lastEventAt >= quietMs;
}

export function isRefreshExpired(queued, now, expiryMs) {
  if (!queued || !Number.isFinite(queued.lastEventAt)) {
    return true;
  }
  return now - queued.lastEventAt > expiryMs;
}
