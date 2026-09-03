#!/usr/bin/env bash
set -euo pipefail

# envsubst substitutes the empty string for an unexported ${VAR} rather than failing, so a
# name missing from the job's env: block writes a blank into the cluster. A blank is
# invisible until a pod is recreated, which is how an empty mongo password sat in
# bot-secrets for three months. No `set -x` — it would print these into the CI log.
for v in MONGO_INITDB_ROOT_PASSWORD DATABASE_URL TELEGRAM_TOKEN OPENROUTER_API_KEY QDRANT_URL LANGSMITH_API_KEY IMAGE_TAG; do
  [[ -n "${!v:-}" ]] || { echo "missing required env: $v" >&2; exit 1; }
done

echo "apply secrets"
# See the header of manifests/secrets.yaml for what a value may safely contain — envsubst
# renders it into a quoted YAML scalar and cannot escape it.
envsubst < manifests/secrets.yaml | kubectl apply -f -

echo "apply configmaps"
envsubst < manifests/configmap.yaml | kubectl apply -f -

echo "apply mongo"
kubectl apply -f manifests/deployment-mongo.yaml
echo "apply mongo backup"
kubectl apply -f manifests/backup-mongo.yaml
echo "apply qdrant"
kubectl apply -f manifests/deployment-qdrant.yaml

# Both stores Ready before the bot rolls, so it never starts against a store that is still
# coming up. --timeout on every wait: the default is to block forever, which turns a wedged
# rollout into a CI job that hangs until GitHub's 6h limit.
echo "wait for stores"
kubectl rollout status statefulset/mongo --timeout=5m
kubectl rollout status statefulset/qdrant --timeout=5m

echo "apply bot deployment"
envsubst < manifests/deployment.yaml | kubectl apply -f -
kubectl rollout status deployment/anchovy-bot-deployment --timeout=5m
