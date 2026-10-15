"""Integração opt-in; cria e remove somente um esquema aleatório de teste."""
import os
import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text

from app.services import abc_closed
from app.services.abc_integrity import DuplicateRecordError
from app.services.abc_methodology import compute_exposure_summary


@pytest.fixture
def isolated_postgres(monkeypatch):
    url = os.getenv("SELLAS_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Defina SELLAS_TEST_POSTGRES_URL para testar em esquema isolado.")
    schema = "sellas_abc_test_" + uuid.uuid4().hex
    admin = create_engine(url, connect_args={"connect_timeout": 15})
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"connect_timeout": 15, "options": f"-c search_path={schema}"})
    monkeypatch.setattr(abc_closed, "_ABC_TABLES_READY", False)
    try:
        abc_closed.init_abc_tables(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_actual_postgres_write_replay_duplicate_edit_load_and_exposure(isolated_postgres):
    engine = isolated_postgres
    patient = str(uuid.uuid4())
    args = dict(patient_token=patient, antecedente_codigo="DEMANDA", comportamento_codigo="CHORO",
        consequencia_codigo="PAUSA", data_hora=datetime.fromisoformat("2026-07-15T10:20:37-03:00"),
        ambiente="Sala", classificacao="C1", causou_lesao=False, houve_sangramento=False,
        direcionado_ponto_vital=False, funcao="Indeterminada / em avaliação", precisao_horario="minute",
        fonte_referencia="Folha teste / linha 1", observador="Observador teste", frequencia=3,
        request_id=str(uuid.uuid4()))
    first = abc_closed.add_closed_record(engine, **args)
    assert first["data_hora"].endswith("10:20:00-03:00")
    replay = abc_closed.add_closed_record(engine, **args)
    assert replay["replayed"] is True
    assert replay["interval_id"] == first["interval_id"]
    with pytest.raises(ValueError, match="outro conteúdo"):
        abc_closed.add_closed_record(engine, **{**args, "frequencia": 8})
    with pytest.raises(DuplicateRecordError):
        abc_closed.add_closed_record(engine, **{**args, "request_id": str(uuid.uuid4())})
    second = abc_closed.add_closed_record(engine, **{**args, "request_id": str(uuid.uuid4()), "confirmar_duplicado": True})
    rows = abc_closed.load_patient_records(engine, patient)
    assert len(rows) == 2
    assert all(r["session_id"] is None for r in rows)
    assert all(r["record_kind"] == "episode" for r in rows)
    assert all(r["frequencia"] == 3 for r in rows)
    intervals = abc_closed.load_observation_metadata(engine, patient_token=patient)
    assert all(i["duracao_planejada_minutos"] is None for i in intervals)
    assert compute_exposure_summary(rows, intervals)["occurrences_per_hour"] is None
    edited = {**args, "request_id": str(uuid.uuid4()), "frequencia": 7,
              "atendimento_referencia": "atendimento-real-1", "confirmar_duplicado": True}
    abc_closed.update_closed_record(engine, interval_id=first["interval_id"], **edited)
    rows = abc_closed.load_patient_records(engine, patient)
    changed = next(r for r in rows if r["intervalo_id"] == first["interval_id"])
    assert changed["frequencia"] == 7
    assert changed["session_id"] == "atendimento-real-1"
    assert changed["fonte_referencia"] == args["fonte_referencia"]
    observation_args = dict(patient_token=patient, start="2026-07-15T10:00:00-03:00",end="2026-07-15T11:00:00-03:00",
        environment="Sala",observer="Observador teste",source_reference="Folha teste",
        complete_recording=True,request_id=str(uuid.uuid4()))
    abc_closed.add_observation_window(engine, **observation_args)
    replay = abc_closed.add_observation_window(engine, **observation_args)
    assert replay["replayed"] is True
    with pytest.raises(ValueError, match="Já existe"):
        abc_closed.add_observation_window(engine, **{**observation_args,"request_id":str(uuid.uuid4())})
    intervals = abc_closed.load_observation_metadata(engine, patient_token=patient)
    exposure = compute_exposure_summary(rows, intervals)
    assert exposure["observed_hours"] == 1
    assert exposure["occurrences_per_hour"] == 2
    summary = abc_closed.build_abc_printable_summary(patient_name="Teste", records=rows, intervals=intervals)
    assert summary["observation_summary"]["behavior_occurrences"] == 2
    assert summary["observation_summary"]["occurrences_per_observed_hour"] == 2
    assert summary["data_quality"]["retroactive_records"] == 2
    abc_closed.delete_closed_record(engine,patient_token=patient,interval_id=second["interval_id"])
    assert len(abc_closed.load_patient_records(engine,patient)) == 1


def test_unknown_time_and_missing_abc_fields_roundtrip(isolated_postgres):
    engine = isolated_postgres
    patient = str(uuid.uuid4())
    abc_closed.add_closed_record(engine,patient_token=patient,antecedente_codigo=None,
        comportamento_codigo="CHORO",consequencia_codigo=None,
        data_hora=datetime.fromisoformat("2026-08-05T10:20:32-03:00"),ambiente="Sala",
        classificacao="NC",causou_lesao=False,houve_sangramento=False,direcionado_ponto_vital=False,
        funcao=None,precisao_horario="unknown",request_id=str(uuid.uuid4()))
    rows=abc_closed.load_patient_records(engine,patient)
    assert len(rows)==1
    assert rows[0]["precisao_horario"]=="unknown"
    assert rows[0]["antecedente_codigo"] is None
    assert rows[0]["consequencia_codigo"] is None
    assert rows[0]["classificacao"]=="C1"
    assert rows[0]["classificacao_assumida"] is True
    assert rows[0]["classificacao_original"]=="NC"
    assert rows[0]["causou_lesao"] is None
    analysis=abc_closed.build_abc_analysis(rows)
    assert analysis["distribuicao_temporal"]["sem_horario"]==1
    assert analysis["registros_recentes"][0]["hora"]=="Não informado"


def test_legacy_rows_keep_their_original_time_but_do_not_create_exposure(isolated_postgres):
    engine=isolated_postgres
    patient=str(uuid.uuid4())
    result=abc_closed.add_closed_record(engine,patient_token=patient,antecedente_codigo="DEMANDA",
        comportamento_codigo="CHORO",consequencia_codigo="PAUSA",data_hora=datetime.fromisoformat("2026-07-15T10:20:37-03:00"),
        ambiente="Sala",classificacao="C1",causou_lesao=False,houve_sangramento=False,
        direcionado_ponto_vital=False,funcao="Indeterminada",precisao_horario="second")
    with engine.begin() as conn:
        conn.execute(text("UPDATE abc_intervals SET metadados='{}'::jsonb,duracao_planejada_minutos=5,fim=inicio+interval '5 minutes' WHERE id=CAST(:i AS UUID)"),{"i":result["interval_id"]})
        conn.execute(text("UPDATE abc_interval_events SET fonte='registro_fechado' WHERE intervalo_id=CAST(:i AS UUID)"),{"i":result["interval_id"]})
    rows=abc_closed.load_patient_records(engine,patient)
    assert rows[0]["data_hora"].endswith("10:20:37-03:00")
    assert rows[0]["precisao_horario"]=="legacy"
    intervals=abc_closed.load_observation_metadata(engine,patient_token=patient)
    assert intervals[0]["record_kind"]=="episode"
    assert compute_exposure_summary(rows,intervals)["occurrences_per_hour"] is None


def test_removing_an_episode_keeps_other_intervals_in_its_session(isolated_postgres):
    engine=isolated_postgres
    patient=str(uuid.uuid4())
    result=abc_closed.add_closed_record(engine,patient_token=patient,antecedente_codigo="DEMANDA",
        comportamento_codigo="CHORO",consequencia_codigo="PAUSA",data_hora=datetime.fromisoformat("2026-07-15T10:20:00-03:00"),
        ambiente="Sala",classificacao="C1",causou_lesao=False,houve_sangramento=False,
        direcionado_ponto_vital=False,funcao="Indeterminada")
    with engine.begin() as conn:
        other_id=conn.execute(text("""INSERT INTO abc_intervals(sessao_id,inicio,fim,timezone,duracao_planejada_minutos,status_observacao)
            SELECT sessao_id,inicio+interval '1 minute',fim+interval '1 minute',timezone,1,'observed'
            FROM abc_intervals WHERE id=CAST(:i AS UUID) RETURNING id"""),{"i":result["interval_id"]}).scalar_one()
    abc_closed.delete_closed_record(engine,patient_token=patient,interval_id=result["interval_id"])
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM abc_intervals WHERE id=CAST(:i AS UUID)"),{"i":other_id}).scalar_one()==1
        assert conn.execute(text("SELECT COUNT(*) FROM abc_sessions WHERE id=CAST(:s AS UUID)"),{"s":result["session_id"]}).scalar_one()==1


def test_read_only_audit_does_not_change_the_default_for_later_writes(isolated_postgres):
    from sqlalchemy import event
    from scripts.verify_abc_patients import configure_read_only
    engine=isolated_postgres
    with engine.connect() as conn:
        initial=conn.execute(text("SHOW default_transaction_read_only")).scalar_one()
    listener=configure_read_only(engine)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SHOW transaction_read_only")).scalar_one()=="on"
            assert conn.execute(text("SHOW default_transaction_read_only")).scalar_one()==initial
    finally:
        event.remove(engine,"begin",listener)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE audit_write_regression(id INTEGER)"))
        conn.execute(text("INSERT INTO audit_write_regression VALUES(1)"))
        assert conn.execute(text("SELECT COUNT(*) FROM audit_write_regression")).scalar_one()==1
