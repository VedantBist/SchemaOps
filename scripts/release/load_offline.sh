#!/usr/bin/env bash
# Air-gapped install: verifies and loads the image archive made by export_offline.sh, then starts the stack
# without contacting any registry.
#   bash scripts/release/load_offline.sh dist/causalops-images-1.0.0.tar.gz
set -euo pipefail
cd "$(dirname "$0")/../.."
archive="${1:?usage: load_offline.sh <causalops-images-*.tar.gz>}"
if [ -f "$archive.sha256" ]; then
  (sha256sum -c "$archive.sha256" 2>/dev/null || shasum -a 256 -c "$archive.sha256") || { echo "checksum mismatch"; exit 1; }
fi
gunzip -c "$archive" | docker load
export CAUSALOPS_VERSION="${CAUSALOPS_VERSION:-$(basename "$archive" | sed -E 's/causalops-images-(.*)\.tar\.gz/\1/')}"
docker compose up -d --no-build --pull never
echo "Started from local images only. Console: http://localhost:3000"
