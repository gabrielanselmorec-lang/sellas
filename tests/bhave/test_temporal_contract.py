from datetime import datetime, timedelta

import pandas as pd

from backend.app.ml.feature_validity import filter_notes_as_of
from backend.app.ml.targets import label_onset_target


def test_excludes_event_in_progress_at_landmark():
    landmark = datetime(2026, 7, 14, 9, 0)
    row = label_onset_target(
        events=[{"start_ts": landmark - timedelta(minutes=5), "end_ts": landmark + timedelta(minutes=10)}],
        observation_intervals=[{"start_ts": landmark, "end_ts": landmark + timedelta(hours=1)}],
        patient_token="P1",
        landmark_ts=landmark,
        horizon_end_ts=landmark + timedelta(hours=1),
    )

    assert row.censored is True
    assert row.event_in_progress is True
    assert row.reason == "event_in_progress"
    assert row.y is None


def test_censors_when_observation_is_insufficient():
    landmark = datetime(2026, 7, 14, 9, 0)
    row = label_onset_target(
        events=[],
        observation_intervals=[{"start_ts": landmark, "end_ts": landmark + timedelta(minutes=10)}],
        patient_token="P1",
        landmark_ts=landmark,
        horizon_end_ts=landmark + timedelta(hours=1),
    )

    assert row.censored is True
    assert row.reason == "insufficient_observation"


def test_labels_first_onset_only_when_coverage_is_sufficient():
    landmark = datetime(2026, 7, 14, 9, 0)
    row = label_onset_target(
        events=[
            {"onset_ts": landmark + timedelta(minutes=30)},
            {"onset_ts": landmark + timedelta(minutes=45)},
        ],
        observation_intervals=[{"start_ts": landmark, "end_ts": landmark + timedelta(hours=1)}],
        patient_token="P1",
        landmark_ts=landmark,
        horizon_end_ts=landmark + timedelta(hours=1),
    )

    assert row.censored is False
    assert row.y == 1


def test_note_authored_after_landmark_is_excluded():
    landmark = pd.Timestamp("2026-07-14T09:00:00")
    notes = pd.DataFrame(
        [
            {"patient_id": "P1", "authored_at": "2026-07-14T08:50:00", "note_scope": "pre_session", "value": 1},
            {"patient_id": "P1", "authored_at": "2026-07-14T09:10:00", "note_scope": "in_session_live", "value": 2},
            {"patient_id": "P1", "authored_at": "2026-07-14T08:55:00", "note_scope": "post_session_summary", "value": 3},
        ]
    )

    filtered = filter_notes_as_of(notes, landmark)

    assert filtered["value"].tolist() == [1]
