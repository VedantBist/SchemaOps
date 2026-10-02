"""One calibrated model set for one environment, and its storage."""
from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import joblib

from .anomaly import AnomalyModel
from .forecast import Forecaster
from .scm import LaggedSCM
from .topology import Topology


def model_root() -> Path:
    return Path(os.environ.get("ENGINE_MODEL_DIR", "/var/lib/causalops/models"))


@dataclass
class EnvironmentModel:
    environment_id: str
    version: str
    topology: Topology
    columns: list[str]
    step_seconds: float
    anomaly: AnomalyModel
    scm: LaggedSCM
    forecaster: Forecaster
    slo: dict
    service_slos: dict
    analysis: dict
    data_from: str
    data_to: str
    metrics: dict = field(default_factory=dict)

    def save(self) -> tuple[str, str]:
        """Writes the model under <root>/<environment>/<version>/model.joblib; returns (path, sha256)."""
        directory = model_root() / self.environment_id / self.version
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "model.joblib"
        joblib.dump(self, path)
        return str(path), sha256(path)


def sha256(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_cache: dict[tuple[str, str], EnvironmentModel] = {}
_lock = threading.Lock()


def load(environment_id: str, version: str, path: str, checksum: str) -> EnvironmentModel:
    """Loads (and caches) a registered model after checking its checksum."""
    key = (environment_id, version)
    with _lock:
        if key in _cache:
            return _cache[key]
    actual = sha256(path)
    if actual != checksum:
        raise RuntimeError(f"Model {version} failed its checksum (expected {checksum[:12]}, found {actual[:12]})")
    model = joblib.load(path)
    with _lock:
        _cache[key] = model
    return model
