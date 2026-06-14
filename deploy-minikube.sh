export IMAGE_TAG=anchovy-bot:$(date +%s)
echo "building image..."
docker compose build
docker tag anchovy-bot:latest $IMAGE_TAG
echo "loading image to minikube..."
minikube image load $IMAGE_TAG
set -a; . ./.env; set +a # exporting variables from .env
kubectl config use-context minikube
./deploy-k8s.sh