from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backend.app.ml.feature_validity import filter_notes_as_of
from backend.app.ml.targets import build_session_landmarks


@dataclass(frozen=True)
class FeatureConfig:
    """Configuration for leakage-safe behavioral feature engineering."""

    recent_sessions: int = 5
    recent_days: int = 14
    alpha: float = 0.3
    horizon_sessions: int = 1
    prediction_window: str = "next_session"


NUMERIC_FEATURES = [
    "frequency_recent_3",
    "frequency_recent_5",
    "frequency_recent_n",
    "frequency_recent_n_mean",
    "frequency_recent_days",
    "frequency_moving_avg",
    "duration_recent_avg",
    "intensity_recent_avg",
    "exponential_recent_risk",
    "historical_frequency",
    "historical_event_rate",
    "time_since_last_occurrence_days",
    "sessions_since_last_occurrence",
    "same_environment_recent_rate",
    "same_antecedent_recent_rate",
    "same_consequence_recent_rate",
    "hour_of_day",
    "day_of_week",
    "prompt_level",
    "independence_score",
    "recent_trend",
    "occurrence_from_note",
    "note_extracted_frequency",
    "note_extracted_intensity",
    "note_extracted_duration",
    "note_extraction_confidence",
    "note_requires_human_review",
    "note_antecedent_demanda",
    "note_antecedent_transicao",
    "note_antecedent_negacao_acesso",
    "note_antecedent_frustracao",
    "note_antecedent_retirada_item",
    "note_consequence_atencao",
    "note_consequence_pausa",
    "note_consequence_redirecionamento",
    "note_consequence_bloqueio",
    "note_context_inicio_sessao",
    "note_context_fim_sessao",
    "note_context_atividade_mesa",
    "coverage_minutes",
    "low_temporal_confidence",
]

CATEGORICAL_FEATURES = [
    "environment",
    "antecedent",
    "consequence",
    "hypothesized_function",
    "therapist_id",
    "strategies_used",
]


def build_feature_frame(
    records: list[dict],
    patient_id: str | None,
    behavior_name: str,
    config: FeatureConfig | None = None,
    note_feature_rows: list[dict] | None = None,
) -> pd.DataFrame:
    """Create X_t and Y_t,h for a target behavior without future leakage.

    Y_t,h = 1 when the target behavior occurs in the next h sessions.
    For the MVP default, h=1, so the target is next-session occurrence.
    Every feature is shifted or calculated from rows at or before session t.
    """

    config = config or FeatureConfig()
    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame()

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if patient_id:
        df = df[df["patient_id"] == patient_id]
    df = df[df["behavior_name"].astype(str).str.lower() == behavior_name.lower()].copy()
    if df.empty:
        return pd.DataFrame()

    for column in ["frequency", "duration", "intensity", "prompt_level", "independence_score"]:
        df[column] = pd.to_numeric(df.get(column), errors="coerce").fillna(0.0)

    for column in CATEGORICAL_FEATURES:
        if column == "strategies_used":
            df[column] = _column_or_default(df, column, "").apply(_strategy_text)
        else:
            df[column] = _column_or_default(df, column, "").fillna("").astype(str)

    df["occurred"] = (df["frequency"] > 0).astype(int)
    df["hour_of_day"] = pd.to_datetime(df.get("start_time"), errors="coerce", format="%H:%M").dt.hour.fillna(0)
    df["day_of_week"] = df["date"].dt.dayofweek.fillna(0)
    df = df.sort_values(["patient_id", "date", "session_id"]).reset_index(drop=True)
    df = _merge_landmark_metadata(df, records, config)
    df = _merge_note_features(df, note_feature_rows or [], behavior_name)

    frames = []
    for _, group in df.groupby("patient_id", sort=False):
        g = group.copy().reset_index(drop=True)
        shifted_freq = g["frequency"].shift(1).fillna(0)
        shifted_duration = g["duration"].shift(1).fillna(0)
        shifted_intensity = g["intensity"].shift(1).fillna(0)
        shifted_occurred = g["occurred"].shift(1).fillna(0).astype(int)

        n = max(1, int(config.recent_sessions))
        g["frequency_recent_3"] = shifted_freq.rolling(3, min_periods=1).sum()
        g["frequency_recent_5"] = shifted_freq.rolling(5, min_periods=1).sum()
        g["frequency_recent_n"] = shifted_freq.rolling(n, min_periods=1).sum()
        g["frequency_recent_n_mean"] = g["frequency_recent_n"] / n
        g["frequency_moving_avg"] = shifted_freq.rolling(n, min_periods=1).mean()
        g["duration_recent_avg"] = shifted_duration.rolling(n, min_periods=1).mean()
        g["intensity_recent_avg"] = shifted_intensity.rolling(n, min_periods=1).mean()
        g["exponential_recent_risk"] = _exponential_risk(shifted_occurred.to_list(), config.alpha)
        g["historical_frequency"] = shifted_freq.expanding(min_periods=1).sum()
        g["historical_event_rate"] = shifted_occurred.expanding(min_periods=1).mean()
        g["recent_trend"] = shifted_freq.rolling(3, min_periods=1).mean() - shifted_freq.rolling(max(n, 8), min_periods=1).mean()
        g["sessions_since_last_occurrence"] = _sessions_since_last(shifted_occurred.to_list())
        g["time_since_last_occurrence_days"] = _days_since_last_occurrence(g["date"], shifted_occurred.to_list())
        g["frequency_recent_days"] = _frequency_in_recent_days(g["date"], g["frequency"], config.recent_days)
        g["same_environment_recent_rate"] = _same_context_recent_rate(g, "environment", shifted_occurred, n)
        g["same_antecedent_recent_rate"] = _same_context_recent_rate(g, "antecedent", shifted_occurred, n)
        g["same_consequence_recent_rate"] = _same_context_recent_rate(g, "consequence", shifted_occurred, n)
        g["target_next_session"] = create_future_target(g["occurred"], horizon_sessions=config.horizon_sessions)
        if "landmark_y" in g.columns:
            g["target_next_session"] = g["landmark_y"].where(g["landmark_y"].notna(), g["target_next_session"])
        g["target_window"] = config.prediction_window
        g["target_horizon_sessions"] = int(config.horizon_sessions)
        frames.append(g)

    features = pd.concat(frames, ignore_index=True)
    features = features[~features.get("censored", False).astype(bool)].dropna(subset=["target_next_session"]).copy()
    features["target_next_session"] = features["target_next_session"].astype(int)
    for column in NUMERIC_FEATURES:
        features[column] = pd.to_numeric(_column_or_default(features, column, 0.0), errors="coerce").fillna(0.0)
    return features


