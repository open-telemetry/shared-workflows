#!/usr/bin/env bash
# Print the W3C TRACEPARENT of the current GitHub Actions step, as the
# OpenTelemetry Collector githubreceiver derives it (UseCheckRunID scheme):
#   trace id = sha256("{run_id}{run_attempt}t")[0:32]
#   span id  = sha256("{check_run_id}-{step_name}-s")[16:32]
# Inputs: GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT, OTEL_GITHUB_CHECK_RUN_ID
# (${{ job.check_run_id }}), and OTEL_GITHUB_STEP_NAME (the step's raw name:).
# Prints nothing and exits 0 when an input is missing; it never fails a step.

otel_github_traceparent() {
  local trace_id span_id
  [[ -n "${GITHUB_RUN_ID:-}" && -n "${GITHUB_RUN_ATTEMPT:-}" ]] || return 0
  [[ -n "${OTEL_GITHUB_CHECK_RUN_ID:-}" && -n "${OTEL_GITHUB_STEP_NAME:-}" ]] || return 0
  trace_id=$(printf '%s' "${GITHUB_RUN_ID}${GITHUB_RUN_ATTEMPT}t" | sha256sum | cut -c1-32) || return 0
  span_id=$(printf '%s' "${OTEL_GITHUB_CHECK_RUN_ID}-${OTEL_GITHUB_STEP_NAME}-s" | sha256sum | cut -c17-32) || return 0
  [[ ${#trace_id} -eq 32 && ${#span_id} -eq 16 ]] || return 0
  printf '00-%s-%s-01\n' "$trace_id" "$span_id"
}

otel_github_traceparent 2>/dev/null
exit 0
