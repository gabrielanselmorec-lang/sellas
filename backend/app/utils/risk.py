from __future__ import annotations

from backend.app.core.config import settings


def classify_risk(
    probability: float,
    low_max: float | None = None,
    moderate_max: float | None = None,
) -> str:
    """Convert a calibrated probability into a clinical risk band."""
    low = settings.risk_low_max if low_max is None else low_max
    moderate = settings.risk_moderate_max if moderate_max is None else moderate_max
    probability = max(0.0, min(1.0, float(probability)))
    if probability < low:
        return "baixo"
    if probability < moderate:
        return "moderado"
    return "alto"
