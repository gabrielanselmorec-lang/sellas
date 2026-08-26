from __future__ import annotations
import datetime as dt
import json
import logging
import threading
from typing import Any, Literal, Optional

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from app.data.models import SessionLocal
from app.security import require_env, sanitize_text
from app.services.knowledge_agent import (
    answer_abc_functional_analysis,
    answer_with_knowledge,
    search as search_knowledge,
    status as knowledge_status,
)
from app.services.abc_closed import (
    add_closed_record,
    build_abc_analysis,
    build_abc_printable_summary,
    combine_local_datetime,
    create_custom_category,
    delete_closed_record,
    generate_patient_excel,
    init_abc_tables,
    latest_patient_excel_path,
    list_categories as list_abc_categories,
    load_observation_metadata,
    load_patient_records as load_abc_patient_records,
    log_abc_report_generation,
    patient_excel_path,
    patient_token_uuid,
    predict_abc_behavior,
    update_closed_record,
)
from app.services.abc_chains import (
    approve_current_chain_candidates,
    build_chain_timeline,
    build_transition_matrix,
    create_transition_rule,
    detect_and_persist_chains,
    get_active_chain_config,
    get_chain_candidate,
    get_chain_features,
    init_abc_chain_tables,
    list_chain_candidates,
    list_chain_stats,
    list_transition_rules,
    review_chain_candidate,
)

app = FastAPI(title="Sellas Project API")

DATABASE_URL = require_env("SELLAS_DATABASE_URL", "DATABASE_URL")
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
_ASSESSMENT_TABLES_READY = False
_CLINICAL_EVOLUTION_COLUMNS_READY = False
_ABC_EXCEL_REFRESH_LOCK = threading.Lock()
_ABC_EXCEL_REFRESH_STATE: dict[str, dict[str, Any]] = {}
logger = logging.getLogger(__name__)


@app.on_event("startup")
def initialize_abc_schema() -> None:
    """Conclui DDL do ABC antes que leituras concorrentes sejam aceitas."""
    init_abc_tables(engine)
    init_abc_chain_tables(engine)

def _clean_query_param(value: str, *, max_length: int) -> str:
    return sanitize_text(value, max_length=max_length, allow_newlines=False)

def _filter_date_range(df: pd.DataFrame, data_inicio: Optional[str], data_fim: Optional[str]) -> pd.DataFrame:
    if df.empty or not data_inicio or not data_fim:
        return df
    inicio = pd.to_datetime(data_inicio, format="%Y-%m-%d", errors="coerce")
    fim = pd.to_datetime(data_fim, format="%Y-%m-%d", errors="coerce")
    if pd.isna(inicio) or pd.isna(fim):
        return df
    df = df.copy()
    df["date_temp"] = pd.to_datetime(df["date"], errors="coerce", dayfirst=True)
    df = df[(df["date_temp"] >= inicio) & (df["date_temp"] <= fim)]
    return df.drop(columns=["date_temp"])


class AssessmentItemPayload(BaseModel):
    codigo: str = Field(..., min_length=1, max_length=20)
    categoria_codigo: str = Field(..., min_length=1, max_length=8)
    categoria: str = Field(..., min_length=1, max_length=200)
    descricao: str = Field(..., min_length=1, max_length=5000)
    max_pontos: int = Field(..., ge=1, le=10)
    aba: Optional[str] = Field(None, max_length=100)
    numero: Optional[int] = Field(None, ge=0, le=10000)
    detalhes: dict[str, Any] = Field(default_factory=dict)


class AssessmentProtocolSyncPayload(BaseModel):
    codigo: str = Field(..., min_length=1, max_length=80)
    nome: str = Field(..., min_length=1, max_length=200)
    itens: list[AssessmentItemPayload]


class AssessmentScorePayload(BaseModel):
    paciente: str = Field(..., min_length=1, max_length=128)
    codigo_item: str = Field(..., min_length=1, max_length=20)
    pontos: Optional[int] = Field(None, ge=0, le=10)
    data_avaliacao: dt.date = Field(default_factory=dt.date.today)
    observacao: Optional[str] = Field(None, max_length=2000)
    fonte: str = Field("manual", max_length=40)


class AssessmentLinkPayload(BaseModel):
    codigo_item: str = Field(..., min_length=1, max_length=20)
    programa_biblioteca: str = Field(..., min_length=1, max_length=200)
    pontos_automaticos: Optional[int] = Field(None, ge=0, le=10)


class ClinicalAgentPayload(BaseModel):
    paciente: str = Field(..., min_length=1, max_length=128)
    pergunta: str = Field(..., min_length=3, max_length=4000)
    data_inicio: Optional[dt.date] = None
    data_fim: Optional[dt.date] = None
    limite_fontes: int = Field(6, ge=1, le=10)


class ABCClosedRecordPayload(BaseModel):
    paciente: str = Field(..., min_length=1, max_length=128)
    antecedente_codigo: str = Field(..., min_length=1, max_length=80)
    comportamento_codigo: str = Field(..., min_length=1, max_length=80)
    consequencia_codigo: str = Field(..., min_length=1, max_length=80)
    data: dt.date = Field(default_factory=dt.date.today)
    hora: dt.time
    ambiente: str = Field(..., min_length=1, max_length=160)
    classificacao: Literal["C1", "C2"]
    causou_lesao: bool = False
    houve_sangramento: bool = False
    direcionado_ponto_vital: bool = False
    funcao: str = Field(..., min_length=1, max_length=120)


class ABCFunctionalAnalysisPayload(BaseModel):
    paciente: str = Field(..., min_length=1, max_length=128)
    cadeia: Optional[str] = Field(None, max_length=700)
    ambiente: Optional[str] = Field(None, max_length=160)
    pergunta: Optional[str] = Field(None, max_length=2000)
    limite_fontes: int = Field(5, ge=1, le=8)


class ABCCustomCategoryPayload(BaseModel):
    paciente: str = Field(..., min_length=1, max_length=128)
    nome: str = Field(..., min_length=1, max_length=200)
    tipo: Literal["antecedente", "comportamento", "consequencia"]


class ABCChainDetectPayload(BaseModel):
    paciente: str = Field(..., min_length=1, max_length=128)
    max_lag_seconds: Optional[int] = Field(None, ge=0, le=86400)
    min_confidence: Optional[float] = Field(None, ge=0, le=1)
    allow_cross_session_chain: Optional[bool] = None
    break_on_not_observed: Optional[bool] = None
    chain_min_repetitions: Optional[int] = Field(None, ge=1, le=1000)
    landmark_ts: Optional[dt.datetime] = None


