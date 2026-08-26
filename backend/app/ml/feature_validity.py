from __future__ import annotations

from typing import Any

import pandas as pd


def filter_as_of(frame: pd.DataFrame, landmark_col: str = "landmark_ts") -> pd.DataFrame:
    """Keep feature rows known at the landmark timestamp."""

    if frame.empty or landmark_col not in frame.columns:
        return frame.copy()
    df = frame.copy()
    landmark = pd.to_datetime(df[landmark_col], errors="coerce")
    effective_from = _timestamp_column(df, "effective_from", default=landmark)
    recorded_at = _timestamp_column(df, "recorded_at", default=landmark)
    effective_to = _timestamp_column(df, "effective_to", default=pd.NaT)
    valid_to = effective_to.isna() | (effective_to > landmark)
    mask = (effective_from <= landmark) & (recorded_at <= landmark) & valid_to
    return df[mask.fillna(False)].copy()


def filter_notes_as_of(notes_df: pd.DataFrame, landmark_ts: Any) -> pd.DataFrame:
    """Exclude notes authored after the decision point or with unsafe scope."""

    if notes_df.empty:
        return notes_df.copy()
    landmark = pd.to_datetime(landmark_ts, errors="coerce")
    if pd.isna(landmark):
        return notes_df.iloc[0:0].copy()
    df = notes_df.copy()
    authored_at = _timestamp_column(df, "authored_at", default=pd.NaT)
    if "note_scope" not in df.columns:
        df["note_scope"] = "unknown"
    allowed_scope = df["note_scope"].fillna("unknown").isin(["pre_session", "in_session_live"])
    mask = authored_at.notna() & (authored_at <= landmark) & allowed_scope
    return df[mask].copy()


def add_temporal_confidence_flags(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flagged = []
    for row in rows:
        item = dict(row)
        item["low_temporal_confidence"] = not bool(item.get("authored_at"))
        if item["low_temporal_confidence"]:
            item.setdefault("temporal_confidence_reason", "missing_authored_at")
        flagged.append(item)
    return flagged


def _timestamp_column(df: pd.DataFrame, column: str, default: Any) -> pd.Series:
    if column in df.columns:
        return pd.to_datetime(df[column], errors="coerce")
    if isinstance(default, pd.Series):
        return default
    return pd.Series([default] * len(df), index=df.index)
