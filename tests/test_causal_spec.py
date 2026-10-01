"""
Automated Validation Test Suite for CausalOps Phase 3A Causal Specification.

Verifies:
1. All causal specification JSON files load cleanly and are valid JSON.
2. Causal schema aligns precisely with the frozen graph schema (dataset/tg_v1/graph_schema.json).
3. Every causal variable maps to a valid telemetry feature.
4. No forbidden labels or leakage variables enter the causal discovery feature space.
5. All intervention definitions reference valid fault types and valid node names.
6. All graph constraint entries (allowed, forbidden, uncertain) reference valid node names.
7. Graph constraints contain zero physically impossible node names.
8. Ground-truth specification is internally consistent and strictly segregates evaluation from training.
9. Variable catalog matches the 10 node features and documents derivation formulas for derived signals.
10. Causal queries A through E are formally specified with valid inputs, outputs, and identifiability classifications.
11. Method comparison selects Topology-Constrained Time-Lagged SCM with rigorous justification.
"""

import json
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.causal import (
    CAUSAL_DIR,
    SCHEMA_PATH,
    INTERVENTIONS_PATH,
    CONSTRAINTS_PATH,
    GROUND_TRUTH_SPEC_PATH,
    VARIABLE_CATALOG_PATH,
    CAUSAL_QUERIES_PATH,
    METHOD_COMPARISON_PATH,
)

TG_GRAPH_SCHEMA_PATH = Path("dataset/tg_v1/graph_schema.json")


