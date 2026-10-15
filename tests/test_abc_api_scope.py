from datetime import date

from fastapi.testclient import TestClient

import api


class Connection:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Engine:
    def connect(self):
        return Connection()


def test_http_scope_hour_bins_and_invalid_period(monkeypatch):
    rows = [dict(intervalo_id="july", data_hora="2026-07-15T10:20:00-03:00",comportamento="Choro",
                 antecedente="Demanda",consequencia="Pausa",classificacao="C1",ambiente="Sala"),
            dict(intervalo_id="august", data_hora="2026-08-15T09:00:00-03:00",comportamento="Grito",
                 antecedente="Demanda",consequencia="Pausa",classificacao="C2",ambiente="Casa")]
    monkeypatch.setattr(api,"engine",Engine())
    monkeypatch.setattr(api,"_patient_id",lambda *args:"patient-test")
    monkeypatch.setattr(api,"load_abc_patient_records",lambda *args:rows)
    monkeypatch.setattr(api,"load_observation_metadata",lambda *args,**kwargs:[])
    client=TestClient(api.app)
    response=client.get("/api/abc/analise",params={"paciente":"Teste","faixa_minutos":30,
        "data_inicio":"2026-07-01","data_fim":"2026-07-31","ambiente":"Sala","classificacao":"C1"})
    assert response.status_code==200,response.text
    body=response.json()
    assert body["total_registros"]==1
    assert body["distribuicao_temporal"]["faixa_minutos"]==30
    assert body["distribuicao_temporal"]["por_horario"][0]["inicio"]=="10:00"
    assert client.get("/api/abc/analise",params={"paciente":"Teste","faixa_minutos":45}).status_code==400
    assert client.get("/api/abc/analise",params={"paciente":"Teste","data_inicio":"2026-08-01","data_fim":"2026-07-01"}).status_code==400


def test_abc_time_question_needs_no_ai_and_uses_the_selected_period(monkeypatch):
    rows=[dict(intervalo_id="july",data_hora="2026-07-15T10:20:00-03:00",comportamento="Choro",
               antecedente="Demanda",consequencia="Pausa",classificacao="C1",ambiente="Sala")]
    monkeypatch.setattr(api,"engine",Engine())
    monkeypatch.setattr(api,"_patient_id",lambda *args:"patient-test")
    monkeypatch.setattr(api,"load_abc_patient_records",lambda *args:rows)
    def no_ai(**kwargs):
        raise AssertionError("Pergunta numérica não pode chamar IA.")
    monkeypatch.setattr(api,"answer_abc_functional_analysis",no_ai)
    response=TestClient(api.app).post("/api/abc/analise-funcional-ia",json={"paciente":"Teste",
        "pergunta":"Qual o horário com mais choro?","data_inicio":"2026-07-01","data_fim":"2026-07-31"})
    assert response.status_code==200,response.text
    assert response.json()["modo"]=="calculo_abc_sellas"


def test_general_agent_receives_sellas_abc_instead_of_inferring_from_other_tables(monkeypatch):
    rows=[dict(intervalo_id="july",data_hora="2026-07-15T10:20:00-03:00",comportamento="Choro",
               antecedente="Demanda",consequencia="Pausa",classificacao="C1",ambiente="Sala")]
    monkeypatch.setattr(api,"engine",Engine())
    monkeypatch.setattr(api,"_patient_id",lambda *args:"patient-test")
    monkeypatch.setattr(api,"load_abc_patient_records",lambda *args:rows)
    monkeypatch.setattr(api,"obter_dados_paciente",lambda **kwargs:{"interferentes":[]})
    captured={}
    def answer(**kwargs):
        captured.update(kwargs)
        return {"resposta":"Teste"}
    monkeypatch.setattr(api,"answer_with_knowledge",answer)
    client=TestClient(api.app)
    response=client.post("/api/agente/clinico",json={"paciente":"Teste","pergunta":"Qual padrão aparece?"})
    assert response.status_code==200,response.text
    assert captured["patient_context"]["abc_sellas"]["total_registros"]==1
    response=client.post("/api/agente/clinico",json={"paciente":"Teste","pergunta":"Qual horário tem mais interferentes?"})
    assert response.status_code==200,response.text
    assert response.json()["modo"]=="calculo_abc_sellas"
