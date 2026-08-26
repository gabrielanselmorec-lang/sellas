from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.storage import save_records


def _seed_records():
    records = [
        {
            "patient_id": "P-NOTES",
            "patient_name": "Paciente Notas",
            "session_id": "AT-1",
            "date": "2026-07-01",
            "start_time": "09:00",
            "end_time": "10:00",
            "behavior_name": "choro",
            "frequency": 1,
            "duration": 5,
            "intensity": 3,
            "antecedent": "demanda",
            "consequence": "pausa",
            "environment": "sala",
            "therapist_id": "T1",
            "strategies_used": ["redirecionamento"],
            "prompt_level": 1,
            "independence_score": 60,
            "notes": "Paciente chorou intensamente por 5 minutos apos retirada do tablet. Recebeu pausa.",
        },
        {
            "patient_id": "P-NOTES",
            "patient_name": "Paciente Notas",
            "session_id": "AT-2",
            "date": "2026-07-10",
            "start_time": "09:00",
            "end_time": "10:00",
            "behavior_name": "choro",
            "frequency": 0,
            "duration": 0,
            "intensity": 0,
            "antecedent": "demanda",
            "consequence": "redirecionamento",
            "environment": "sala",
            "therapist_id": "T1",
            "strategies_used": ["DRA"],
            "prompt_level": 0,
            "independence_score": 80,
            "notes": "Nao apresentou choro. Sessao sem intercorrencias.",
        },
    ]
    save_records(records)


def test_appointment_note_endpoints_and_features_from_notes():
    _seed_records()
    client = TestClient(app)

    appointments = client.get("/api/patients/P-NOTES/appointments", params={"start_date": "2026-07-01", "end_date": "2026-07-05"})
    assert appointments.status_code == 200
    assert len(appointments.json()) == 1

    detail = client.get("/api/appointments/AT-1")
    notes = client.get("/api/appointments/AT-1/notes")
    extraction = client.post("/api/appointments/AT-1/notes/extract")
    assert detail.status_code == 200
    assert notes.status_code == 200
    assert extraction.status_code == 200
    assert extraction.json()["extracted_behaviors"][0]["behavior_name"] == "choro"

    correction = client.post(
        "/api/notes/extractions/AT-1/confirm",
        json={
            "human_confirmed": True,
            "human_corrected": True,
            "corrected_by": "tester",
            "corrected_extraction": {
                "behavior_name": "choro",
                "behavior_occurred": 1,
                "frequency": 2,
                "intensity": 3,
                "duration_minutes": 5,
                "antecedents": ["retirada_item"],
                "consequences": ["pausa"],
                "context_tags": ["mesa"],
            },
        },
    )
    assert correction.status_code == 200

    fetched = client.get("/api/notes/extractions/AT-1")
    assert fetched.status_code == 200
    assert fetched.json()["human_corrected"] is True

    note_features = client.post("/api/ml/features/from-notes", json={"patient_id": "P-NOTES", "behavior_name": "choro"})
    assert note_features.status_code == 200
    assert note_features.json()["features"][0]["note_extracted_frequency"] == 2

    features = client.post("/api/ml/features", json={"patient_id": "P-NOTES", "behavior_name": "choro"})
    assert features.status_code == 200
    assert "occurrence_from_note" in features.json()["features"]


def test_batch_extract_notes():
    _seed_records()
    client = TestClient(app)
    batch = client.post("/api/notes/batch-extract", params={"patient_id": "P-NOTES"})
    assert batch.status_code == 200
    assert batch.json()["extractions"] == 2
