from backend.app.services.normalizer import normalize_record


def test_normalize_record_maps_aliases_and_anonymizes_names():
    raw = {
        "paciente_id": "123",
        "paciente_nome": "Nome real",
        "sessao_id": "S1",
        "data": "13/07/2026",
        "comportamento": "agressao",
        "frequencia": "2",
        "duracao": "120",
        "intensidade": "4",
        "terapeuta_nome": "Terapeuta real",
        "terapeuta_id": "T1",
        "estrategias_utilizadas": "FCT, DRA",
    }

    normalized = normalize_record(raw)

    assert normalized["patient_id"].startswith("pac_")
    assert normalized["therapist_id"].startswith("ter_")
    assert "patient_name" not in normalized
    assert "therapist_name" not in normalized
    assert normalized["date"] == "2026-07-13"
    assert normalized["frequency"] == 2.0
    assert normalized["strategies_used"] == ["FCT", "DRA"]
