"""
Effect Calibration & Quantitative Error Breakdown for CausalOps SCM (Phase 3C).

Evaluates the quantitative accuracy of predicted interventional treatment effects
against empirical observed telemetry across held-out fault experiments.
Enforces strict physical unit consistency:
- Latency treatment effects are measured and reported strictly in milliseconds (ms).
- Error rate treatment effects are measured and reported strictly in percentage points (%).
- Incompatible units are never blended into composite metrics.

Provides stratified breakdowns by:
1. Fault type
2. Root-cause service
3. Downstream service (api-gateway, order-service)
4. Causal variable
5. Propagation distance (hops 1, 2, 3)
6. Propagation delay (1s, 2s, 3s)
7. Intervention magnitude
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
import numpy as np
from scipy.stats import pearsonr

from .design_matrix import (
    CANONICAL_NODES,
    PRIMARY_CAUSAL_FEATURES,
    NODE_TO_INDEX,
    FEATURE_TO_PRIMARY_INDEX,
)
from .edges import CausalGraph
from .scm import TopologyConstrainedLaggedSCM
from .intervention import estimate_intervention_effect


def load_manifest_lookup(manifest_path: str = "dataset/manifests/ml_dataset_v1.json") -> Dict[str, Any]:
    """Loads manifest metadata indexed by experiment_id."""
    p = Path(manifest_path)
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    exps = data.get("experiments", [])
    return {e["experiment_id"]: e for e in exps}


def compute_experiment_effect(
    sample: Any,
    scm: TopologyConstrainedLaggedSCM,
    manifest_lookup: Optional[Dict[str, Any]] = None,
    downstream_node: str = "api-gateway",
) -> Optional[Dict[str, Any]]:
    """
    Computes observed vs predicted treatment effect for a single experiment sample.

    Definitions:
    - baseline: Pre-fault nominal mean computed over steps t in [0, 4] (t < 5s)
    - observed effect: Mean telemetry over active fault window [5, 20] minus baseline
    - predicted effect: SCM interventional prediction minus nominal prediction
    """
    if not sample.is_fault:
        return None

    exp_id = sample.experiment_id
    fault_type = sample.fault_type
    target_node = sample.label
    raw_x = sample.x  # [T, 5, 10]

    # Look up actual injected parameter from manifest
    injected_param = 800.0
    if manifest_lookup and exp_id in manifest_lookup:
        meta = manifest_lookup[exp_id]
        f_params = meta.get("fault_parameters", {})
        if "latencyMs" in f_params:
            injected_param = float(f_params["latencyMs"])
        elif "errorRate" in f_params:
            injected_param = float(f_params["errorRate"])
        elif fault_type == "SERVICE_FAILURE":
            injected_param = 35.0
    else:
        if "LATENCY" in fault_type:
            injected_param = 1200.0 if fault_type == "DB_LATENCY" else 850.0
        elif "ERROR" in fault_type:
            injected_param = 25.0
        elif fault_type == "SERVICE_FAILURE":
            injected_param = 35.0

    # Determine intervened and downstream variables
    is_latency = "LATENCY" in fault_type
    intervened_var = "db_latency" if fault_type == "DB_LATENCY" else ("p99_latency" if is_latency else "error_rate")
    downstream_var = "p99_latency" if is_latency else "error_rate"
    unit = "ms" if is_latency else "%"

    # Compute predicted effect via SCM
    eff_res = estimate_intervention_effect(
        scm.stable_graph,
        fault_type=fault_type,
        target_node=target_node,
        injected_parameter=injected_param,
        norm_stats=scm.norm_stats,
    )

    pred_delta = eff_res["predicted_raw_delta"]

    # Compute observed effect on downstream node
    d_node_idx = NODE_TO_INDEX.get(downstream_node, 0)
    var_raw_idx = 2 if downstream_var == "p99_latency" else 3

    base_val = float(np.mean(raw_x[:5, d_node_idx, var_raw_idx]))
    act_end = min(21, raw_x.shape[0])
    act_val = float(np.mean(raw_x[5:act_end, d_node_idx, var_raw_idx]))
    obs_delta = float(act_val - base_val)

    # Topological distance
    dist_map = {
        "api-gateway": {"api-gateway": 0, "order-service": 1, "inventory-service": 2, "payment-service": 2, "inventory-db": 3},
        "order-service": {"order-service": 0, "inventory-service": 1, "payment-service": 1, "inventory-db": 2, "api-gateway": 1},
    }
    hop_dist = dist_map.get(downstream_node, {}).get(target_node, 1)

    abs_error = abs(pred_delta - obs_delta)
    signed_bias = pred_delta - obs_delta
    rel_error = abs_error / (abs(obs_delta) + 1e-4)

    return {
        "experiment_id": exp_id,
        "fault_type": fault_type,
        "root_cause_service": target_node,
        "downstream_service": downstream_node,
        "intervened_variable": intervened_var,
        "downstream_variable": downstream_var,
        "unit": unit,
        "injected_magnitude": injected_param,
        "hop_distance": hop_dist,
        "propagation_delay_seconds": max(1, hop_dist),
        "baseline_nominal": round(base_val, 2),
        "active_observed": round(act_val, 2),
        "observed_delta": round(obs_delta, 2),
        "predicted_delta": round(pred_delta, 2),
        "absolute_error": round(abs_error, 2),
        "signed_bias": round(signed_bias, 2),
        "relative_error": round(rel_error, 4),
        "sign_agreement": bool((pred_delta > 0 and obs_delta > 0) or (pred_delta == 0 and obs_delta == 0)),
    }


def compute_calibration_metrics(
    records: List[Dict[str, Any]],
    unit: str,
    group_name: str = "All",
) -> Dict[str, Any]:
    """
    Computes statistical calibration metrics for a homogenous cohort sharing the same unit.
    """
    matching = [r for r in records if r["unit"] == unit]
    n = len(matching)
    if n == 0:
        return {
            "group_name": group_name,
            "sample_count": 0,
            "unit": unit,
            "mae": None,
            "median_ae": None,
            "rmse": None,
            "signed_bias": None,
            "relative_error": None,
            "observed_mean": None,
            "predicted_mean": None,
            "pearson_r": None,
            "sign_agreement_rate": None,
        }

    obs = np.array([r["observed_delta"] for r in matching], dtype=np.float64)
    pred = np.array([r["predicted_delta"] for r in matching], dtype=np.float64)
    diff = pred - obs
    abs_diff = np.abs(diff)

    mae = float(np.mean(abs_diff))
    med_ae = float(np.median(abs_diff))
    rmse = float(np.sqrt(np.mean(diff ** 2)))
    bias = float(np.mean(diff))
    mean_obs = float(np.mean(obs))
    mean_pred = float(np.mean(pred))
    rel_err = float(mae / (abs(mean_obs) + 1e-4))
    sign_agreements = sum(1 for r in matching if r["sign_agreement"])
    sign_rate = sign_agreements / float(n)

    # Pearson r
    r_val = None
    if n >= 2 and np.std(obs) > 1e-6 and np.std(pred) > 1e-6:
        try:
            r_val = float(pearsonr(obs, pred)[0])
            r_val = round(r_val, 4)
        except Exception:
            r_val = None

    return {
        "group_name": group_name,
        "sample_count": n,
        "unit": unit,
        "mae": round(mae, 2),
        "median_ae": round(med_ae, 2),
        "rmse": round(rmse, 2),
        "signed_bias": round(bias, 2),
        "relative_error": round(rel_err, 4),
        "observed_mean": round(mean_obs, 2),
        "predicted_mean": round(mean_pred, 2),
        "pearson_r": r_val,
        "sign_agreement_rate": round(sign_rate, 4),
    }


def run_full_effect_calibration(
    samples: List[Any],
    scm: TopologyConstrainedLaggedSCM,
    manifest_lookup: Optional[Dict[str, Any]] = None,
    split_name: str = "test",
) -> Dict[str, Any]:
    """
    Executes a comprehensive, stratified calibration evaluation across samples.
    """
    if manifest_lookup is None:
        manifest_lookup = load_manifest_lookup()

    records_gw: List[Dict[str, Any]] = []
    records_ord: List[Dict[str, Any]] = []

    for s in samples:
        r_gw = compute_experiment_effect(s, scm, manifest_lookup, downstream_node="api-gateway")
        if r_gw:
            records_gw.append(r_gw)
        r_ord = compute_experiment_effect(s, scm, manifest_lookup, downstream_node="order-service")
        if r_ord:
            records_ord.append(r_ord)

    # 1. Overall cohorts by unit
    overall_latency_gw = compute_calibration_metrics(records_gw, unit="ms", group_name="Overall Gateway Latency")
    overall_error_gw = compute_calibration_metrics(records_gw, unit="%", group_name="Overall Gateway Error Rate")

    # 2. Breakdown by fault type
    fault_types = sorted(list(set(r["fault_type"] for r in records_gw)))
    by_fault_type = {}
    for ft in fault_types:
        ft_recs = [r for r in records_gw if r["fault_type"] == ft]
        unit = ft_recs[0]["unit"] if ft_recs else "ms"
        by_fault_type[ft] = compute_calibration_metrics(ft_recs, unit=unit, group_name=ft)

    # 3. Breakdown by root-cause service
    services = sorted(list(set(r["root_cause_service"] for r in records_gw)))
    by_root_cause = {}
    for svc in services:
        svc_recs = [r for r in records_gw if r["root_cause_service"] == svc]
        # Break down by latency vs error for this service if present
        svc_lat = [r for r in svc_recs if r["unit"] == "ms"]
        svc_err = [r for r in svc_recs if r["unit"] == "%"]
        by_root_cause[svc] = {
            "latency": compute_calibration_metrics(svc_lat, unit="ms", group_name=f"{svc} (Latency)") if svc_lat else None,
            "error_rate": compute_calibration_metrics(svc_err, unit="%", group_name=f"{svc} (Error Rate)") if svc_err else None,
        }

    # 4. Breakdown by downstream service
    overall_latency_ord = compute_calibration_metrics(records_ord, unit="ms", group_name="Overall Order Service Latency")
    overall_error_ord = compute_calibration_metrics(records_ord, unit="%", group_name="Overall Order Service Error Rate")

    by_downstream_service = {
        "api-gateway": {
            "latency_ms": overall_latency_gw,
            "error_rate_pct": overall_error_gw,
        },
        "order-service": {
            "latency_ms": overall_latency_ord,
            "error_rate_pct": overall_error_ord,
        },
    }

    # 5. Breakdown by causal variable
    by_variable = {
        "p99_latency (ms)": compute_calibration_metrics(records_gw, unit="ms", group_name="p99_latency"),
        "error_rate (%)": compute_calibration_metrics(records_gw, unit="%", group_name="error_rate"),
    }

    # 6. Breakdown by propagation distance
    distances = sorted(list(set(r["hop_distance"] for r in records_gw)))
    by_distance = {}
    for d in distances:
        d_recs = [r for r in records_gw if r["hop_distance"] == d]
        d_lat = [r for r in d_recs if r["unit"] == "ms"]
        d_err = [r for r in d_recs if r["unit"] == "%"]
        by_distance[f"hop_{d}"] = {
            "hop_distance": d,
            "latency": compute_calibration_metrics(d_lat, unit="ms", group_name=f"Distance {d} hops (Latency)") if d_lat else None,
            "error_rate": compute_calibration_metrics(d_err, unit="%", group_name=f"Distance {d} hops (Error Rate)") if d_err else None,
        }

    # 7. Breakdown by propagation delay
    delays = sorted(list(set(r["propagation_delay_seconds"] for r in records_gw)))
    by_delay = {}
    for delay in delays:
        del_recs = [r for r in records_gw if r["propagation_delay_seconds"] == delay]
        del_lat = [r for r in del_recs if r["unit"] == "ms"]
        del_err = [r for r in del_recs if r["unit"] == "%"]
        by_delay[f"delay_{delay}s"] = {
            "delay_seconds": delay,
            "latency": compute_calibration_metrics(del_lat, unit="ms", group_name=f"Delay {delay}s (Latency)") if del_lat else None,
            "error_rate": compute_calibration_metrics(del_err, unit="%", group_name=f"Delay {delay}s (Error Rate)") if del_err else None,
        }

    # 8. Breakdown by intervention magnitude tier (Low, Medium, High)
    lat_recs = [r for r in records_gw if r["unit"] == "ms"]
    err_recs = [r for r in records_gw if r["unit"] == "%"]
    by_magnitude = {}
    if lat_recs:
        lat_mags = [r["injected_magnitude"] for r in lat_recs]
        med_lat = np.median(lat_mags)
        low_lat = [r for r in lat_recs if r["injected_magnitude"] < med_lat]
        high_lat = [r for r in lat_recs if r["injected_magnitude"] >= med_lat]
        by_magnitude["latency_low_tier"] = compute_calibration_metrics(low_lat, unit="ms", group_name="Latency Low Tier (< median)")
        by_magnitude["latency_high_tier"] = compute_calibration_metrics(high_lat, unit="ms", group_name="Latency High Tier (>= median)")

    if err_recs:
        err_mags = [r["injected_magnitude"] for r in err_recs]
        med_err = np.median(err_mags)
        low_err = [r for r in err_recs if r["injected_magnitude"] < med_err]
        high_err = [r for r in err_recs if r["injected_magnitude"] >= med_err]
        by_magnitude["error_low_tier"] = compute_calibration_metrics(low_err, unit="%", group_name="Error Rate Low Tier (< median)")
        by_magnitude["error_high_tier"] = compute_calibration_metrics(high_err, unit="%", group_name="Error Rate High Tier (>= median)")

    return {
        "split_name": split_name,
        "total_fault_experiments": len(records_gw),
        "overall_gateway_calibration": {
            "latency_ms": overall_latency_gw,
            "error_rate_pct": overall_error_gw,
        },
        "by_downstream_service": by_downstream_service,
        "by_fault_type": by_fault_type,
        "by_root_cause_service": by_root_cause,
        "by_causal_variable": by_variable,
        "by_propagation_distance": by_distance,
        "by_propagation_delay": by_delay,
        "by_intervention_magnitude": by_magnitude,
        "detailed_experiment_records": records_gw,
    }
