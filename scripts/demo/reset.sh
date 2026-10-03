#!/usr/bin/env bash
# Puts the demo back to its starting point between runs, keeping all data:
#  - AIOps: stops every injected fault, restores default autonomy (automatic up to tier 1, kill switch off).
#  - Log pipeline: stops fault scenarios, re-activates the bundled parser packs (so the FortiGate drift demo
#    works again), retires packs created during the run (Parser Studio, automatic repairs) and unbinds their
#    sources (so the VPN onboarding demo works again), closes open pipeline incidents with a note.
# Parser-drift repairs have a 10-minute promotion cooldown (a safety rule): wait that long between drift demos.
set -euo pipefail
API=http://localhost:8080
curl -sf -o /dev/null -X POST "$API/api/faults/clear" && echo "AIOps faults cleared"
curl -sf -o /dev/null -X PUT "$API/api/remediation/autonomy" -H 'Content-Type: application/json' \
  -d '{"autoExecuteMaxTier":1,"killSwitch":false,"dryRun":false,"changedBy":"scripts/demo/reset.sh"}' && echo "autonomy: automatic up to tier 1, kill switch off"
curl -sf -X POST "$API/api/ulpf/admin/demo-reset" -H 'Content-Type: application/json' -d '{}' && echo && echo "log pipeline reset"
echo "Wait until Active incidents is empty (about 1 minute), and about 5 minutes after any restart, before the next fault."
