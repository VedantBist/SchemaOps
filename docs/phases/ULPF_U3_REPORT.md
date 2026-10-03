# ULPF Phase U3: Self-calibrating log quality (the core USPs)

**Result:** U3 is complete. CausalOps now watches the log pipeline itself. A new `ulpf-monitor` service:
- learns each source's normal parsing quality, volume and clock;
- detects **parser drift** (USP 1), **silent sources** (USP 3) and **clock skew** (USP 7);
- repairs them through the tiered, verified, audited remediation model of CausalOps;
- repairs past data through **historical re-normalization** (USP 5).

The two known gaps from U2 are closed, and seven bugs found along the way are fixed.

## How it works

| Step | Where | What |
|---|---|---|
| Quality series | `monitor.aggregate`, `ulpf_quality` | Every 20 s bucket per source: events, normalized share, **fill** (share of the pack's expected OCSF attributes present), lossless count, and **median device-clock offset** (arrival minus the time the device states). Rebuilt from stored events after a restart. |
| Calibration | `quality.py`, `monitor.calibrate`, `source_baselines` | Same method as the CausalOps anomaly gate. Robust median and MAD × 1.4826 with floors. The drift threshold is set so at most 1% of the source's own normal buckets cross it, never below z = 4. An alarm needs 2 buckets in a row. Buckets inside past incidents are excluded. The default learning window is 20 min, and baselines refit every minute. |
| Silence | `quality.silence_buckets` | Uses the learned rate λ per bucket. A Poisson source at that rate would produce k empty buckets in a row with probability below 10⁻⁴, so k = ⌈9.21/λ⌉ (at least 3). A busy firewall is flagged after 60 s; a quiet source waits proportionally longer. |
| Parser drift → repair | `repair.py` | The drifted events are parsed with the current pack to find which expected attributes went missing. New fields are scored as replacements: alias table +3, value types +2, name similarity +0..2. The repaired pack **adds** mappings and keeps the old ones, so devices still on the old firmware keep parsing. A vanished time field gets a timestamp-shaped fallback. |
| Shadow test and policy | `monitor._progress_parser_drift` | The challenger is saved as a SHADOW version and tested against the champion on drifted events and on events from before the change. There are **9 policy rules**: kill switch, dry run, tier, fixes the drift (≥ 99% fill), no regression, lossless, enough evidence, cooldown, reversible. Tier 1 runs automatically; anything else waits for approval in the console. |
| Verify, roll back, escalate | `_drive_promotion` | After the workers reload (≤ 12 s), the next 2 live buckets must be back within the learned normal. Otherwise the previous version is reactivated and the incident escalates to a human. |
| Re-normalization (USP 5) | `renormalize.py`, `renormalization_jobs`, `ulpf_event_versions` | Re-runs the current pack over the original bytes from the vault. Each changed event keeps its previous record as a revision ("as parsed by v1 vs v2") and goes through the lossless proof again. The job runs in a background thread so detection never waits. It is throttled to 250 events/s and announced to CausalOps as a MAINTENANCE change window. It starts automatically after a verified drift fix, or manually with **Re-normalize history** on a source. |
| Clock skew (USP 7) | `_progress_clock_skew`, `log_sources.skew_ms`, pipeline | Three consistent buckets beyond 30 s open CLOCK_SKEW. A tier-1 correction is then applied per source. The workers correct `time`, keep the device's own time in `metadata.original_time` and `ulpf.clock`, and verify that corrected times align with arrival. When the device clock recovers, the correction is removed automatically and the incident resolves. |
| Fault lab | `replay._scenarios`, `/api/ulpf/scenarios` | Firmware format change, silenced device and clock drift, applied to the demo traffic within 2 s. Every injection is audited. |
| Console | **Log pipeline → Pipeline health** | Learned normal per source, the incidents table with MTTD/MTTR, the incident page (evidence, the attributes lost, the repair and its evidence, shadow test, the 9 rules, timeline, approve/reject), quality charts, the Fault lab, autonomy settings, and re-normalization jobs. **Event explorer** shows the revision history; **Log sources** has *Re-normalize history*. |
| Audit | shared `audit_log` (append-only) | Every incident transition, action decision, fault injection, setting change and re-normalization is recorded. |

## Measured results (live stack, 2026-10-03)

| Scenario | Detected (first bad event → incident) | Outcome |
|---|---|---|
| FortiGate firmware renames its fields (LOG-1001) | **30 s**. Evidence: 5 of 6 expected attributes gone, 11 new fields named | Repaired pack v2 inferred (`src_ip`, `dst_ip`, `src_port`, `dst_port`, `fw_action`, `eventtime`). Shadow test 17% → 100% on drifted events and 100% on earlier events. Passed all 9 rules and was auto-promoted, then verified on 2 live buckets. **286 events re-normalized, 1,430 fields recovered, 100% lossless.** Resolved in **2 min 1 s**. |
| Same, run by `verify_ulpf.py` (LOG-1004) | **34 s** | Resolved; 1,062 events repaired, 5,310 fields recovered, all lossless. MTTR was 249 s because the monitor was stuck on the version bug below until it was redeployed mid-incident. The incident then continued from its stored state. |
| Juniper SRX clock 240 s fast (LOG-1002) | **60 s** | Correction applied automatically and verified (residual offset **0 ms**). Removed automatically once the device clock recovered; resolved. |
| Cisco ASA stops logging (LOG-1003) | **75 s** after its last event, at a learned rate of 133 events per 20 s | Escalated, because nothing on the device side can be fixed from the collector. Resolved when traffic returned. |
| History repair of U1-era events (known gap) | none | 5 jobs: **19,762 events re-normalized, 112,605 fields recovered, all lossless**. Every source is now **100% normalized over its whole history**. |
| Conservation during all of this | none | received = stored + in flight, balanced throughout (e.g. 142,650 = 142,649 + 1) |

**`scripts/dev/verify_ulpf.py` full run: 23/23 checks passed in 502 s.** This covers U1, U2 and U3. It re-injects firmware drift (LOG-1004), clock drift (LOG-1005, residual 0 ms) and silence (LOG-1006: detected, then resolved with MTTR 131 s) from scratch.

Across the whole store, **162,166 / 162,166 events are NORMALIZED and proven lossless**.

## Known gaps from U2: fixed

1. **Low lifetime "normalized" on Palo Alto, ASA, pfSense, sshd:** fixed by USP 5 itself. All sources are at 100%.
2. **Stale entity links:** the entity view now shows a time window (last hour by default, or 6 h, 24 h, all history), so old links age out and nothing is deleted. The demo snapshot in U5 will also start from clean traffic.

## Bugs found and fixed in U3

| Bug | Fix |
|---|---|
| Juniper sample labelled UTC times as +05:30 (it would have looked like a 5.5 h clock skew) | Sample converts to IST before formatting |
| Two workers could **deadlock** after U2's single-transaction batch (unordered source upserts) | Deterministic lock order, plus a retry on deadlock |
| Calibration treated hours before quality aggregation as silence (volume median 0) | 6 h backfill on start; zero-fill only from a source's first bucket |
| A repair after a rollback reused an existing version number | Next free version |
| Re-normalization ran inside the monitor loop (detection paused during a big job) and was orphaned by a restart | Background thread; interrupted jobs resume (they are idempotent) |
| MTTD/MTTR came back as strings (Decimal) | Numbers in JSON |
| Bulk ULPF work on the shared Postgres disturbed the reference system (INC-1091 overlapped the history job) | Throttled to 250 events/s and announced as a CausalOps MAINTENANCE change window |

**Note on reference-system incidents today:** most of INC-1086 to INC-1092 coincide with my image builds and API restarts on this host, the same host-contention effect seen in Phase 3. Steady ULPF load (40 events/s, every ULPF container under 4% CPU) produced none in 20 minutes. In production the monitored system would not share the platform's database. In the demo stack it does, so bulk jobs are throttled and announced.

## Check it yourself

- **Log pipeline → Pipeline health → Fault lab:**
  1. Choose `fgt-dc-01` and click *Firmware format change*.
  2. Watch the incident appear (about 30 s), then open it to see the repair, the shadow test and the rules, then *MITIGATED* and *RESOLVED*.
- **Pipeline health → Autonomy:** set *Run automatically up to tier 0*. The next repair waits for your **Approve**.
- **Clock drift:** `srx-core-01`, 240 s. **Silence:** `asa-perimeter`.
- **Event explorer:** open a repaired event to see its revision history.
- **Commands:**
  ```bash
  python3 scripts/dev/verify_ulpf.py
  ```
  ```bash
  python3 scripts/dev/verify_ulpf.py --quick
  ```
  ```bash
  python -m pytest tests/ulpf -q
  ```
