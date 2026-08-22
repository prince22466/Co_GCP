#!/bin/bash
set -euxo pipefail

REGISTRY_HOST="${REGION}-docker.pkg.dev"
IMAGE="${AGENT_IMAGE}"
TOKEN="$(curl -fsS -H 'Metadata-Flavor: Google' \
  'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token' \
  | awk -F'"' '/access_token/{print $4}')"

echo "$TOKEN" | docker login -u oauth2accesstoken --password-stdin "$REGISTRY_HOST"
docker rm -f arm-controller || true
docker run -d --name arm-controller --restart=always \
  -e PROJECT_ID='${PROJECT_ID}' \
  -e REGION='${REGION}' \
  -e MIG_NAME='${MIG_NAME}' \
  -e MIG_MIN='${MIG_MIN}' \
  -e MIG_MAX='${MIG_MAX}' \
  -e SCALE_UP_CPU='${SCALE_UP_CPU}' \
  -e SCALE_DOWN_CPU='${SCALE_DOWN_CPU}' \
  -e DECISION_INTERVAL='${DECISION_INTERVAL}' \
  -e COOLDOWN_SECONDS='${COOLDOWN_SECONDS}' \
  -e CPU_LOOKBACK_SECONDS='${CPU_LOOKBACK_SECONDS}' \
  "$IMAGE"
