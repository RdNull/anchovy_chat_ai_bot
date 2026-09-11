#!/usr/bin/env bash
# One-time cluster infrastructure: Traefik (routing) and cert-manager (TLS), plus the
# Let's Encrypt ClusterIssuers. Run by hand against the prod context, not from CI — the
# deploy job renders application manifests on every push, while this installs cluster-wide
# controllers with their own CRDs and RBAC. Idempotent: re-running upgrades in place to the
# pinned versions below, so bumping a version here and re-running is the upgrade path.
#
#   KUBE_CONTEXT=anchovy-prod scripts/cluster-bootstrap.sh
#
# Must run before the first deploy that carries manifests/blackbox/: the Ingress there
# references Traefik Middleware CRDs and a cert-manager ClusterIssuer.
set -euo pipefail

TRAEFIK_CHART_VERSION=41.5.0       # Traefik v3.7.13
CERT_MANAGER_CHART_VERSION=v1.21.2

: "${KUBE_CONTEXT:?set KUBE_CONTEXT, e.g. anchovy-prod}"
cd "$(dirname "$0")/.."

kube=(--kube-context "$KUBE_CONTEXT")

helm repo add traefik https://traefik.github.io/charts --force-update
helm repo add jetstack https://charts.jetstack.io --force-update
helm repo update traefik jetstack

echo "install traefik $TRAEFIK_CHART_VERSION"
helm upgrade --install traefik traefik/traefik "${kube[@]}" \
  --namespace traefik --create-namespace \
  --version "$TRAEFIK_CHART_VERSION" \
  --values manifests/cluster/traefik-values.yaml \
  --wait --timeout 5m

echo "install cert-manager $CERT_MANAGER_CHART_VERSION"
helm upgrade --install cert-manager jetstack/cert-manager "${kube[@]}" \
  --namespace cert-manager --create-namespace \
  --version "$CERT_MANAGER_CHART_VERSION" \
  --values manifests/cluster/cert-manager-values.yaml \
  --wait --timeout 5m

echo "apply cluster issuers"
kubectl --context "$KUBE_CONTEXT" apply -f manifests/cluster/cluster-issuers.yaml

# Nothing here may be a LoadBalancer: the Linode CCM would provision a billed NodeBalancer.
if kubectl --context "$KUBE_CONTEXT" get svc -A -o jsonpath='{.items[*].spec.type}' | grep -qw LoadBalancer; then
  echo "a LoadBalancer Service exists — the CCM will bill a NodeBalancer for it" >&2
  exit 1
fi
