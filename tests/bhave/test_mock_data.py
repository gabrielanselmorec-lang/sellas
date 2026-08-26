from backend.app.services.mock_data import BEHAVIORS, PATIENTS, generate_mock_records


def test_mock_data_has_required_patients_behaviors_and_sessions():
    records = generate_mock_records(days=35, seed=7)
    patients = {record["patient_id"] for record in records}
    behaviors = {record["behavior_name"] for record in records}
    sessions = {record["session_id"] for record in records}

    assert len(patients) == len(PATIENTS)
    assert len(behaviors) == len(BEHAVIORS)
    assert len(sessions) > 20
    assert {"frequency", "duration", "intensity", "environment", "antecedent", "consequence"}.issubset(records[0])
