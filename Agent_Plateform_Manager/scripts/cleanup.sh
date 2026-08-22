#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

set +e
gcloud compute forwarding-rules delete arm-stage1-http --global --quiet
gcloud compute target-http-proxies delete arm-stage1-proxy --quiet
gcloud compute url-maps delete arm-stage1-map --quiet
gcloud compute backend-services delete arm-stage1-backend --global --quiet
gcloud compute health-checks delete arm-stage1-hc --quiet
gcloud compute firewall-rules delete arm-stage1-allow-gfe --quiet
gcloud compute instances delete "$CONTROLLER_VM" --zone "$ZONE" --quiet
gcloud compute instance-groups managed delete "$MIG_NAME" --region "$REGION" --quiet
gcloud compute instance-templates delete "$MIG_TEMPLATE" --quiet
gcloud container clusters delete "$GKE_CLUSTER" --region "$REGION" --quiet
set -e

echo "Runtime resources removed. Artifact Registry and service accounts were intentionally kept."