@pytest.fixture
def tg_graph_schema():
    assert TG_GRAPH_SCHEMA_PATH.exists(), f"Missing frozen graph schema at {TG_GRAPH_SCHEMA_PATH}"
    with open(TG_GRAPH_SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def causal_schema():
    assert SCHEMA_PATH.exists(), f"Missing causal schema at {SCHEMA_PATH}"
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def interventions():
    assert INTERVENTIONS_PATH.exists(), f"Missing interventions spec at {INTERVENTIONS_PATH}"
    with open(INTERVENTIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def graph_constraints():
    assert CONSTRAINTS_PATH.exists(), f"Missing graph constraints at {CONSTRAINTS_PATH}"
    with open(CONSTRAINTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def ground_truth_spec():
    assert GROUND_TRUTH_SPEC_PATH.exists(), f"Missing ground truth spec at {GROUND_TRUTH_SPEC_PATH}"
    with open(GROUND_TRUTH_SPEC_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def variable_catalog():
    assert VARIABLE_CATALOG_PATH.exists(), f"Missing variable catalog at {VARIABLE_CATALOG_PATH}"
    with open(VARIABLE_CATALOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def causal_queries():
    assert CAUSAL_QUERIES_PATH.exists(), f"Missing causal queries at {CAUSAL_QUERIES_PATH}"
    with open(CAUSAL_QUERIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def method_comparison():
    assert METHOD_COMPARISON_PATH.exists(), f"Missing method comparison at {METHOD_COMPARISON_PATH}"
    with open(METHOD_COMPARISON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ─── Test Group 1: Files Existence & Loadability ─────────────────────────────

def test_all_specification_files_exist_and_load(
    causal_schema,
    interventions,
    graph_constraints,
    ground_truth_spec,
    variable_catalog,
    causal_queries,
    method_comparison,
):
    """Ensure all Phase 3A JSON documents exist and load valid non-empty dictionaries."""
    assert isinstance(causal_schema, dict) and len(causal_schema) > 0
    assert isinstance(interventions, dict) and len(interventions) > 0
    assert isinstance(graph_constraints, dict) and len(graph_constraints) > 0
    assert isinstance(ground_truth_spec, dict) and len(ground_truth_spec) > 0
    assert isinstance(variable_catalog, dict) and len(variable_catalog) > 0
    assert isinstance(causal_queries, dict) and len(causal_queries) > 0
    assert isinstance(method_comparison, dict) and len(method_comparison) > 0


# ─── Test Group 2: Schema Topology & Feature Alignment ───────────────────────

def test_causal_schema_aligns_with_tg_graph_schema(causal_schema, tg_graph_schema):
    """Verify nodes, count, indices, and feature names match the frozen temporal graph schema."""
    assert causal_schema["node_count"] == tg_graph_schema["node_count"]
    assert causal_schema["nodes"] == tg_graph_schema["node_order"]
    assert causal_schema["node_indices"] == tg_graph_schema["node_name_to_index"]
    assert causal_schema["node_variables"] == tg_graph_schema["node_feature_names"]
    assert causal_schema["variable_count_per_node"] == tg_graph_schema["feature_count"]
    assert causal_schema["total_causal_variables"] == 50

    # Verify dependency call edges match
    assert causal_schema["service_dependency_graph"]["directed_edges"] == tg_graph_schema["edge_list"]
    assert causal_schema["service_dependency_graph"]["edge_index"] == tg_graph_schema["edge_index"]


def test_observed_variables_naming_and_cardinality(causal_schema):
    """Verify that every observed variable follows 'node.feature' naming and count is exactly 50."""
    observed = causal_schema["observed_variables"]
    assert len(observed) == 50
    nodes = set(causal_schema["nodes"])
    feats = set(causal_schema["node_variables"])

    for var in observed:
        assert "." in var, f"Observed variable '{var}' missing dot separator"
        node, feat = var.split(".", 1)
        assert node in nodes, f"Unknown node '{node}' in observed variable '{var}'"
        assert feat in feats, f"Unknown feature '{feat}' in observed variable '{var}'"


# ─── Test Group 3: Leakage Isolation & Forbidden Labels ──────────────────────

def test_strictly_no_forbidden_labels_in_observed_variables(causal_schema, ground_truth_spec):
    """Ensure no ground truth labels, experiment metadata, or model predictions enter input space."""
    observed = set(causal_schema["observed_variables"])
    forbidden_list = causal_schema["leakage_isolation_rules"]["strictly_forbidden_from_causal_discovery"]

    assert len(forbidden_list) >= 10, "Forbidden leakage list is insufficiently comprehensive"

    for forbidden in forbidden_list:
        assert forbidden not in observed, f"Forbidden label '{forbidden}' found in observed variables!"

    for field_spec in ground_truth_spec["ground_truth_fields"]:
        field_name = field_spec["field_name"]
        # Split compound names if any
        for name in field_name.split(" / "):
            assert name not in observed, f"Ground truth field '{name}' found in observed variables!"


# ─── Test Group 4: Interventions Specification ───────────────────────────────

def test_interventions_match_supported_fault_types(interventions, causal_schema):
    """Verify all 5 supported fault types are specified with valid targets and operators."""
    expected_faults = {
        "DB_LATENCY",
        "SERVICE_LATENCY",
        "NETWORK_LATENCY",
        "ERROR_RATE",
        "SERVICE_FAILURE",
    }
    specified_faults = {item["fault_type"] for item in interventions["interventions"]}
    assert expected_faults == specified_faults, f"Mismatch in fault types: {specified_faults} vs {expected_faults}"

    valid_nodes = set(causal_schema["nodes"])
    for item in interventions["interventions"]:
        assert item["intervention_target"] in valid_nodes, f"Invalid target {item['intervention_target']}"
        assert item["operator"].startswith("do("), f"Operator must use do-calculus: {item['operator']}"
        assert len(item["expected_effects"]) > 0, f"No expected effects for {item['fault_type']}"
        assert len(item["expected_non_effects"]) > 0, f"No expected non-effects for {item['fault_type']}"

        # Verify expected effect variables belong to valid nodes
        for eff in item["expected_effects"]:
            eff_node = eff["variable"].split(".")[0]
            assert eff_node in valid_nodes, f"Unknown node '{eff_node}' in effect '{eff['variable']}'"

        # Verify non-effect variables belong to valid nodes
        for non_eff in item["expected_non_effects"]:
            non_eff_node = non_eff["variable"].split(".")[0]
            assert non_eff_node in valid_nodes, f"Unknown node '{non_eff_node}' in non-effect '{non_eff['variable']}'"


# ─── Test Group 5: Graph Constraints Validation ──────────────────────────────

def test_graph_constraints_nodes_and_topology(graph_constraints, causal_schema):
    """Verify that all graph constraints reference only valid nodes and contain no impossible names."""
    valid_nodes = set(causal_schema["nodes"])
    constraint_nodes = set(graph_constraints["nodes"])
    assert constraint_nodes == valid_nodes, f"Constraint nodes {constraint_nodes} != {valid_nodes}"

    # Validate allowed edges
    allowed = graph_constraints["node_level_constraints"]["allowed_directed_edges"]
    assert len(allowed) >= 8, f"Insufficient allowed edges: {len(allowed)}"
    for edge in allowed:
        assert edge["source"] in valid_nodes, f"Invalid source node '{edge['source']}'"
        assert edge["target"] in valid_nodes, f"Invalid target node '{edge['target']}'"
        assert edge["source"] != edge["target"], "Inter-node edges must connect distinct nodes"

    # Validate forbidden edges
    forbidden = graph_constraints["node_level_constraints"]["forbidden_directed_edges"]
    assert len(forbidden) >= 8, f"Insufficient forbidden edges: {len(forbidden)}"
    for edge in forbidden:
        assert edge["source"] in valid_nodes, f"Invalid source node '{edge['source']}'"
        assert edge["target"] in valid_nodes, f"Invalid target node '{edge['target']}'"
        assert edge["source"] != edge["target"]

    # Verify no edge is simultaneously allowed and forbidden
    allowed_pairs = {(e["source"], e["target"]) for e in allowed}
    forbidden_pairs = {(e["source"], e["target"]) for e in forbidden}
    intersection = allowed_pairs.intersection(forbidden_pairs)
    assert len(intersection) == 0, f"Contradiction: Edges both allowed and forbidden: {intersection}"

    # Verify specific physical impossibilities are forbidden
    assert ("inventory-db", "payment-service") in forbidden_pairs
    assert ("payment-service", "inventory-db") in forbidden_pairs
    assert ("inventory-service", "payment-service") in forbidden_pairs
    assert ("inventory-db", "api-gateway") in forbidden_pairs


def test_variable_level_constraint_rules(graph_constraints):
    """Verify rules prohibiting anti-causal derived-to-raw or backward-time edges."""
    rules = graph_constraints["variable_level_constraints"]["forbidden_edge_rules"]
    rule_ids = {r["rule_id"] for r in rules}
    assert "NO_ANTI_CAUSAL_DERIVED_TO_RAW" in rule_ids
    assert "NO_ANTI_CAUSAL_DELTA_TO_SOURCE" in rule_ids
    assert "NO_BACKWARD_TIME_EDGES" in rule_ids
    assert "NO_NON_DB_DB_LATENCY_SOURCES" in rule_ids


# ─── Test Group 6: Variable Catalog & Derived Signals ────────────────────────

def test_variable_catalog_completeness_and_anomaly_score_handling(variable_catalog, causal_schema):
    """Verify variable catalog defines all 10 features and strictly tags anomaly_score as non-causal driver."""
    features = {f["name"]: f for f in variable_catalog["node_features"]}
    assert set(features.keys()) == set(causal_schema["node_variables"])

    # anomaly_score must not be usable as a general causal discovery driver
    anomaly_spec = features["anomaly_score"]
    assert anomaly_spec["observed_or_derived"] == "derived"
    assert anomaly_spec["usable_for_causal_discovery"] is False
    assert anomaly_spec["derivation_formula"] is not None
    assert "latency" in anomaly_spec["derivation_formula"]
    assert "error_rate" in anomaly_spec["derivation_formula"]

    # Delta features must also be derived differences
    for delta_feat in ["p99_latency_delta", "error_rate_delta"]:
        spec = features[delta_feat]
        assert spec["observed_or_derived"] == "derived"
        assert spec["usable_for_causal_discovery"] is False


# ─── Test Group 7: Causal Queries Specification ──────────────────────────────

def test_causal_queries_specification_completeness(causal_queries):
    """Verify Queries A through E are defined with formal mathematical expressions and identifiability."""
    query_ids = {q["query_id"] for q in causal_queries["queries"]}
    expected_ids = {"QUERY_A", "QUERY_B", "QUERY_C", "QUERY_D", "QUERY_E"}
    assert query_ids == expected_ids, f"Queries {query_ids} != {expected_ids}"

    for q in causal_queries["queries"]:
        assert "mathematical_formulation" in q and len(q["mathematical_formulation"]) > 5
        assert "identifiability" in q
        assert q["identifiability"] in {"IDENTIFIABLE", "PARTIALLY_IDENTIFIABLE", "NOT_IDENTIFIABLE"}
        assert len(q["assumptions"]) > 0
        assert "evaluation_criteria" in q

    # Query C must specify Pearl's 3-step counterfactual procedure
    query_c = next(q for q in causal_queries["queries"] if q["query_id"] == "QUERY_C")
    assert "pearl_three_step_procedure" in query_c
    proc = query_c["pearl_three_step_procedure"]
    assert "step_1_abduction" in proc
    assert "step_2_action" in proc
    assert "step_3_prediction" in proc


# ─── Test Group 8: Method Comparison & Primary Strategy Selection ────────────

def test_method_comparison_and_selection(method_comparison):
    """Verify method comparison evaluates required approaches and selects Topology-Constrained Lagged SCM."""
    methods = {m["method_name"] for m in method_comparison["evaluated_approaches"]}
    assert any("PC" in m for m in methods), "PC algorithm missing from comparison"
    assert any("LiNGAM" in m for m in methods), "LiNGAM missing from comparison"
    assert any("NOTEARS" in m for m in methods), "NOTEARS missing from comparison"
    assert any("Topology-Constrained" in m for m in methods), "Topology-Constrained SCM missing"

    selection = method_comparison["selection_rationale"]
    assert "Topology-Constrained" in selection["primary_method"]
    assert "justification_summary" in selection
    assert len(selection["justification_summary"]) > 50
