# CausalOps on a Mac: Setup and Exact Demo Script

This guide takes a Mac from `git clone` to a working demo with **one command**. The repository ships a **demo snapshot**: the reference environment already calibrated and ACTIVE, with its trained model, incident history and outcome metrics. Nothing needs to be recalibrated on the Mac.

---

## Part A: One-time setup (do this the day before)

### A1. Install the prerequisites
1. **Docker Desktop for Mac.** Pick *Apple Silicon* or *Intel* to match your Mac (Apple menu → About This Mac).
   - Download: https://www.docker.com/products/docker-desktop/
   - Start it once and wait until the whale icon says *Docker Desktop is running*.
2. **Docker Desktop → Settings → Resources:** at least **4 CPUs** and **8 GB memory** (6 CPUs / 10 GB if you can). Then **Apply & restart**.
3. **Docker Desktop → Settings → Advanced:** make sure *Allow the default Docker socket to be used* is **ticked**. CausalOps restarts containers through `/var/run/docker.sock`.
4. **Git.** Open *Terminal* and run `git --version`. If macOS offers to install the Command Line Tools, accept.

### A2. Get the code and start everything
```bash
git clone <your-github-repo-url> causalops
```
```bash
cd causalops
```
```bash
bash scripts/demo/start.sh
```
The first run downloads base images and builds the services, which takes **5–15 minutes** and needs internet. The script:
- builds and starts all containers: the reference shop, OpenTelemetry, Prometheus, Tempo, Loki, Grafana, Postgres, the API, the AI engine and the console;
- restores the demo snapshot into the fresh database and model store;
- waits until the API is healthy;
- records a 10-minute "warm-up" window, so the cold start does not trigger automatic remediation.

At the end it prints:
```
CausalOps is up.   Environment status: ACTIVE
  Console     http://localhost:3000
```

### A3. Check that it works
1. Open **http://localhost:3000** in Chrome or Safari.
2. Check the header: environment `reference` shows **ACTIVE**, and the live dot is green.
3. Check the bottom-left **Platform health**: every dot is green (api, database, aiEngine, prometheus, tempo, loki, collector, ingestion).
4. **Overview:** the service graph shows 5 nodes (api-gateway → order-service → inventory-service → inventory-db, and order-service → payment-service) with live p99 values.
5. **Optional full rehearsal** (about 15 minutes; it injects real faults):
   ```bash
   python3 scripts/dev/verify_phase4.py --skip-control
   ```
   Afterwards run `bash scripts/demo/reset.sh`.

### A4. Stopping and starting again
- **Stop** (data kept): `docker compose stop`
- **Start again** (fast; no rebuild needed): `bash scripts/demo/start.sh`
- **Back to the pristine snapshot** (deletes local demo data): `docker compose down -v`, then `bash scripts/demo/start.sh`

---

## Part B: On demo day

**T−30 min:**
1. Start Docker Desktop.
2. Run `bash scripts/demo/start.sh`.
3. Open http://localhost:3000.

The JVMs need about 10 minutes to warm up. Starting early also gives the anomaly gate this Mac's own normal behaviour to compare against.

**T−5 min:**
1. Run `bash scripts/demo/reset.sh`.
2. Check that **Active incidents** shows nothing.
3. Check that **Remediation center** shows *Run automatically up to tier* = **1** and the kill switch is off.
4. Keep two browser tabs open:
   - **Tab 1:** http://localhost:3000/#/overview
   - **Tab 2:** http://localhost:3000/#/faults

> **Golden rule:** wait about **5 minutes** after any automatic restart before injecting the next fault. A JVM that has just restarted runs slower for a few minutes and can mislead the first root-cause pass.

---

## Part C: The demo script (about 20 minutes)

### Act 1: "It understands the system" (3 min)
| Do | Say |
|---|---|
| **Overview** | "This is a live shop of 5 services. Nobody drew this graph: CausalOps discovered it from OpenTelemetry traces. Every number is live from Prometheus." |
| Point at **Models: ACTIVE** and **Median MTTR** | "The platform learned this system by itself and only became ACTIVE after passing quality gates on labelled incidents." |
| **Calibration** | "Detection recall 100%, median 22 s. The right root cause is ranked first 83% of the time and in the top two 100%. Causal-model fit R² 0.94, no false alarms on quiet data. Each row in the table is a real injected incident." |
| **Topology** → click **order-service** | "Live measurements per node and per call edge." |

### Act 2: "A real failure, fixed with no human" (5 min)
| Do | Say |
|---|---|
| **Fault lab** → Fault *Service failure (HTTP 503)* → Target **payment-service** → Duration **600** → **Inject fault** | "I am breaking the payment service for real; it now answers every request with errors, for 10 minutes." |
| Switch to **Overview** and wait about 20–30 s until the red incident pill appears in the header; click it | "Detected within seconds, by the SLO breach or the anomaly gate." |
| On the **incident page**, read the **Timeline** from the top | "Detected → root cause identified." |
| Point at the **graph** (payment-service outlined red, *ROOT*) and the **Root cause analysis** panel | "Three services are alarming, but the causal model says payment-service started it, and why: its deviation is not explained by its dependencies." |
| **Counterfactual** panel → select **error rate (%)** and **api-gateway** | "If payment-service had stayed normal, this is what users would have seen. The band is the model's uncertainty." |
| **Recommended mitigations / Executions** | "Tier-1 action: restart its containers. Expand *policy* to see every rule that passed: kill switch, calibrated models, top root cause, confidence, fresh telemetry, blast radius, budget, cooldown, no deployment in progress." |
| Wait about 1–2 min: **Executed → Recovery verified → Recovered** | "The container was really restarted through the Docker API, recovery was verified on real measurements, and the incident resolved, although the fault was scheduled to last 10 minutes." |
| Optional: run `docker ps` in Terminal | Shows payment-service "Up 1 minute". |
| Scroll to **Audit trail** | "Every decision is recorded: who, what and why." |
| **Remediation center → Outcomes** | "MTTR and downtime for auto-remediated versus not-remediated incidents, measured from real timestamps." |