class ABCChainReviewPayload(BaseModel):
    status: Literal["accepted", "rejected"]
    revisado_por: str = Field(..., min_length=2, max_length=160)
    observacao: Optional[str] = Field(None, max_length=2000)


class ABCTransitionRulePayload(BaseModel):
    from_consequence_code: str = Field(..., min_length=1, max_length=80)
    to_antecedent_code: str = Field(..., min_length=1, max_length=80)
    relation_type: Literal["exact", "mapped", "clinical_review"] = "mapped"
    active: bool = True
    rule_version: str = Field("1", min_length=1, max_length=30)
    rationale: Optional[str] = Field(None, max_length=2000)


class ABCApproveCurrentChainsPayload(BaseModel):
    paciente: str = Field(..., min_length=1, max_length=128)
    revisado_por: str = Field(..., min_length=2, max_length=160)
    justificativa: str = Field(..., min_length=5, max_length=2000)


def init_assessment_tables() -> None:
    """Fallback de segurança — as tabelas agora são gerenciadas pela migration
    alembic/versions/a1b2c3d4e5f6_assessment_tables.py.
    Esta função pode ser removida após confirmar que `alembic upgrade head` foi executado."""
    global _ASSESSMENT_TABLES_READY
    if _ASSESSMENT_TABLES_READY:
        return

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS assessment_protocols (
                    id SERIAL PRIMARY KEY,
                    code VARCHAR(80) UNIQUE NOT NULL,
                    name VARCHAR(200) NOT NULL,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                CREATE TABLE IF NOT EXISTS assessment_items (
                    id SERIAL PRIMARY KEY,
                    protocol_id INTEGER NOT NULL REFERENCES assessment_protocols(id) ON DELETE CASCADE,
                    item_code VARCHAR(20) NOT NULL,
                    source_sheet VARCHAR(100),
                    category_code VARCHAR(8) NOT NULL,
                    category_name VARCHAR(200) NOT NULL,
                    item_number INTEGER,
                    description TEXT NOT NULL,
                    max_points INTEGER NOT NULL CHECK (max_points BETWEEN 1 AND 10),
                    details JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE(protocol_id, item_code)
                );

                CREATE TABLE IF NOT EXISTS assessment_scores (
                    id SERIAL PRIMARY KEY,
                    patient_id VARCHAR NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
                    item_id INTEGER NOT NULL REFERENCES assessment_items(id) ON DELETE CASCADE,
                    assessment_date DATE NOT NULL DEFAULT CURRENT_DATE,
                    points INTEGER CHECK (points >= 0),
                    source VARCHAR(40) NOT NULL DEFAULT 'manual',
                    notes TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE(patient_id, item_id, assessment_date)
                );

                CREATE TABLE IF NOT EXISTS assessment_item_program_links (
                    id SERIAL PRIMARY KEY,
                    item_id INTEGER NOT NULL REFERENCES assessment_items(id) ON DELETE CASCADE,
                    program_library_name VARCHAR(200) NOT NULL,
                    auto_points INTEGER CHECK (auto_points IS NULL OR auto_points >= 0),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE(item_id, program_library_name)
                );

                CREATE INDEX IF NOT EXISTS idx_assessment_scores_patient_date
                    ON assessment_scores(patient_id, assessment_date);
                CREATE INDEX IF NOT EXISTS idx_assessment_items_protocol_category
                    ON assessment_items(protocol_id, category_code, item_number);
                """
            )
        )
    _ASSESSMENT_TABLES_READY = True


def ensure_clinical_evolution_columns() -> None:
    """Fallback de segurança — a coluna `evolution` agora é parte da migration inicial
    563592740d7d. Esta função pode ser removida após confirmar que `alembic upgrade head` foi executado."""
    global _CLINICAL_EVOLUTION_COLUMNS_READY
    if _CLINICAL_EVOLUTION_COLUMNS_READY:
        return
    with engine.begin() as conn:
        for ddl in [
            "ALTER TABLE program_records ADD COLUMN IF NOT EXISTS evolution TEXT",
            "ALTER TABLE program_target_records ADD COLUMN IF NOT EXISTS evolution TEXT",
            "ALTER TABLE interfering_records ADD COLUMN IF NOT EXISTS evolution TEXT",
        ]:
            conn.execute(text(ddl))
    _CLINICAL_EVOLUTION_COLUMNS_READY = True


@app.on_event("startup")
def _startup_assessment_tables() -> None:
    init_assessment_tables()
    ensure_clinical_evolution_columns()
    init_abc_tables(engine)


def _protocol_id(conn, protocolo: str) -> int:
    protocolo_limpo = _clean_query_param(protocolo, max_length=80)
    row = conn.execute(
        text("SELECT id FROM assessment_protocols WHERE code = :code AND active = TRUE"),
        {"code": protocolo_limpo},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Protocolo avaliativo nao encontrado.")
    return int(row["id"])


def _patient_id(conn, paciente: str) -> str:
    paciente_limpo = _clean_query_param(paciente, max_length=128)
    row = conn.execute(
        text("SELECT id FROM patients WHERE name_hash = :paciente OR name = :paciente"),
        {"paciente": paciente_limpo},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Paciente nao encontrado.")
    return str(row["id"])


def _refresh_abc_excel(*, patient_token: str, patient_name: str) -> dict[str, Any]:
    try:
        path = generate_patient_excel(
            engine,
            patient_token=patient_token,
            patient_name=patient_name,
        )
        return {"excel_path": str(path), "excel_warning": None}
    except Exception as exc:
        detail = sanitize_text(exc, max_length=400)
        return {
            "excel_path": None,
            "excel_warning": (
                "O registro foi salvo no banco, mas o Excel não pôde ser atualizado agora: "
                f"{detail}"
            ),
        }


def _run_queued_abc_excel_refresh(patient_token: str) -> None:
    """Gera a planilha fora da resposta HTTP e consolida alteracoes concorrentes."""
    while True:
        with _ABC_EXCEL_REFRESH_LOCK:
            state = _ABC_EXCEL_REFRESH_STATE.get(patient_token)
            if not state:
                return
            patient_name = str(state["patient_name"])
            state["dirty"] = False

        result = _refresh_abc_excel(patient_token=patient_token, patient_name=patient_name)
        if result.get("excel_warning"):
            logger.warning("Falha ao atualizar Excel ABC em segundo plano: %s", result["excel_warning"])

        with _ABC_EXCEL_REFRESH_LOCK:
            state = _ABC_EXCEL_REFRESH_STATE.get(patient_token)
            if state and state.get("dirty"):
                continue
            _ABC_EXCEL_REFRESH_STATE.pop(patient_token, None)
            return


def _queue_abc_excel_refresh(
    background_tasks: BackgroundTasks,
    *,
    patient_token: str,
    patient_name: str,
) -> dict[str, Any]:
    current_path = latest_patient_excel_path(patient_name)
    should_start = False
    with _ABC_EXCEL_REFRESH_LOCK:
        state = _ABC_EXCEL_REFRESH_STATE.get(patient_token)
        if state:
            state["dirty"] = True
            state["patient_name"] = patient_name
        else:
            _ABC_EXCEL_REFRESH_STATE[patient_token] = {
                "patient_name": patient_name,
                "dirty": False,
            }
            should_start = True
    if should_start:
        background_tasks.add_task(_run_queued_abc_excel_refresh, patient_token)
    return {
        "excel_path": str(current_path) if current_path else None,
        "excel_warning": None,
        "excel_status": "queued",
    }


def _row_dict(row) -> dict[str, Any]:
    data = dict(row)
    for key, value in list(data.items()):
        if isinstance(value, (dt.date, dt.datetime)):
            data[key] = value.isoformat()
    return data


@app.get("/api/abc/categorias")
def listar_categorias_abc() -> list[dict[str, Any]]:
    try:
        return list_abc_categories(engine)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao carregar categorias ABC: {sanitize_text(exc, max_length=240)}") from exc


@app.post("/api/abc/categorias")
def adicionar_categoria_abc(payload: ABCCustomCategoryPayload) -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            patient_id = _patient_id(conn, payload.paciente)
        token = patient_token_uuid(patient_id)
        item = create_custom_category(
            engine,
            name=_clean_query_param(payload.nome, max_length=200),
            category_type=payload.tipo,
            patient_token=token,
        )
        return item
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao criar categoria ABC: {sanitize_text(exc, max_length=400)}") from exc


@app.post("/api/abc/registros")
def adicionar_registro_abc(payload: ABCClosedRecordPayload, background_tasks: BackgroundTasks) -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            patient_id = _patient_id(conn, payload.paciente)
        data_hora = combine_local_datetime(payload.data, payload.hora)
        token = patient_token_uuid(patient_id)
        result = add_closed_record(
            engine,
            patient_token=token,
            antecedente_codigo=_clean_query_param(payload.antecedente_codigo, max_length=80),
            comportamento_codigo=_clean_query_param(payload.comportamento_codigo, max_length=80),
            consequencia_codigo=_clean_query_param(payload.consequencia_codigo, max_length=80),
            data_hora=data_hora,
            ambiente=_clean_query_param(payload.ambiente, max_length=160),
            classificacao=payload.classificacao,
            causou_lesao=payload.causou_lesao,
            houve_sangramento=payload.houve_sangramento,
            direcionado_ponto_vital=payload.direcionado_ponto_vital,
            funcao=_clean_query_param(payload.funcao, max_length=120),
        )
        return {
            **result,
            **_queue_abc_excel_refresh(
                background_tasks,
                patient_token=token,
                patient_name=payload.paciente,
            ),
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar registro ABC: {sanitize_text(exc, max_length=240)}") from exc


@app.patch("/api/abc/registros/{interval_id}")
def editar_registro_abc(
    interval_id: str,
    payload: ABCClosedRecordPayload,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            patient_id = _patient_id(conn, payload.paciente)
        token = patient_token_uuid(patient_id)
        result = update_closed_record(
            engine,
            patient_token=token,
            interval_id=interval_id,
            antecedente_codigo=_clean_query_param(payload.antecedente_codigo, max_length=80),
            comportamento_codigo=_clean_query_param(payload.comportamento_codigo, max_length=80),
            consequencia_codigo=_clean_query_param(payload.consequencia_codigo, max_length=80),
            data_hora=combine_local_datetime(payload.data, payload.hora),
            ambiente=_clean_query_param(payload.ambiente, max_length=160),
            classificacao=payload.classificacao,
            causou_lesao=payload.causou_lesao,
            houve_sangramento=payload.houve_sangramento,
            direcionado_ponto_vital=payload.direcionado_ponto_vital,
            funcao=_clean_query_param(payload.funcao, max_length=120),
        )
        return {
            **result,
            **_queue_abc_excel_refresh(
                background_tasks,
                patient_token=token,
                patient_name=payload.paciente,
            ),
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao editar registro ABC: {sanitize_text(exc, max_length=400)}") from exc


@app.delete("/api/abc/registros/{interval_id}")
def remover_registro_abc(
    interval_id: str,
    background_tasks: BackgroundTasks,
    paciente: str = Query(..., min_length=1, max_length=128),
) -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            patient_id = _patient_id(conn, paciente)
        token = patient_token_uuid(patient_id)
        result = delete_closed_record(engine, patient_token=token, interval_id=interval_id)
        return {
            **result,
            **_queue_abc_excel_refresh(
                background_tasks,
                patient_token=token,
                patient_name=paciente,
            ),
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao remover registro ABC: {sanitize_text(exc, max_length=400)}") from exc


@app.get("/api/abc/analise")
def obter_analise_abc(
    paciente: str = Query(..., min_length=1, max_length=128),
    ambiente: Optional[str] = Query(None, min_length=1, max_length=160),
) -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            patient_id = _patient_id(conn, paciente)
        records = load_abc_patient_records(engine, patient_token_uuid(patient_id))
        if ambiente:
            ambiente_limpo = _clean_query_param(ambiente, max_length=160)
            records = [record for record in records if record.get("ambiente") == ambiente_limpo]
        analysis = build_abc_analysis(records)
        return {
            **analysis,
            "filtro_ambiente": ambiente,
            "excel_path": str(latest_patient_excel_path(paciente) or patient_excel_path(paciente)),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao analisar registros ABC: {sanitize_text(exc, max_length=240)}") from exc


@app.get("/api/abc/reports/summary")
def obter_resumo_imprimivel_abc(
    paciente: str = Query(..., min_length=1, max_length=128),
    data_inicio: Optional[dt.date] = Query(None),
    data_fim: Optional[dt.date] = Query(None),
    ambiente: Optional[str] = Query(None, min_length=1, max_length=160),
    incluir_finais_semana: bool = Query(True),
    incluir_cadeias_candidatas: bool = Query(True),
    apenas_cadeias_revisadas: bool = Query(False),
    incluir_graficos: bool = Query(True),
    anonimizar_paciente: bool = Query(False),
    gerado_por: str = Query("Usuário local", min_length=2, max_length=160),
    formato: Literal["preview", "pdf", "docx"] = Query("preview"),
) -> dict[str, Any]:
    """Retorna um único contrato para prévia e impressão, com auditoria de acesso."""
    if data_inicio and data_fim and data_inicio > data_fim:
        raise HTTPException(status_code=400, detail="A data inicial não pode ser posterior à data final.")
    try:
        with engine.connect() as conn:
            patient_id = _patient_id(conn, paciente)
        token = patient_token_uuid(patient_id)
        clean_environment = _clean_query_param(ambiente, max_length=160) if ambiente else None
        intervals = load_observation_metadata(
            engine,
            patient_token=token,
            start_date=data_inicio,
            end_date=data_fim,
            include_weekends=incluir_finais_semana,
            environment=clean_environment,
        )
        interval_ids = {str(item.get("intervalo_id")) for item in intervals}
        records = [
            item
            for item in load_abc_patient_records(engine, token)
            if str(item.get("intervalo_id")) in interval_ids
        ]
        interval_session = {
            str(item.get("intervalo_id")): str(item.get("session_id"))
            for item in intervals
            if item.get("intervalo_id") and item.get("session_id")
        }
        candidates = []
        for item in list_chain_candidates(engine, patient_token=token):
            candidates.append(
                {
                    **item,
                    "from_session_id": interval_session.get(str(item.get("from_interval_id"))),
                    "to_session_id": interval_session.get(str(item.get("to_interval_id"))),
                }
            )
        generated_by = _clean_query_param(gerado_por, max_length=160)
        summary = build_abc_printable_summary(
            patient_name=paciente,
            records=records,
            intervals=intervals,
            temporal_candidates=candidates,
            anonymize_patient=anonimizar_paciente,
            include_candidate_chains=incluir_cadeias_candidatas,
            reviewed_chains_only=apenas_cadeias_revisadas,
            generated_by=generated_by,
        )
        summary["report_metadata"].update(
            {
                "period_start": data_inicio.isoformat() if data_inicio else None,
                "period_end": data_fim.isoformat() if data_fim else None,
                "environment": clean_environment or "Todos os ambientes",
                "include_weekends": incluir_finais_semana,
                "include_charts": incluir_graficos,
                "include_candidate_chains": incluir_cadeias_candidatas,
                "reviewed_chains_only": apenas_cadeias_revisadas,
                "format": formato,
            }
        )
        log_abc_report_generation(
            engine,
            patient_token=token,
            generated_by=generated_by,
            filters={
                "data_inicio": data_inicio.isoformat() if data_inicio else None,
                "data_fim": data_fim.isoformat() if data_fim else None,
                "ambiente": clean_environment,
                "incluir_finais_semana": incluir_finais_semana,
                "incluir_cadeias_candidatas": incluir_cadeias_candidatas,
                "apenas_cadeias_revisadas": apenas_cadeias_revisadas,
                "incluir_graficos": incluir_graficos,
            },
            output_format=formato,
            anonymized=anonimizar_paciente,
        )
        return summary
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao montar resumo ABC: {sanitize_text(exc, max_length=300)}",
        ) from exc


@app.post("/api/abc/analise-funcional-ia")
def analisar_funcao_abc_com_ia(payload: ABCFunctionalAnalysisPayload) -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            patient_id = _patient_id(conn, payload.paciente)
        records = load_abc_patient_records(engine, patient_token_uuid(patient_id))
        if not records:
            raise HTTPException(status_code=404, detail="Paciente sem registros ABC para análise.")

        analysis = build_abc_analysis(records)
        chain = _clean_query_param(payload.cadeia, max_length=700) if payload.cadeia else None
        environment = _clean_query_param(payload.ambiente, max_length=160) if payload.ambiente else None
        if chain and chain not in {item.get("cadeia") for item in analysis.get("cadeias_completas", [])}:
            raise HTTPException(status_code=400, detail="A cadeia selecionada não pertence aos registros do paciente.")
        if environment and environment != "Todos os ambientes" and environment not in analysis.get("ambientes_observados", []):
            raise HTTPException(status_code=400, detail="O ambiente selecionado não pertence aos registros do paciente.")

        return answer_abc_functional_analysis(
            analysis=analysis,
            selected_chain=chain,
            selected_environment=environment,
            question=payload.pergunta or "",
            limit=payload.limite_fontes,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erro na análise funcional assistida por IA: {sanitize_text(exc, max_length=300)}",
        ) from exc


@app.post("/api/abc/chains/detect")
def detectar_cadeias_abc(payload: ABCChainDetectPayload) -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            patient_id = _patient_id(conn, payload.paciente)
        overrides = {
            key: value
            for key, value in payload.model_dump(
                exclude={"paciente", "landmark_ts"},
                exclude_none=True,
            ).items()
        }
        return detect_and_persist_chains(
            engine,
            patient_token=patient_token_uuid(patient_id),
            overrides=overrides,
            landmark_ts=payload.landmark_ts,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao detectar cadeias ABC: {sanitize_text(exc, max_length=400)}") from exc


@app.post("/api/abc/chains/approve-current")
def aprovar_cadeias_abc_atuais(payload: ABCApproveCurrentChainsPayload) -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            patient_id = _patient_id(conn, payload.paciente)
        return approve_current_chain_candidates(
            engine,
            patient_token=patient_token_uuid(patient_id),
            reviewed_by=_clean_query_param(payload.revisado_por, max_length=160),
            rationale=_clean_query_param(payload.justificativa, max_length=2000),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao aprovar cadeias atuais: {sanitize_text(exc, max_length=400)}") from exc


@app.get("/api/abc/chains/candidates")
def listar_candidatos_cadeia_abc(
    paciente: str = Query(..., min_length=1, max_length=128),
    status: Optional[Literal["candidate", "accepted", "rejected", "censored"]] = Query(None),
) -> list[dict[str, Any]]:
    try:
        with engine.connect() as conn:
            patient_id = _patient_id(conn, paciente)
        return list_chain_candidates(engine, patient_token=patient_token_uuid(patient_id), status=status)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao listar cadeias ABC: {sanitize_text(exc, max_length=300)}") from exc


@app.get("/api/abc/chains/stats")
def obter_estatisticas_cadeia_abc(
    paciente: str = Query(..., min_length=1, max_length=128),
) -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            patient_id = _patient_id(conn, paciente)
        return {
            "stats": list_chain_stats(engine, patient_token=patient_token_uuid(patient_id)),
            "interpretation": "Padroes temporais descritivos; nao confirmam causa ou funcao comportamental.",
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro nas estatisticas de cadeia: {sanitize_text(exc, max_length=300)}") from exc


@app.get("/api/abc/chains/transition-matrix")
def obter_matriz_transicao_abc(
    paciente: str = Query(..., min_length=1, max_length=128),
) -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            patient_id = _patient_id(conn, paciente)
        return {"matrix": build_transition_matrix(engine, patient_token=patient_token_uuid(patient_id))}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro na matriz de transicao: {sanitize_text(exc, max_length=300)}") from exc


@app.get("/api/abc/chains/timeline")
def obter_timeline_cadeia_abc(
    paciente: str = Query(..., min_length=1, max_length=128),
) -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            patient_id = _patient_id(conn, paciente)
        return {"timeline": build_chain_timeline(engine, patient_token=patient_token_uuid(patient_id))}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro na timeline ABC: {sanitize_text(exc, max_length=300)}") from exc


@app.get("/api/abc/chains/{candidate_id}")
def obter_candidato_cadeia_abc(candidate_id: str) -> dict[str, Any]:
    try:
        item = get_chain_candidate(engine, candidate_id)
        if not item:
            raise HTTPException(status_code=404, detail="Candidato de cadeia nao encontrado.")
        return item
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao carregar candidato: {sanitize_text(exc, max_length=300)}") from exc


@app.post("/api/abc/chains/{candidate_id}/review")
def revisar_candidato_cadeia_abc(candidate_id: str, payload: ABCChainReviewPayload) -> dict[str, Any]:
    try:
        return review_chain_candidate(
            engine,
            candidate_id=candidate_id,
            status=payload.status,
            reviewed_by=_clean_query_param(payload.revisado_por, max_length=160),
            note=_clean_query_param(payload.observacao, max_length=2000) if payload.observacao else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao revisar candidato: {sanitize_text(exc, max_length=300)}") from exc


@app.get("/api/abc/config/chain-rules")
def obter_regras_cadeia_abc() -> dict[str, Any]:
    try:
        return {
            "active_config": get_active_chain_config(engine),
            "rules": list_transition_rules(engine),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao carregar regras de cadeia: {sanitize_text(exc, max_length=300)}") from exc


@app.post("/api/abc/config/chain-rules")
def adicionar_regra_cadeia_abc(payload: ABCTransitionRulePayload) -> dict[str, Any]:
    try:
        return create_transition_rule(
            engine,
            from_consequence_code=_clean_query_param(payload.from_consequence_code, max_length=80),
            to_antecedent_code=_clean_query_param(payload.to_antecedent_code, max_length=80),
            relation_type=payload.relation_type,
            rule_version=_clean_query_param(payload.rule_version, max_length=30),
            rationale=_clean_query_param(payload.rationale, max_length=2000) if payload.rationale else None,
            active=payload.active,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar regra de cadeia: {sanitize_text(exc, max_length=300)}") from exc


@app.get("/api/ml/features/chains")
def obter_features_cadeias_abc(
    paciente: str = Query(..., min_length=1, max_length=128),
    landmark_ts: dt.datetime = Query(...),
    session_id: Optional[str] = Query(None, max_length=120),
    ambiente: Optional[str] = Query(None, max_length=160),
) -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            patient_id = _patient_id(conn, paciente)
        return get_chain_features(
            engine,
            patient_token=patient_token_uuid(patient_id),
            landmark_ts=landmark_ts,
            session_id=session_id,
            environment=_clean_query_param(ambiente, max_length=160) if ambiente else None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro nas features temporais: {sanitize_text(exc, max_length=300)}") from exc


@app.get("/api/abc/estimativa-descritiva")
@app.get("/api/abc/previsao", deprecated=True)
def prever_comportamento_abc(
    paciente: str = Query(..., min_length=1, max_length=128),
    comportamento: str = Query(..., min_length=1, max_length=200),
    antecedente: Optional[str] = Query(None, min_length=1, max_length=200),
    ambiente: Optional[str] = Query(None, min_length=1, max_length=160),
    classificacao: Optional[Literal["C1", "C2"]] = Query(None),
    funcao: Optional[str] = Query(None, min_length=1, max_length=120),
) -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            patient_id = _patient_id(conn, paciente)
        records = load_abc_patient_records(engine, patient_token_uuid(patient_id))
        return predict_abc_behavior(
            records,
            behavior=_clean_query_param(comportamento, max_length=200),
            antecedent=_clean_query_param(antecedente, max_length=200) if antecedente else None,
            environment=_clean_query_param(ambiente, max_length=160) if ambiente else None,
            classification=classificacao,
            function=_clean_query_param(funcao, max_length=120) if funcao else None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao calcular estimativa descritiva ABC: {sanitize_text(exc, max_length=300)}") from exc


@app.get("/api/abc/excel")
def baixar_excel_abc(
    paciente: str = Query(..., min_length=1, max_length=128),
) -> FileResponse:
    try:
        with engine.connect() as conn:
            patient_id = _patient_id(conn, paciente)
        token = patient_token_uuid(patient_id)
        path = latest_patient_excel_path(paciente)
        if path is None:
            path = generate_patient_excel(engine, patient_token=token, patient_name=paciente)
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=path.name,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar Excel ABC: {sanitize_text(exc, max_length=400)}") from exc


def _ordered_program_records_sql() -> str:
    return """
        SELECT date, success_rate
        FROM program_records
        WHERE program_id = :program_id
        ORDER BY
            CASE
                WHEN date ~ '^\\d{4}-\\d{2}-\\d{2}$' THEN to_date(date, 'YYYY-MM-DD')
                WHEN date ~ '^\\d{2}/\\d{2}/\\d{4}$' THEN to_date(date, 'DD/MM/YYYY')
                ELSE NULL
            END DESC NULLS LAST,
            id DESC
        LIMIT :limit
    """

@app.get("/api/pacientes")
def listar_pacientes():
    try:
        df = pd.read_sql(text("SELECT name_hash, name FROM patients"), engine)
        lista_pacientes = []
        for _, row in df.iterrows():
            if pd.notna(row.get('name')) and str(row['name']).strip():
                lista_pacientes.append(str(row['name']))
            else:
                lista_pacientes.append(str(row['name_hash']))
        return lista_pacientes
    except Exception as exc:
        print(f"Erro ao listar pacientes: {sanitize_text(exc, max_length=200)}")
        return []

@app.get("/api/dados_paciente")
def obter_dados_paciente(
    paciente: str = Query(..., min_length=1, max_length=128),
    data_inicio: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    data_fim: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    try:
        ensure_clinical_evolution_columns()
        paciente_id = _clean_query_param(paciente, max_length=128)
        q_prog = text(
            """
            SELECT p.name as programa, p.objective, pr.date, pr.success_rate,
                   pr.independent_rate, pr.prompt_rate, pr.phase, pr.evolution
            FROM program_records pr
            JOIN programs p ON pr.program_id = p.id
            JOIN patients pat ON p.patient_id = pat.id
            WHERE pat.name_hash = :paciente OR pat.name = :paciente
            """
        )
        q_beh = text("""
            SELECT b.name as comportamento, ir.date, ir.count, ir.rate, ir.evolution
            FROM interfering_records ir
            JOIN interfering_behaviors b ON ir.behavior_id = b.id
            JOIN patients pat ON b.patient_id = pat.id
            WHERE pat.name_hash = :paciente OR pat.name = :paciente
        """)
        q_targets = text("""
            SELECT p.name as programa, p.objective, tr.target_name, tr.attempts,
                   tr.independent_rate, tr.prompt_rate, tr.success_rate, tr.prompt_type,
                   tr.date, tr.evolution, phase_by_date.phase
            FROM program_target_records tr
            JOIN programs p ON tr.program_id = p.id
            JOIN patients pat ON p.patient_id = pat.id
            LEFT JOIN (
                SELECT program_id, date, MAX(phase) as phase
                FROM program_records
                GROUP BY program_id, date
            ) phase_by_date ON phase_by_date.program_id = tr.program_id
                AND phase_by_date.date = tr.date
            WHERE pat.name_hash = :paciente OR pat.name = :paciente
        """)
        params = {"paciente": paciente_id}
        df_p = pd.read_sql(q_prog, engine, params=params).fillna(0)
        df_b = pd.read_sql(q_beh, engine, params=params).fillna(0)
        df_t = pd.read_sql(q_targets, engine, params=params).fillna(0)
        df_p = _filter_date_range(df_p, data_inicio, data_fim)
        df_b = _filter_date_range(df_b, data_inicio, data_fim)
        df_t = _filter_date_range(df_t, data_inicio, data_fim)
        for df in (df_p, df_b, df_t):
            if "evolution" in df.columns:
                df["evolution"] = df["evolution"].replace(0, "").fillna("").astype(str)
        return {
            "programas": df_p.to_dict(orient="records"),
            "interferentes": df_b.to_dict(orient="records"),
            "alvos": df_t.to_dict(orient="records"),
        }
    except Exception as exc:
        print(f"Erro ao buscar dados do paciente: {sanitize_text(exc, max_length=200)}")
        return {"programas": [], "interferentes": [], "alvos": []}

@app.get("/api/alvos")
def obter_alvos(
    paciente: str = Query(..., min_length=1, max_length=128),
    programa: str = Query(..., min_length=1, max_length=200),
):
    try:
        ensure_clinical_evolution_columns()
        paciente_id = _clean_query_param(paciente, max_length=128)
        programa_nome = _clean_query_param(programa, max_length=200)
        query = text("""
            SELECT tr.*
            FROM program_target_records tr
            JOIN programs p ON tr.program_id = p.id
            JOIN patients pat ON p.patient_id = pat.id
            WHERE (pat.name_hash = :paciente OR pat.name = :paciente) AND p.name = :programa
        """)
        df = pd.read_sql(query, engine, params={"paciente": paciente_id, "programa": programa_nome})
        return df.fillna(0).to_dict(orient="records")
    except Exception as exc:
        print(f"Erro ao buscar alvos do programa: {sanitize_text(exc, max_length=200)}")
        return []

@app.get("/api/biblioteca")
def get_biblioteca():
    session = SessionLocal()
    try:
        q = text("SELECT name, objective_template, mastery_threshold_percent, mastery_days, suggested_targets FROM program_library")
        result = session.execute(q).fetchall()
        biblioteca = []
        for row in result:
            biblioteca.append({
                "name": sanitize_text(row[0], max_length=200),
                "objective_template": sanitize_text(row[1], max_length=4000, allow_newlines=True),
                "mastery_threshold_percent": float(row[2]) if row[2] else 90,
                "mastery_days": row[3] if row[3] else 3,
                "suggested_targets": row[4],
            })
        return biblioteca
    except Exception as exc:
        return {"erro": sanitize_text(exc, max_length=200)}
    finally:
        session.close()


@app.get("/api/conhecimento/status")
def obter_status_conhecimento():
    try:
        return knowledge_status()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao carregar base de conhecimento: {sanitize_text(exc, max_length=200)}",
        )


@app.get("/api/conhecimento/buscar")
def buscar_conhecimento(
    q: str = Query(..., min_length=3, max_length=4000),
    limite: int = Query(6, ge=1, le=10),
):
    try:
        return search_knowledge(q, limit=limite)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao buscar na base de conhecimento: {sanitize_text(exc, max_length=200)}",
        )


@app.post("/api/agente/clinico")
def consultar_agente_clinico(payload: ClinicalAgentPayload):
    data_inicio = payload.data_inicio.isoformat() if payload.data_inicio else None
    data_fim = payload.data_fim.isoformat() if payload.data_fim else None
    contexto_paciente = obter_dados_paciente(
        paciente=payload.paciente,
        data_inicio=data_inicio,
        data_fim=data_fim,
    )
    return answer_with_knowledge(
        question=payload.pergunta,
        patient_context=contexto_paciente,
        limit=payload.limite_fontes,
    )


@app.get("/api/avaliacoes")
def listar_avaliacoes():
    init_assessment_tables()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT p.code, p.name, COUNT(i.id) AS total_itens
                FROM assessment_protocols p
                LEFT JOIN assessment_items i ON i.protocol_id = p.id
                WHERE p.active = TRUE
                GROUP BY p.id, p.code, p.name
                ORDER BY p.name
                """
            )
        ).mappings().all()
    return [_row_dict(row) for row in rows]


