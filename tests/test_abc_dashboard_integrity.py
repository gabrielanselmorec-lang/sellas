from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest


def dashboard_app(extra_setup=""):
    source = Path("app/web/dashboard.py").read_text(encoding="utf-8").split('\nst.set_page_config(', 1)[0]
    prefix = "import sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path.cwd()/'app'/'web'))\n"
    fixture = '''
from app.services.abc_closed import build_abc_analysis,predict_abc_behavior
from app.services.abc_integrity import build_time_summary,filter_records
st.set_page_config(layout="wide")
_rows=[{"intervalo_id":"e1","data_hora":"2026-07-15T10:20:00-03:00","criado_em":"2026-10-15T10:00:00-03:00",
        "ambiente":"Sala de terapia","antecedente":"Demanda apresentada","antecedente_codigo":"DEMANDA",
        "comportamento":"Choro","comportamento_codigo":"CHORO","consequencia":"Pausa","consequencia_codigo":"PAUSA",
        "classificacao":"C1","intensidade":1,"funcao":"Indeterminada / em avaliação","precisao_horario":"minute"}]
def load_abc_categories():
    return [{"nome":"Demanda apresentada","codigo":"DEMANDA","tipo":"antecedente"},
            {"nome":"Choro","codigo":"CHORO","tipo":"comportamento"},
            {"nome":"Pausa","codigo":"PAUSA","tipo":"consequencia"}]
def load_abc_analysis(patient,environment=None,*,scoped=False):
    params=_abc_scope_params(patient) if scoped else {}
    rows=filter_records(_rows,start_date=date.fromisoformat(params['data_inicio']) if params.get('data_inicio') else None,
        end_date=date.fromisoformat(params['data_fim']) if params.get('data_fim') else None,
        environment=environment,behavior=params.get('comportamento'),classification=params.get('classificacao'))
    result=build_abc_analysis(rows)
    result['distribuicao_temporal']=build_time_summary(rows,int(params.get('faixa_minutos',60)))
    return result
def load_abc_excel(patient):
    return b"test", "test.xlsx"
def load_abc_prediction(patient,behavior,antecedent=None,environment=None,classification=None,function=None):
    return predict_abc_behavior(_rows,behavior=behavior,antecedent=antecedent,environment=environment,classification=classification,function=function)
def load_abc_chain_rules():
    return {"rules":[],"config":{}}
def load_abc_temporal_chains(patient):
    return {"candidates":[],"stats":[],"matrix":[],"timeline":[]}
def save_abc_record(patient,payload):
    st.session_state['test_saved']=payload
    return {"ok":True,"excel_status":"queued"}
render_abc_registration_and_analysis("Paciente de teste")
'''
    fixture=fixture.replace('\nrender_abc_registration_and_analysis(', '\n'+extra_setup+'\nrender_abc_registration_and_analysis(')
    return AppTest.from_string(prefix + source + fixture, default_timeout=30)


def test_dashboard_defaults_and_unknown_time_submission():
    app=dashboard_app().run()
    assert not app.exception, [e.message for e in app.exception]
    assert app.date_input(key="abc_record_date").value is None
    assert app.text_input(key="abc_record_time").value == ""
    assert app.number_input(key="abc_record_second").value == 0
    assert app.selectbox(key="abc_behavior").value is None
    assert app.radio(key="abc_classification").value == "Não informado"
    assert app.selectbox(key="abc_function").value == "Indeterminada / em avaliação"
    app.date_input(key="abc_record_date").set_value(date(2026,8,5))
    app.selectbox(key="abc_behavior").select("Choro")
    app.selectbox(key="abc_time_precision").select("Horário não informado")
    app.button(key="abc_add_record").click().run()
    assert not app.exception, [e.message for e in app.exception]
    saved=app.session_state["test_saved"]
    assert saved["hora"] is None
    assert saved["precisao_horario"] == "unknown"
    assert saved["classificacao"] == "NC"
    assert saved["antecedente_codigo"] is None
    assert saved["consequencia_codigo"] is None


