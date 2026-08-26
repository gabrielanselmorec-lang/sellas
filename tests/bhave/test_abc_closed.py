from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.abc_analysis import floor_timestamp_to_interval
from backend.app.services.storage import (
    save_abc_categories,
    save_abc_events,
    save_abc_instruments,
    save_abc_intervals,
    save_abc_sessions,
)


def _reset_abc():
    save_abc_sessions([])
    save_abc_intervals([])
    save_abc_categories([])
    save_abc_events([])
    save_abc_instruments([])


def _create_session(client: TestClient, start="2026-07-11T10:00:00-03:00", end="2026-07-11T11:00:00-03:00"):
    response = client.post(
        "/api/abc/sessions",
        json={
            "patient_token": "P-ABC",
            "service_id": "SVC",
            "data_inicio": start,
            "data_fim": end,
            "timezone": "America/Sao_Paulo",
            "observacao_completa": True,
            "instrumento_versao": "1",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_interval(client: TestClient, session_id: str, timestamp: str, status="observed"):
    response = client.post(
        f"/api/abc/sessions/{session_id}/intervals",
        json={
            "inicio": timestamp,
            "timezone": "America/Sao_Paulo",
            "interval_minutes": 5,
            "status_observacao": status,
            "observador_token": "OBS",
            "instrumento_versao": "1",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _event(client: TestClient, interval_id: str, code: str, occurred):
    response = client.post(
        f"/api/abc/intervals/{interval_id}/events",
        json={"categoria_codigo": code, "ocorreu": occurred, "fonte": "registro_fechado", "confianca_registro": 1.0},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_floor_timestamp_to_interval_handles_boundaries_midnight_and_invalid_interval():
    assert floor_timestamp_to_interval(datetime(2026, 7, 14, 10, 32, tzinfo=timezone.utc), 5).minute == 30
    assert floor_timestamp_to_interval(datetime(2026, 7, 14, 10, 35, tzinfo=timezone.utc), 5).minute == 35
    floored = floor_timestamp_to_interval(datetime(2026, 7, 14, 23, 59, tzinfo=timezone.utc), 5)
    assert (floored.hour, floored.minute) == (23, 55)
    next_day = floor_timestamp_to_interval(datetime(2026, 7, 15, 0, 1, tzinfo=timezone.utc), 5)
    assert (next_day.day, next_day.hour, next_day.minute) == (15, 0, 0)
    try:
        floor_timestamp_to_interval(datetime(2026, 7, 14, 10, 32, tzinfo=timezone.utc), 0)
        assert False, "intervalo invalido deveria falhar"
    except ValueError:
        assert True


def test_abc_endpoints_reject_duplicate_interval_and_duplicate_category_event():
    _reset_abc()
    client = TestClient(app)
    session = _create_session(client)
    interval = _create_interval(client, session["id"], "2026-07-11T10:32:00-03:00")
    duplicate_interval = client.post(
        f"/api/abc/sessions/{session['id']}/intervals",
        json={"inicio": "2026-07-11T10:34:00-03:00", "interval_minutes": 5, "status_observacao": "observed"},
    )
    assert duplicate_interval.status_code == 400

    _event(client, interval["id"], "PEDIDO_NEGADO", True)
    duplicate_event = client.post(
        f"/api/abc/intervals/{interval['id']}/events",
        json={"categoria_codigo": "PEDIDO_NEGADO", "ocorreu": False},
    )
    assert duplicate_event.status_code == 400


def test_abc_conditional_metrics_use_observed_denominator_and_keep_null_distinct_from_false():
    _reset_abc()
    client = TestClient(app)
    session = _create_session(client)
    intervals = [
        _create_interval(client, session["id"], "2026-07-11T10:00:00-03:00"),
        _create_interval(client, session["id"], "2026-07-11T10:05:00-03:00"),
        _create_interval(client, session["id"], "2026-07-11T10:10:00-03:00"),
        _create_interval(client, session["id"], "2026-07-11T10:15:00-03:00", status="not_observed"),
    ]
    _event(client, intervals[0]["id"], "PEDIDO_NEGADO", True)
    _event(client, intervals[0]["id"], "AGRESSAO_FISICA", True)
    _event(client, intervals[1]["id"], "PEDIDO_NEGADO", True)
    _event(client, intervals[1]["id"], "AGRESSAO_FISICA", False)
    _event(client, intervals[2]["id"], "PEDIDO_NEGADO", True)
    _event(client, intervals[2]["id"], "AGRESSAO_FISICA", None)
    _event(client, intervals[3]["id"], "PEDIDO_NEGADO", True)
    _event(client, intervals[3]["id"], "AGRESSAO_FISICA", True)

    analysis = client.get(
        "/api/abc/analysis/conditional-probabilities",
        params={"patient_token": "P-ABC", "minimum_valid_intervals": 1},
    )
    assert analysis.status_code == 200
    row = next(
        item for item in analysis.json()["metricas"]
        if item["antecedente"]["codigo"] == "PEDIDO_NEGADO" and item["comportamento"]["codigo"] == "AGRESSAO_FISICA"
    )
    assert row["intervalos_observados_total"] == 3
    assert row["intervalos_observados"] == 2
    assert row["dados_ausentes"] == 1
    assert row["intervalos_com_ambos"] == 1
    assert row["probabilidade_condicional"] == 0.5
    assert "nao determina causa ou funcao" in row["interpretacao"]


def test_abc_weekends_are_included_by_default_and_period_filter_is_configurable():
    _reset_abc()
    client = TestClient(app)
    saturday_session = _create_session(client, start="2026-07-11T09:00:00-03:00", end="2026-07-11T10:00:00-03:00")
    monday_session = _create_session(client, start="2026-07-13T09:00:00-03:00", end="2026-07-13T10:00:00-03:00")
    saturday = _create_interval(client, saturday_session["id"], "2026-07-11T09:00:00-03:00")
    monday = _create_interval(client, monday_session["id"], "2026-07-13T09:00:00-03:00")
    for interval in (saturday, monday):
        _event(client, interval["id"], "PEDIDO_NEGADO", True)
        _event(client, interval["id"], "AGRESSAO_FISICA", True)

    include = client.get("/api/abc/analysis/conditional-probabilities", params={"patient_token": "P-ABC", "minimum_valid_intervals": 1})
    exclude = client.get(
        "/api/abc/analysis/conditional-probabilities",
        params={"patient_token": "P-ABC", "include_weekends": False, "minimum_valid_intervals": 1},
    )
    period = client.get(
        "/api/abc/analysis/conditional-probabilities",
        params={
            "patient_token": "P-ABC",
            "periodo_inicio": "2026-07-13T00:00:00Z",
            "periodo_fim": "2026-07-13T23:59:00Z",
            "minimum_valid_intervals": 1,
        },
    )

    assert include.json()["intervalos_observados_total"] == 2
    assert exclude.json()["intervalos_observados_total"] == 1
    assert period.json()["intervalos_observados_total"] == 1


def test_abc_associations_and_prediction_rows_do_not_use_same_interval_consequence_for_same_behavior():
    _reset_abc()
    client = TestClient(app)
    session = _create_session(client)
    first = _create_interval(client, session["id"], "2026-07-11T10:00:00-03:00")
    second = _create_interval(client, session["id"], "2026-07-11T10:05:00-03:00")
    _event(client, first["id"], "AGRESSAO_FISICA", True)
    _event(client, first["id"], "PAUSA", True)
    _event(client, second["id"], "AGRESSAO_FISICA", False)

    associations = client.get("/api/abc/analysis/associations", params={"patient_token": "P-ABC", "minimum_valid_intervals": 1})
    prediction_rows = client.get("/api/abc/prediction/rows", params={"patient_token": "P-ABC", "behavior_code": "AGRESSAO_FISICA"})

    assert associations.status_code == 200
    assert associations.json()["titulo"] == "Mapa descritivo de associacoes ABC"
    assert "nao confirmam causa ou funcao" in associations.json()["aviso"]
    assert prediction_rows.status_code == 200
    assert prediction_rows.json()["rows"][0]["leakage_guard"] == "same_interval_consequence_not_used_to_predict_same_interval_behavior"
