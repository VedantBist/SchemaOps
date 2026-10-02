"""Environment-agnostic CausalOps engine.

Learns each monitored environment from its own measured telemetry (stored by the platform API
in PostgreSQL) and serves anomaly detection, failure forecasting, root-cause analysis and
counterfactual simulation for any topology. Nothing in this package refers to a particular
service, node count or dataset; the archived tg_v1 research code lives elsewhere in ``ml/``.
"""
