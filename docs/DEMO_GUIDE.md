# CausalOps: Setup and Demo Guide

CausalOps watches a running microservice system through OpenTelemetry. It does five things, each learned from that system's own telemetry:
1. **Detects** incidents, from SLO breaches and from a calibrated anomaly gate that can fire before an SLO is crossed.
2. **Explains** them, by naming the root cause with a topology-constrained causal model.
3. **Estimates** the impact of fixing the root cause, with a counterfactual simulation.
4. **Fixes** them with tiered auto-remediation: Docker, Kubernetes or runbook webhooks. Fixes are verified on real measurements, rolled back if they don't help, and escalated to a human when nothing safe is left.
5. **Measures** detection time, MTTR and downtime.

Nothing in the UI is mocked. Every number comes from the API, and a missing value shows as "—" or an empty state.

---

## 1. Setup

```bash
bash scripts/demo/start.sh
```

One command builds and starts everything, including the console on **http://localhost:3000**. The demo snapshot is restored automatically, so the `reference` environment starts **ACTIVE**.

On a Mac, follow **[MAC_SETUP_AND_DEMO.md](MAC_SETUP_AND_DEMO.md)**: prerequisites, the exact demo script and troubleshooting. It supersedes the script below.

---

## 2. Demo script (15–20 minutes)

### Act 1: "It understands the system" (3 min)
1. **Overview.** Point out:
   - the service graph discovered from traces (nobody drew it);
   - live p99, error rate and traffic per service;
   - the model lifecycle (ACTIVE, next weekly retrain);
   - measured MTTR from real incident history.
2. **Topology.** Click a node to see its live measurements and its call edges with rates.
3. **Calibration.** Show what the platform learned about *this* system:
   - detection recall and median detection delay;
   - RCA top-1 / top-2 accuracy (leave-one-incident-out);
   - the gate's false-positive rate on held-out quiet data;
   - the causal-model fit (cross-validated R²).

   Then the per-incident validation table, and the model registry with SHA-256 checksums and champion/challenger.

### Act 2: "A real failure, fixed without a human" (5 min)
1. **Fault lab** → *Service failure (HTTP 503)* on `payment-service`, duration 600 s → **Inject fault**. This is a real fault inside the running service.
2. **Overview / Active incidents.** An incident opens within about 20–30 s, from the SLO breach or the anomaly gate. Open it.
3. On the **incident page**, walk the timeline as it fills in live:
   - **Detected** → **Root cause identified**: `payment-service`, with candidate scores and the evidence statements.
   - **Counterfactual chart:** "what if payment-service had stayed at baseline". Observed vs counterfactual gateway latency, with an ensemble uncertainty band and validity PASS.
   - **Remediation proposed:** *Restart the service's containers* (tier 1). Expand the policy to show the rules that passed (kill switch, calibrated, top root cause, RCA confidence, fresh telemetry, counterfactual valid, blast radius, budget, cooldown, no deployment in progress, …).
   - **Executing → Executed:** the real container restart through the Docker Engine API (`docker ps` shows it restarted).
   - **Recovery verified:** healthy samples counted against the SLO.
   - **Recovered:** the incident resolves, although the injected fault was still scheduled to run for minutes.
4. **Remediation center** → *Outcomes*: MTTR, time to mitigation and downtime for **auto-remediated** vs **not remediated** incidents, from real timestamps.
5. **Audit trail** at the bottom of the incident page: every decision, with the actor and why.

### Act 3: "Humans stay in control" (5 min)
1. **Remediation center** → set *Run automatically up to tier* to **none**. It's audited.
2. **Fault lab** → *Service latency* on `order-service`, 700 ms, 900 s.
3. When the incident opens, the proposal shows **Waiting for approval** and lists the rules that need a human.
4. Click **Approve & execute** on *Raise CPU and memory limits* (tier 2):
   - CausalOps changes the container's limits for real (`docker update`).
   - Verification fails because latency doesn't come down, so the change is **rolled back** automatically to the previous limits.
   - The next proposal (restart) is offered.
5. **Approve** the restart. It's verified and the incident resolves.
6. Optionally click **Engage kill switch**: every automatic and approved action is blocked (safety rule), and incidents escalate to a human.
7. Set autonomy back to tier **1** afterwards.

### Act 4: "Ask what-if" and "it knows its limits" (3 min)
- **Simulation:** pick any past incident and any component (or call link), choose how much of its deviation to remove, and run the SCM counterfactual. The result shows its validity checks.
- **Fault lab** → *Database latency* on `inventory-db`, 400 ms:
  - RCA names `inventory-db`.
  - No enabled executor can fix a database, so CausalOps does **not** restart a healthy service. It escalates to a human, and the timeline explains why ("no enabled action addresses inventory-db (database)").
  - Enabling a runbook webhook in the Setup wizard would give it a path.
- **Changes:** deployments and maintenance are recorded (CI/CD can `POST /api/changes`). They're kept out of model training, block automation while in progress, and are correlated with new incidents.

### Act 5: "Any system" (2 min)
**Setup wizard:**
1. Endpoints → **Check connection**: live series counts per metric template and the discovered topology.
2. SLOs.
3. Executors (Docker / Kubernetes / signed webhooks).
4. Autonomy tier.
5. Learning window and weekly retraining.

Any service with OpenTelemetry instrumentation works; no code changes are needed.

**Logs / Traces / Metrics:** live from Loki, Tempo and Prometheus, with logs linked to traces.

---

> **Pacing:** wait about 5 minutes between faults. After an automatic restart the JVM warms up for a few minutes, and a new fault injected during that window can briefly mislead the first root-cause pass (a re-analysis corrects it).

## 3. Reset between demo runs

```bash
curl -X POST localhost:8080/api/faults/clear
```

Then:
- In Remediation center, set autonomy back to tier 1 and the kill switch off.
- Wait until **Active incidents** is empty (about 1 min after faults stop).

The verification scripts replay the acts end to end and print PASS/FAIL:

```bash
python scripts/dev/verify_phase3.py --incident
```

```bash
python scripts/dev/verify_phase4.py
```

---

## 4. Talking points and honest limits

- **Learned, not hand-coded.** Baselines, the anomaly gate, the causal model and forecasters are fitted per environment and validated on labelled incidents. A new model replaces the champion only if it is not worse, or if the champion fails the quality gates on current data.
- **Tiered autonomy is the USP.** Reversible, low-blast-radius fixes run on their own. Everything else is a recommendation with its evidence, the predicted benefit, and the learned success rate of that action in this environment.
- **Forecasting is honest.** On this system's abrupt faults the learned forecaster had no skill (AUC < 0.7). The platform says so and uses trend extrapolation instead of inventing probabilities.
- **Weakest RCA case:** network-path latency. The link is usually ranked second, behind its caller.
- **Not yet built (Phase 6/7):**
  - Sign-in and roles (the console runs in a labelled "auth not enabled" mode).
  - A Helm chart and in-cluster Kubernetes validation. The Kubernetes and webhook executors are tested against API fakes only.
  - The regenerated evidence report from long chaos campaigns.
