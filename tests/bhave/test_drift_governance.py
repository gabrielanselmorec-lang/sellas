from backend.app.ml.drift import drift_report, reference_stats
from backend.app.ml.features import build_feature_frame
from backend.app.services.governance import governance_status
from backend.app.services.mock_data import generate_mock_records
from backend.app.services.normalizer import normalize_records


def test_drift_report_has_status_and_sections():
    records = normalize_records(generate_mock_records(days=60, seed=21))
    frame = build_feature_frame(records, records[0]["patient_id"], records[0]["behavior_name"])
    reference = reference_stats(frame.iloc[: max(4, len(frame) // 2)])

    report = drift_report(frame, reference)

    assert report["status"] in {"baixo", "moderado", "alto"}
    assert "numeric" in report
    assert "categorical" in report


def test_governance_blocks_production_use_by_default():
    status = governance_status()
    assert status["production_use_allowed"] is False
    assert status["lgpd_checklist"]
    assert "analise regulatoria SaMD" in status["required_before_real_use"]
