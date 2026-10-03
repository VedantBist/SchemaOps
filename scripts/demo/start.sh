#!/usr/bin/env bash
# One command to run the whole CausalOps demo: builds and starts every container (reference system,
# observability pipeline, platform, console), waits until the platform is healthy, and records the
# cold start as a maintenance window so JVM warm-up neither trains the models nor triggers
# automatic remediation. Works on macOS (Apple Silicon or Intel), Linux and Windows (Git Bash).
set -euo pipefail
cd "$(dirname "$0")/../.."

API=http://localhost:8080
command -v docker >/dev/null || { echo "Docker Desktop is required: https://www.docker.com/products/docker-desktop/"; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop and run this again."; exit 1; }

echo "==> Building and starting CausalOps (first run downloads and builds images: 5-15 minutes)"
docker compose up -d --build

echo "==> Waiting for the platform API"
for _ in $(seq 1 120); do
  curl -sf "$API/actuator/health" >/dev/null 2>&1 && break
  sleep 5
done
curl -sf "$API/actuator/health" >/dev/null || { echo "The API did not become healthy. Check: docker compose logs causalops-api"; exit 1; }

# 10-minute warm-up window (BSD date on macOS, GNU date elsewhere).
END=$(date -u -v+10M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '+10 minutes' +%Y-%m-%dT%H:%M:%SZ)
curl -s -o /dev/null -X POST "$API/api/changes" -H 'Content-Type: application/json' \
  -d "{\"kind\":\"RESTART\",\"target\":\"all-services\",\"endedAt\":\"$END\",\"source\":\"scripts/demo/start.sh\",\"description\":\"Stack started; JVM warm-up\"}" || true

STATUS=$(curl -s "$API/api/calibration/status" | sed -n 's/.*"environment":{[^}]*"status":"\([A-Z]*\)".*/\1/p')
echo
echo "CausalOps is up.   Environment status: ${STATUS:-unknown}"
echo "  Console     http://localhost:3000"
echo "  API         $API/api   (health: $API/actuator/health)"
echo "  AI engine   http://localhost:8000/docs"
echo "  Grafana     http://localhost:3001"
echo
echo "Let it warm up for about 10 minutes before injecting faults (see docs/MAC_SETUP_AND_DEMO.md)."
