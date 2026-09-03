#!/usr/bin/env bash
set -euo pipefail

# envsubst substitutes the empty string for anything that is not exported, so an
# unset variable here writes a blank value into the cluster instead of failing.
# No `set -x` — it would print the secrets below into the CI log.
for v in MONGO_INITDB_ROOT_PASSWORD DATABASE_URL TELEGRAM_TOKEN OPENROUTER_API_KEY QDRANT_URL IMAGE_TAG; do
  [[ -n "${!v:-}" ]] || { echo "missing required env: $v" >&2; exit 1; }
done

echo "apply configmaps"
envsubst < manifests/configmap.yaml | kubectl apply -f -
echo "apply secrets"
envsubst < manifests/secrets.yaml | kubectl apply -f -
echo "apply mongo"
kubectl apply -f manifests/deployment-mongo.yaml
echo "apply qdrant"
kubectl apply -f manifests/deployment-qdrant.yaml
echo "apply bot deployment"
envsubst < manifests/deployment.yaml | kubectl apply -f -
kubectl rollout status deployment/anchovy-bot-deployment