@app.post("/api/avaliacoes/sincronizar")
def sincronizar_protocolo_avaliativo(payload: AssessmentProtocolSyncPayload):
    init_assessment_tables()
    codigo = _clean_query_param(payload.codigo, max_length=80)
    nome = _clean_query_param(payload.nome, max_length=200)

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO assessment_protocols (code, name, updated_at)
                VALUES (:code, :name, now())
                ON CONFLICT (code) DO UPDATE
                SET name = EXCLUDED.name, active = TRUE, updated_at = now()
                RETURNING id
                """
            ),
            {"code": codigo, "name": nome},
        ).mappings().first()
        protocol_id = int(row["id"])

        for item in payload.itens:
            item_code = _clean_query_param(item.codigo, max_length=20)
            categoria_codigo = _clean_query_param(item.categoria_codigo, max_length=8)
            categoria = _clean_query_param(item.categoria, max_length=200)
            descricao = sanitize_text(item.descricao, max_length=5000, allow_newlines=True)
            aba = sanitize_text(item.aba or "", max_length=100, allow_newlines=False) or None
            detalhes_json = json.dumps(item.detalhes or {}, ensure_ascii=True)

            conn.execute(
                text(
                    """
                    INSERT INTO assessment_items (
                        protocol_id, item_code, source_sheet, category_code,
                        category_name, item_number, description, max_points,
                        details, updated_at
                    )
                    VALUES (
                        :protocol_id, :item_code, :source_sheet, :category_code,
                        :category_name, :item_number, :description, :max_points,
                        CAST(:details AS jsonb), now()
                    )
                    ON CONFLICT (protocol_id, item_code) DO UPDATE
                    SET source_sheet = EXCLUDED.source_sheet,
                        category_code = EXCLUDED.category_code,
                        category_name = EXCLUDED.category_name,
                        item_number = EXCLUDED.item_number,
                        description = EXCLUDED.description,
                        max_points = EXCLUDED.max_points,
                        details = EXCLUDED.details,
                        updated_at = now()
                    """
                ),
                {
                    "protocol_id": protocol_id,
                    "item_code": item_code,
                    "source_sheet": aba,
                    "category_code": categoria_codigo,
                    "category_name": categoria,
                    "item_number": item.numero,
                    "description": descricao,
                    "max_points": item.max_pontos,
                    "details": detalhes_json,
                },
            )

    return {"ok": True, "protocolo": codigo, "itens": len(payload.itens)}


@app.get("/api/avaliacoes/{protocolo}/itens")
def listar_itens_avaliacao(protocolo: str):
    init_assessment_tables()
    with engine.begin() as conn:
        protocol_id = _protocol_id(conn, protocolo)
        rows = conn.execute(
            text(
                """
                SELECT item_code AS codigo, source_sheet AS aba,
                       category_code AS categoria_codigo, category_name AS categoria,
                       item_number AS numero, description AS descricao,
                       max_points AS max_pontos, details AS detalhes
                FROM assessment_items
                WHERE protocol_id = :protocol_id
                ORDER BY category_code, item_number, item_code
                """
            ),
            {"protocol_id": protocol_id},
        ).mappings().all()
    return [_row_dict(row) for row in rows]


@app.get("/api/avaliacoes/{protocolo}/resultados")
def obter_resultados_avaliacao(
    protocolo: str,
    paciente: str = Query(..., min_length=1, max_length=128),
    data_avaliacao: Optional[dt.date] = Query(None),
):
    init_assessment_tables()
    data_ref = data_avaliacao or dt.date.today()
    with engine.begin() as conn:
        protocol_id = _protocol_id(conn, protocolo)
        patient_id = _patient_id(conn, paciente)
        rows = conn.execute(
            text(
                """
                SELECT i.item_code AS codigo, i.source_sheet AS aba,
                       i.category_code AS categoria_codigo, i.category_name AS categoria,
                       i.item_number AS numero, i.description AS descricao,
                       i.max_points AS max_pontos, i.details AS detalhes,
                       s.points AS pontos, s.source AS fonte, s.notes AS observacao,
                       s.updated_at,
                       EXISTS (
                           SELECT 1
                           FROM assessment_scores prev
                           WHERE prev.patient_id = :patient_id
                             AND prev.item_id = i.id
                             AND prev.assessment_date < :assessment_date
                       ) AS reavaliacao,
                       (
                           SELECT prev.points
                           FROM assessment_scores prev
                           WHERE prev.patient_id = :patient_id
                             AND prev.item_id = i.id
                             AND prev.assessment_date < :assessment_date
                           ORDER BY prev.assessment_date DESC, prev.updated_at DESC
                           LIMIT 1
                       ) AS pontos_anteriores
                FROM assessment_items i
                LEFT JOIN assessment_scores s
                  ON s.item_id = i.id
                 AND s.patient_id = :patient_id
                 AND s.assessment_date = :assessment_date
                WHERE i.protocol_id = :protocol_id
                ORDER BY i.category_code, i.item_number, i.item_code
                """
            ),
            {
                "protocol_id": protocol_id,
                "patient_id": patient_id,
                "assessment_date": data_ref,
            },
        ).mappings().all()
    return [_row_dict(row) for row in rows]


@app.post("/api/avaliacoes/{protocolo}/pontuar")
def salvar_pontuacao_avaliacao(protocolo: str, payload: AssessmentScorePayload):
    init_assessment_tables()
    with engine.begin() as conn:
        protocol_id = _protocol_id(conn, protocolo)
        patient_id = _patient_id(conn, payload.paciente)
        item = conn.execute(
            text(
                """
                SELECT id, max_points
                FROM assessment_items
                WHERE protocol_id = :protocol_id AND item_code = :item_code
                """
            ),
            {
                "protocol_id": protocol_id,
                "item_code": _clean_query_param(payload.codigo_item, max_length=20),
            },
        ).mappings().first()
        if not item:
            raise HTTPException(status_code=404, detail="Item avaliativo nao encontrado.")

        item_id = int(item["id"])
        max_points = int(item["max_points"])
        if payload.pontos is None:
            conn.execute(
                text(
                    """
                    DELETE FROM assessment_scores
                    WHERE patient_id = :patient_id
                      AND item_id = :item_id
                      AND assessment_date = :assessment_date
                    """
                ),
                {
                    "patient_id": patient_id,
                    "item_id": item_id,
                    "assessment_date": payload.data_avaliacao,
                },
            )
            return {"ok": True, "removido": True}

        if payload.pontos > max_points:
            raise HTTPException(status_code=400, detail=f"Este item aceita no maximo {max_points} ponto(s).")

        source = _clean_query_param(payload.fonte or "manual", max_length=40)
        if source not in {"manual", "auto_bhave"}:
            source = "manual"
        observacao = sanitize_text(payload.observacao or "", max_length=2000, allow_newlines=True) or None
        conn.execute(
            text(
                """
                INSERT INTO assessment_scores (
                    patient_id, item_id, assessment_date, points, source, notes, updated_at
                )
                VALUES (:patient_id, :item_id, :assessment_date, :points, :source, :notes, now())
                ON CONFLICT (patient_id, item_id, assessment_date) DO UPDATE
                SET points = EXCLUDED.points,
                    source = EXCLUDED.source,
                    notes = EXCLUDED.notes,
                    updated_at = now()
                """
            ),
            {
                "patient_id": patient_id,
                "item_id": item_id,
                "assessment_date": payload.data_avaliacao,
                "points": payload.pontos,
                "source": source,
                "notes": observacao,
            },
        )
    return {"ok": True}


@app.get("/api/avaliacoes/biblioteca_bhave")
def listar_biblioteca_bhave_para_avaliacao():
    df = pd.read_sql(
        text(
            """
            SELECT name, objective_template, mastery_threshold_percent, mastery_days
            FROM program_library
            ORDER BY name
            """
        ),
        engine,
    )
    return df.fillna("").to_dict(orient="records")


@app.get("/api/avaliacoes/{protocolo}/vinculos_bhave")
def listar_vinculos_bhave(
    protocolo: str,
    codigo_item: Optional[str] = Query(None, min_length=1, max_length=20),
):
    init_assessment_tables()
    with engine.begin() as conn:
        protocol_id = _protocol_id(conn, protocolo)
        params: dict[str, Any] = {"protocol_id": protocol_id}
        filtro_item = ""
        if codigo_item:
            filtro_item = "AND i.item_code = :item_code"
            params["item_code"] = _clean_query_param(codigo_item, max_length=20)

        rows = conn.execute(
            text(
                f"""
                SELECT i.item_code AS codigo, l.program_library_name AS programa_biblioteca,
                       l.auto_points AS pontos_automaticos, l.updated_at
                FROM assessment_item_program_links l
                JOIN assessment_items i ON i.id = l.item_id
                WHERE i.protocol_id = :protocol_id
                {filtro_item}
                ORDER BY i.item_code, l.program_library_name
                """
            ),
            params,
        ).mappings().all()
    return [_row_dict(row) for row in rows]


@app.post("/api/avaliacoes/{protocolo}/vincular_bhave")
def vincular_item_ao_bhave(protocolo: str, payload: AssessmentLinkPayload):
    init_assessment_tables()
    with engine.begin() as conn:
        protocol_id = _protocol_id(conn, protocolo)
        item = conn.execute(
            text(
                """
                SELECT id, max_points
                FROM assessment_items
                WHERE protocol_id = :protocol_id AND item_code = :item_code
                """
            ),
            {
                "protocol_id": protocol_id,
                "item_code": _clean_query_param(payload.codigo_item, max_length=20),
            },
        ).mappings().first()
        if not item:
            raise HTTPException(status_code=404, detail="Item avaliativo nao encontrado.")

        programa = _clean_query_param(payload.programa_biblioteca, max_length=200)
        pontos_auto = payload.pontos_automaticos
        if pontos_auto is not None and pontos_auto > int(item["max_points"]):
            raise HTTPException(status_code=400, detail="Pontuacao automatica acima do maximo do item.")

        conn.execute(
            text(
                """
                INSERT INTO assessment_item_program_links (
                    item_id, program_library_name, auto_points, updated_at
                )
                VALUES (:item_id, :program_library_name, :auto_points, now())
                ON CONFLICT (item_id, program_library_name) DO UPDATE
                SET auto_points = EXCLUDED.auto_points,
                    updated_at = now()
                """
            ),
            {
                "item_id": int(item["id"]),
                "program_library_name": programa,
                "auto_points": pontos_auto,
            },
        )
    return {"ok": True}


@app.get("/api/avaliacoes/{protocolo}/sugestoes_bhave")
def sugerir_pontuacao_bhave(
    protocolo: str,
    paciente: str = Query(..., min_length=1, max_length=128),
):
    init_assessment_tables()
    sugestoes = []

    with engine.begin() as conn:
        protocol_id = _protocol_id(conn, protocolo)
        patient_id = _patient_id(conn, paciente)
        links = conn.execute(
            text(
                """
                SELECT i.id AS item_id, i.item_code, i.description, i.max_points,
                       l.program_library_name, l.auto_points,
                       COALESCE(pl.mastery_threshold_percent, 90) AS threshold,
                       COALESCE(pl.mastery_days, 3) AS mastery_days,
                       p.id AS program_id
                FROM assessment_item_program_links l
                JOIN assessment_items i ON i.id = l.item_id
                JOIN programs p ON p.name = l.program_library_name AND p.patient_id = :patient_id
                LEFT JOIN program_library pl ON pl.name = l.program_library_name
                WHERE i.protocol_id = :protocol_id
                """
            ),
            {"protocol_id": protocol_id, "patient_id": patient_id},
        ).mappings().all()

        for link in links:
            mastery_sessions = max(1, int(link["mastery_days"] or 3))
            threshold = float(link["threshold"] or 90)
            historico = conn.execute(
                text(_ordered_program_records_sql()),
                {"program_id": int(link["program_id"]), "limit": mastery_sessions},
            ).mappings().all()

            if len(historico) < mastery_sessions:
                continue

            taxas = [float(row["success_rate"] or 0) for row in historico]
            if all(taxa >= threshold for taxa in taxas):
                sugestoes.append(
                    {
                        "codigo": link["item_code"],
                        "descricao": link["description"],
                        "programa": link["program_library_name"],
                        "threshold": threshold,
                        "mastery_days": mastery_sessions,
                        "taxas": taxas,
                        "pontos_sugeridos": int(link["auto_points"] or link["max_points"]),
                    }
                )

    return sugestoes
