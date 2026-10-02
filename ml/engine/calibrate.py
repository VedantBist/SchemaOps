"""Calibration: learn an environment from its own telemetry, evaluate honestly, register.

Steps
  1. Load up to 7 days of measured telemetry and the current topology.
  2. Turn recorded fault injections into labelled episodes (if any exist).
  3. Fit the anomaly gate on undisturbed samples, the SCM on everything (disturbances are what
     reveal how problems propagate), and the forecaster on SLO-breach onsets.
  4. Evaluate without leakage: gate false-positive rate on undisturbed samples held out in time;
     detection and RCA on every labelled episode with the SCM refitted without that episode
     (leave-one-episode-out); forecasting with grouped cross-validation.
  5. Quality gates decide whether the environment may become ACTIVE.
  6. Champion/challenger: a new model is promoted only if it is not worse than the current
     champion on the same recent data.
"""
from __future__ import annotations

import logging
import threading
import traceback
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import rca as rca_mod
from .anomaly import AnomalyModel
from .episodes import Episode, disturbed_mask, from_faults, groups
from .forecast import Forecaster
from .model import EnvironmentModel, load
from .scm import LaggedSCM
from .store import Store
from .topology import Topology
from .window import Frame, build_frame, slo_breach, usable_columns

log = logging.getLogger("causalops.engine.calibration")

MAX_HISTORY = timedelta(days=7)
HOLDOUT_FRACTION = 0.3
MIN_EPISODES_FOR_ACTIVE = 3
MIN_DETECTION_RECALL = 0.8
MIN_RCA_TOP1 = 0.6
PROMOTION_TOLERANCE = 0.02


def start(store: Store, environment_id: str, mode: str, trigger: str) -> str:
    """Registers a RUNNING run and calibrates in a background thread."""
    if mode not in ("INITIAL", "RETRAIN", "MANUAL"):
        raise ValueError("mode must be INITIAL, RETRAIN or MANUAL")
    if store.has_running_calibration(environment_id):
        raise RuntimeError("A calibration run is already in progress for this environment")
    run_id = store.start_run(environment_id, mode, trigger)
    threading.Thread(target=_run, args=(store, environment_id, run_id), daemon=True, name=f"calibration-{run_id}").start()
    return run_id


def _run(store: Store, environment_id: str, run_id: str) -> None:
    try:
        result = calibrate(store, environment_id, run_id)
        store.finish_run(run_id, status="SUCCEEDED", **result)
    except Exception as e:  # recorded on the run; the platform reports it on the environment
        log.error("Calibration %s failed: %s\n%s", run_id, e, traceback.format_exc())
        store.finish_run(run_id, status="FAILED", error=f"{type(e).__name__}: {e}")


def load_data(store: Store, environment_id: str, data_from: datetime, data_to: datetime) -> tuple[Frame, Topology, list[Episode]]:
    nodes, edges = store.topology(environment_id)
    topology = Topology.from_rows(nodes, edges)
    frame = build_frame(store.telemetry(environment_id, data_from, data_to),
                        store.edge_telemetry(environment_id, data_from, data_to), topology)
    episodes = from_faults(store.faults(environment_id, data_from, data_to))
    return frame, topology, episodes


