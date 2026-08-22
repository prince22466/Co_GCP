#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

SYSTEM="${1:-}"
if [[ "$SYSTEM" != "gke" && "$SYSTEM" != "agent" ]]; then
  echo "Usage: $0 gke|agent" >&2
  exit 2
fi

python3 - <<'PY'
try:
    import aiohttp
except ImportError:
    raise SystemExit("aiohttp is missing. Run: python3 -m pip install -r loadgen/requirements.txt")
PY

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="results/${SYSTEM}/${RUN_ID}"
mkdir -p "$OUT"

if [[ "$SYSTEM" == "gke" ]]; then
  [[ -f .state/gke_url ]] || { echo "Deploy GKE first: ./scripts/deploy_gke.sh" >&2; exit 1; }
  TARGET="$(cat .state/gke_url)"
  gcloud container clusters get-credentials "$GKE_CLUSTER" --region "$REGION" >/dev/null
  kubectl -n "$GKE_NAMESPACE" scale deployment "$GKE_DEPLOYMENT" --replicas=1 >/dev/null
  OBS_ARGS=(--system gke --namespace "$GKE_NAMESPACE" --deployment "$GKE_DEPLOYMENT")
else
  [[ -f .state/mig_url ]] || { echo "Deploy MIG first: ./scripts/deploy_mig.sh" >&2; exit 1; }
  TARGET="$(cat .state/mig_url)"
  gcloud compute instance-groups managed resize "$MIG_NAME" --region "$REGION" --size "$MIG_MIN" >/dev/null
  OBS_ARGS=(--system mig --project "$PROJECT_ID" --region "$REGION" --mig "$MIG_NAME")
fi

TARGET="${TARGET%/}/work?cpu_ms=${WORK_CPU_MS}"
echo "System: $SYSTEM"
echo "Target: $TARGET"
echo "Resetting to one 2-vCPU resource unit; warm-up ${WARMUP_SECONDS}s..."
sleep "$WARMUP_SECONDS"

python3 scripts/observe.py "${OBS_ARGS[@]}" \
  --unit-vcpu "$UNIT_VCPU" --unit-gib "$UNIT_GIB" \
  --output "$OUT/resources.csv" &
OBS_PID=$!
trap 'kill "$OBS_PID" 2>/dev/null || true' EXIT

python3 loadgen/run.py \
  --target "$TARGET" \
  --trace "$TRACE" \
  --output-dir "$OUT"

kill "$OBS_PID" 2>/dev/null || true
wait "$OBS_PID" 2>/dev/null || true
trap - EXIT

echo "$OUT" > ".state/last_${SYSTEM}_result"
echo "Result: $OUT"
