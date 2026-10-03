# Demo snapshot

`snapshot/` lets a fresh clone start **already calibrated**, so a demo machine needs no learning window or chaos campaign:

| File | Contents | Restored by |
|---|---|---|
| `causalops.sql.gz` | The platform database: the `reference` environment (ACTIVE), its configuration, the discovered topology, recorded telemetry, labelled faults, incident history with RCA and remediation, change events and the model registry | `infrastructure/postgres/02-restore-snapshot.sh`, on the first start of an empty Postgres volume |
| `models.tar.gz` | The champion model file referenced by the registry (SHA-256 checked when loaded) | the `model-seed` service, when the model volume is empty |

Both restores run **only into empty volumes**, so an existing installation is never overwritten.

- **Start from scratch instead** (the environment begins in LEARNING): set `CAUSALOPS_RESTORE_SNAPSHOT=false` before the first `docker compose up`.
- **Refresh the snapshot** from a running stack: `./scripts/demo/snapshot.sh`.
- **Reset a machine back to the snapshot** (this deletes its local CausalOps data): `docker compose down -v`, then `./scripts/demo/start.sh`.

The snapshot holds measurements of the bundled reference system only: no credentials and no personal data.
