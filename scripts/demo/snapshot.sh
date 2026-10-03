#!/usr/bin/env bash
# Maintainers: refreshes demo/snapshot from the running stack (platform database + current champion
# model) so a fresh clone starts calibrated. Run on a stack with no open incidents or active faults.
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p demo/snapshot
docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-causalops}" -d "${POSTGRES_DB:-causalops}" --no-owner --no-privileges \
  | gzip -9 > demo/snapshot/causalops.sql.gz
CHAMPION=$(docker compose exec -T postgres psql -U "${POSTGRES_USER:-causalops}" -d "${POSTGRES_DB:-causalops}" -At \
  -c "SELECT artifact_path FROM model_registry WHERE status = 'CHAMPION'" | head -1)
REL=$(dirname "${CHAMPION#/var/lib/causalops/models/}")
docker run --rm -v causalops_engine-models:/m:ro -v "$PWD/demo/snapshot:/out" alpine:3.20 \
  sh -c "cd /m && tar czf /out/models.tar.gz '$REL'"
ls -la demo/snapshot
