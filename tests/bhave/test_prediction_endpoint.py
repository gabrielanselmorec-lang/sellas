import importlib.util

import pytest


pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("fastapi") is None or importlib.util.find_spec("sklearn") is None,
    reason="FastAPI/scikit-learn nao instalados",
)


def test_prediction_endpoint_with_mock_data():
    from fastapi.testclient import TestClient

    from backend.app.main import app

    client = TestClient(app)
    sync = client.post("/api/sync/bhave", json={"use_mock": True})
    assert sync.status_code == 200

    patients = client.get("/api/patients").json()
    patient_id = patients[0]["patient_id"]
    behavior = client.get(f"/api/patients/{patient_id}/behaviors").json()[0]["behavior_name"]

    train = client.post("/api/ml/train", json={"patient_id": patient_id, "behavior_name": behavior})
    predict = client.post("/api/ml/predict", json={"patient_id": patient_id, "behavior_name": behavior})
    features = client.post("/api/ml/features", json={"patient_id": patient_id, "behavior_name": behavior})
    calibration = client.get("/api/ml/calibration")
    importance = client.get(
        "/api/ml/feature-importance",
        params={"patient_id": patient_id, "behavior_name": behavior},
    )

    assert train.status_code == 200
    assert predict.status_code == 200
    assert features.status_code == 200
    assert calibration.status_code == 200
    assert importance.status_code == 200
    assert "probability" in predict.json()
    payload = predict.json()
    assert "risk_probability" in payload
    assert "personal_baseline" in payload
    assert "uncertainty" in payload
    assert "data_quality" in payload
    assert "abstain" in payload
    assert "audit_id" in payload
    assert features.json()["target"]["name"] == "target_next_session"
    targets = client.post("/api/ml/targets", json={"patient_id": patient_id, "behavior_name": behavior})
    assert targets.status_code == 200
    assert targets.json()["target_contract"]["landmark_table"] is True
    assert "model_metrics" in calibration.json()
    assert "feature_importance" in importance.json()
