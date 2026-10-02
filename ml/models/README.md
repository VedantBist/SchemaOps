# Archived: synthetic `tg_v1` reference benchmark

Every artifact in this directory was trained on the **synthetic `tg_v1` dataset**: telemetry the pre-Phase-2
backend computed from a formula (`baseline + latencyMs * 0.62^hops`, p50 = 0.60*p99, constant 60 req/min).
They are kept as a historical, synthetic reference benchmark only.

- Nothing in the serving path loads them. Since Phase 3, the AI engine (`ml/engine/`) learns per-environment
  models from real telemetry and stores them in the model registry (`model_registry` table plus the
  `ENGINE_MODEL_DIR` volume). Champion/challenger promotion and weekly retraining apply there.
- The copies previously bundled into `ai-engine/app/models/` were removed in Phase 3. The originals here are untouched.
- The legacy research and evaluation scripts under `ml/` (and the `scripts/phase8_*` evidence) still reference
  these paths. Phase 7 rewrites that evidence on real telemetry. These files then move to
  `ml/models/archive/tg_v1_synthetic/`, and every number derived from them is reported as synthetic.
