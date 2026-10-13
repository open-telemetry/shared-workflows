import assert from "node:assert/strict";
import test from "node:test";

import { executionMode } from "./netlify/lib/execution-mode.mjs";

test("execution mode requires an explicit owned or paused operational setting", () => {
  const previous = process.env.PR_DASHBOARD_EXECUTION_MODE;
  try {
    for (const value of ["owned", "paused"]) {
      process.env.PR_DASHBOARD_EXECUTION_MODE = value;
      assert.equal(executionMode(), value);
    }
    for (const value of [undefined, "", "legacy", "invalid"]) {
      if (value === undefined) delete process.env.PR_DASHBOARD_EXECUTION_MODE;
      else process.env.PR_DASHBOARD_EXECUTION_MODE = value;
      assert.throws(executionMode, /invalid PR_DASHBOARD_EXECUTION_MODE/);
    }
  } finally {
    if (previous === undefined) delete process.env.PR_DASHBOARD_EXECUTION_MODE;
    else process.env.PR_DASHBOARD_EXECUTION_MODE = previous;
  }
});
