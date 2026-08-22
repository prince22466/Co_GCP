#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

[[ -f .state/last_gke_result ]] || { echo "No GKE result yet." >&2; exit 1; }
[[ -f .state/last_agent_result ]] || { echo "No agent result yet." >&2; exit 1; }
GKE="$(cat .state/last_gke_result)"
AGENT="$(cat .state/last_agent_result)"

ARGS=(--gke "$GKE" --agent "$AGENT")
if [[ -f pricing.json ]]; then
  ARGS+=(--pricing pricing.json)
fi
python3 scripts/compare.py "${ARGS[@]}"
