#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

APP_SA="arm-stage1-app@${PROJECT_ID}.iam.gserviceaccount.com"
CONTROLLER_SA="arm-stage1-controller@${PROJECT_ID}.iam.gserviceaccount.com"

if ! gcloud iam service-accounts describe "$APP_SA" >/dev/null 2>&1; then
  gcloud iam service-accounts create arm-stage1-app --display-name="ARM Stage1 app VM"
fi
if ! gcloud iam service-accounts describe "$CONTROLLER_SA" >/dev/null 2>&1; then
  gcloud iam service-accounts create arm-stage1-controller --display-name="ARM Stage1 controller"
fi

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${APP_SA}" --role=roles/artifactregistry.reader >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CONTROLLER_SA}" --role=roles/artifactregistry.reader >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CONTROLLER_SA}" --role=roles/monitoring.viewer >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CONTROLLER_SA}" --role=roles/compute.instanceAdmin.v1 >/dev/null

TMP_APP="$(mktemp)"
TMP_CTL="$(mktemp)"
trap 'rm -f "$TMP_APP" "$TMP_CTL"' EXIT
export APP_IMAGE AGENT_IMAGE PROJECT_ID REGION MIG_NAME MIG_MIN MIG_MAX SCALE_UP_CPU SCALE_DOWN_CPU DECISION_INTERVAL COOLDOWN_SECONDS CPU_LOOKBACK_SECONDS
envsubst < mig/startup.sh.tpl > "$TMP_APP"
envsubst < mig/controller-startup.sh.tpl > "$TMP_CTL"

if ! gcloud compute instance-templates describe "$MIG_TEMPLATE" >/dev/null 2>&1; then
  gcloud compute instance-templates create "$MIG_TEMPLATE" \
    --machine-type="$MIG_MACHINE_TYPE" \
    --network="$NETWORK" \
    --tags=arm-stage1-web \
    --service-account="$APP_SA" \
    --scopes=cloud-platform \
    --image-family=cos-stable \
    --image-project=cos-cloud \
    --metadata-from-file=startup-script="$TMP_APP"
fi

if ! gcloud compute instance-groups managed describe "$MIG_NAME" --region "$REGION" >/dev/null 2>&1; then
  gcloud compute instance-groups managed create "$MIG_NAME" \
    --region="$REGION" \
    --template="$MIG_TEMPLATE" \
    --size="$MIG_MIN" \
    --base-instance-name=arm-stage1-web
fi

gcloud compute instance-groups managed set-named-ports "$MIG_NAME" \
  --region="$REGION" --named-ports=http:8080

# Stage 1 intentionally leaves Compute Engine autoscaling disabled: the controller owns target size.
if gcloud compute instance-groups managed describe "$MIG_NAME" --region "$REGION" --format='value(autoscaler)' | grep -q .; then
  gcloud compute instance-groups managed update-autoscaling "$MIG_NAME" --region "$REGION" --mode off || true
fi

if ! gcloud compute firewall-rules describe arm-stage1-allow-gfe >/dev/null 2>&1; then
  gcloud compute firewall-rules create arm-stage1-allow-gfe \
    --network="$NETWORK" \
    --allow=tcp:8080 \
    --source-ranges=130.211.0.0/22,35.191.0.0/16 \
    --target-tags=arm-stage1-web
fi

if ! gcloud compute health-checks describe arm-stage1-hc >/dev/null 2>&1; then
  gcloud compute health-checks create http arm-stage1-hc \
    --port=8080 --request-path=/healthz \
    --check-interval=5s --timeout=3s --healthy-threshold=2 --unhealthy-threshold=3
fi

if ! gcloud compute backend-services describe arm-stage1-backend --global >/dev/null 2>&1; then
  gcloud compute backend-services create arm-stage1-backend \
    --global --protocol=HTTP --port-name=http --health-checks=arm-stage1-hc --timeout=10s
  gcloud compute backend-services add-backend arm-stage1-backend \
    --global --instance-group="$MIG_NAME" --instance-group-region="$REGION" \
    --balancing-mode=UTILIZATION --max-utilization=0.8
fi

if ! gcloud compute url-maps describe arm-stage1-map >/dev/null 2>&1; then
  gcloud compute url-maps create arm-stage1-map --default-service=arm-stage1-backend
fi
if ! gcloud compute target-http-proxies describe arm-stage1-proxy >/dev/null 2>&1; then
  gcloud compute target-http-proxies create arm-stage1-proxy --url-map=arm-stage1-map
fi
if ! gcloud compute forwarding-rules describe arm-stage1-http --global >/dev/null 2>&1; then
  gcloud compute forwarding-rules create arm-stage1-http \
    --global --target-http-proxy=arm-stage1-proxy --ports=80
fi

if ! gcloud compute instances describe "$CONTROLLER_VM" --zone "$ZONE" >/dev/null 2>&1; then
  gcloud compute instances create "$CONTROLLER_VM" \
    --zone="$ZONE" \
    --machine-type="$CONTROLLER_MACHINE_TYPE" \
    --network="$NETWORK" \
    --service-account="$CONTROLLER_SA" \
    --scopes=cloud-platform \
    --image-family=cos-stable \
    --image-project=cos-cloud \
    --metadata-from-file=startup-script="$TMP_CTL"
fi

IP="$(gcloud compute forwarding-rules describe arm-stage1-http --global --format='value(IPAddress)')"
URL="http://${IP}"
echo "$URL" | tee .state/mig_url

echo "MIG target: $URL"
echo "Controller logs: gcloud compute ssh $CONTROLLER_VM --zone $ZONE --command='docker logs -f arm-controller'"
