from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class TargetRow:
    patient_token: str
    landmark_ts: datetime
    horizon: str
    y: int | None
    censored: bool
    event_in_progress: bool
    coverage_minutes: float
    cooldown_applied: bool
    reason: str | None


def label_onset_target(
    events: list[dict[str, Any]],
    observation_intervals: list[dict[str, Any]],
    patient_token: str,
    landmark_ts: datetime,
    horizon_end_ts: datetime,
    horizon: str = "next_session",
    cooldown_minutes: int = 15,
    minimum_coverage_ratio: float = 0.8,
) -> TargetRow:
    """Label first-onset occurrence after a decision landmark.

    A negative is valid only when the future horizon has enough confirmed
    observation coverage. Events already in progress at the landmark are
    censored because they are not future onsets.
    """

    if horizon_end_ts <= landmark_ts:
        return TargetRow(patient_token, landmark_ts, horizon, None, True, False, 0.0, False, "invalid_horizon")

    if has_event_in_progress(events, landmark_ts):
        return TargetRow(patient_token, landmark_ts, horizon, None, True, True, 0.0, False, "event_in_progress")

    coverage = covered_minutes(observation_intervals, landmark_ts, horizon_end_ts)
    required = required_minutes(horizon_end_ts - landmark_ts, minimum_coverage_ratio)
    if coverage < required:
        return TargetRow(patient_token, landmark_ts, horizon, None, True, False, coverage, False, "insufficient_observation")

    first_onset = first_onset_after(events, landmark_ts, horizon_end_ts)
    cooldown_applied = False
    if first_onset is not None and is_within_cooldown(events, first_onset, cooldown_minutes):
        cooldown_applied = True
        first_onset = first_onset_after(events, first_onset + timedelta(minutes=cooldown_minutes), horizon_end_ts)

    return TargetRow(
        patient_token=patient_token,
        landmark_ts=landmark_ts,
        horizon=horizon,
        y=1 if first_onset is not None else 0,
        censored=False,
        event_in_progress=False,
        coverage_minutes=coverage,
        cooldown_applied=cooldown_applied,
        reason=None,
    )


def build_session_landmarks(
    records: list[dict[str, Any]],
    horizon_sessions: int = 1,
    prediction_window: str = "next_session",
) -> pd.DataFrame:
    """Create a lightweight landmark table from session-level records."""

    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(
            columns=[
                "patient_token",
                "landmark_ts",
                "horizon",
                "y",
                "censored",
                "coverage_minutes",
                "event_in_progress",
                "abstain_candidate_reason",
            ]
        )
    df["date"] = pd.to_datetime(df.get("date"), errors="coerce")
    df["landmark_ts"] = [_combine_date_time(date, start) for date, start in zip(df["date"], df.get("start_time", ""))]
    df["occurred"] = pd.to_numeric(df.get("frequency"), errors="coerce").fillna(0).gt(0).astype(int)
    rows: list[dict[str, Any]] = []
    h = max(1, int(horizon_sessions))
    for patient_id, group in df.sort_values(["patient_id", "landmark_ts", "session_id"]).groupby("patient_id", sort=False):
        g = group.reset_index(drop=True)
        future = pd.concat([g["occurred"].shift(-step) for step in range(1, h + 1)], axis=1).max(axis=1)
        for idx, record in g.iterrows():
            y = future.iloc[idx]
            censored = bool(pd.isna(y))
            rows.append(
                {
                    "patient_token": patient_id,
                    "patient_id": patient_id,
                    "session_id": record.get("session_id"),
                    "landmark_ts": record.get("landmark_ts"),
                    "horizon": prediction_window,
                    "y": None if censored else int(y),
                    "censored": censored,
                    "coverage_minutes": _session_minutes(record),
                    "event_in_progress": False,
                    "cooldown_applied": False,
                    "abstain_candidate_reason": "future_session_unobserved" if censored else None,
                }
            )
    return pd.DataFrame(rows)


def has_event_in_progress(events: list[dict[str, Any]], landmark_ts: datetime) -> bool:
    for event in events:
        start = _parse_ts(event.get("start_ts") or event.get("start") or event.get("onset_ts"))
        end = _parse_ts(event.get("end_ts") or event.get("end") or event.get("offset_ts"))
        if start and start <= landmark_ts and (end is None or end > landmark_ts):
            return True
    return False


def first_onset_after(events: list[dict[str, Any]], landmark_ts: datetime, horizon_end_ts: datetime) -> datetime | None:
    candidates = []
    for event in events:
        onset = _parse_ts(event.get("onset_ts") or event.get("start_ts") or event.get("start"))
        if onset and landmark_ts < onset <= horizon_end_ts:
            candidates.append(onset)
    return min(candidates) if candidates else None


def covered_minutes(observation_intervals: list[dict[str, Any]], start_ts: datetime, end_ts: datetime) -> float:
    total = 0.0
    for interval in observation_intervals:
        start = _parse_ts(interval.get("start_ts") or interval.get("start"))
        end = _parse_ts(interval.get("end_ts") or interval.get("end"))
        if start is None or end is None:
            continue
        overlap_start = max(start, start_ts)
        overlap_end = min(end, end_ts)
        if overlap_end > overlap_start:
            total += (overlap_end - overlap_start).total_seconds() / 60
    return float(total)


def required_minutes(delta: timedelta, minimum_coverage_ratio: float = 0.8) -> float:
    ratio = max(0.0, min(1.0, float(minimum_coverage_ratio)))
    return float(delta.total_seconds() / 60 * ratio)


def is_within_cooldown(events: list[dict[str, Any]], onset_ts: datetime, cooldown_minutes: int) -> bool:
    cooldown = timedelta(minutes=max(0, int(cooldown_minutes)))
    for event in events:
        previous = _parse_ts(event.get("onset_ts") or event.get("start_ts") or event.get("start"))
        if previous and previous < onset_ts and onset_ts - previous <= cooldown:
            return True
    return False


def _parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=False)
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _combine_date_time(date_value: Any, time_value: Any) -> datetime | None:
    date = pd.to_datetime(date_value, errors="coerce")
    if pd.isna(date):
        return None
    text_time = str(time_value or "00:00")
    parsed = pd.to_datetime(f"{date.date().isoformat()} {text_time}", errors="coerce")
    if pd.isna(parsed):
        parsed = pd.to_datetime(date.date().isoformat(), errors="coerce")
    return None if pd.isna(parsed) else parsed.to_pydatetime()


def _session_minutes(record: pd.Series) -> float:
    start = _combine_date_time(record.get("date"), record.get("start_time"))
    end = _combine_date_time(record.get("date"), record.get("end_time"))
    if not start or not end or end <= start:
        return 0.0
    return float((end - start).total_seconds() / 60)