def calibrate(store: Store, environment_id: str, run_id: str) -> dict:
    env = store.environment(environment_id)
    cfg = env.config
    analysis, slo, service_slos = cfg["analysis"], cfg["slo"], cfg.get("serviceSlos") or {}
    first, last = store.data_span(environment_id)
    if first is None:
        raise ValueError("No telemetry stored for this environment yet")
    data_from = max(first, last - MAX_HISTORY)
    frame, topology, episodes = load_data(store, environment_id, data_from, last)
    minutes = (frame.data.index.max() - frame.data.index.min()).total_seconds() / 60
    if minutes < cfg["calibration"]["minLearningMinutes"]:
        raise ValueError(f"Only {minutes:.0f} minutes of telemetry; {cfg['calibration']['minLearningMinutes']} are required")

    columns = usable_columns(frame)
    data = frame.data[columns]
    breach_any = slo_breach(frame, slo, service_slos).any(axis=1)
    quiet = ~disturbed_mask(data.index, episodes) & ~breach_any
    clean = data[quiet]
    if len(clean) < 120:
        raise ValueError(f"Only {len(clean)} undisturbed samples to learn normal behaviour from; need at least 120")

    fit_args = dict(z_floor=analysis["anomalyZFloor"], max_fpr=analysis["maxGateFalsePositiveRate"],
                    consecutive=analysis["gateConsecutiveSamples"])
    anomaly = AnomalyModel.fit(clean, **fit_args)
    model_frame = Frame(data, frame.slo_latency, frame.slo_error, frame.segment, frame.step_seconds)
    scm = LaggedSCM.fit(model_frame, columns, anomaly.baselines, topology, analysis["lagOrder"],
                        analysis["ridgeAlpha"], bootstrap=analysis["bootstrapModels"])
    forecaster = Forecaster.fit(model_frame, anomaly, topology, slo, service_slos,
                                analysis["forecastHorizonsSeconds"], groups(data.index, episodes))

    metrics = {
        "data": {"from": str(data.index.min()), "to": str(data.index.max()), "samples": int(len(data)),
                 "undisturbed_samples": int(len(clean)), "step_seconds": frame.step_seconds,
                 "variables": len(columns), "nodes": len(topology.nodes), "edges": len(topology.edges),
                 "labelled_episodes": len(episodes)},
        "gate": evaluate_gate(model_frame, quiet, fit_args),
        "scm": scm.summary(),
        "forecast": forecaster.summary(),
    }
    if episodes:
        metrics["episodes"] = evaluate_episodes(model_frame, anomaly, topology, episodes, analysis, columns)

    passed, reasons = quality(metrics, analysis)
    version = f"{env.name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    model = EnvironmentModel(environment_id, version, topology, columns, frame.step_seconds, anomaly, scm, forecaster,
                             slo, service_slos, analysis, str(data.index.min()), str(data.index.max()), metrics)
    path, checksum = model.save()

    promote, decision = champion_challenger(store, environment_id, model, model_frame, quiet, episodes, metrics)
    status = "CHAMPION" if promote else "CHALLENGER"
    store.register_model(environment_id, version=version, status=status, run_id=run_id,
                         data_from=data.index.min().to_pydatetime(), data_to=data.index.max().to_pydatetime(),
                         artifact_path=path, checksum=checksum, metrics=metrics,
                         components={"anomaly": "robust z + isolation forest", "scm": "topology-constrained lagged ridge",
                                     "forecast": {h.horizon_seconds: h.method for h in forecaster.horizons},
                                     "rca": "residual + anomaly + precedence + graph"})
    gate_text = "quality gates passed" if passed else "quality gates not met: " + "; ".join(reasons)
    return {"data_from": data.index.min().to_pydatetime(), "data_to": data.index.max().to_pydatetime(),
            "model_version": version, "promoted": promote, "quality_passed": passed,
            "decision": f"{decision}; {gate_text}", "metrics": metrics}


def evaluate_gate(frame: Frame, quiet: pd.Series, fit_args: dict) -> dict:
    """False-positive rate on undisturbed samples the evaluation model never saw (later in time)."""
    idx = np.flatnonzero(quiet.to_numpy())
    if len(idx) < 200:
        return {"note": "too few undisturbed samples for a held-out estimate"}
    cut = idx[int(len(idx) * (1 - HOLDOUT_FRACTION))]
    train = frame.data.iloc[:cut][quiet.iloc[:cut].to_numpy()]
    model = AnomalyModel.fit(train, **fit_args)
    flagged = model.flagged(frame.data).any(axis=1)
    test = quiet.copy()
    test.iloc[:cut] = False
    fpr = float(flagged[test].mean()) if test.any() else None
    return {"heldout_false_positive_rate": None if fpr is None else round(fpr, 5),
            "heldout_samples": int(test.sum()), "threshold_quantile": 1 - fit_args["max_fpr"]}


