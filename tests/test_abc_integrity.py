from datetime import date, datetime

import pytest
from pydantic import ValidationError

from app.services.abc_integrity import (
    answer_time_question, build_time_summary, filter_records, record_metadata,
)
from app.services.abc_closed import build_abc_analysis, build_abc_printable_summary
from app.services.abc_methodology import audit_abc_data_quality, compute_exposure_summary
from api import ABCClosedRecordPayload


def episode(identifier="e1", **overrides):
    return {"intervalo_id": identifier, "data_hora": "2026-07-15T10:20:00-03:00",
            "record_kind": "episode", "precisao_horario": "minute",
            "antecedente": "Demanda", "comportamento": "Choro", "consequencia": "Pausa",
            "ambiente": "Sala", "classificacao": "C1", "frequencia": 1, **overrides}


def window(identifier="w1", **overrides):
    return {"intervalo_id": identifier, "session_id": "s1", "record_kind": "observation",
            "inicio": "2026-07-15T10:00:00-03:00", "fim": "2026-07-15T11:00:00-03:00",
            "duracao_planejada_minutos": 60, "status_observacao": "observed",
            "observacao_completa": True, "ambiente": "Sala", **overrides}


@pytest.mark.parametrize("count", [1, 10, 64])
def test_episodes_never_create_observed_time(count):
    records = [episode(str(i)) for i in range(count)]
    intervals = [window(str(i), record_kind="episode", duracao_planejada_minutos=5) for i in range(count)]
    exposure = compute_exposure_summary(records, intervals)
    assert exposure["status"] == "unavailable"
    assert exposure["occurrences_per_hour"] is None
    assert exposure["observed_intervals_with_duration"] == 0


def test_real_observation_counts_only_matching_episodes_once():
    records = [episode(), episode("e2", frequencia=3),
               episode("outside", data_hora="2026-07-15T12:00:00-03:00"),
               episode("other_environment", ambiente="Casa")]
    result = compute_exposure_summary(records, [window()])
    assert result["observed_hours"] == 1
    assert result["episodes_in_observed_time"] == 2
    assert result["occurrences_per_hour"] == 2
    assert result["by_environment"]["Sala"]["responses"] == 4


def test_explicit_complete_observation_without_events_supports_zero():
    result = compute_exposure_summary([], [window()])
    assert result["occurrences_per_hour"] == 0
    assert result["opportunities_without_occurrence"] == 1


def test_overlapping_observation_windows_cannot_generate_a_rate():
    result = compute_exposure_summary([episode()], [window(), window("w2")])
    assert result["status"] == "unavailable"
    assert result["occurrences_per_hour"] is None
    assert "sobrepostas" in result["warning"]


def test_unobserved_and_incomplete_windows_are_excluded():
    result = compute_exposure_summary([episode()], [window(observacao_completa=False), window("w2",status_observacao="not_observed")])
    assert result["status"] == "unavailable"


def test_unknown_behavior_does_not_become_an_occurrence_after_normalization():
    records = [episode(comportamento=None)]
    report = build_abc_printable_summary(patient_name="Teste", records=records, intervals=[window()])
    assert report["observation_summary"]["behavior_occurrences"] == 0
    assert report["observation_summary"]["occurrence_intervals"] == 0
    assert report["observation_summary"]["opportunities_without_occurrence"] == 1


def test_period_environment_and_both_chain_endpoints_are_enforced():
    records = [episode(record_kind="observation")]
    candidates = [dict(from_interval_id="e1",to_interval_id="september", validation_status="accepted"),
                  dict(from_interval_id="e1",to_interval_id="e2", origin_behavior_code="CHORO",
                       from_consequence_code="PAUSA",to_antecedent_code="DEMANDA",next_behavior_code="CHORO",
                       validation_status="candidate",chain_confidence=1,delta_seconds=30)]
    report = build_abc_printable_summary(patient_name="Teste",records=records,
        intervals=[window("e1"),window("e2")],temporal_candidates=candidates)
    assert len(report["behavior_chains"]) == 1
    assert report["behavior_chains"][0]["repeticoes"] == 1


def test_legacy_episode_chains_are_not_presented_as_measured_abc_timings():
    candidate = dict(from_interval_id="e1",to_interval_id="e1",validation_status="accepted")
    report = build_abc_printable_summary(patient_name="Teste",records=[episode()],
        intervals=[window("e1",record_kind="episode")],temporal_candidates=[candidate])
    assert report["behavior_chains"] == []


def test_hours_use_local_time_and_exclude_unknown_times():
    rows = [episode(),episode("utc",data_hora="2026-07-15T13:25:00Z"),
            episode("unknown",precisao_horario="unknown"),
            episode("approximate",precisao_horario="approximate", data_hora="2026-07-15T10:40:00-03:00")]
    result = build_time_summary(rows,30)
    assert result["total_registros"] == 4
    assert result["horarios_validos"] == 3
    assert result["sem_horario"] == 1
    assert result["horarios_aproximados"] == 1
    assert result["por_horario"][0]["inicio"] == "10:00"
    assert result["por_horario"][0]["quantidade"] == 2
    assert result["por_horario"][0]["percentual_total"] == .5


