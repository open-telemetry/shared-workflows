#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 || ( $# -eq 3 && $3 != --secret ) ]]; then
  echo "Usage: $0 KEY VALUE [--secret]" >&2
  exit 2
fi

key=$1
value=$2
secret_flag=()
if [[ $# -eq 3 ]]; then
  secret_flag=(--secret)
fi

output=$(mktemp)
trap 'rm -f "$output"' EXIT

set_env() {
  npx --yes netlify-cli@26.0.2 env:set "$key" "$value" \
    --context production "$@" \
    --site "$NETLIFY_SITE_ID" \
    --auth "$NETLIFY_AUTH_TOKEN" \
    "${secret_flag[@]}" --force >"$output" 2>&1
}

if set_env --scope functions; then
  exit 0
fi

if ! grep -Fq 'Setting the context and scope at the same time on an existing env var is not allowed. Run the set command separately for each update.' "$output"; then
  echo "Netlify env:set failed for $key" >&2
  exit 1
fi

if ! set_env; then
  echo "Netlify env:set failed while updating the production context for $key" >&2
  exit 1
fi
