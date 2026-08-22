#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Missing .env. Copy .env.example to .env and set PROJECT_ID." >&2
  exit 1
fi
# shellcheck disable=SC1091
source .env

: "${PROJECT_ID:?PROJECT_ID is required}"
: "${REGION:?REGION is required}"
: "${ZONE:?ZONE is required}"
: "${AR_REPO:?AR_REPO is required}"

APP_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${APP_IMAGE_NAME}:stage1"
AGENT_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${AGENT_IMAGE_NAME}:stage1"
export ROOT APP_IMAGE AGENT_IMAGE

gcloud config set project "$PROJECT_ID" >/dev/null
mkdir -p .state results