*Wait about 5 minutes, or run `bash scripts/demo/reset.sh`, before Act 3.* Use the time to show Logs, Traces and Metrics (Act 5).

### Act 3: "Humans stay in control" (5 min)
| Do | Say |
|---|---|
| **Remediation center** → *Run automatically up to tier* → **none** | "Now nothing runs without approval, and the change itself is audited." |
| **Fault lab** → *Service latency* → **order-service** → 700 ms → 900 s → **Inject fault** | |
| Open the new incident from the header pill and wait for **Waiting for approval** | "It proposes fixes for the root cause, with the predicted benefit and the learned success rate, but it asks first. The policy lists exactly why." |
| On the **order-service** proposal *Restart the service's containers*, click **Approve & execute** | "One click. It executes, verifies and resolves." |
| Optional: **Engage kill switch** in Remediation center | "One switch stops all automation." Release it afterwards. |
| **Remediation center** → tier back to **1** | |

> If you try to approve an action that was already executed for the same incident, you'll see **HTTP 409 · blocked: NOT_ALREADY_TRIED**. That is the safety rule refusing to repeat a fix that already failed.

### Act 4: "What-if, and knowing its limits" (4 min)
| Do | Say |
|---|---|
| **Simulation** → pick a past incident → component **order-service** → 100% → **Run** | "Ask what-if on any past incident. The causal model abducts what happened and replays it with the fix, with validity checks." |
| **Fault lab** → *Database latency* → **inventory-db** → 400 ms → 300 s | |
| Open the incident | "Root cause: inventory-db. No enabled executor can fix a database, so it does **not** restart a healthy service. It escalates to a human, and the timeline says why. A runbook webhook would give it a path." |
| **Changes** | "Deployments and maintenance are recorded, kept out of training, block automation while in progress, and get correlated with new incidents." |

### Act 5: "Any system" (3 min)
| Do | Say |
|---|---|
| **Setup wizard** → **Check connection** | "Point it at any OpenTelemetry + Prometheus stack: live series counts per metric, and the topology it would discover." |
| Show the steps: SLOs → Executors (Docker / Kubernetes / signed webhooks) → Autonomy tier → Learning window & weekly retraining | "Learns in at most 24 hours, retrains every 7 days, and only promotes a model that passes the gates." |
| **Logs** → service *order-service* → Search; click **trace** | "Logs from Loki, linked to traces from Tempo." |

### Close (30 s)
"CausalOps closes the loop that monitoring tools leave open: it detects the problem, names the cause, shows what the fix will change, fixes it safely or asks you, and proves it worked."

After the demo: `bash scripts/demo/reset.sh`.

---

## Part D: Troubleshooting

| Symptom | Fix |
|---|---|
| `start.sh` says Docker is not running | Start Docker Desktop and wait for *running*, then run it again. |
| A build step fails with a network or TLS error | Run `bash scripts/demo/start.sh` again. Layers already downloaded are cached. |
| Port already in use (3000, 8080, 8000, 3001, 5432) | Quit the other program, or set another port, e.g. `CAUSALOPS_UI_PORT=3100 bash scripts/demo/start.sh`. |
| Header shows LEARNING instead of ACTIVE | The volumes existed before the snapshot was added. Run `docker compose down -v`, then `bash scripts/demo/start.sh`. |
| Incidents open right after starting | JVM warm-up. Wait 10 minutes; they resolve on their own and automation is held during the warm-up window. |
| Remediation fails with "no running container of compose service" | The stack must run under the project name `causalops`; don't pass `-p`. Check Docker Desktop's default socket setting (A1, step 3). |
| A platform-health dot stays red | `docker compose ps`, then `docker compose restart <service>`. |
| Everything is slow | Give Docker Desktop more CPUs and memory (A1, step 2). |
| False incidents keep opening on a slower Mac | After 30 minutes of running: **Calibration → Calibrate now** (about 1 minute). |

---

## Part E: What runs where

| URL | What |
|---|---|
| http://localhost:3000 | CausalOps console |
| http://localhost:8080/api | Platform API (Spring Boot); `/actuator/health` |
| http://localhost:8000/docs | AI engine (FastAPI) |
| http://localhost:3001 | Grafana (RED dashboard; Prometheus, Tempo, Loki) |

**Containers:**
- **Reference system:** api-gateway, order-service, inventory-service, payment-service, Postgres (inventory-db), Toxiproxy, load generator.
- **Telemetry:** otel-collector, Prometheus, Tempo, Loki, Grafana.
- **Platform:** postgres (platform DB), ai-engine, causalops-api, frontend (nginx).
