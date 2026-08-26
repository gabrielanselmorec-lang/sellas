import importlib.util

import pytest

from backend.app.ml.training import predict_next_session, train_models
from backend.app.services.mock_data import generate_mock_records
from backend.app.services.normalizer import normalize_records


pytestmark = pytest.mark.skipif(importlib.util.find_spec("sklearn") is None, reason="scikit-learn nao instalado")


def test_training_and_prediction_pipeline_runs_with_mock_data():
    records = normalize_records(generate_mock_records(days=70, seed=13))
    patient_id = records[0]["patient_id"]
    behavior_name = records[0]["behavior_name"]

    result = train_models(records, patient_id=patient_id, behavior_name=behavior_name)
    prediction = predict_next_session(records, patient_id=patient_id, behavior_name=behavior_name)

    assert result["selected_model"] in {"baseline_historico", "regressao_logistica", "random_forest", "xgboost"}
    assert result["model_version_id"]
    assert "baseline_probability" in result
    assert "target_definition" in result
    assert "feature_importance" in result
    assert "calibration" in result
    assert "validation" in result
    assert "brier_score" in result["metrics"]["random_forest"]
    assert "pr_auc" in result["metrics"]["random_forest"]
    assert 0 <= prediction["probability"] <= 1
    assert 0 <= prediction["baseline_probability"] <= 1
    assert prediction["prediction_window"] == "next_session"
    assert prediction["model_version_id"]
    assert prediction["risk"] in {"baixo", "moderado", "alto"}
    assert prediction["clinical_factor_summary"]
