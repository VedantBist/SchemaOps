"""Feature extraction logic for CausalOps experiment telemetry and topology."""

import math
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
import numpy as np

from ml.schema import SERVICES, get_feature_definitions, GROUP_A_TELEMETRY, GROUP_B_TEMPORAL, GROUP_C_GRAPH

def _parse_iso_ts(ts_str: str) -> float:
    """Parses ISO timestamp string to epoch seconds."""
    try:
        # e.g. 2026-09-25T19:28:29.733+00:00
        dt = datetime.fromisoformat(ts_str)
        return dt.timestamp()
    except Exception:
        return 0.0

def _calc_slope(x: np.ndarray, y: np.ndarray) -> float:
    """Calculates linear regression slope (dy/dx) safely."""
    if len(x) < 2 or len(y) < 2:
        return 0.0
    x_var = np.var(x)
    if x_var <= 1e-9:
        return 0.0
    cov = np.cov(x, y)[0, 1]
    return float(cov / x_var)

def extract_experiment_features(
    metrics_data: Dict[str, Any],
    topology_data: Dict[str, Any],
    experiment_id: Optional[str] = None
) -> Dict[str, float]:
    """
    Extracts a tabular feature dictionary for one experiment.
    
    Strict Leakage Prevention:
    - Only reads telemetry snapshots and service topology graph.
    - Zero access to ground truth labels, fault types, parameters, or heuristic RCA outputs.
    """
    if isinstance(metrics_data, list):
        samples = metrics_data
    elif isinstance(metrics_data, dict):
        samples = metrics_data.get('samples', [])
    else:
        samples = []

    features: Dict[str, float] = {}
    catalog = get_feature_definitions()

    # 1. Group telemetry samples by service and sort chronologically
    by_svc: Dict[str, List[Dict[str, Any]]] = {svc: [] for svc in SERVICES}
    for s in samples:
        svc_name = s.get('service') or s.get('service_name')
        if svc_name in by_svc:
            by_svc[svc_name].append(s)

    for svc in SERVICES:
        by_svc[svc].sort(key=lambda item: _parse_iso_ts(str(item.get('timestamp') or item.get('captured_at') or '')))

    # 2. Extract Group A & B features per service
    service_first_anom_time: Dict[str, float] = {}
    service_max_p99: Dict[str, float] = {}
    service_max_err: Dict[str, float] = {}
    service_anom_flag: Dict[str, bool] = {}

    # Determine global reference start time
    all_ts = [_parse_iso_ts(str(s.get('timestamp') or s.get('captured_at') or '')) for s in samples if s.get('timestamp') or s.get('captured_at')]
    window_t0 = min(all_ts) if all_ts else 0.0
    window_duration = (max(all_ts) - window_t0) if all_ts else 38.0
    if window_duration <= 0.0:
        window_duration = 38.0

    for svc in SERVICES:
        s_list = by_svc[svc]
        if not s_list:
            # Safe defaults if service telemetry is missing
            for feat_name, feat_def in catalog.items():
                if feat_def.service == svc:
                    features[feat_name] = 0.0
            service_first_anom_time[svc] = 999.0
            service_max_p99[svc] = 0.0
            service_max_err[svc] = 0.0
            service_anom_flag[svc] = False
            continue

        def _get_val(x: Dict[str, Any], *keys: str) -> float:
            for k in keys:
                v = x.get(k)
                if v is not None:
                    try:
                        return float(v)
                    except (ValueError, TypeError):
                        pass
            return 0.0

        p99_arr = np.array([_get_val(x, 'p99Latency', 'p99_latency', 'latency') for x in s_list], dtype=np.float64)
        p50_arr = np.array([_get_val(x, 'p50Latency', 'p50_latency') for x in s_list], dtype=np.float64)
        err_arr = np.array([_get_val(x, 'errorRate', 'error_rate') for x in s_list], dtype=np.float64)
        req_arr = np.array([_get_val(x, 'requestRate', 'request_rate') for x in s_list], dtype=np.float64)
        pool_arr = np.array([_get_val(x, 'poolUtilization', 'pool_utilization') for x in s_list], dtype=np.float64)
        anom_arr = np.array([_get_val(x, 'anomalyScore', 'anomaly_score', 'anomaly') for x in s_list], dtype=np.float64)
        time_arr = np.array([_parse_iso_ts(str(x.get('timestamp') or x.get('captured_at') or '')) - window_t0 for x in s_list], dtype=np.float64)

        # Baseline definitions (first 5 samples)
        n_samples = len(s_list)
        n_base = min(5, n_samples)
        base_p99 = float(np.mean(p99_arr[:n_base]))
        base_err = float(np.mean(err_arr[:n_base]))
        base_pool = float(np.mean(pool_arr[:n_base]))

        # Anomaly mask (threshold > 0.3)
        anom_mask = anom_arr > 0.3
        anom_count = int(np.sum(anom_mask))
        anom_fraction = float(anom_count / n_samples) if n_samples > 0 else 0.0
        service_anom_flag[svc] = (anom_count > 0)

        # First anomaly timestamp relative to window start
        if anom_count > 0:
            first_idx = int(np.argmax(anom_mask))
            t_first = float(time_arr[first_idx])
            service_first_anom_time[svc] = t_first
        else:
            service_first_anom_time[svc] = window_duration

        # --- Group A: Statistical Telemetry Features ---
        features[f"{svc}__p99_mean"] = float(np.mean(p99_arr))
        features[f"{svc}__p99_median"] = float(np.median(p99_arr))
        features[f"{svc}__p99_p95"] = float(np.percentile(p99_arr, 95))
        features[f"{svc}__p99_max"] = float(np.max(p99_arr))
        features[f"{svc}__p99_min"] = float(np.min(p99_arr))
        features[f"{svc}__p99_std"] = float(np.std(p99_arr))
        features[f"{svc}__p99_delta"] = float(np.max(p99_arr) - base_p99)

        features[f"{svc}__p50_mean"] = float(np.mean(p50_arr))
        features[f"{svc}__error_rate_mean"] = float(np.mean(err_arr))
        features[f"{svc}__error_rate_max"] = float(np.max(err_arr))
        features[f"{svc}__error_rate_std"] = float(np.std(err_arr))
        features[f"{svc}__error_rate_delta"] = float(np.max(err_arr) - base_err)

        features[f"{svc}__request_rate_mean"] = float(np.mean(req_arr))
        features[f"{svc}__pool_util_mean"] = float(np.mean(pool_arr))
        features[f"{svc}__pool_util_max"] = float(np.max(pool_arr))
        features[f"{svc}__pool_util_delta"] = float(np.max(pool_arr) - base_pool)

        features[f"{svc}__anomaly_score_mean"] = float(np.mean(anom_arr))
        features[f"{svc}__anomaly_score_max"] = float(np.max(anom_arr))
        features[f"{svc}__anomaly_count"] = float(anom_count)
        features[f"{svc}__anomaly_fraction"] = float(anom_fraction)

        service_max_p99[svc] = float(np.max(p99_arr))
        service_max_err[svc] = float(np.max(err_arr))

        # --- Group B: Temporal Features ---
        n_tail = min(5, n_samples)
        features[f"{svc}__latency_step_t0"] = base_p99
        features[f"{svc}__latency_step_t_last"] = float(np.mean(p99_arr[-n_tail:]))

        # 1-step and 2-step lag differences
        if n_samples >= 2:
            lag1_diffs = p99_arr[1:] - p99_arr[:-1]
            features[f"{svc}__latency_lag1_diff"] = float(np.mean(lag1_diffs))
        else:
            features[f"{svc}__latency_lag1_diff"] = 0.0

        if n_samples >= 3:
            lag2_diffs = p99_arr[2:] - p99_arr[:-2]
            features[f"{svc}__latency_lag2_diff"] = float(np.mean(lag2_diffs))
        else:
            features[f"{svc}__latency_lag2_diff"] = 0.0

        # Rolling 3-step statistics
        if n_samples >= 3:
            rolling_means = [np.mean(p99_arr[i:i+3]) for i in range(n_samples - 2)]
            rolling_stds = [np.std(p99_arr[i:i+3]) for i in range(n_samples - 2)]
            features[f"{svc}__latency_rolling_mean_max"] = float(np.max(rolling_means))
            features[f"{svc}__latency_rolling_std_max"] = float(np.max(rolling_stds))
        else:
            features[f"{svc}__latency_rolling_mean_max"] = float(np.max(p99_arr))
            features[f"{svc}__latency_rolling_std_max"] = 0.0

        features[f"{svc}__latency_slope"] = _calc_slope(time_arr, p99_arr)
        features[f"{svc}__error_rate_slope"] = _calc_slope(time_arr, err_arr)
        features[f"{svc}__time_to_first_anomaly"] = service_first_anom_time[svc]
        features[f"{svc}__anomaly_duration_sec"] = float(anom_count * 1.0)  # each cycle ~1s

    # Relative anomaly onset rank across services (1 to 5)
    sorted_times = sorted(service_first_anom_time.items(), key=lambda kv: kv[1])
    for rank_idx, (svc_name, _) in enumerate(sorted_times, start=1):
        features[f"{svc_name}__relative_anomaly_rank"] = float(rank_idx)

    # 3. Extract Group C: Graph-Aware Topology Features
    # Construct topology graph structures from topology_data
    edges = topology_data.get('edges', [])
    parents: Dict[str, List[str]] = {s: [] for s in SERVICES}  # upstream callers
    children: Dict[str, List[str]] = {s: [] for s in SERVICES} # downstream callees

    for edge in edges:
        src = edge.get('source')
        tgt = edge.get('target')
        if src in children and tgt in parents:
            children[src].append(tgt)
            parents[tgt].append(src)

    # Precompute ancestor and descendant sets
    def get_ancestors(node: str, visited: Optional[set] = None) -> set:
        if visited is None: visited = set()
        for p in parents.get(node, []):
            if p not in visited:
                visited.add(p)
                get_ancestors(p, visited)
        return visited

    def get_descendants(node: str, visited: Optional[set] = None) -> set:
        if visited is None: visited = set()
        for c in children.get(node, []):
            if c not in visited:
                visited.add(c)
                get_descendants(c, visited)
        return visited

    # Find earliest anomalous service
    earliest_svc = sorted_times[0][0] if sorted_times else 'api-gateway'

    # Undirected hop distance matrix among services
    undirected_adj: Dict[str, List[str]] = {s: [] for s in SERVICES}
    for edge in edges:
        s, t = edge.get('source'), edge.get('target')
        if s in undirected_adj and t in undirected_adj:
            undirected_adj[s].append(t)
            undirected_adj[t].append(s)

    def shortest_hop(u: str, v: str) -> float:
        if u == v: return 0.0
        queue = [(u, 0)]
        visited = {u}
        while queue:
            curr, dist = queue.pop(0)
            if curr == v:
                return float(dist)
            for neighbor in undirected_adj.get(curr, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, dist + 1))
        return 5.0 # disconnected fallback

    for svc in SERVICES:
        in_deg = len(parents[svc])
        out_deg = len(children[svc])
        anc_count = len(get_ancestors(svc))
        desc_count = len(get_descendants(svc))

        features[f"{svc}__in_degree"] = float(in_deg)
        features[f"{svc}__out_degree"] = float(out_deg)
        features[f"{svc}__upstream_count"] = float(anc_count)
        features[f"{svc}__downstream_count"] = float(desc_count)

        # Direct upstream caller anomalies & latency
        up_anom_cnt = sum(1 for p in parents[svc] if service_anom_flag.get(p, False))
        features[f"{svc}__upstream_anomaly_count"] = float(up_anom_cnt)
        if parents[svc]:
            up_latencies = [service_max_p99.get(p, 0.0) for p in parents[svc]]
            features[f"{svc}__upstream_mean_latency"] = float(np.mean(up_latencies))
        else:
            features[f"{svc}__upstream_mean_latency"] = 0.0

        # Direct downstream callee anomalies & latency
        down_anom_cnt = sum(1 for c in children[svc] if service_anom_flag.get(c, False))
        features[f"{svc}__downstream_anomaly_count"] = float(down_anom_cnt)
        if children[svc]:
            down_latencies = [service_max_p99.get(c, 0.0) for c in children[svc]]
            down_mean_lat = float(np.mean(down_latencies))
            features[f"{svc}__downstream_mean_latency"] = down_mean_lat
            # Ratio of this service's latency surge to downstream mean latency
            svc_delta = features.get(f"{svc}__p99_delta", 0.0)
            features[f"{svc}__downstream_latency_ratio"] = float(svc_delta / (down_mean_lat + 1.0))
        else:
            features[f"{svc}__downstream_mean_latency"] = 0.0
            features[f"{svc}__downstream_latency_ratio"] = 1.0

        # Hop distance to earliest anomalous node
        features[f"{svc}__dist_to_earliest_anomaly"] = shortest_hop(svc, earliest_svc)

        # Propagation order score: does this service lead downstream anomalies?
        # +1 if service anomalous before or at same time as children; -1 if after; 0 if no anomaly
        if not service_anom_flag.get(svc, False):
            prop_score = 0.0
        elif not children[svc] or not any(service_anom_flag.get(c, False) for c in children[svc]):
            prop_score = 1.0  # anomalous leaf or children are healthy
        else:
            child_times = [service_first_anom_time[c] for c in children[svc] if service_anom_flag.get(c, False)]
            if service_first_anom_time[svc] <= min(child_times):
                prop_score = 1.0
            else:
                prop_score = -1.0
        features[f"{svc}__propagation_order_score"] = float(prop_score)

    # 4. Global System-Wide Summary Features
    total_anom_services = sum(1 for flag in service_anom_flag.values() if flag)
    features["global__total_anomalous_services"] = float(total_anom_services)
    features["global__system_max_p99_latency"] = float(max(service_max_p99.values())) if service_max_p99 else 0.0
    features["global__system_max_error_rate"] = float(max(service_max_err.values())) if service_max_err else 0.0

    # Cascade diameter: max hop distance between anomalous nodes
    anom_nodes = [s for s, flag in service_anom_flag.items() if flag]
    if len(anom_nodes) >= 2:
        max_dist = max(shortest_hop(u, v) for u in anom_nodes for v in anom_nodes)
        features["global__cascade_diameter"] = float(max_dist)
    else:
        features["global__cascade_diameter"] = 0.0

    # Ensure all defined features exist and no NaNs / Infs
    for feat_name in catalog:
        val = features.get(feat_name, 0.0)
        if math.isnan(val) or math.isinf(val):
            features[feat_name] = 0.0

    return features
