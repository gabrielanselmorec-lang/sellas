from __future__ import annotations

from typing import Any

import pandas as pd


def temporal_split(frame: pd.DataFrame, test_fraction: float = 0.25) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    ordered = frame.sort_values(["date", "patient_id", "session_id"]).reset_index(drop=True)
    split_idx = max(1, min(len(ordered) - 1, int(len(ordered) * (1 - test_fraction))))
    train = ordered.iloc[:split_idx].copy()
    test = ordered.iloc[split_idx:].copy()
    return train, test, {
        "method": "temporal_holdout",
        "test_fraction": test_fraction,
        "cutoff_date": _date_value(test["date"].min()) if not test.empty else None,
        "train_period": [_date_value(train["date"].min()), _date_value(train["date"].max())] if not train.empty else [None, None],
        "test_period": [_date_value(test["date"].min()), _date_value(test["date"].max())] if not test.empty else [None, None],
    }


def grouped_event_rates(frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {"by_patient": {}, "by_period": {}}
    if frame.empty:
        return result
    if "patient_id" in frame.columns:
        for patient_id, group in frame.groupby("patient_id"):
            result["by_patient"][str(patient_id)] = {
                "samples": int(len(group)),
                "event_rate": float(group["target_next_session"].mean()) if len(group) else 0.0,
            }
    month = pd.to_datetime(frame["date"], errors="coerce").dt.to_period("M").astype(str)
    for period, group in frame.assign(_period=month).groupby("_period"):
        result["by_period"][str(period)] = {
            "samples": int(len(group)),
            "event_rate": float(group["target_next_session"].mean()) if len(group) else 0.0,
        }
    return result


def evaluate_slices(frame: pd.DataFrame, probabilities, predictions) -> dict[str, Any]:
    data = frame.copy()
    data["_probability"] = probabilities
    data["_prediction"] = predictions
    data["_correct"] = (data["_prediction"].astype(int) == data["target_next_session"].astype(int)).astype(int)
    return {
        "patient_slices": _slice_metrics(data, "patient_id"),
        "period_slices": _slice_metrics(data.assign(period=pd.to_datetime(data["date"]).dt.to_period("M").astype(str)), "period"),
    }


def _slice_metrics(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    if column not in frame.columns:
        return metrics
    for value, group in frame.groupby(column):
        metrics[str(value)] = {
            "samples": int(len(group)),
            "event_rate": float(group["target_next_session"].mean()) if len(group) else 0.0,
            "mean_probability": float(group["_probability"].mean()) if len(group) else 0.0,
            "accuracy": float(group["_correct"].mean()) if len(group) else 0.0,
        }
    return metrics


def _date_value(value) -> str | None:
    if pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()
