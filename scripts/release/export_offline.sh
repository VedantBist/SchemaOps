#!/usr/bin/env bash
# Saves every image the stack needs (CausalOps and third-party) into one archive for air-gapped installs.
#   bash scripts/release/export_offline.sh            -> dist/causalops-images-<version>.tar.gz
# Copy the archive and the repository to the isolated network, then run load_offline.sh there.
set -euo pipefail
cd "$(dirname "$0")/../.."
export DOCKERHUB_NAMESPACE="${DOCKERHUB_NAMESPACE:-causalops}"
export CAUSALOPS_VERSION="${CAUSALOPS_VERSION:-1.0.0}"
mkdir -p dist
images=$(docker compose config --images | sort -u)
for img in $images; do
  docker image inspect "$img" >/dev/null 2>&1 || docker pull "$img"
done
out="dist/causalops-images-${CAUSALOPS_VERSION}.tar.gz"
echo "Saving $(echo "$images" | wc -l | tr -d ' ') images to $out ..."
docker save $images | gzip -1 > "$out"
sha256sum "$out" > "$out.sha256" 2>/dev/null || shasum -a 256 "$out" > "$out.sha256"
ls -lh "$out"; cat "$out.sha256"
