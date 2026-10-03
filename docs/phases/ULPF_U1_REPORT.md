# ULPF Phase U1: Lossless core

**Result:** U1 is complete. CausalOps now has a working log pre-processing pipeline. It takes in logs from any device, keeps every raw byte in a hash-chained vault, normalizes events to OCSF 1.3, and proves for each event that the normalized record still contains the whole original.

**Covers SIH26156 points:**
- (a) keep the raw event with no loss;
- (b) parse source-specific fields;
- (c) normalize into a common taxonomy;
- (d) trace each normalized event back to its raw event.

**USP 2 (provable losslessness)** and **zero-loss intake** are working now, ahead of the rest of the plan.

## What was built

| Part | Where | What it does |
|---|---|---|
| Intake | `ml/ulpf/collector.py`, `api.py` `/ingest` | Syslog over UDP and TCP (newline or RFC 6587 octet-counting framing) on 5514. TLS on 6514; a local certificate is created on first start. HTTP on 8090 or through `/api/ulpf/ingest`. Messages are written unchanged to the Redis stream `ulpf:raw`. |
| Queue | `redis` (AOF) | Events survive a restart. The stream entry ID is the event's permanent `uid`. |
| Workers | `ml/ulpf/worker.py`, 2 replicas | Stateless consumer group. For each batch: append to the vault with fsync, then insert into Postgres, then acknowledge. A dead worker's pending entries are reclaimed (`XAUTOCLAIM`). Inserts are idempotent. |
| Raw vault | `ml/ulpf/vault.py` | Append-only segments. Each record stores its SHA-256 and a chain hash: `chain_i = sha256(chain_{i-1} ‖ sha_i)`. Segments continue the chain across restarts and are made read-only when sealed. `verify_writer` recomputes everything. |
| Parsing | `ml/ulpf/detect.py`, `parsers/` | Syslog RFC 5424 (with structured data) and RFC 3164 / vendor variants; CEF; LEEF 1.0/2.0; JSON; XML (including Windows `<Data Name>`); key=value; CSV; Cisco ASA; plain text. Every field keeps its exact character span. |
| OCSF | `ml/ulpf/ocsf.py` | Classes 4001 Network Activity, 4002 HTTP, 4003 DNS, 3002 Authentication, 2004 Detection Finding, 0 Base. Mapped attributes: endpoints, ports, protocol, disposition/action, user, severity, time (including inferring the RFC 3164 year), metadata.product and device. Anything not mapped goes to `unmapped`. |
| Lossless proof | `ml/ulpf/lossless.py` | Keeps a literal skeleton plus, for each field, where its value lives in the OCSF record. The raw event is rebuilt **from the normalized record alone** and its SHA-256 must equal the vault's. |
| Storage | Flyway `V8__ulpf.sql` | `log_sources`, `ulpf_events` (OCSF JSONB plus indexed columns and the vault pointer), `vault_segments`. |
| API | `ulpf` service, proxied by causalops-api `/api/ulpf/**` (`UlpfProxyController`) | `/stats` (including conservation), `/sources`, `/events`, `/events/{uid}`, `/events/{uid}/verify`, `/vault/verify`, `/parse` (test bench). |
| Console | `src/pages/LogPipeline.tsx`, nav section **Log pipeline** | **Log sources:** pipeline statistics, vault integrity check, source table. **Event explorer:** search, plus a lineage view showing raw bytes with highlighted fields, the OCSF record, field locations and a **Verify lossless** button. |
| Demo traffic | `ml/ulpf/samples.py`, `replay.py`, `log-replayer` service | Realistic Palo Alto, FortiGate (two firmware variants), Cisco ASA, Check Point (LEEF 2), pfSense, Juniper SRX (5424 structured data), Sophos, Suricata EVE, F5 WAF (CEF), sshd and Windows Security XML. Sent over UDP and TCP. |

## Measured results (live stack, 2026-10-03)

| Check | Result |
|---|---|
| Unit tests (`tests/ulpf/test_core.py`) | **23 passed**: 4,400 generated events across 11 vendors, all lossless; escapes, nesting, non-UTF-8 bytes, garbage input, parser crash leading to quarantine, tamper detection, chain continuity after a crash |
| Sources discovered automatically | 11 |
| Events proven lossless | **100%** of stored events (`losslessVerified == stored`) |
| Per-event proof via the API | vault hash ✔, vault record is this event ✔, rebuilt-from-OCSF SHA-256 = vault SHA-256 ✔ (Palo Alto, Suricata, Windows XML, Check Point checked) |
| Full vault verification | `ok: true`, 10,044 records, all hashes and chain links recomputed |
| Conservation (received = stored + in flight) | **balanced** throughout, read as an atomic Redis snapshot |
| Hard-killing a worker (`docker kill`) under load | its pending entry was reclaimed by the other worker; conservation stayed balanced (10,033 = 10,032 + 1); every stored event is unique and lossless |
| Frontend typecheck | clean |

## Known limits (planned for U2)

- **Palo Alto CSV, pfSense filterlog, Cisco ASA message text and sshd text** are still `PARTIAL`. They are stored losslessly, but the vendor fields are not yet mapped to OCSF.
  - The vendor **YAML packs** in U2 will map them. They will also set `syslog.host` / device names for sources that currently show the container IP (Check Point, Sophos, Suricata, Windows).
- The vault sits on a local Docker volume. In U4 it gets MinIO and the Parquet lake.
- UDP datagrams the kernel drops before the collector reads them cannot be counted by any receiver. Use TCP or TLS where loss matters. UDP drops at the collector's own queue are counted (`ulpf:dropped`).

## How to check it yourself

- **Console:**
  - **Log pipeline → Log sources:** statistics, *Verify whole vault*, per-source normalized and lossless percentages.
  - **Log pipeline → Event explorer:** open any event, then *Verify lossless*.
- **API:**
  - `curl localhost:8080/api/ulpf/stats` shows the conservation block.
  - `curl -X POST localhost:8080/api/ulpf/vault/verify`.
- **Send your own log:**
  ```bash
  echo '<14>Oct  3 10:00:00 myfw CEF:0|Acme|FW|1|100|blocked|7|src=10.0.0.1 dst=8.8.8.8 act=deny' | nc -u -w1 localhost 5514
  ```
- **Tests:**
  ```bash
  python -m pytest tests/ulpf -q
  ```
