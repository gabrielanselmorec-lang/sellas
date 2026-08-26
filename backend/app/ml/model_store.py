from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib

from backend.app.core.config import MODEL_DIR

INDEX_PATH = MODEL_DIR / "model_index.json"


@dataclass
class ModelVersionMetadata:
    version_id: str
    patient_id: str | None
    behavior_name: str
    selected_model: str
    trained_at: str
    artifact_path: str
    samples: int
    event_rate: float
    metrics: dict[str, Any]
    validation: dict[str, Any]
    calibration: dict[str, Any]
    reference_stats: dict[str, Any]


def create_version_id(behavior_name: str, patient_id: str | None) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_behavior = "".join(ch if ch.isalnum() else "_" for ch in behavior_name.lower()).strip("_")[:40]
    safe_patient = patient_id or "all"
    safe_patient = "".join(ch if ch.isalnum() else "_" for ch in safe_patient.lower()).strip("_")[:32]
    return f"{stamp}_{safe_patient}_{safe_behavior}"


def save_model_version(version: ModelVersionMetadata, model: Any) -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = MODEL_DIR / Path(version.artifact_path).name
    joblib.dump(model, artifact_path)
    metadata_path = artifact_path.with_suffix(".json")
    metadata_path.write_text(json.dumps(asdict(version), ensure_ascii=False, indent=2), encoding="utf-8")
    index = load_model_index()
    index[model_key(version.patient_id, version.behavior_name)] = asdict(version)
    INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def load_latest_model(patient_id: str | None, behavior_name: str) -> tuple[Any, dict[str, Any]] | None:
    index = load_model_index()
    metadata = index.get(model_key(patient_id, behavior_name))
    if not metadata:
        return None
    artifact_path = MODEL_DIR / Path(metadata["artifact_path"]).name
    if not artifact_path.exists():
        return None
    return joblib.load(artifact_path), metadata


def load_latest_metadata(patient_id: str | None, behavior_name: str) -> dict[str, Any] | None:
    index = load_model_index()
    return index.get(model_key(patient_id, behavior_name))


def load_model_index() -> dict[str, dict[str, Any]]:
    if not INDEX_PATH.exists():
        return {}
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def model_key(patient_id: str | None, behavior_name: str) -> str:
    return f"{patient_id or 'all'}::{behavior_name.lower()}"