def test_filtering_uses_local_dates_and_does_not_fill_missing_days_with_zero():
    rows=[episode(),episode("june",data_hora="2026-07-01T01:00:00Z"),episode("other",ambiente="Casa")]
    scoped=filter_records(rows,start_date=date(2026,7,1),end_date=date(2026,7,31),environment="Sala")
    assert [r["intervalo_id"] for r in scoped] == ["e1"]
    assert build_time_summary(scoped)["por_mes"][0]["dias"] == 1
    with pytest.raises(ValueError):
        filter_records(rows,start_date=date(2026,8,1),end_date=date(2026,7,1))


def test_audit_recognizes_actual_loader_field_names_and_ignores_episode_envelopes():
    result=audit_abc_data_quality([], [window(),window("w2")])
    assert result["overlap_evaluable"] is True
    assert result["overlapping_intervals"] == 1
    result=audit_abc_data_quality([], [window(record_kind="episode")])
    assert result["overlap_evaluable"] is False


def test_saved_time_question_uses_all_records_and_preserves_ties_and_behavior_scope():
    rows=[episode(str(i),data_hora=f"2026-07-15T10:20:{i:02d}-03:00") for i in range(40)]
    rows += [episode("grito",comportamento="Grito",data_hora="2026-07-15T09:00:00-03:00")]
    analysis=build_abc_analysis(rows)
    result=answer_time_question("Qual o horário com mais choro?",analysis)
    assert result["modo"] == "calculo_abc_sellas"
    assert result["indicadores_abc"]["total_registros"] == 40
    assert "10:00–10:59" in result["resposta"]
    assert answer_time_question("Qual hipótese funcional?",analysis) is None


def test_unknown_time_is_not_midnight_in_history_or_timeline():
    result=build_abc_analysis([episode(precisao_horario="unknown")])
    assert result["registros_recentes"][0]["hora"] == "Não informado"
    assert result["linha_tempo"] == []


def test_legacy_metadata_never_confirms_artificial_exposure_or_second_precision():
    metadata=record_metadata({"registro_fechado":True})
    assert metadata["record_kind"] == "episode"
    assert metadata["precisao_horario"] == "legacy"


def test_api_requires_a_time_or_explicit_unknown_and_accepts_missing_clinical_fields():
    base={"paciente":"Teste","comportamento_codigo":"CHORO","ambiente":"Sala"}
    with pytest.raises(ValidationError):
        ABCClosedRecordPayload(**base)
    payload=ABCClosedRecordPayload(**base,precisao_horario="unknown")
    assert payload.classificacao == "NC"
    assert payload.antecedente_codigo is None
    assert payload.consequencia_codigo is None
    with pytest.raises(ValidationError):
        ABCClosedRecordPayload(**base,precisao_horario="unknown",hora="09:00")


def test_dataset_hash_changes_when_real_exposure_changes():
    first=build_abc_printable_summary(patient_name="Teste",records=[episode()],intervals=[window()])
    second=build_abc_printable_summary(patient_name="Teste",records=[episode()],intervals=[window(fim="2026-07-15T12:00:00-03:00")])
    assert first["report_metadata"]["dataset_hash"] != second["report_metadata"]["dataset_hash"]


def test_unknown_event_time_prevents_false_absence_in_an_observed_window():
    result=compute_exposure_summary([episode(precisao_horario="unknown")],[window()])
    assert result["status"]=="unavailable"
    assert result["occurrences_per_hour"] is None
    assert result["opportunities_without_occurrence"] is None


def test_monthly_counts_and_measured_time_use_separate_numerators():
    from app.services.abc_integrity import attach_exposure_to_time_summary
    records=[episode(),episode("outside",data_hora="2026-07-15T12:00:00-03:00")]
    exposure=compute_exposure_summary(records,[window()])
    result=attach_exposure_to_time_summary(build_time_summary(records),exposure)
    month=result["por_mes"][0]
    assert month["quantidade"]==2
    assert month["horas_observadas"]==1
    assert month["episodios_no_tempo_observado"]==1
    assert month["episodios_por_hora_observada"]==1


def test_analytical_cache_hash_changes_after_an_edit_without_count_change():
    original=build_abc_analysis([episode()])
    changed=build_abc_analysis([episode(comportamento="Grito")])
    assert original["total_registros"]==changed["total_registros"]
    assert original["dataset_hash"]!=changed["dataset_hash"]


def test_unspecified_classification_uses_authorized_c1_policy_with_provenance():
    from app.services.abc_closed import _enrich_record,classify_interference_severity
    severity=classify_interference_severity("NC",caused_injury=False,bleeding=False,targeted_vital_point=False)
    assert severity["codigo"]=="C1"
    assert severity["classificacao_assumida"] is True
    row=_enrich_record({"intensidade":None,"causou_lesao":None,"houve_sangramento":None,"direcionado_ponto_vital":None})
    assert row["classificacao"]=="C1"
    assert row["classificacao_original"]=="NC"
    assert row["causou_lesao"] is None
