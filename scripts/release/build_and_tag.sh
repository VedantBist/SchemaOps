#!/usr/bin/env bash
# Builds every CausalOps image and tags it for Docker Hub. Pushing is left to you:
#   DOCKERHUB_NAMESPACE=yourname CAUSALOPS_VERSION=1.0.0 bash scripts/release/build_and_tag.sh
#   docker login && bash scripts/release/build_and_tag.sh --push
set -euo pipefail
cd "$(dirname "$0")/../.."
export DOCKERHUB_NAMESPACE="${DOCKERHUB_NAMESPACE:-causalops}"
export CAUSALOPS_VERSION="${CAUSALOPS_VERSION:-1.0.0}"
echo "Building images as ${DOCKERHUB_NAMESPACE}/<name>:${CAUSALOPS_VERSION}"
docker compose build
ours=$(docker compose config --images | grep "^${DOCKERHUB_NAMESPACE}/" | sort -u)
for img in $ours; do
  docker tag "$img" "${img%:*}:latest"
  echo "  $img (and :latest)"
done
if [ "${1:-}" = "--push" ]; then
  for img in $ours; do docker push "$img"; docker push "${img%:*}:latest"; done
else
  echo "Not pushed. To publish: docker login, then run again with --push (or docker push each image above)."
fi
