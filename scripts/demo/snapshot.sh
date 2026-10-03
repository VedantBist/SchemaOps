#!/usr/bin/env bash
# Maintainers: refreshes demo/snapshot from the running stack so a fresh clone starts calibrated:
#   causalops.sql.gz   platform database (AIOps calibration and incidents, ULPF packs, baselines, events)
#   models.tar.gz      the current champion model
#   ulpf-vault.tar.gz  the raw log vault the ULPF events point to (keys and TLS certificate excluded)
# Run on a quiet stack: no open incidents, no active faults or scenarios.
set -euo pipefail
cd "$(dirname "$0")/../.."
P="${COMPOSE_PROJECT:-causalops}"
mkdir -p demo/snapshot
docker compose -p "$P" stop ulpf ulpf-worker ulpf-monitor ulpf-exporter log-replayer >/dev/null  # consistent vault + database
docker compose -p "$P" exec -T postgres pg_dump -U "${POSTGRES_USER:-causalops}" -d "${POSTGRES_DB:-causalops}" --no-owner --no-privileges \
  | gzip -9 > demo/snapshot/causalops.sql.gz
CHAMPION=$(docker compose -p "$P" exec -T postgres psql -U "${POSTGRES_USER:-causalops}" -d "${POSTGRES_DB:-causalops}" -At \
  -c "SELECT artifact_path FROM model_registry WHERE status = 'CHAMPION'" | head -1)
REL=$(dirname "${CHAMPION#/var/lib/causalops/models/}")
docker run --rm -v "${P}_engine-models:/m:ro" -v "$PWD/demo/snapshot:/out" alpine:3.20 \
  sh -c "cd /m && tar czf /out/models.tar.gz '$REL'"
docker run --rm -v "${P}_ulpf-vault:/u:ro" -v "$PWD/demo/snapshot:/out" alpine:3.20 \
  sh -c "cd /u && tar czf /out/ulpf-vault.tar.gz vault"
docker compose -p "$P" start ulpf ulpf-worker ulpf-monitor ulpf-exporter log-replayer >/dev/null
ls -la demo/snapshot
