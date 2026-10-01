# CausalOps — Backup and Restore Guide

## Overview

CausalOps uses PostgreSQL 16 for all persistent operational state. This guide covers backup, restore, and verification procedures.

**What is persisted:**
- Incident state machine history and timelines
- Failure predictions (with model versions)
- Counterfactual simulation records
- Remediation approvals and execution audit trails
- Service registry and topology
- Append-only audit log

**What is NOT persisted in PostgreSQL (separate concern):**
- ML model artifacts (`./ml/models/`) — should be version-controlled or stored in artifact registry
- Frozen datasets (`./dataset/`) — version-controlled
- Prometheus/Loki/Tempo time-series data (separate volumes; see below)

---

## 1. PostgreSQL Backup

### One-shot backup

```bash
# Create a timestamped compressed backup
docker exec causalops-postgres-1 \
  pg_dump -U causalops causalops \
  | gzip > backups/causalops-backup-$(date +%Y%m%d-%H%M%S).sql.gz
```

Verify the backup was created:
```bash
ls -lh backups/causalops-backup-*.sql.gz
```

### Full volume backup (alternative)

```bash
# Stop the postgres container
docker compose -f docker-compose.prod.yml stop postgres

# Back up the Docker volume
docker run --rm \
  -v causalops_postgres-data:/data \
  -v $(pwd)/backups:/backup \
  alpine \
  tar czf /backup/postgres-volume-$(date +%Y%m%d-%H%M%S).tar.gz -C /data .

# Restart postgres
docker compose -f docker-compose.prod.yml start postgres
```

---

## 2. Scheduled Backups

For production, schedule a daily backup:

```bash
# Add to crontab (crontab -e)
0 2 * * * cd /path/to/causalops && \
  docker exec causalops-postgres-1 pg_dump -U causalops causalops \
  | gzip > /backups/causalops-$(date +\%Y\%m\%d).sql.gz \
  && find /backups -name "causalops-*.sql.gz" -mtime +30 -delete
```

This runs at 02:00 daily and retains the last 30 days.

---

## 3. Restore Procedure

### From SQL dump backup

```bash
# 1. Ensure postgres is running
docker compose -f docker-compose.prod.yml up -d postgres

# 2. Wait for postgres to be healthy
docker compose -f docker-compose.prod.yml ps postgres

# 3. Create a fresh database (if restoring to a new instance)
docker exec causalops-postgres-1 \
  psql -U causalops -c "DROP DATABASE IF EXISTS causalops_restore; CREATE DATABASE causalops_restore;"

# 4. Restore from backup
gunzip -c backups/causalops-backup-YYYYMMDD-HHMMSS.sql.gz \
  | docker exec -i causalops-postgres-1 \
    psql -U causalops causalops

# 5. Verify row counts
docker exec causalops-postgres-1 psql -U causalops causalops \
  -c "SELECT 'incidents' as tbl, COUNT(*) FROM incidents
      UNION ALL SELECT 'predictions', COUNT(*) FROM predictions
      UNION ALL SELECT 'remediation_executions', COUNT(*) FROM remediation_executions
      UNION ALL SELECT 'audit_log', COUNT(*) FROM audit_log;"
```

### Complete destroy-and-restore test

```bash
# 1. Record current state
docker exec causalops-postgres-1 psql -U causalops causalops \
  -c "SELECT COUNT(*) as incident_count FROM incidents;" > before_restore.txt

# 2. Create backup
docker exec causalops-postgres-1 \
  pg_dump -U causalops causalops \
  | gzip > backups/test-restore-$(date +%Y%m%d).sql.gz

# 3. Destroy persistence
docker compose -f docker-compose.prod.yml down -v

# 4. Restart (fresh database)
docker compose -f docker-compose.prod.yml up -d postgres
# Wait for healthy
sleep 20

# 5. Restore
gunzip -c backups/test-restore-YYYYMMDD.sql.gz \
  | docker exec -i causalops-postgres-1 \
    psql -U causalops causalops

# 6. Verify state matches
docker exec causalops-postgres-1 psql -U causalops causalops \
  -c "SELECT COUNT(*) as incident_count FROM incidents;" > after_restore.txt

diff before_restore.txt after_restore.txt
echo "Restore verified successfully"

# 7. Restart full stack
docker compose -f docker-compose.prod.yml up -d
```

---

## 4. Model Artifact Backup

Model artifacts are separate from the database. They should be committed to version control or stored in an artifact registry.

```bash
# Create a tarball of all model artifacts
tar czf backups/ml-models-$(date +%Y%m%d).tar.gz \
  ml/models/ \
  ml/causal/ \
  ml/failure_prediction/audit/

# Verify checksums match Phase 6A audit
cat ml/failure_prediction/audit/checksums.json
```

**To restore model artifacts:**
```bash
tar xzf backups/ml-models-YYYYMMDD.tar.gz -C .
```

---

## 5. Observability Data (Prometheus / Loki / Tempo)

Time-series observability data is less critical than operational state. These volumes can be backed up similarly:

```bash
for vol in prometheus loki tempo; do
  docker run --rm \
    -v causalops_${vol}-data:/data \
    -v $(pwd)/backups:/backup \
    alpine \
    tar czf /backup/${vol}-$(date +%Y%m%d).tar.gz -C /data .
done
```

These are best-effort backups; data loss here does not affect incident history or model artifacts.

---

## 6. Backup Verification Checklist

After any restore:
- [ ] `GET /health` returns `status: UP`
- [ ] `GET /ready` returns `ready: true`
- [ ] `GET /incidents` returns expected incident list
- [ ] `GET /models` returns all model artifacts present
- [ ] Row counts match pre-backup values
- [ ] Remediation approvals and executions are present in audit log