def merge_note_feature_rows(records: list[dict], note_feature_rows: list[dict], behavior_name: str) -> list[dict]:
    frame = pd.DataFrame(records)
    if frame.empty:
        return records
    merged = _merge_note_features(frame, note_feature_rows, behavior_name)
    return merged.to_dict(orient="records")


def create_future_target(occurred: pd.Series, horizon_sessions: int = 1) -> pd.Series:
    """Y_t,h: occurrence in the next h sessions."""

    h = max(1, int(horizon_sessions))
    future = pd.concat([occurred.shift(-step) for step in range(1, h + 1)], axis=1)
    return future.max(axis=1)


def split_xy(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    columns = NUMERIC_FEATURES + CATEGORICAL_FEATURES
    x = frame.reindex(columns=columns).copy()
    y = frame["target_next_session"].astype(int)
    return x, y


def baseline_event_rate(frame: pd.DataFrame) -> float:
    if frame.empty or "occurred" not in frame.columns:
        return 0.0
    return float(frame["occurred"].mean())


def _sessions_since_last(previous_occurrences: list[int]) -> list[int]:
    result = []
    counter = 99
    for occurred in previous_occurrences:
        if occurred:
            counter = 0
        else:
            counter += 1
        result.append(counter)
    return result


def _days_since_last_occurrence(dates: pd.Series, previous_occurrences: list[int]) -> list[float]:
    result: list[float] = []
    last_date = None
    for current_date, occurred in zip(pd.to_datetime(dates, errors="coerce"), previous_occurrences):
        if last_date is None or pd.isna(current_date):
            result.append(999.0)
        else:
            result.append(float(max((current_date - last_date).days, 0)))
        if occurred and not pd.isna(current_date):
            last_date = current_date
    return result


def _frequency_in_recent_days(dates: pd.Series, shifted_frequency: pd.Series, recent_days: int) -> list[float]:
    recent_days = max(1, int(recent_days))
    clean_dates = pd.to_datetime(dates, errors="coerce")
    values: list[float] = []
    for idx, current_date in enumerate(clean_dates):
        if pd.isna(current_date):
            values.append(0.0)
            continue
        start = current_date - pd.Timedelta(days=recent_days)
        mask = (clean_dates < current_date) & (clean_dates >= start)
        values.append(float(shifted_frequency.where(mask, 0).sum()))
    return values


def _exponential_risk(previous_occurrences: list[int], alpha: float) -> list[float]:
    alpha = max(0.0, min(1.0, float(alpha)))
    risk = 0.0
    values = []
    for occurred in previous_occurrences:
        risk = alpha * int(occurred) + (1 - alpha) * risk
        values.append(float(risk))
    return values


def _same_context_recent_rate(group: pd.DataFrame, column: str, shifted_occurred: pd.Series, recent_sessions: int) -> list[float]:
    values: list[float] = []
    recent_sessions = max(1, int(recent_sessions))
    for idx, current_value in enumerate(group[column].fillna("").astype(str)):
        start = max(0, idx - recent_sessions)
        previous = group.iloc[start:idx]
        if previous.empty:
            values.append(0.0)
            continue
        previous_occurrences = shifted_occurred.iloc[start:idx].astype(int)
        same_context = previous[column].fillna("").astype(str) == current_value
        denominator = max(int(previous_occurrences.sum()), 1)
        values.append(float((same_context.astype(int) * previous_occurrences).sum() / denominator))
    return values


def _strategy_text(value) -> str:
    if isinstance(value, list):
        return ", ".join(sorted(map(str, value)))
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    return str(value)


def _column_or_default(df: pd.DataFrame, column: str, default) -> pd.Series:
    if column in df.columns:
        return df[column]
    return pd.Series([default] * len(df), index=df.index)


def _merge_note_features(df: pd.DataFrame, note_feature_rows: list[dict], behavior_name: str) -> pd.DataFrame:
    note_columns = [column for column in NUMERIC_FEATURES if column.startswith("note_") or column == "occurrence_from_note"]
    out = df.copy()
    for column in note_columns:
        out[column] = 0.0
    if not note_feature_rows:
        return out

    notes = pd.DataFrame(note_feature_rows)
    if notes.empty:
        return out
    if "behavior_name" not in notes.columns:
        return out
    notes = notes[notes["behavior_name"].astype(str).str.lower() == behavior_name.lower()].copy()
    if notes.empty:
        return out
    if "session_id" not in notes.columns and "appointment_id" in notes.columns:
        notes["session_id"] = notes["appointment_id"]
    notes["date"] = pd.to_datetime(notes.get("date"), errors="coerce")
    notes["authored_at"] = pd.to_datetime(notes.get("authored_at"), errors="coerce")
    safe_note_rows = []
    for _, row in out.iterrows():
        patient_notes = notes[notes["patient_id"].astype(str) == str(row.get("patient_id"))]
        landmark_ts = row.get("landmark_ts") or row.get("date")
        valid_notes = filter_notes_as_of(patient_notes, landmark_ts)
        if valid_notes.empty:
            continue
        aggregate = {"patient_id": row.get("patient_id"), "session_id": row.get("session_id")}
        for column in note_columns:
            if column in valid_notes.columns:
                aggregate[column] = pd.to_numeric(valid_notes[column], errors="coerce").fillna(0).max()
        safe_note_rows.append(aggregate)
    if not safe_note_rows:
        return out
    grouped = pd.DataFrame(safe_note_rows)
    out = out.merge(grouped, on=["patient_id", "session_id"], how="left", suffixes=("", "_note"))
    for column in note_columns:
        note_col = f"{column}_note"
        if note_col in out.columns:
            out[column] = pd.to_numeric(out[note_col], errors="coerce").fillna(out[column]).fillna(0.0)
            out = out.drop(columns=[note_col])
        out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0.0)
    return out


