from backend.app.ml.features import FeatureConfig, baseline_event_rate, build_feature_frame, create_future_target, split_xy
from backend.app.services.mock_data import generate_mock_records
from backend.app.services.normalizer import normalize_records


def test_feature_engineering_creates_next_session_target():
    records = normalize_records(generate_mock_records(days=45, seed=11))
    patient_id = records[0]["patient_id"]
    behavior_name = records[0]["behavior_name"]

    frame = build_feature_frame(records, patient_id=patient_id, behavior_name=behavior_name)
    x, y = split_xy(frame)

    assert not frame.empty
    assert "frequency_recent_3" in x.columns
    assert "frequency_recent_n" in x.columns
    assert "exponential_recent_risk" in x.columns
    assert "sessions_since_last_occurrence" in x.columns
    assert set(y.unique()).issubset({0, 1})


def test_future_target_recent_frequency_ema_and_baseline_are_leakage_safe():
    records = normalize_records(generate_mock_records(days=35, seed=21))
    patient_id = records[0]["patient_id"]
    behavior_name = records[0]["behavior_name"]
    config = FeatureConfig(recent_sessions=4, recent_days=10, alpha=0.4, horizon_sessions=1)

    frame = build_feature_frame(records, patient_id=patient_id, behavior_name=behavior_name, config=config)
    assert not frame.empty
    first = frame.iloc[0]
    assert first["frequency_recent_n"] == 0
    assert first["frequency_recent_n_mean"] == 0
    assert 0 <= first["exponential_recent_risk"] <= 1
    assert 0 <= baseline_event_rate(frame) <= 1

    target = create_future_target(frame["occurred"], horizon_sessions=1)
    assert target.iloc[0] == frame["occurred"].shift(-1).iloc[0]
