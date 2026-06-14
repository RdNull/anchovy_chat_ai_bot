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