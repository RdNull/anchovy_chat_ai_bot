#!/usr/bin/env bash
set -euo pipefail

# Everything below writes into the cluster without failing on an empty value: envsubst
# substitutes the empty string for an unexported ${VAR}, and --from-literal happily stores
# "". A blank is invisible until a pod is recreated, which is how an empty mongo password
# sat in bot-secrets for three months. No `set -x` — it would print these into the CI log.
for v in MONGO_INITDB_ROOT_PASSWORD DATABASE_URL TELEGRAM_TOKEN OPENROUTER_API_KEY QDRANT_URL LANGSMITH_API_KEY IMAGE_TAG; do
  [[ -n "${!v:-}" ]] || { echo "missing required env: $v" >&2; exit 1; }
done

echo "apply secrets"
# Never round-trips through YAML: a value holding `"`, `\` or a newline breaks the document,
# and one holding `$` is partly eaten by envsubst — both silently, both producing a wrong
# secret rather than an error. --server-side keeps the values out of the
# kubectl.kubernetes.io/last-applied-configuration annotation, where a client-side apply
# leaves the whole manifest in etcd for anyone with `kubectl get secret -o yaml`.
kubectl create secret generic bot-secrets \
  --from-literal=MONGO_INITDB_ROOT_PASSWORD="$MONGO_INITDB_ROOT_PASSWORD" \
  --from-literal=DATABASE_URL="$DATABASE_URL" \
  --from-literal=TELEGRAM_TOKEN="$TELEGRAM_TOKEN" \
  --from-literal=OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
  --from-literal=QDRANT_URL="$QDRANT_URL" \
  --from-literal=LANGSMITH_API_KEY="$LANGSMITH_API_KEY" \
  --dry-run=client -o yaml | kubectl apply --server-side --force-conflicts -f -

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
