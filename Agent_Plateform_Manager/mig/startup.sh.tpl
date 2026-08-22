#!/bin/bash
set -euxo pipefail

REGISTRY_HOST="${REGION}-docker.pkg.dev"
IMAGE="${APP_IMAGE}"
TOKEN="$(curl -fsS -H 'Metadata-Flavor: Google' \
  'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token' \
  | awk -F'"' '/access_token/{print $4}')"

echo "$TOKEN" | docker login -u oauth2accesstoken --password-stdin "$REGISTRY_HOST"
docker rm -f arm-web || true
docker run -d --name arm-web --restart=always \
  -p 8080:8080 \
  -e WEB_CONCURRENCY=2 \
  -e DEFAULT_CPU_MS=8 \
  "$IMAGE"
