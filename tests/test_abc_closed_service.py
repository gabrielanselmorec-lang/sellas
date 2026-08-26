import pytest
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import app.services.abc_closed as abc_closed

from app.services.abc_closed import (
    _category_code,
    _is_valid_xlsx,
    _safe_file_stem,
    build_abc_analysis,
    build_abc_printable_summary,
    classify_interference_severity,
    patient_token_uuid,
    parse_hour_minute_text,
    predict_abc_behavior,
)


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("9:30", "09:30:00"),
        ("09:30", "09:30:00"),
        ("0930", "09:30:00"),
        ("930", "09:30:00"),
        ("9h30", "09:30:00"),
        ("9.30", "09:30:00"),
        ("9", "09:00:00"),
    ],
)
def test_parse_hour_minute_text_accepts_natural_formats(typed, expected):
    assert parse_hour_minute_text(typed).isoformat() == expected


@pytest.mark.parametrize("typed", ["", "24:00", "12:60", "hora", "12345"])
def test_parse_hour_minute_text_rejects_invalid_values(typed):
    with pytest.raises(ValueError):
        parse_hour_minute_text(typed)


def test_xlsx_validation_checks_the_actual_package(tmp_path):
    valid_path = tmp_path / "valid.xlsx"
    with zipfile.ZipFile(valid_path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
    invalid_path = tmp_path / "invalid.xlsx"
    invalid_path.write_text("not an xlsx", encoding="utf-8")

    assert _is_valid_xlsx(valid_path) is True
    assert _is_valid_xlsx(invalid_path) is False


def test_excel_generation_accepts_valid_output_and_falls_back_when_final_is_locked(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "node_modules").mkdir()
    builder = runtime_dir / "build_abc_excel.mjs"
    builder.write_text("// test", encoding="utf-8")
    export_dir = tmp_path / "exports"

    monkeypatch.setattr(abc_closed, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(abc_closed, "ABC_EXPORT_DIR", export_dir)
    monkeypatch.setattr(abc_closed, "ABC_EXCEL_RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(abc_closed, "ABC_EXCEL_BUILDER", builder)
    monkeypatch.setattr(abc_closed, "init_abc_tables", lambda engine: None)
    monkeypatch.setattr(abc_closed, "load_patient_records", lambda engine, token: [])
    monkeypatch.setattr(abc_closed, "load_action_logs", lambda engine, token: [])
    monkeypatch.setattr(abc_closed, "list_categories", lambda engine: [])
    monkeypatch.setattr(abc_closed, "_node_binary", lambda: Path("node.exe"))

    def fake_run(args, **kwargs):
        output = Path(args[3])
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types />")
        Path(f"{output}.inspect.ndjson").write_text("inspection warning", encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="", stderr="inspection failed after export")

    real_replace = os.replace

    def replace_with_locked_final(source, destination):
        if Path(destination).name == "Paciente_abc_logs.xlsx":
            raise PermissionError("file is open")
        return real_replace(source, destination)

    monkeypatch.setattr(abc_closed.subprocess, "run", fake_run)
    monkeypatch.setattr(abc_closed.os, "replace", replace_with_locked_final)

    result = abc_closed.generate_patient_excel(
        object(),
        patient_token="patient-token",
        patient_name="Paciente",
    )

    assert "_atualizado_" in result.name
    assert _is_valid_xlsx(result)
    assert not list(export_dir.glob("*.inspect*"))


def test_c1_c2_classification_requires_objective_criteria():
    light = classify_interference_severity(
        "C1",
        caused_injury=False,
        bleeding=False,
        targeted_vital_point=False,
    )
    intense = classify_interference_severity(
        "C2",
        caused_injury=True,
        bleeding=False,
        targeted_vital_point=False,
    )
    assert light["intensidade"] == 1
    assert light["indice_perigo"] == 0.20
    assert intense["intensidade"] == 2
    assert intense["indice_perigo"] == 1.0
    with pytest.raises(ValueError):
        classify_interference_severity(
            "C2",
            caused_injury=False,
            bleeding=False,
            targeted_vital_point=False,
        )
    with pytest.raises(ValueError):
        classify_interference_severity(
            "C1",
            caused_injury=False,
            bleeding=True,
            targeted_vital_point=False,
        )


def test_patient_token_is_stable_and_uuid_compatible():
    first = patient_token_uuid("patient-local-id")
    second = patient_token_uuid("patient-local-id")
    assert first == second
    assert len(first) == 36


def test_custom_category_code_and_excel_filename_are_safe_and_stable():
    assert _category_code("Mudança não planejada!", "antecedente") == "ANT_MUDANCA_NAO_PLANEJADA"
    assert _category_code("Pedido de atenção", "comportamento") == "COM_PEDIDO_DE_ATENCAO"
    assert _safe_file_stem("PACIENTE TESTE / 01") == "PACIENTE_TESTE_01"


def test_analysis_builds_temporal_environment_and_conditional_metrics():
    rows = [
        {
            "intervalo_id": "1",
            "data_hora": "2026-07-14T09:00:00-03:00",
            "ambiente": "Sala de terapia",
            "antecedente": "Demanda apresentada",
            "comportamento": "Choro",
            "consequencia": "Pausa",
            "classificacao": "C1",
            "funcao": "Fuga ou esquiva",
        },
        {
            "intervalo_id": "2",
            "data_hora": "2026-07-14T10:00:00-03:00",
            "ambiente": "Sala de terapia",
            "antecedente": "Demanda apresentada",
            "comportamento": "Choro",
            "consequencia": "Atencao social",
            "classificacao": "C2",
            "funcao": "Atenção social",
        },
        {
            "intervalo_id": "3",
            "data_hora": "2026-07-15T10:00:00-03:00",
            "ambiente": "Sala de aula",
            "antecedente": "Transicao",
            "comportamento": "Grito",
            "consequencia": "Atencao social",
            "classificacao": "C1",
            "funcao": "Atenção social",
        },
    ]

    result = build_abc_analysis(rows)

    assert result["total_registros"] == 3
    assert result["ambientes_distintos"] == 2
    assert sum(item["quantidade"] for item in result["serie_temporal"]) == 3
    demand_cry = next(
        item
        for item in result["antecedente_comportamento"]
        if item["antecedente"] == "Demanda apresentada" and item["comportamento"] == "Choro"
    )
    assert demand_cry["suporte"] == 2
    assert demand_cry["probabilidade_condicional"] == 1.0
    assert round(demand_cry["probabilidade_baseline"], 4) == round(2 / 3, 4)
    assert demand_cry["lift"] == 1.5

    demand_cry_pause = next(
        item
        for item in result["cadeias_completas"]
        if item["antecedente"] == "Demanda apresentada"
        and item["comportamento"] == "Choro"
        and item["consequencia"] == "Pausa"
    )
    assert demand_cry_pause["suporte"] == 1
    assert demand_cry_pause["probabilidade_conjunta"] == 1 / 3
    assert demand_cry_pause["probabilidade_comportamento_dado_antecedente"] == 1.0
    assert demand_cry_pause["probabilidade_consequencia_dada_cadeia_ab"] == 0.5
    assert result["registros_recentes"][0]["hora"] == "10:00:00"
    assert len(result["heatmap_cadeias_ambiente"]) == 3
    assert result["c1_leve"] == 2
    assert result["c2_intenso"] == 1
    assert len(result["mapa_risco"]) == 3
    intense_chain = next(item for item in result["mapa_risco"] if item["c2_intenso"] == 1)
    assert intense_chain["indice_perigo"] == 1.0
    assert intense_chain["funcao"] == "Atenção social"


def test_recent_records_follow_addition_order_and_management_keeps_may_records():
    event_start = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)
    added_start = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    rows = [
        {
            "intervalo_id": f"record-{index:02d}",
            "data_hora": (event_start + timedelta(days=index)).isoformat(),
            "criado_em": (added_start + timedelta(minutes=index)).isoformat(),
            "ambiente": "Sala de terapia",
            "antecedente": "Demanda",
            "comportamento": "Choro",
            "consequencia": "Pausa",
            "classificacao": "C1",
            "funcao": "Fuga/esquiva",
        }
        for index in range(55)
    ]
    rows[0]["criado_em"] = (added_start + timedelta(days=30)).isoformat()

    result = build_abc_analysis(rows)

    assert len(result["registros_recentes"]) == 50
    assert len(result["registros_para_gestao"]) == 55
    assert result["registros_recentes"][0]["intervalo_id"] == "record-00"
    assert result["registros_recentes"][0]["data"] == "2026-05-01"
    assert any(item["data"].startswith("2026-05") for item in result["registros_para_gestao"])


def test_printable_summary_uses_distinct_observed_intervals_and_preserves_null():
    records = [
        {
            "intervalo_id": "i1",
            "data_hora": "2026-07-14T09:00:00-03:00",
            "ambiente": "Sala",
            "antecedente": "Demanda",
            "comportamento": "Choro",
            "consequencia": "Pausa",
            "classificacao": "C1",
            "funcao": "Fuga ou esquiva",
        },
        {
            "intervalo_id": "i1",
            "data_hora": "2026-07-14T09:00:01-03:00",
            "ambiente": "Sala",
            "antecedente": "Demanda",
            "comportamento": "Grito",
            "consequencia": "Redirecionamento",
            "classificacao": "C1",
            "funcao": "Fuga ou esquiva",
        },
    ]
    intervals = [
        {"intervalo_id": "i1", "session_id": "s1", "status_observacao": "observed", "duracao_planejada_minutos": 5, "eventos_nao_informados": 1, "instrumento_versao": "1"},
        {"intervalo_id": "i2", "session_id": "s1", "status_observacao": "observed", "duracao_planejada_minutos": 5, "eventos_nao_informados": 0, "instrumento_versao": "1"},
        {"intervalo_id": "i3", "session_id": "s1", "status_observacao": "not_observed", "duracao_planejada_minutos": 5, "eventos_nao_informados": 0, "instrumento_versao": "1"},
        {"intervalo_id": "i4", "session_id": "s2", "status_observacao": "invalid", "duracao_planejada_minutos": 5, "eventos_nao_informados": 0, "instrumento_versao": "1"},
    ]

    result = build_abc_printable_summary(
        patient_name="Paciente",
        records=records,
        intervals=intervals,
    )

    observation = result["observation_summary"]
    assert observation["sessions"] == 2
    assert observation["total_records"] == 2
    assert observation["behavior_occurrences"] == 2
    assert observation["total_intervals"] == 4
    assert observation["observed_intervals"] == 2
    assert observation["occurrence_intervals"] == 1
    assert observation["occurrence_percentage"] == 0.5
    assert observation["coverage"] == 0.5
    assert observation["coverage"] <= 1.0
    assert result["data_quality"]["missing_event_values"] == 1
    assert any("nenhum foi convertido em ausência" in item for item in result["data_quality"]["warnings"])


def test_printable_summary_anonymizes_and_excludes_rejected_or_unreviewed_chains():
    records = [
        {
            "intervalo_id": "i1",
            "data_hora": "2026-07-14T09:00:00-03:00",
            "ambiente": "Sala",
            "antecedente": "Demanda",
            "comportamento": "Choro",
            "consequencia": "Pausa",
            "classificacao": "C1",
            "funcao": "Fuga ou esquiva",
        }
    ]
    intervals = [
        {"intervalo_id": "i1", "session_id": "s1", "status_observacao": "observed", "duracao_planejada_minutos": 5, "instrumento_versao": "1"}
    ]
    candidates = [
        {"origin_behavior_code": "B1", "from_consequence_code": "C1", "to_antecedent_code": "A2", "next_behavior_code": "B2", "delta_seconds": 30, "chain_confidence": 0.95, "validation_status": "accepted"},
        {"origin_behavior_code": "B3", "from_consequence_code": "C3", "to_antecedent_code": "A4", "next_behavior_code": "B4", "delta_seconds": 60, "chain_confidence": 0.80, "validation_status": "candidate"},
        {"origin_behavior_code": "B5", "from_consequence_code": "C5", "to_antecedent_code": "A6", "next_behavior_code": "B6", "delta_seconds": 90, "chain_confidence": 0.90, "validation_status": "rejected"},
    ]

    result = build_abc_printable_summary(
        patient_name="Nome sensível",
        records=records,
        intervals=intervals,
        temporal_candidates=candidates,
        anonymize_patient=True,
        reviewed_chains_only=True,
    )

    assert result["patient"] == {"display_name": "Paciente anonimizado", "anonymized": True}
    assert len(result["behavior_chains"]) == 1
    assert result["behavior_chains"][0]["status"] == "Revisada e aceita"
    assert "Nome sensível" not in str(result)
    assert "não demonstram causalidade" in result["clinical_disclaimer"]


def test_printable_summary_without_data_is_explicitly_insufficient():
    result = build_abc_printable_summary(
        patient_name="Paciente",
        records=[],
        intervals=[],
    )

    assert result["observation_summary"]["total_intervals"] == 0
    assert result["observation_summary"]["total_records"] == 0
    assert result["data_quality"]["status"] == "insuficiente"
    assert "não é suficiente" in result["descriptive_summary"]
    assert result["behavior_chains"] == []


def test_report_audit_log_persists_metadata_without_clinical_content(monkeypatch):
    calls = []

    class FakeConnection:
        def execute(self, statement, params=None):
            calls.append((str(statement), params))

    class FakeTransaction:
        def __enter__(self):
            return FakeConnection()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class FakeEngine:
        def begin(self):
            return FakeTransaction()

    monkeypatch.setattr(abc_closed, "init_abc_tables", lambda engine: None)
    abc_closed.log_abc_report_generation(
        FakeEngine(),
        patient_token="token-anonimo",
        generated_by="Profissional teste",
        filters={"ambiente": "Sala", "data_inicio": "2026-07-01"},
        output_format="pdf",
        anonymized=True,
    )

    insert_params = next(params for sql, params in calls if "INSERT INTO abc_report_audit_logs" in sql)
    assert insert_params["patient_token"] == "token-anonimo"
    assert insert_params["output_format"] == "pdf"
    assert insert_params["anonymized"] is True
    serialized = str(insert_params)
    assert "antecedente" not in serialized.lower()
    assert "comportamento" not in serialized.lower()
    assert "consequencia" not in serialized.lower()


def test_database_read_retries_deadlock_and_closes_failed_connection(monkeypatch):
    attempts = {"count": 0}

    class DeadlockError(Exception):
        orig = SimpleNamespace(pgcode="40P01")

    class FakeResult:
        def mappings(self):
            return self

        def all(self):
            return [{"ok": True}]

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, statement, params):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise DeadlockError("deadlock detected")
            return FakeResult()

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    monkeypatch.setattr(abc_closed.time_module, "sleep", lambda seconds: None)
    rows = abc_closed._query_mappings_with_retry(FakeEngine(), object(), {})

    assert rows == [{"ok": True}]
    assert attempts["count"] == 3


def test_abc_schema_initialization_runs_only_once(monkeypatch):
    calls = []
    monkeypatch.setattr(abc_closed, "_ABC_TABLES_READY", False)
    monkeypatch.setattr(abc_closed, "_initialize_abc_tables", lambda engine: calls.append(engine))

    engine = object()
    abc_closed.init_abc_tables(engine)
    abc_closed.init_abc_tables(engine)

    assert calls == [engine]


def test_behavior_estimate_is_descriptive_and_reproducible():
    rows = [
        {
            "data_hora": f"2026-07-14T09:00:0{index}-03:00",
            "ambiente": "Sala de terapia",
            "antecedente": "Demanda apresentada",
            "comportamento": "Choro" if index < 3 else "Grito",
            "consequencia": "Pausa",
            "classificacao": "C2" if index == 0 else "C1",
            "funcao": "Fuga ou esquiva" if index < 3 else "Atenção social",
        }
        for index in range(5)
    ]

    result = predict_abc_behavior(
        rows,
        behavior="Choro",
        antecedent="Demanda apresentada",
        environment="Sala de terapia",
        classification="C2",
        function="Fuga ou esquiva",
    )

    assert result["amostra_total"] == 5
    assert result["amostra_contexto"] == 5
    assert result["sucessos_contexto"] == 3
    assert result["analysis_mode"] == "descriptive"
    assert result["absolute_risk_available"] is False
    assert result["probabilidade_baseline"] == 3 / 5
    assert result["numerador"] == 3
    assert result["denominador"] == 5
    assert result["estimativa_descritiva"] == 3 / 5
    assert result["probabilidade_classificacao"] == 1 / 3
    assert result["probabilidade_funcao"] == 1.0
    assert result["intervalo_wilson_inferior"] < result["estimativa_descritiva"] < result["intervalo_wilson_superior"]
    assert result["legacy_probability_field_deprecated"] is True
    assert result["componentes"] == []
