from __future__ import annotations

from hashlib import sha256
from typing import Any

import pandas as pd

from backend.app.services.governance import governance_status


def evaluate_abstention(
    latest_row: pd.Series | dict[str, Any],
    *,
    drift_status: str | None = None,
    minimum_observed_sessions: int = 8,
    observed_sessions: int | None = None,
) -> dict[str, Any]:
    row = latest_row.to_dict() if hasattr(latest_row, "to_dict") else dict(latest_row)
    reasons: list[str] = []
    data_quality = "adequate"

    if bool(row.get("censored")):
        reasons.append(str(row.get("abstain_candidate_reason") or "target_censored"))
    if bool(row.get("event_in_progress")):
        reasons.append("event_in_progress")
    if float(row.get("coverage_minutes") or 0) <= 0:
        reasons.append("missing_observation_coverage")
    if bool(row.get("note_requires_human_review")):
        reasons.append("note_requires_human_review")
    if bool(row.get("low_temporal_confidence")):
        reasons.append("low_temporal_confidence")
    if observed_sessions is not None and observed_sessions < minimum_observed_sessions:
        reasons.append("insufficient_patient_history")
    if drift_status == "alto":
        reasons.append("high_drift")

    if reasons:
        data_quality = "limited" if len(reasons) <= 2 else "poor"

    return {
        "abstain": bool(reasons),
        "abstain_reason": ";".join(dict.fromkeys(reasons)) or None,
        "data_quality": data_quality,
        "governance": {
            "production_use_allowed": governance_status().get("production_use_allowed", False),
            "clinical_use_gate": "blocked_until_governance_review",
        },
    }


def prediction_audit_id(patient_id: str | None, behavior_name: str, landmark_ts: Any, model_version_id: str | None) -> str:
    raw = f"{patient_id or 'all'}|{behavior_name}|{landmark_ts}|{model_version_id or 'unversioned'}"
    return "pred_" + sha256(raw.encode("utf-8")).hexdigest()[:16]


def probability_uncertainty(probability: float, samples: int) -> dict[str, Any]:
    n = max(int(samples), 1)
    p = min(max(float(probability), 0.0), 1.0)
    # Conservative normal approximation. It is transparent and stable for MVP reporting.
    margin = 1.96 * ((p * (1 - p) / n) ** 0.5)
    return {
        "type": "normal_approximation_ci",
        "lower": round(max(0.0, p - margin), 4),
        "upper": round(min(1.0, p + margin), 4),
        "samples": n,
    }
