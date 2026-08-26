from backend.app.services.note_extraction import (
    adapt_appointment_note,
    extract_appointment_note,
    extraction_to_feature_rows,
    normalize_note_text,
)


def _extract(text: str):
    return extract_appointment_note(
        {
            "patient_id": "P1",
            "appointment_id": "A1",
            "appointment_date": "2026-07-13",
            "raw_note_text": text,
        }
    )


def _behavior(extraction, name):
    return next(item for item in extraction["extracted_behaviors"] if item["behavior_name"] == name)


def test_normalize_note_text_removes_accents_and_spaces():
    assert normalize_note_text("  Não   houve  CRISE! ") == "nao houve crise"


def test_detect_present_behavior_frequency_intensity_duration_context():
    extraction = _extract(
        "Durante a transição para atividade de mesa, o paciente chorou intensamente por cerca de 5 minutos."
    )
    choro = _behavior(extraction, "choro")
    assert choro["occurred"] == 1
    assert choro["intensity"] == 3
    assert choro["duration_minutes"] == 5
    assert "transicao" in extraction["extracted_antecedents"]
    assert "mesa" in extraction["extracted_context"]
    assert extraction["extraction_confidence"] >= 0.9


def test_detect_numeric_and_word_frequency():
    agressao = _behavior(_extract("Agressão ocorreu 3 vezes durante transição."), "agressao")
    grito = _behavior(_extract("Gritou duas vezes durante a sessão."), "grito")
    assert agressao["frequency"] == 3
    assert grito["frequency"] == 2


def test_detect_negated_behavior_and_general_absence():
    extraction = _extract("Não apresentou agressão nem autolesão. Sessão sem intercorrências.")
    assert _behavior(extraction, "agressao")["occurred"] == 0
    assert _behavior(extraction, "autolesao")["occurred"] == 0
    assert extraction["binary_occurrences"]["general_problem_behavior_occurred"] == 0
    assert extraction["requires_human_review"] is False


def test_ambiguous_text_requires_human_review():
    extraction = _extract("Pareceu iniciar uma crise, mas foi redirecionado antes de apresentar agressão.")
    crise = _behavior(extraction, "birra")
    agressao = _behavior(extraction, "agressao")
    assert crise["possible_behavior"] is True
    assert crise["occurred"] is None
    assert agressao["occurred"] == 0
    assert "redirecionamento" in extraction["extracted_consequences"]
    assert extraction["requires_human_review"] is True
    assert extraction["extraction_confidence"] < 0.7


def test_extract_antecedent_consequence_and_feature_rows_with_human_correction_priority():
    extraction = _extract("Teve fuga da demanda durante atividade de mesa após retirada do tablet. Recebeu pausa.")
    assert _behavior(extraction, "fuga")["occurred"] == 1
    assert "demanda" in extraction["extracted_antecedents"]
    assert "retirada_item" in extraction["extracted_antecedents"]
    assert "pausa" in extraction["extracted_consequences"]

    extraction["human_corrected"] = True
    extraction["correction"] = {
        "corrected_extraction": {
            "behavior_name": "fuga",
            "behavior_occurred": 0,
            "frequency": 0,
            "intensity": 1,
            "duration_minutes": 0,
            "antecedents": ["demanda"],
            "consequences": ["redirecionamento"],
            "context_tags": ["mesa"],
        }
    }
    row = extraction_to_feature_rows([extraction])[0]
    assert row["occurrence_from_note"] == 0
    assert row["note_extracted_intensity"] == 1
    assert row["note_consequence_redirecionamento"] == 1
    assert row["note_context_atividade_mesa"] == 1


def test_adapt_appointment_note_accepts_variable_fields():
    adapted = adapt_appointment_note(
        {
            "client_id": "P1",
            "appointment_id": "A2",
            "session_date": "2026-07-13",
            "behavioral_notes": "Sem comportamento-problema.",
        }
    )
    assert adapted["patient_id"] == "P1"
    assert adapted["appointment_id"] == "A2"
    assert adapted["raw_note_text"] == "Sem comportamento-problema."