def evaluate_episodes(frame: Frame, anomaly: AnomalyModel, topology: Topology, episodes: list[Episode],
                      analysis: dict, columns: list[str]) -> dict:
    """Detection delay and leave-one-episode-out RCA accuracy on every labelled episode."""
    flagged = anomaly.flagged(frame.data).any(axis=1)
    link_units = {u.name for u in rca_mod.units(topology, columns) if u.kind == "link"}
    rows = []
    for e in episodes:
        window = (frame.data.index >= e.start) & (frame.data.index <= e.end + pd.Timedelta(seconds=60))
        hits = frame.data.index[window & flagged.to_numpy()]
        detected_at = hits.min() if len(hits) else None
        delay = (detected_at - e.start).total_seconds() if detected_at is not None else None

        # Leave this episode out of the SCM fit, then analyse it as the platform would.
        held = frame.data.copy()
        held.loc[(held.index >= e.start - pd.Timedelta(seconds=60)) & (held.index <= e.end + pd.Timedelta(seconds=150))] = np.nan
        scm = LaggedSCM.fit(Frame(held, frame.slo_latency, frame.slo_error, frame.segment, frame.step_seconds),
                            columns, anomaly.baselines, topology, analysis["lagOrder"], analysis["ridgeAlpha"])
        t_eval = (detected_at or e.start + pd.Timedelta(seconds=30)) + pd.Timedelta(seconds=analysis["rcaDelaySeconds"])
        sel = (frame.data.index >= e.start - pd.Timedelta(seconds=120)) & (frame.data.index <= t_eval)
        sub = Frame(frame.data[sel], frame.slo_latency[sel], frame.slo_error[sel], frame.segment[sel], frame.step_seconds)
        expected = e.expected_units(topology, link_units)
        try:
            ranked = rca_mod.rank(sub, anomaly, scm, topology, analysis["rcaWeights"],
                                  analysis_start=e.start - pd.Timedelta(seconds=30))["candidates"]
        except Exception as ex:  # an episode the model cannot analyse counts as a miss
            ranked, err = [], str(ex)
        else:
            err = None
        names = [c["service"] for c in ranked]
        rows.append({"type": e.type, "target": e.target, "start": e.start.isoformat(),
                     "concurrent": e.concurrent,
                     "detected": detected_at is not None, "detection_delay_s": delay,
                     "expected": sorted(expected), "predicted": names[:3],
                     "top1": bool(names) and names[0] in expected,
                     "top2": any(n in expected for n in names[:2]), "error": err})
    n = len(rows)
    detected = [r for r in rows if r["detected"]]
    delays = [r["detection_delay_s"] for r in detected]
    single = [r for r in rows if not r["concurrent"]]   # one root cause: single-root accuracy applies
    by_type: dict[str, dict] = {}
    for r in single:
        t = by_type.setdefault(r["type"], {"episodes": 0, "top1": 0})
        t["episodes"] += 1
        t["top1"] += int(r["top1"])
    k = len(single) or 1
    return {"count": n, "single_root": len(single), "concurrent_excluded_from_rca": n - len(single),
            "detection_recall": round(len(detected) / n, 4),
            "median_detection_delay_s": round(float(np.median(delays)), 1) if delays else None,
            "rca_top1_accuracy": round(sum(r["top1"] for r in single) / k, 4) if single else None,
            "rca_top2_accuracy": round(sum(r["top2"] for r in single) / k, 4) if single else None,
            "rca_by_fault_type": by_type, "method": "leave-one-episode-out", "per_episode": rows}