def _merge_landmark_metadata(df: pd.DataFrame, records: list[dict], config: FeatureConfig) -> pd.DataFrame:
    landmarks = build_session_landmarks(records, horizon_sessions=config.horizon_sessions, prediction_window=config.prediction_window)
    out = df.copy()
    defaults = {
        "landmark_ts": pd.NaT,
        "landmark_y": pd.NA,
        "censored": False,
        "coverage_minutes": 0.0,
        "event_in_progress": False,
        "cooldown_applied": False,
        "abstain_candidate_reason": None,
    }
    for column, default in defaults.items():
        out[column] = default
    if landmarks.empty:
        return out
    keep = [
        "patient_id",
        "session_id",
        "landmark_ts",
        "y",
        "censored",
        "coverage_minutes",
        "event_in_progress",
        "cooldown_applied",
        "abstain_candidate_reason",
    ]
    meta = landmarks[[column for column in keep if column in landmarks.columns]].rename(columns={"y": "landmark_y"})
    out = out.drop(columns=[column for column in defaults if column in out.columns])
    out = out.merge(meta, on=["patient_id", "session_id"], how="left")
    out["censored"] = out.get("censored", False).fillna(False).astype(bool)
    out["event_in_progress"] = out.get("event_in_progress", False).fillna(False).astype(bool)
    out["cooldown_applied"] = out.get("cooldown_applied", False).fillna(False).astype(bool)
    out["coverage_minutes"] = pd.to_numeric(out.get("coverage_minutes"), errors="coerce").fillna(0.0)
    return out
