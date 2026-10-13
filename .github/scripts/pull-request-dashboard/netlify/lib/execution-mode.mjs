export function executionMode() {
  const mode = process.env.PR_DASHBOARD_EXECUTION_MODE;
  if (!["paused", "owned"].includes(mode)) {
    throw new Error(`invalid PR_DASHBOARD_EXECUTION_MODE: ${mode}`);
  }
  return mode;
}