def quality(metrics: dict, analysis: dict) -> tuple[bool, list[str]]:
    reasons = []
    fpr = metrics["gate"].get("heldout_false_positive_rate")
    if fpr is None:
        reasons.append("no held-out estimate of the false-positive rate yet")
    elif fpr > analysis["maxGateFalsePositiveRate"]:
        reasons.append(f"held-out false-positive rate {fpr:.4f} exceeds {analysis['maxGateFalsePositiveRate']}")
    ep = metrics.get("episodes")
    if not ep or ep["single_root"] < MIN_EPISODES_FOR_ACTIVE:
        reasons.append(f"needs at least {MIN_EPISODES_FOR_ACTIVE} labelled single-root incidents to validate detection "
                       f"and RCA (has {0 if not ep else ep['single_root']})")
    else:
        if ep["detection_recall"] < MIN_DETECTION_RECALL:
            reasons.append(f"detection recall {ep['detection_recall']:.2f} below {MIN_DETECTION_RECALL}")
        if ep["rca_top1_accuracy"] < MIN_RCA_TOP1:
            reasons.append(f"RCA top-1 accuracy {ep['rca_top1_accuracy']:.2f} below {MIN_RCA_TOP1}")
    return not reasons, reasons


def _score(metrics: dict, analysis: dict) -> float:
    """One number to compare models: mean of the available normalized quality metrics."""
    parts = []
    fpr = metrics.get("gate", {}).get("heldout_false_positive_rate")
    if fpr is not None:
        parts.append(max(0.0, 1.0 - fpr / analysis["maxGateFalsePositiveRate"] / 2))
    ep = metrics.get("episodes")
    if ep:
        parts += [ep["detection_recall"]] + ([ep["rca_top1_accuracy"]] if ep["rca_top1_accuracy"] is not None else [])
    return float(np.mean(parts)) if parts else 0.0


def champion_challenger(store: Store, environment_id: str, challenger: EnvironmentModel, frame: Frame,
                        quiet: pd.Series, episodes: list[Episode], metrics: dict) -> tuple[bool, str]:
    champ_row = store.champion(environment_id)
    if champ_row is None:
        return True, "first model for this environment: promoted to champion"
    learning_started = pd.Timestamp(store.environment(environment_id).learning_started_at)
    if champ_row.get("data_to") is not None and pd.Timestamp(champ_row["data_to"]) < learning_started:
        return True, (f"current champion {champ_row['version']} was trained on telemetry from before learning restarted "
                      f"at {learning_started.isoformat()} (metric definitions changed): replaced")
    try:
        champion = load(environment_id, champ_row["version"], champ_row["artifact_path"], champ_row["checksum"])
    except Exception as e:
        return True, f"current champion {champ_row['version']} could not be loaded ({e}): replaced"
    analysis = challenger.analysis
    # The champion is scored on the same recent data and the same labelled episodes.
    cols = [c for c in champion.columns if c in frame.data.columns]
    champ_metrics: dict = {}
    flagged = champion.anomaly.flagged(frame.data.reindex(columns=champion.columns)).any(axis=1)
    recent = quiet.copy()
    recent.iloc[: int(len(recent) * (1 - HOLDOUT_FRACTION))] = False
    if recent.any():
        champ_metrics["gate"] = {"heldout_false_positive_rate": round(float(flagged[recent].mean()), 5)}
    # Leave-one-episode-out refits the SCM for every episode, so scoring the champion's gate and
    # RCA on all episodes is leak-free and compares both models on exactly the same incidents.
    if episodes:
        champ_metrics["episodes"] = evaluate_episodes(frame, champion.anomaly, champion.topology, episodes, analysis, cols)
    new, old = _score(metrics, analysis), _score(champ_metrics, analysis)
    if new + PROMOTION_TOLERANCE >= old:
        return True, f"challenger {challenger.version} scored {new:.3f} vs champion {champion.version} {old:.3f}: promoted"
    return False, f"challenger {challenger.version} scored {new:.3f} vs champion {champion.version} {old:.3f}: champion kept"
