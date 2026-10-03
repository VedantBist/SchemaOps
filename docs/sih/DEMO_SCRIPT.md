# CausalOps ULPF: demo video script (4 to 5 minutes)

SIH 2026 · PS 26156 · Universal Log Pre-processing Framework.

Each scene below says what to click, what appears, and what to say. Every number on screen is measured live.

## Before recording (10 minutes earlier)

1. Start the stack:
   ```bash
   bash scripts/demo/start.sh
   ```
   Open http://localhost:3000 and leave it running for about 10 minutes. The demo snapshot already has learned baselines, so all sources show **watched** under **Log pipeline → Pipeline health**.
2. Make sure nothing is left over from an earlier run:
   ```bash
   bash scripts/demo/reset.sh
   ```
   This clears active fault scenarios and resolves open demo incidents. It keeps all data.
3. **Browser:** zoom to 90%, full screen. Keep one terminal visible for scene 8.

## Scene 1: the problem (0:00 to 0:25) · *Log pipeline → Log sources*

> "Security teams receive logs from dozens of products, each in its own format: Syslog, CEF, LEEF, JSON, XML, CSV, plain text. Parsers are written by hand, fields get lost, and when a vendor changes its format nobody notices for weeks. CausalOps ULPF ingests any format, keeps every byte, normalizes to OCSF, and watches itself."

Point at the source table: 14 sources from 12 vendors, each with its pack, normalized %, fill % and lossless %. Then point at the top stats: **Received = Stored + In flight**, "balanced". That is the zero-loss conservation check.

## Scene 2: lossless and traceable (0:25 to 0:55) · *Event explorer*

1. Filter OCSF class **4001 Network Activity** and open a Palo Alto event.

> "On the left, the exact bytes from the write-once vault, with every extracted field highlighted. On the right, the OCSF record. Vendor fields we do not map are kept, not dropped."

2. Click **Verify lossless**.

> "We rebuild the raw log from the normalized record alone and compare SHA-256 with the vault. Byte-identical. That proof runs for every event."

## Scene 3: onboarding a format nobody has seen (0:55 to 1:30) · *Parser studio*

1. Choose source **vpn-gw-01-vpnd** (an in-house VPN with free-text logs, no pack yet). Click **Load samples**, then **Analyze**.

> "Drain3 template mining finds the three message shapes and proposes a pack: named fields, OCSF mapping, outcome values. No code."

2. Click **Test on samples**: 100% matched, normalized and lossless.
3. Click **Activate**. Open **Event explorer**: the VPN events are now *Authentication*, with user and source IP.

## Scene 4: the differentiator, a pipeline that repairs itself (1:30 to 2:30) · *Pipeline health*

1. In **Fault lab**, pick device **fgt-dc-01** and click **Firmware format change**.

> "FortiGate just changed its log format: srcip became src_ip, action became fw_action, the timestamp moved to nanosecond epoch. Every other tool keeps parsing silently and loses the fields."

2. About 30 s later a **Parser drift** incident appears. Open it.

> "Detected from the learned fill rate. It names the five attributes we lost and the new fields that appeared."

3. Scroll to the action card.

> "It inferred the repair from aliases, value types and name similarity, and tested it in shadow mode: 17% to 100% on the new format, still 100% on the old one, lossless. Nine policy rules passed, so this tier-1, reversible change was promoted automatically, then verified on live data."

4. Wait for **RESOLVED**.

> "Then it re-normalized the broken window from the vault. Hundreds of events repaired, every one proven lossless again. Detected in about 30 seconds, resolved in about two minutes."

## Scene 5: clock skew, silence, CERT-In (2:30 to 3:05)

1. In **Fault lab**, set **srx-core-01** to clock drift 240 s, and silence **asa-perimeter**.
2. While waiting, open **Compliance**.

> "CERT-In's 2022 directions: 180-day retention, Indian jurisdiction, NTP sync, reporting sources, log integrity, and evidence within six hours. Each one is measured, not assumed."

3. Back on **Pipeline health**:
   - **Clock skew** is corrected automatically: the corrected time equals arrival, and the device's own time is kept.
   - **Silent source** is escalated to a human, because the fix is on the device side.
4. Click **Re-check now** on Compliance: the NTP and reporting checks fail for exactly those sources.

## Scene 6: one rule, every vendor, and an attack chain (3:05 to 3:45)

1. In **Fault lab**, click **Attack: scan → brute force → login**.

> "One address scans our perimeter, brute-forces SSH and the VPN, then logs in."

2. Open **Detections**.

> "These are standard Sigma rules running on OCSF, so one port-scan rule counts ports across Palo Alto, FortiGate, Cisco and pfSense together."

3. Open **Pipeline health** and the new **Attack chain** incident.

> "Reconnaissance, credential attack, access gained, correlated by the same actor across products. Blocking is a tier-2 action, so it waits for an analyst."

4. Click **Approve**.

## Scene 7: SIEM cost, privacy, signed export (3:45 to 4:25)

1. **Outputs & cost**: the full stream goes to the Parquet lake on MinIO.

> "Every event goes to the data lake. The SIEM receives security events whole and routine traffic summarised with pointers back to the lake. Measured: X% less volume, about ₹Y a month, nothing lost."

2. **Privacy**: pick an authentication event.

> "Exports carry format-preserving tokens instead of names, IPs, e-mails, mobile or Aadhaar numbers, for the DPDP Act. Revealing one needs a reason and is audited."

3. **Compliance**: click **Export signed bundle**, then **Tamper test**.

> "The bundle is Ed25519-signed for one-way transfer across a data diode. One flipped byte, and the import rejects it and names the event."

## Scene 8: self-healing and deployment (4:25 to 4:55)

1. In **Fault lab**, click **Crash a pipeline worker**. On **Pipeline health**, a *Pipeline* incident opens, the worker is started through the CausalOps Docker executor, and it is verified: received = stored + in flight, nothing lost.
2. **Benchmarks**: show events/s per worker (measured).
3. In the terminal, show:
   ```bash
   docker compose ps
   ```
   ```bash
   ls dist/
   ```

> "One command to run. Docker Hub images, and an offline image archive for air-gapped networks. Every claim you saw is checked end to end by verify_ulpf.py."

## Check every claim yourself

```bash
python3 scripts/dev/verify_ulpf.py
```
