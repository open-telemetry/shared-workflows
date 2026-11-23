#!/usr/bin/env bash
set -euo pipefail

fail() { echo "Sandbox check failed: $1" >&2; exit 1; }

checkout=$1
inputs=$2
host_config=$3
shift 3
test ! -S /var/run/docker.sock || fail 'Docker socket is visible'
test -z "${GH_TOKEN:-}${GITHUB_TOKEN:-}${ACTIONS_RUNTIME_TOKEN:-}${ACTIONS_ID_TOKEN_REQUEST_TOKEN:-}" ||
  fail 'Actions credential is visible'
test "${COPILOT_GITHUB_TOKEN:-}" = ghu_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ||
  fail 'Copilot proxy token not isolated'
test ! -e "$host_config" || fail 'Host configuration is visible'
test -d "$checkout/.git" || fail 'Expected checkout is not available'
test ! -w "$inputs" && test -r "$inputs/prompt.txt" || fail 'Audit inputs are not read-only'
echo '[instruction-audit] Verified credential isolation' >&2

copilot=$1
test -w "$HOME" || fail 'Copilot home is not writable'
"$copilot" --version >/dev/null || fail 'Copilot CLI version check failed'
echo '[instruction-audit] Copilot binary executed' >&2
exec "$@" -C "$inputs" -p "$(cat "$inputs/prompt.txt")
