from __future__ import annotations

from typing import Any

import numpy as np


def compute_operational_metrics(y_true, probabilities, threshold: float = 0.5, exposure_hours: float | None = None) -> dict[str, Any]:
    y = np.asarray(y_true).astype(int)
    p = np.asarray(probabilities).astype(float)
    pred = (p >= threshold).astype(int)
    false_positives = int(((pred == 1) & (y == 0)).sum())
    true_positives = int(((pred == 1) & (y == 1)).sum())
    false_negatives = int(((pred == 0) & (y == 1)).sum())
    true_negatives = int(((pred == 0) & (y == 0)).sum())
    precision = true_positives / max(true_positives + false_positives, 1)
    recall = true_positives / max(true_positives + false_negatives, 1)
    specificity = true_negatives / max(true_negatives + false_positives, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-12)
    hours = max(float(exposure_hours or len(y)), 1e-9)
    return {
        "threshold": float(threshold),
        "prevalence": float(y.mean()) if len(y) else 0.0,
        "precision": float(precision),
        "recall": float(recall),
        "specificity": float(specificity),
        "f1_score": float(f1),
        "brier_score": float(np.mean((p - y) ** 2)) if len(y) else 0.0,
        "false_alerts": false_positives,
        "false_alerts_per_patient_hour": float(false_positives / hours),
        "alerts_per_session": float(pred.sum() / max(len(pred), 1)),
    }


def calibration_summary(y_true, probabilities) -> dict[str, Any]:
    y = np.asarray(y_true).astype(int)
    p = np.asarray(probabilities).astype(float)
    if len(y) == 0:
        return {"brier_score": 0.0, "calibration_intercept": None, "calibration_slope": None}
    summary = {"brier_score": float(np.mean((p - y) ** 2))}
    try:
        from sklearn.linear_model import LogisticRegression

        eps = 1e-6
        logits = np.log(np.clip(p, eps, 1 - eps) / np.clip(1 - p, eps, 1 - eps)).reshape(-1, 1)
        model = LogisticRegression().fit(logits, y)
        summary["calibration_intercept"] = float(model.intercept_[0])
        summary["calibration_slope"] = float(model.coef_[0][0])
    except Exception:
        summary["calibration_intercept"] = None
        summary["calibration_slope"] = None
    return summary
