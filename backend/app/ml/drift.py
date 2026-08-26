from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backend.app.ml.features import CATEGORICAL_FEATURES, NUMERIC_FEATURES


def reference_stats(frame: pd.DataFrame) -> dict[str, Any]:
    stats: dict[str, Any] = {"numeric": {}, "categorical": {}}
    for column in NUMERIC_FEATURES:
        if column in frame.columns:
            series = pd.to_numeric(frame[column], errors="coerce").dropna()
            stats["numeric"][column] = {
                "mean": float(series.mean()) if len(series) else 0.0,
                "std": float(series.std(ddof=0)) if len(series) else 0.0,
                "quantiles": [float(series.quantile(q)) if len(series) else 0.0 for q in [0.0, 0.25, 0.5, 0.75, 1.0]],
            }
    for column in CATEGORICAL_FEATURES:
        if column in frame.columns:
            counts = frame[column].fillna("").astype(str).value_counts(normalize=True).head(20)
            stats["categorical"][column] = counts.to_dict()
    return stats


def drift_report(current_frame: pd.DataFrame, reference: dict[str, Any]) -> dict[str, Any]:
    current = reference_stats(current_frame)
    numeric = {}
    categorical = {}
    for column, ref in reference.get("numeric", {}).items():
        cur = current["numeric"].get(column, {"mean": 0.0, "std": 0.0})
        ref_std = max(float(ref.get("std", 0.0)), 1e-6)
        numeric[column] = {
            "reference_mean": ref.get("mean", 0.0),
            "current_mean": cur.get("mean", 0.0),
            "z_shift": abs(float(cur.get("mean", 0.0)) - float(ref.get("mean", 0.0))) / ref_std,
        }
    for column, ref_dist in reference.get("categorical", {}).items():
        cur_dist = current["categorical"].get(column, {})
        categorical[column] = {
            "total_variation_distance": _tvd(ref_dist, cur_dist),
            "new_values": sorted(set(cur_dist) - set(ref_dist)),
        }
    max_numeric = max([item["z_shift"] for item in numeric.values()] or [0.0])
    max_cat = max([item["total_variation_distance"] for item in categorical.values()] or [0.0])
    return {
        "status": _status(max_numeric, max_cat),
        "max_numeric_z_shift": max_numeric,
        "max_categorical_tvd": max_cat,
        "numeric": numeric,
        "categorical": categorical,
    }


def _tvd(reference: dict[str, float], current: dict[str, float]) -> float:
    keys = set(reference) | set(current)
    return float(0.5 * sum(abs(float(reference.get(key, 0.0)) - float(current.get(key, 0.0))) for key in keys))


def _status(max_numeric: float, max_cat: float) -> str:
    if max_numeric >= 2.0 or max_cat >= 0.35:
        return "alto"
    if max_numeric >= 1.0 or max_cat >= 0.20:
        return "moderado"
    return "baixo"