def test_dashboard_analysis_scope_can_change_without_runtime_errors():
    app=dashboard_app().run()
    assert not app.exception
    app.selectbox(key="abc_scope_bin::Paciente de teste").select(30).run()
    assert not app.exception, [e.message for e in app.exception]
    app.selectbox(key="abc_scope_classification::Paciente de teste").select("C2").run()
    assert not app.exception, [e.message for e in app.exception]
    # A scope with no records must not hide registration or the full history.
    assert app.selectbox(key="abc_behavior") is not None
    assert any("Últimos registros" in heading.value for heading in app.markdown)


def test_dashboard_tabs_separate_hours_reports_and_registration():
    app=dashboard_app().run()
    assert not app.exception, [e.message for e in app.exception]
    tabs=app.get("tab")
    main_labels=["Horários", "Evolução", "Contextos", "Cadeias", "Registros", "Relatórios"]
    assert [tab.label for tab in tabs if tab.label in main_labels] == main_labels
    hours=next(tab for tab in tabs if tab.label=="Horários")
    assert any("10:00 às 10:59" in item.value for item in hours.markdown)
    assert not hours.button
    assert not hours.get("download_button")
    app.radio(key="abc_hour_order::Paciente de teste").set_value("Mais registros").run()
    assert not app.exception
    app.selectbox(key="abc_scope_bin::Paciente de teste").select(30).run()
    assert not app.exception
    assert app.selectbox(key="abc_hour_detail::Paciente de teste").value == "10:00 às 10:29"


def test_dashboard_without_records_keeps_registration_available():
    app=dashboard_app("_rows=[]").run()
    assert not app.exception, [e.message for e in app.exception]
    assert app.selectbox(key="abc_behavior").value is None
    assert app.button(key="abc_add_record") is not None
    assert any("Não há episódios" in info.value for info in app.info)


def test_real_dashboard_opens_abc_without_requesting_other_patient_data(monkeypatch):
    import requests
    from app.services.abc_closed import build_abc_analysis,predict_abc_behavior
    row={"intervalo_id":"e1","data_hora":"2026-07-15T10:20:00-03:00","criado_em":"2026-10-15T10:00:00-03:00",
         "ambiente":"Sala de terapia","antecedente":"Demanda apresentada","antecedente_codigo":"DEMANDA",
         "comportamento":"Choro","comportamento_codigo":"CHORO","consequencia":"Pausa","consequencia_codigo":"PAUSA",
         "classificacao":"C1","intensidade":1,"funcao":"Indeterminada / em avaliação","precisao_horario":"minute"}
    calls=[]
    class Response:
        ok=True
        content=b"test"
        headers={"Content-Disposition":'attachment; filename="test.xlsx"'}
        def __init__(self,data): self.data=data
        def json(self): return self.data
    def get(url,params=None,**kwargs):
        calls.append(url)
        if url.endswith('/api/pacientes'): return Response(["Paciente de teste"])
        if url.endswith('/api/abc/categorias'):
            return Response([{"nome":"Demanda apresentada","codigo":"DEMANDA","tipo":"antecedente"},
                             {"nome":"Choro","codigo":"CHORO","tipo":"comportamento"},
                             {"nome":"Pausa","codigo":"PAUSA","tipo":"consequencia"}])
        if url.endswith('/api/abc/analise'): return Response(build_abc_analysis([row]))
        if url.endswith('/api/abc/excel'): return Response({})
        if url.endswith('/api/abc/estimativa-descritiva'): return Response(predict_abc_behavior([row],behavior="Choro"))
        if url.endswith('/api/abc/config/chain-rules'): return Response({"rules":[],"config":{}})
        if url.endswith('/api/abc/chains/scoped'): return Response({"candidates":[],"stats":[],"matrix":[],"timeline":[]})
        raise AssertionError(f"Consulta fora do módulo ABC: {url}")
    monkeypatch.setattr(requests,"get",get)
    app=AppTest.from_file("app/web/dashboard.py",default_timeout=30).run()
    assert not app.exception,[e.message for e in app.exception]
    assert app.radio(key="prediction_area").value=="Análise ABC (fechado)"
    assert all('/api/abc/' in url or url.endswith('/api/pacientes') for url in calls)
