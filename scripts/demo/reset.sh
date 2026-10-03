#!/usr/bin/env bash
# Puts the demo back to its starting point between runs: stops every injected fault and restores the
# default autonomy (automatic up to tier 1, kill switch off, dry run off). Open incidents resolve on
# their own within about a minute once the faults are gone.
set -euo pipefail
API=http://localhost:8080
curl -sf -o /dev/null -X POST "$API/api/faults/clear" && echo "faults cleared"
curl -sf -o /dev/null -X PUT "$API/api/remediation/autonomy" -H 'Content-Type: application/json' \
  -d '{"autoExecuteMaxTier":1,"killSwitch":false,"dryRun":false,"changedBy":"scripts/demo/reset.sh"}' && echo "autonomy: automatic up to tier 1, kill switch off"
echo "Wait until Active incidents is empty (about 1 minute), and about 5 minutes after any restart, before the next fault."
