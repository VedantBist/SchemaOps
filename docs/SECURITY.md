# CausalOps Security Guide

## Phase 7: Secret Handling & Security Posture

### 1. Credential Storage Policy

**Prohibited:**
- Passwords, API keys, JWT secrets, or database credentials in source code
- Credentials in Docker Compose files (use `${VAR}` references only)
- Credentials in `.env` files committed to version control
- Secrets logged at any log level
- Secrets returned in API responses or error messages

**Required:**
- All secrets injected via environment variables
- `.env` file excluded from version control (`.gitignore` entry: `.env`)
- Production secrets managed via a secrets manager (AWS Secrets Manager, HashiCorp Vault, or equivalent)

### 2. Local Development

```bash
# 1. Copy the example file
cp .env.example .env

# 2. Fill in real values — this file is gitignored
nano .env

# 3. Never commit .env
git status   # should NOT show .env
```

**Minimum required for local dev:**
```
POSTGRES_PASSWORD=<any local password>
CAUSALOPS_JWT_SECRET=<32-byte hex, e.g. openssl rand -hex 32>
CAUSALOPS_ADMIN_PASSWORD=<any local password>
```

### 3. Production Configuration

For production deployments:

1. **Never** use `.env` files in production
2. Inject secrets via CI/CD pipeline environment variables or a secrets manager
3. Rotate the `POSTGRES_PASSWORD` and `CAUSALOPS_JWT_SECRET` immediately after first deployment
4. Use distinct credentials per environment (dev / staging / production)
5. Use a read-only database user for reporting/analytics access

**Docker Compose production pattern:**
```bash
# Export secrets from your secrets manager, then:
export POSTGRES_PASSWORD=$(vault kv get -field=password secret/causalops/postgres)
export CAUSALOPS_JWT_SECRET=$(vault kv get -field=jwt_secret secret/causalops/api)
docker compose -f docker-compose.prod.yml up -d
```

### 4. Secret Inventory

| Secret | Variable | Used By | Required |
|---|---|---|---|
| PostgreSQL password | `POSTGRES_PASSWORD` | postgres, causalops-api, microservices | ✅ |
| JWT signing secret | `CAUSALOPS_JWT_SECRET` | causalops-api | ✅ (when auth enabled) |
| Admin initial password | `CAUSALOPS_ADMIN_PASSWORD` | causalops-api (bootstrap) | ✅ (first run) |
| Admin username | `CAUSALOPS_ADMIN_USER` | causalops-api | Optional (default: admin) |

### 5. CORS Configuration

**Development (permissive):**
```
CAUSALOPS_CORS_ORIGINS=http://localhost:3000,http://localhost:5173
```

**Production (explicit):**
```
CAUSALOPS_CORS_ORIGINS=https://causalops.yourcompany.com
```

The AI engine (`ai-engine`) uses the same `CAUSALOPS_CORS_ORIGINS` variable. The wildcard `*` is forbidden in production.

### 6. Logging Safety Rules

- Logs MUST NOT contain: passwords, tokens, API keys, full request bodies with credentials
- Structured JSON logging includes: `correlation_id`, `incident_id`, error codes — never sensitive values
- The `Phase7Middleware` logs request method, path, status, and latency — never body content
- Stack traces are suppressed from API responses; they appear only in server-side logs

### 7. API Error Responses

Safe error format (no internal paths or stack traces exposed):
```json
{
  "error_code": "INCIDENT_NOT_FOUND",
  "message": "Incident 'INC-001' not found.",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "retryable": false,
  "details": {}
}
```

### 8. Frozen Model Artifact Protection

The following directories contain frozen, validated ML artifacts:
- `dataset/tg_v1/` — frozen graph telemetry benchmark
- `dataset/ml_v1/` — frozen classical ML dataset
- `ml/models/causal_scm/` — frozen Phase 3B SCM
- `ml/models/failure_prediction/` — frozen Phase 6A prediction models

**Prohibited in production:**
- Retraining or modifying frozen model files
- Replacing artifact files without checksum verification and phase approval
- Silently ignoring missing artifacts (startup will fail with a clear error)

Checksum baseline: `ml/failure_prediction/audit/checksums.json`

### 9. Authentication Roles

| Role | Capabilities |
|---|---|
| `VIEWER` | Read incidents, predictions, simulations, health, metrics |
| `OPERATOR` | VIEWER + approve remediation, execute permitted actions |
| `ADMIN` | OPERATOR + operational configuration changes |

Authentication is **additive** over Phase 5 safety gates:
- Explicit remediation approval is still required regardless of role
- Approval identity is still recorded in the audit timeline
- Allowlists, rollback policies, and blast-radius limits are unchanged

### 10. Prohibited Patterns (Code Review Checklist)

- [ ] No hardcoded passwords or tokens in any `.py`, `.java`, `.ts`, `.yml` file
- [ ] No `allow_origins=["*"]` in production CORS config
- [ ] No `print()` or `logger.info()` calls that include request body content
- [ ] No stack traces in `JSONResponse` error bodies
- [ ] No model artifact modifications outside a controlled retraining pipeline
- [ ] No `.env` file in `git status --short` output
