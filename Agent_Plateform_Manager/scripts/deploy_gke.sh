#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

if ! gcloud container clusters describe "$GKE_CLUSTER" --region "$REGION" >/dev/null 2>&1; then
  gcloud container clusters create-auto "$GKE_CLUSTER" \
    --region "$REGION" \
    --release-channel regular
fi

gcloud container clusters get-credentials "$GKE_CLUSTER" --region "$REGION"

kubectl apply -f gke/namespace.yaml
kubectl apply -f gke/quota.yaml
APP_IMAGE="$APP_IMAGE" envsubst < gke/deployment.yaml.tpl | kubectl apply -f -
kubectl apply -f gke/service.yaml
kubectl apply -f gke/ingress.yaml
kubectl apply -f gke/hpa.yaml

kubectl -n "$GKE_NAMESPACE" rollout status deployment/"$GKE_DEPLOYMENT" --timeout=10m

echo "Waiting for GKE HTTP Ingress external IP..."
for _ in $(seq 1 180); do
  IP="$(kubectl -n "$GKE_NAMESPACE" get ingress "$GKE_INGRESS" -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)"
  if [[ -n "$IP" ]]; then
    URL="http://${IP}"
    echo "$URL" | tee .state/gke_url
    echo "GKE target: $URL"
    exit 0
  fi
  sleep 5
done

echo "Timed out waiting for GKE external IP." >&2
exit 1
