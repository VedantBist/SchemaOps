#!/usr/bin/env bash
# Recreate the local PostgreSQL volume from scratch.
#
# Use this once after upgrading to the Phase 1 layout: the Postgres init script only
# runs on an empty volume, and older volumes contain tables that conflict with Flyway.
# It deletes ALL data in the local dev database (incidents, telemetry, faults), so it
# asks for confirmation first. Nothing else (images, other volumes) is touched.
set -euo pipefail

cd "$(dirname "$0")/../.."
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
PROJECT="$(basename "$PWD")"
VOLUME="${PROJECT}_postgres-data"

echo "This permanently deletes the Docker volume '${VOLUME}' (all local CausalOps database data)."
read -r -p "Type 'reset' to continue: " answer
if [[ "${answer}" != "reset" ]]; then
  echo "Aborted; nothing was changed."
  exit 1
fi

docker compose -f "${COMPOSE_FILE}" stop causalops-api inventory-service postgres
docker compose -f "${COMPOSE_FILE}" rm -f postgres
docker volume rm "${VOLUME}"
docker compose -f "${COMPOSE_FILE}" up -d postgres
echo "PostgreSQL recreated. Start the rest with: docker compose -f ${COMPOSE_FILE} up -d"
