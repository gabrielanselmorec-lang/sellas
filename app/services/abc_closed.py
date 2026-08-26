from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time as time_module
import unicodedata
import uuid
import zipfile
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import text

from app.services.abc_methodology import (
    EVIDENCE_RULE_VERSION,
    METHODOLOGY_VERSION,
    NORMALIZATION_VERSION,
    SeverityConfig,
    analysis_run_hash,
    audit_abc_data_quality,
    beta_posterior_interval,
    cluster_bootstrap_interval,
    compute_exposure_summary,
    compute_evidence_quality,
    descriptive_behavior_estimate,
    normalize_abc_records,
    normalize_clinical_value,
    severity_summary,
    wilson_interval,
)


DEFAULT_TIMEZONE = "America/Sao_Paulo"
INTERVAL_MINUTES = 5
PROJECT_DIR = Path(__file__).resolve().parents[2]
ABC_EXPORT_DIR = PROJECT_DIR / "exports" / "abc_logs"
ABC_EXCEL_RUNTIME_DIR = PROJECT_DIR / "tools" / "abc_excel_runtime"
ABC_EXCEL_BUILDER = ABC_EXCEL_RUNTIME_DIR / "build_abc_excel.mjs"
SEVERITY_LIGHT = 1
SEVERITY_INTENSE = 2
SEVERITY_LABELS = {
    SEVERITY_LIGHT: "C1 - leve",
    SEVERITY_INTENSE: "C2 - intenso",
}

ABC_CATEGORIES = (
    ("PEDIDO_NEGADO", "Pedido negado", "antecedente", "Acesso, item ou pedido negado antes do comportamento."),
    ("DEMANDA", "Demanda apresentada", "antecedente", "Instrução, tarefa ou solicitação apresentada."),
    ("TRANSICAO", "Transição", "antecedente", "Mudança de ambiente, atividade ou rotina."),
    ("ESPERA", "Espera", "antecedente", "Período de espera antes de acesso, atividade ou instrução."),
    ("AGRESSAO_FISICA", "Agressão física", "comportamento", "Contato físico agressivo observável."),
    ("CHORO", "Choro", "comportamento", "Choro audível ou visível durante o intervalo."),
    ("FUGA_ESQUIVA", "Fuga ou esquiva", "comportamento", "Tentativa observável de evitar ou interromper uma atividade."),
    ("GRITO", "Grito", "comportamento", "Vocalização alta ou grito observável no intervalo."),
    ("PAUSA", "Pausa", "consequencia", "Interrupção temporária da demanda ou atividade."),
    ("ATENCAO", "Atenção social", "consequencia", "Atenção social apresentada após o comportamento."),
    ("REDIRECIONAMENTO", "Redirecionamento", "consequencia", "Redirecionamento verbal, gestual ou físico."),
    ("ACESSO_ITEM", "Acesso a item", "consequencia", "Acesso a item, brinquedo ou atividade."),
)

_ABC_TABLES_READY = False
_ABC_TABLES_INIT_LOCK = threading.Lock()
_ABC_EXCEL_LOCKS: dict[str, threading.Lock] = {}
_ABC_EXCEL_LOCKS_GUARD = threading.Lock()

_TRANSIENT_DATABASE_SQLSTATES = {"40P01", "40001", "55P03"}


def _database_sqlstate(exc: BaseException) -> str | None:
    original = getattr(exc, "orig", exc)
    return getattr(original, "pgcode", None) or getattr(original, "sqlstate", None)


def _is_transient_database_error(exc: BaseException) -> bool:
    return _database_sqlstate(exc) in _TRANSIENT_DATABASE_SQLSTATES


def _query_mappings_with_retry(
    engine,
    statement,
    params: dict[str, Any],
    *,
    attempts: int = 3,
) -> list[dict[str, Any]]:
    """Repete apenas deadlock, serializacao ou timeout de lock do PostgreSQL."""
    for attempt in range(attempts):
        try:
            with engine.connect() as conn:
                return list(conn.execute(statement, params).mappings().all())
        except Exception as exc:
            if attempt + 1 >= attempts or not _is_transient_database_error(exc):
                raise
            time_module.sleep(0.15 * (2**attempt))
    return []


def patient_token_uuid(patient_id: str) -> str:
    try:
        return str(uuid.UUID(str(patient_id)))
    except (ValueError, TypeError, AttributeError):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"sellas-patient:{patient_id}"))


def combine_local_datetime(record_date: date, record_time: time, timezone_name: str = DEFAULT_TIMEZONE) -> datetime:
    local_tz = ZoneInfo(timezone_name)
    value = datetime.combine(record_date, record_time)
    return value.replace(tzinfo=local_tz)


def parse_hour_minute_text(value: str) -> time:
    raw = re.sub(r"\s+", "", str(value).strip().lower())
    normalized = raw.replace("h", ":").replace(".", ":")
    if normalized.isdigit() and len(normalized) in {3, 4}:
        normalized = f"{normalized[:-2]}:{normalized[-2:]}"

    match = re.fullmatch(r"(\d{1,2})(?::(\d{1,2}))?", normalized)
    if not match:
        raise ValueError("Informe a hora como HH:MM, 9:30, 0930 ou 9h30.")

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour > 23 or minute > 59:
        raise ValueError("Hora invalida. Use horas de 00 a 23 e minutos de 00 a 59.")
    return time(hour=hour, minute=minute)


def classify_interference_severity(
    classification: str,
    *,
    caused_injury: bool,
    bleeding: bool,
    targeted_vital_point: bool,
) -> dict[str, Any]:
    code = str(classification).strip().upper()
    criteria = bool(caused_injury or bleeding or targeted_vital_point)
    if code not in {"C1", "C2"}:
        raise ValueError("A classificação deve ser C1 (leve) ou C2 (intenso).")
    if code == "C1" and criteria:
        raise ValueError("C1 só pode ser usado quando não houve lesão, sangramento ou direção a ponto vital.")
    if code == "C2" and not criteria:
        raise ValueError("C2 exige ao menos um critério: lesão, sangramento ou direção a ponto vital.")
    intensity = SEVERITY_LIGHT if code == "C1" else SEVERITY_INTENSE
    return {
        "codigo": code,
        "rotulo": SEVERITY_LABELS[intensity],
        "intensidade": intensity,
        "indice_perigo": 0.20 if intensity == SEVERITY_LIGHT else 1.0,
    }


def severity_details(value: Any) -> dict[str, Any]:
    try:
        intensity = int(value)
    except (TypeError, ValueError):
        return {
            "codigo": "NC",
            "rotulo": "Não classificado",
            "intensidade": None,
            "indice_perigo": 0.50,
        }
    if intensity == SEVERITY_LIGHT:
        return {
            "codigo": "C1",
            "rotulo": SEVERITY_LABELS[SEVERITY_LIGHT],
            "intensidade": intensity,
            "indice_perigo": 0.20,
        }
    if intensity == SEVERITY_INTENSE:
        return {
            "codigo": "C2",
            "rotulo": SEVERITY_LABELS[SEVERITY_INTENSE],
            "intensidade": intensity,
            "indice_perigo": 1.0,
        }
    return {
        "codigo": "NC",
        "rotulo": "Não classificado",
        "intensidade": intensity,
        "indice_perigo": 0.50,
    }


def init_abc_tables(engine) -> None:
    global _ABC_TABLES_READY
    if _ABC_TABLES_READY:
        return
    with _ABC_TABLES_INIT_LOCK:
        if _ABC_TABLES_READY:
            return
        _initialize_abc_tables(engine)
        _ABC_TABLES_READY = True


def _initialize_abc_tables(engine) -> None:
    statements = (
        "CREATE EXTENSION IF NOT EXISTS pgcrypto",
        """
        CREATE TABLE IF NOT EXISTS abc_instrument_versions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            codigo VARCHAR(80) NOT NULL,
            nome VARCHAR(200) NOT NULL,
            versao VARCHAR(30) NOT NULL DEFAULT '1',
            ativo BOOLEAN NOT NULL DEFAULT TRUE,
            metadados JSONB NOT NULL DEFAULT '{}'::jsonb,
            criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (codigo, versao)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS abc_sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            patient_token UUID NOT NULL,
            service_id UUID,
            data_inicio TIMESTAMPTZ NOT NULL,
            data_fim TIMESTAMPTZ NOT NULL,
            timezone VARCHAR(80) NOT NULL DEFAULT 'America/Sao_Paulo',
            ambiente VARCHAR(160) NOT NULL DEFAULT 'Nao informado',
            observacao_completa BOOLEAN NOT NULL DEFAULT TRUE,
            instrumento_versao VARCHAR(30) NOT NULL DEFAULT '1',
            criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_abc_sessions_periodo CHECK (data_fim > data_inicio)
        )
        """,
        "ALTER TABLE abc_sessions ADD COLUMN IF NOT EXISTS ambiente VARCHAR(160) NOT NULL DEFAULT 'Nao informado'",
        "CREATE INDEX IF NOT EXISTS ix_abc_sessions_patient_inicio ON abc_sessions (patient_token, data_inicio)",
        """
        CREATE TABLE IF NOT EXISTS abc_intervals (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            sessao_id UUID NOT NULL REFERENCES abc_sessions(id) ON DELETE CASCADE,
            inicio TIMESTAMPTZ NOT NULL,
            fim TIMESTAMPTZ NOT NULL,
            timezone VARCHAR(80) NOT NULL DEFAULT 'America/Sao_Paulo',
            duracao_planejada_minutos INTEGER NOT NULL DEFAULT 5,
            status_observacao VARCHAR(30) NOT NULL DEFAULT 'observed',
            atraso_registro_segundos INTEGER,
            observador_token UUID,
            instrumento_versao VARCHAR(30) NOT NULL DEFAULT '1',
            criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_abc_intervals_sessao_inicio UNIQUE (sessao_id, inicio),
            CONSTRAINT chk_abc_intervals_periodo CHECK (fim > inicio),
            CONSTRAINT chk_abc_intervals_duracao CHECK (duracao_planejada_minutos > 0),
            CONSTRAINT chk_abc_intervals_status CHECK (
                status_observacao IN ('observed', 'not_observed', 'not_applicable', 'invalid')
            )
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_abc_intervals_inicio ON abc_intervals (inicio)",
        """
        CREATE TABLE IF NOT EXISTS abc_categories (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            codigo VARCHAR(80) NOT NULL,
            nome VARCHAR(200) NOT NULL,
            tipo VARCHAR(20) NOT NULL,
            definicao_operacional TEXT,
            versao INTEGER NOT NULL DEFAULT 1,
            ativa BOOLEAN NOT NULL DEFAULT TRUE,
            service_id UUID,
            organization_id UUID,
            criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_abc_categories_codigo_versao UNIQUE (codigo, versao),
            CONSTRAINT chk_abc_categories_tipo CHECK (tipo IN ('antecedente', 'comportamento', 'consequencia'))
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_abc_categories_tipo_ativa ON abc_categories (tipo, ativa)",
        "ALTER TABLE abc_categories ADD COLUMN IF NOT EXISTS nome_original VARCHAR(200)",
        "ALTER TABLE abc_categories ADD COLUMN IF NOT EXISTS nome_normalizado VARCHAR(200)",
        "ALTER TABLE abc_categories ADD COLUMN IF NOT EXISTS regra_normalizacao VARCHAR(160)",
        "ALTER TABLE abc_categories ADD COLUMN IF NOT EXISTS versao_normalizacao VARCHAR(40)",
        """
        CREATE TABLE IF NOT EXISTS abc_interval_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            intervalo_id UUID NOT NULL REFERENCES abc_intervals(id) ON DELETE CASCADE,
            categoria_id UUID NOT NULL REFERENCES abc_categories(id),
            ocorreu BOOLEAN,
            frequencia INTEGER,
            duracao_segundos INTEGER,
            intensidade SMALLINT,
            causou_lesao BOOLEAN,
            houve_sangramento BOOLEAN,
            direcionado_ponto_vital BOOLEAN,
            funcao_hipotese VARCHAR(120),
            confianca_registro NUMERIC(4,3),
            fonte VARCHAR(40) NOT NULL DEFAULT 'registro_fechado',
            revisado_humano BOOLEAN NOT NULL DEFAULT FALSE,
            criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_abc_event_intervalo_categoria UNIQUE (intervalo_id, categoria_id),
            CONSTRAINT chk_abc_event_frequencia CHECK (frequencia IS NULL OR frequencia >= 0),
            CONSTRAINT chk_abc_event_duracao CHECK (duracao_segundos IS NULL OR duracao_segundos >= 0),
            CONSTRAINT chk_abc_event_intensidade CHECK (intensidade IS NULL OR intensidade BETWEEN 0 AND 5),
            CONSTRAINT chk_abc_event_confianca CHECK (confianca_registro IS NULL OR confianca_registro BETWEEN 0 AND 1)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_abc_events_intervalo ON abc_interval_events (intervalo_id)",
        "ALTER TABLE abc_interval_events ADD COLUMN IF NOT EXISTS onset_ts TIMESTAMPTZ",
        "ALTER TABLE abc_interval_events ADD COLUMN IF NOT EXISTS offset_ts TIMESTAMPTZ",
        "ALTER TABLE abc_interval_events ADD COLUMN IF NOT EXISTS causou_lesao BOOLEAN",
        "ALTER TABLE abc_interval_events ADD COLUMN IF NOT EXISTS houve_sangramento BOOLEAN",
        "ALTER TABLE abc_interval_events ADD COLUMN IF NOT EXISTS direcionado_ponto_vital BOOLEAN",
        "ALTER TABLE abc_interval_events ADD COLUMN IF NOT EXISTS funcao_hipotese VARCHAR(120)",
        "ALTER TABLE abc_interval_events ADD COLUMN IF NOT EXISTS funcao_hipotese_original VARCHAR(120)",
        "ALTER TABLE abc_interval_events ADD COLUMN IF NOT EXISTS funcao_hipotese_normalizada VARCHAR(120)",
        "ALTER TABLE abc_interval_events ADD COLUMN IF NOT EXISTS versao_normalizacao VARCHAR(40)",
        """
        UPDATE abc_categories
           SET nome_original = COALESCE(nome_original, nome),
               nome_normalizado = CASE
                   WHEN lower(trim(nome)) IN ('manejo fisíco', 'manejo fisico') THEN 'Manejo físico'
                   WHEN lower(trim(nome)) = 'do nada' THEN 'Antecedente não identificado no registro'
                   ELSE regexp_replace(trim(nome), '\\s+', ' ', 'g')
               END,
               regra_normalizacao = CASE
                   WHEN lower(trim(nome)) IN ('manejo fisíco', 'manejo fisico') THEN 'known_alias:consequencia:manejo fisico'
                   WHEN lower(trim(nome)) = 'do nada' THEN 'known_alias:antecedente:do nada'
                   ELSE 'trim_whitespace'
               END,
               versao_normalizacao = 'abc-normalization-v1'
         WHERE versao_normalizacao IS NULL
        """,
        """
        UPDATE abc_interval_events
           SET funcao_hipotese_original = COALESCE(funcao_hipotese_original, funcao_hipotese),
               funcao_hipotese_normalizada = CASE
                   WHEN funcao_hipotese IS NULL OR trim(funcao_hipotese) = '' THEN 'Não identificada'
                   WHEN lower(trim(funcao_hipotese)) IN ('fuga ou esquiva', 'fuga/esquiva') THEN 'Fuga/esquiva'
                   ELSE regexp_replace(trim(funcao_hipotese), '\\s+', ' ', 'g')
               END,
               versao_normalizacao = 'abc-normalization-v1'
         WHERE versao_normalizacao IS NULL
        """,
        """
        CREATE TABLE IF NOT EXISTS abc_action_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            patient_token UUID NOT NULL,
            acao VARCHAR(40) NOT NULL,
            intervalo_id UUID,
            categoria_id UUID,
            snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
            criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_abc_action_logs_acao CHECK (
                acao IN ('registro_adicionado', 'registro_editado', 'registro_removido', 'categoria_criada')
            )
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS abc_report_audit_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            patient_token UUID NOT NULL,
            generated_by VARCHAR(160) NOT NULL,
            filters JSONB NOT NULL DEFAULT '{}'::jsonb,
            output_format VARCHAR(20) NOT NULL,
            anonymized BOOLEAN NOT NULL DEFAULT FALSE,
            logic_version VARCHAR(80) NOT NULL,
            generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_abc_action_logs_patient_time ON abc_action_logs (patient_token, criado_em)",
        "CREATE INDEX IF NOT EXISTS ix_abc_report_audit_patient_time ON abc_report_audit_logs (patient_token, generated_at)",
        "ALTER TABLE abc_instrument_versions ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE abc_sessions ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE abc_intervals ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE abc_categories ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE abc_interval_events ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE abc_action_logs ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE abc_report_audit_logs ENABLE ROW LEVEL SECURITY",
    )
    for attempt in range(4):
        try:
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL lock_timeout = '5s'"))
                conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('sellas:abc-schema:v4'))"))
                for statement in statements:
                    conn.execute(text(statement))
                _seed_categories(conn)
            return
        except Exception as exc:
            if attempt >= 3 or not _is_transient_database_error(exc):
                raise
            time_module.sleep(0.25 * (2**attempt))


def _seed_categories(conn) -> None:
    conn.execute(
        text(
            """
            INSERT INTO abc_instrument_versions (codigo, nome, versao, ativo, metadados)
            VALUES ('ABC_FECHADO', 'Instrumento ABC fechado por intervalos', '1', TRUE, '{"interval_minutes": 5}'::jsonb)
            ON CONFLICT (codigo, versao) DO UPDATE
            SET nome = EXCLUDED.nome, ativo = EXCLUDED.ativo, metadados = EXCLUDED.metadados
            """
        )
    )
    for codigo, nome, tipo, definicao in ABC_CATEGORIES:
        conn.execute(
            text(
                """
                INSERT INTO abc_categories (
                    codigo, nome, nome_original, nome_normalizado, regra_normalizacao,
                    versao_normalizacao, tipo, definicao_operacional, versao, ativa
                )
                VALUES (:codigo, :nome, :nome, :nome, 'seed_canonical', :normalization_version, :tipo, :definicao, 1, TRUE)
                ON CONFLICT (codigo, versao) DO UPDATE
                SET nome = EXCLUDED.nome,
                    nome_normalizado = EXCLUDED.nome_normalizado,
                    regra_normalizacao = EXCLUDED.regra_normalizacao,
                    versao_normalizacao = EXCLUDED.versao_normalizacao,
                    tipo = EXCLUDED.tipo,
                    definicao_operacional = EXCLUDED.definicao_operacional,
                    ativa = TRUE
                """
            ),
            {"codigo": codigo, "nome": nome, "tipo": tipo, "definicao": definicao, "normalization_version": NORMALIZATION_VERSION},
        )


def list_categories(engine) -> list[dict[str, Any]]:
    init_abc_tables(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, codigo, COALESCE(nome_normalizado, nome) AS nome, nome_original,
                       regra_normalizacao, versao_normalizacao, tipo, definicao_operacional
                FROM abc_categories
                WHERE ativa = TRUE
                ORDER BY CASE tipo WHEN 'antecedente' THEN 1 WHEN 'comportamento' THEN 2 ELSE 3 END, nome
                """
            )
        ).mappings().all()
    return [{key: str(value) if key == "id" else value for key, value in dict(row).items()} for row in rows]


def create_custom_category(
    engine,
    *,
    name: str,
    category_type: str,
    patient_token: str | None = None,
) -> dict[str, Any]:
    init_abc_tables(engine)
    clean_name = " ".join(str(name).strip().split())
    if not clean_name:
        raise ValueError("Informe o nome da nova opção ABC.")
    if len(clean_name) > 200:
        raise ValueError("O nome da opção ABC deve ter no máximo 200 caracteres.")
    if category_type not in {"antecedente", "comportamento", "consequencia"}:
        raise ValueError("Tipo de categoria ABC inválido.")

    with engine.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT id, codigo, COALESCE(nome_normalizado, nome) AS nome, nome_original,
                       regra_normalizacao, versao_normalizacao, tipo, definicao_operacional
                FROM abc_categories
                WHERE tipo = :tipo AND lower(nome) = lower(:nome) AND ativa = TRUE
                ORDER BY versao DESC
                LIMIT 1
                """
            ),
            {"tipo": category_type, "nome": clean_name},
        ).mappings().first()
        if existing:
            return {**_serialize_row(existing), "created": False}

        base_code = _category_code(clean_name, category_type)
        code = base_code
        suffix = 2
        while conn.execute(
            text("SELECT 1 FROM abc_categories WHERE codigo = :codigo AND versao = 1"),
            {"codigo": code},
        ).first():
            suffix_text = f"_{suffix}"
            code = f"{base_code[:80 - len(suffix_text)]}{suffix_text}"
            suffix += 1

        normalized = normalize_clinical_value(clean_name, category_type)
        row = conn.execute(
            text(
                """
                INSERT INTO abc_categories (
                    codigo, nome, nome_original, nome_normalizado, regra_normalizacao,
                    versao_normalizacao, tipo, definicao_operacional, versao, ativa
                ) VALUES (
                    :codigo, :nome, :nome, :normalized_name, :normalization_rule,
                    :normalization_version, :tipo, :definicao, 1, TRUE
                )
                RETURNING id, codigo, nome_normalizado AS nome, nome_original,
                          regra_normalizacao, versao_normalizacao, tipo, definicao_operacional
                """
            ),
            {
                "codigo": code,
                "nome": clean_name,
                "normalized_name": normalized["normalized_value"],
                "normalization_rule": normalized["normalization_rule"],
                "normalization_version": normalized["normalization_version"],
                "tipo": category_type,
                "definicao": f"Categoria personalizada: {clean_name}.",
            },
        ).mappings().one()
        if patient_token:
            _write_action_log(
                conn,
                patient_token=patient_token,
                action="categoria_criada",
                category_id=str(row["id"]),
                snapshot={"codigo": row["codigo"], "nome": row["nome"], "tipo": row["tipo"]},
            )
    return {**_serialize_row(row), "created": True}


def add_closed_record(
    engine,
    *,
    patient_token: str,
    antecedente_codigo: str,
    comportamento_codigo: str,
    consequencia_codigo: str,
    data_hora: datetime,
    ambiente: str,
    classificacao: str,
    causou_lesao: bool,
    houve_sangramento: bool,
    direcionado_ponto_vital: bool,
    funcao: str,
) -> dict[str, Any]:
    init_abc_tables(engine)
    severity = classify_interference_severity(
        classificacao,
        caused_injury=causou_lesao,
        bleeding=houve_sangramento,
        targeted_vital_point=direcionado_ponto_vital,
    )
    function_normalization = normalize_clinical_value(funcao, "funcao")
    clean_function = str(function_normalization["normalized_value"])
    if not clean_function:
        raise ValueError("Informe a função ou hipótese funcional do comportamento.")
    if len(clean_function) > 120:
        raise ValueError("A função deve ter no máximo 120 caracteres.")
    selected = {
        "antecedente": antecedente_codigo,
        "comportamento": comportamento_codigo,
        "consequencia": consequencia_codigo,
    }
    end_at = data_hora + timedelta(minutes=INTERVAL_MINUTES)
    with engine.begin() as conn:
        categories = conn.execute(
            text("SELECT id, codigo, COALESCE(nome_normalizado, nome) AS nome, tipo FROM abc_categories WHERE ativa = TRUE ORDER BY tipo, COALESCE(nome_normalizado, nome)")
        ).mappings().all()
        available = {(row["tipo"], row["codigo"]) for row in categories}
        missing = [f"{kind}:{code}" for kind, code in selected.items() if (kind, code) not in available]
        if missing:
            raise ValueError(f"Categoria ABC invalida: {', '.join(missing)}")

        session_id = conn.execute(
            text(
                """
                INSERT INTO abc_sessions (
                    patient_token, data_inicio, data_fim, timezone, ambiente,
                    observacao_completa, instrumento_versao
                ) VALUES (
                    CAST(:patient_token AS UUID), :inicio, :fim, :timezone, :ambiente, TRUE, '1'
                ) RETURNING id
                """
            ),
            {
                "patient_token": patient_token,
                "inicio": data_hora,
                "fim": end_at,
                "timezone": DEFAULT_TIMEZONE,
                "ambiente": ambiente,
            },
        ).scalar_one()
        interval_id = conn.execute(
            text(
                """
                INSERT INTO abc_intervals (
                    sessao_id, inicio, fim, timezone, duracao_planejada_minutos,
                    status_observacao, instrumento_versao
                ) VALUES (
                    :session_id, :inicio, :fim, :timezone, :minutes, 'observed', '1'
                ) RETURNING id
                """
            ),
            {
                "session_id": session_id,
                "inicio": data_hora,
                "fim": end_at,
                "timezone": DEFAULT_TIMEZONE,
                "minutes": INTERVAL_MINUTES,
            },
        ).scalar_one()

        for category in categories:
            occurred = category["codigo"] == selected[category["tipo"]]
            selected_behavior = category["tipo"] == "comportamento" and occurred
            conn.execute(
                text(
                    """
                    INSERT INTO abc_interval_events (
                        intervalo_id, categoria_id, ocorreu, frequencia, intensidade,
                        onset_ts, offset_ts,
                        causou_lesao, houve_sangramento, direcionado_ponto_vital,
                        funcao_hipotese, funcao_hipotese_original, funcao_hipotese_normalizada,
                        versao_normalizacao, fonte, revisado_humano
                    ) VALUES (
                        :interval_id, :category_id, :occurred, :frequency, :intensity,
                        :onset_ts, :offset_ts,
                        :caused_injury, :bleeding, :targeted_vital_point,
                        :function_raw, :function_raw, :function, :normalization_version,
                        'registro_fechado', TRUE
                    )
                    """
                ),
                {
                    "interval_id": interval_id,
                    "category_id": category["id"],
                    "occurred": occurred,
                    "frequency": 1 if occurred else 0,
                    "intensity": severity["intensidade"] if selected_behavior else None,
                    "onset_ts": data_hora if occurred else None,
                    "offset_ts": data_hora if occurred else None,
                    "caused_injury": bool(causou_lesao) if selected_behavior else None,
                    "bleeding": bool(houve_sangramento) if selected_behavior else None,
                    "targeted_vital_point": bool(direcionado_ponto_vital) if selected_behavior else None,
                    "function": clean_function if selected_behavior else None,
                    "function_raw": str(funcao).strip() if selected_behavior else None,
                    "normalization_version": NORMALIZATION_VERSION if selected_behavior else None,
                },
            )

        selected_categories = {
            category["tipo"]: {"codigo": category["codigo"], "nome": category["nome"]}
            for category in categories
            if category["codigo"] == selected[category["tipo"]]
        }
        snapshot = {
            "data_hora": data_hora.isoformat(),
            "ambiente": ambiente,
            "antecedente": selected_categories["antecedente"]["nome"],
            "antecedente_codigo": selected_categories["antecedente"]["codigo"],
            "comportamento": selected_categories["comportamento"]["nome"],
            "comportamento_codigo": selected_categories["comportamento"]["codigo"],
            "consequencia": selected_categories["consequencia"]["nome"],
            "consequencia_codigo": selected_categories["consequencia"]["codigo"],
            "classificacao": severity["codigo"],
            "classificacao_rotulo": severity["rotulo"],
            "intensidade": severity["intensidade"],
            "causou_lesao": bool(causou_lesao),
            "houve_sangramento": bool(houve_sangramento),
            "direcionado_ponto_vital": bool(direcionado_ponto_vital),
            "funcao": clean_function,
        }
        _write_action_log(
            conn,
            patient_token=patient_token,
            action="registro_adicionado",
            interval_id=str(interval_id),
            snapshot=snapshot,
        )

    return {
        "ok": True,
        "session_id": str(session_id),
        "interval_id": str(interval_id),
        "data_hora": data_hora.isoformat(),
        "ambiente": ambiente,
        "classificacao": severity["codigo"],
        "funcao": clean_function,
        **selected,
    }


def update_closed_record(
    engine,
    *,
    patient_token: str,
    interval_id: str,
    antecedente_codigo: str,
    comportamento_codigo: str,
    consequencia_codigo: str,
    data_hora: datetime,
    ambiente: str,
    classificacao: str,
    causou_lesao: bool,
    houve_sangramento: bool,
    direcionado_ponto_vital: bool,
    funcao: str,
) -> dict[str, Any]:
    init_abc_tables(engine)
    try:
        uuid.UUID(str(interval_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Identificador do acontecimento inválido.") from exc

    severity = classify_interference_severity(
        classificacao,
        caused_injury=causou_lesao,
        bleeding=houve_sangramento,
        targeted_vital_point=direcionado_ponto_vital,
    )
    function_normalization = normalize_clinical_value(funcao, "funcao")
    clean_function = str(function_normalization["normalized_value"])
    if not clean_function:
        raise ValueError("Informe a função ou hipótese funcional do comportamento.")
    if len(clean_function) > 120:
        raise ValueError("A função deve ter no máximo 120 caracteres.")

    selected = {
        "antecedente": antecedente_codigo,
        "comportamento": comportamento_codigo,
        "consequencia": consequencia_codigo,
    }
    end_at = data_hora + timedelta(minutes=INTERVAL_MINUTES)
    with engine.begin() as conn:
        before = _load_interval_record(conn, patient_token, interval_id)
        if not before:
            raise ValueError("Acontecimento ABC não encontrado para este paciente.")

        categories = conn.execute(
            text("SELECT id, codigo, COALESCE(nome_normalizado, nome) AS nome, tipo FROM abc_categories WHERE ativa = TRUE ORDER BY tipo, COALESCE(nome_normalizado, nome)")
        ).mappings().all()
        available = {(row["tipo"], row["codigo"]) for row in categories}
        missing = [f"{kind}:{code}" for kind, code in selected.items() if (kind, code) not in available]
        if missing:
            raise ValueError(f"Categoria ABC inválida: {', '.join(missing)}")

        conn.execute(
            text(
                """
                UPDATE abc_sessions
                SET data_inicio = :inicio, data_fim = :fim, ambiente = :ambiente
                WHERE id = CAST(:session_id AS UUID)
                  AND patient_token = CAST(:patient_token AS UUID)
                """
            ),
            {
                "inicio": data_hora,
                "fim": end_at,
                "ambiente": ambiente,
                "session_id": before["session_id"],
                "patient_token": patient_token,
            },
        )
        conn.execute(
            text(
                """
                UPDATE abc_intervals
                SET inicio = :inicio, fim = :fim
                WHERE id = CAST(:interval_id AS UUID)
                """
            ),
            {"inicio": data_hora, "fim": end_at, "interval_id": interval_id},
        )

        for category in categories:
            occurred = category["codigo"] == selected[category["tipo"]]
            selected_behavior = category["tipo"] == "comportamento" and occurred
            conn.execute(
                text(
                    """
                    INSERT INTO abc_interval_events (
                        intervalo_id, categoria_id, ocorreu, frequencia, intensidade,
                        onset_ts, offset_ts,
                        causou_lesao, houve_sangramento, direcionado_ponto_vital,
                        funcao_hipotese, funcao_hipotese_original, funcao_hipotese_normalizada,
                        versao_normalizacao, fonte, revisado_humano
                    ) VALUES (
                        CAST(:interval_id AS UUID), :category_id, :occurred, :frequency, :intensity,
                        :onset_ts, :offset_ts,
                        :caused_injury, :bleeding, :targeted_vital_point,
                        :function_raw, :function_raw, :function, :normalization_version,
                        'registro_fechado', TRUE
                    )
                    ON CONFLICT (intervalo_id, categoria_id) DO UPDATE SET
                        ocorreu = EXCLUDED.ocorreu,
                        frequencia = EXCLUDED.frequencia,
                        intensidade = EXCLUDED.intensidade,
                        onset_ts = EXCLUDED.onset_ts,
                        offset_ts = EXCLUDED.offset_ts,
                        causou_lesao = EXCLUDED.causou_lesao,
                        houve_sangramento = EXCLUDED.houve_sangramento,
                        direcionado_ponto_vital = EXCLUDED.direcionado_ponto_vital,
                        funcao_hipotese = EXCLUDED.funcao_hipotese,
                        funcao_hipotese_original = EXCLUDED.funcao_hipotese_original,
                        funcao_hipotese_normalizada = EXCLUDED.funcao_hipotese_normalizada,
                        versao_normalizacao = EXCLUDED.versao_normalizacao,
                        revisado_humano = TRUE
                    """
                ),
                {
                    "interval_id": interval_id,
                    "category_id": category["id"],
                    "occurred": occurred,
                    "frequency": 1 if occurred else 0,
                    "intensity": severity["intensidade"] if selected_behavior else None,
                    "onset_ts": data_hora if occurred else None,
                    "offset_ts": data_hora if occurred else None,
                    "caused_injury": bool(causou_lesao) if selected_behavior else None,
                    "bleeding": bool(houve_sangramento) if selected_behavior else None,
                    "targeted_vital_point": bool(direcionado_ponto_vital) if selected_behavior else None,
                    "function": clean_function if selected_behavior else None,
                    "function_raw": str(funcao).strip() if selected_behavior else None,
                    "normalization_version": NORMALIZATION_VERSION if selected_behavior else None,
                },
            )

        after = _load_interval_record(conn, patient_token, interval_id)
        if not after:
            raise ValueError("Não foi possível reler o acontecimento editado.")
        _write_action_log(
            conn,
            patient_token=patient_token,
            action="registro_editado",
            interval_id=interval_id,
            snapshot={**after, "anterior": before},
        )
    return {"ok": True, "intervalo_id": interval_id, "registro": after}


def delete_closed_record(engine, *, patient_token: str, interval_id: str) -> dict[str, Any]:
    init_abc_tables(engine)
    try:
        uuid.UUID(str(interval_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Identificador do acontecimento inválido.") from exc

    with engine.begin() as conn:
        record = _load_interval_record(conn, patient_token, interval_id)
        if not record:
            raise ValueError("Acontecimento ABC não encontrado para este paciente.")
        _write_action_log(
            conn,
            patient_token=patient_token,
            action="registro_removido",
            interval_id=interval_id,
            snapshot=record,
        )
        deleted = conn.execute(
            text(
                """
                DELETE FROM abc_sessions
                WHERE id = CAST(:session_id AS UUID)
                  AND patient_token = CAST(:patient_token AS UUID)
                """
            ),
            {"session_id": record["session_id"], "patient_token": patient_token},
        ).rowcount
        if deleted != 1:
            raise ValueError("Não foi possível remover o acontecimento ABC.")
    return {"ok": True, "intervalo_id": interval_id, "removido": record}


def load_patient_records(engine, patient_token: str) -> list[dict[str, Any]]:
    init_abc_tables(engine)
    rows = _query_mappings_with_retry(
        engine,
        text(
            """
                SELECT
                    i.id AS intervalo_id,
                    s.data_inicio AS data_hora,
                    i.criado_em AS criado_em,
                    s.ambiente,
                    MAX(CASE WHEN c.tipo = 'antecedente' AND e.ocorreu IS TRUE THEN c.codigo END) AS antecedente_codigo,
                    MAX(CASE WHEN c.tipo = 'antecedente' AND e.ocorreu IS TRUE THEN COALESCE(c.nome_normalizado, c.nome) END) AS antecedente,
                    MAX(CASE WHEN c.tipo = 'comportamento' AND e.ocorreu IS TRUE THEN c.codigo END) AS comportamento_codigo,
                    MAX(CASE WHEN c.tipo = 'comportamento' AND e.ocorreu IS TRUE THEN COALESCE(c.nome_normalizado, c.nome) END) AS comportamento,
                    MAX(CASE WHEN c.tipo = 'comportamento' AND e.ocorreu IS TRUE THEN e.intensidade END) AS intensidade,
                    BOOL_OR(c.tipo = 'comportamento' AND e.ocorreu IS TRUE AND COALESCE(e.causou_lesao, FALSE)) AS causou_lesao,
                    BOOL_OR(c.tipo = 'comportamento' AND e.ocorreu IS TRUE AND COALESCE(e.houve_sangramento, FALSE)) AS houve_sangramento,
                    BOOL_OR(c.tipo = 'comportamento' AND e.ocorreu IS TRUE AND COALESCE(e.direcionado_ponto_vital, FALSE)) AS direcionado_ponto_vital,
                    MAX(CASE WHEN c.tipo = 'comportamento' AND e.ocorreu IS TRUE THEN COALESCE(e.funcao_hipotese_normalizada, e.funcao_hipotese) END) AS funcao,
                    MAX(CASE WHEN c.tipo = 'consequencia' AND e.ocorreu IS TRUE THEN c.codigo END) AS consequencia_codigo,
                    MAX(CASE WHEN c.tipo = 'consequencia' AND e.ocorreu IS TRUE THEN COALESCE(c.nome_normalizado, c.nome) END) AS consequencia
                FROM abc_sessions s
                JOIN abc_intervals i ON i.sessao_id = s.id
                JOIN abc_interval_events e ON e.intervalo_id = i.id
                JOIN abc_categories c ON c.id = e.categoria_id
                WHERE s.patient_token = CAST(:patient_token AS UUID)
                  AND i.status_observacao = 'observed'
                GROUP BY i.id, i.criado_em, s.data_inicio, s.ambiente
                ORDER BY s.data_inicio
            """
        ),
        {"patient_token": patient_token},
    )
    result = []
    for row in rows:
        item = dict(row)
        item["intervalo_id"] = str(item["intervalo_id"])
        item["data_hora"] = item["data_hora"].astimezone(ZoneInfo(DEFAULT_TIMEZONE)).isoformat()
        item["criado_em"] = item["criado_em"].astimezone(ZoneInfo(DEFAULT_TIMEZONE)).isoformat()
        result.append(_enrich_record(item))
    return result


def load_action_logs(engine, patient_token: str) -> list[dict[str, Any]]:
    init_abc_tables(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, acao, intervalo_id, categoria_id, snapshot, criado_em
                FROM abc_action_logs
                WHERE patient_token = CAST(:patient_token AS UUID)
                ORDER BY criado_em
                """
            ),
            {"patient_token": patient_token},
        ).mappings().all()
    result = []
    for row in rows:
        item = _serialize_row(row)
        item["criado_em"] = row["criado_em"].astimezone(ZoneInfo(DEFAULT_TIMEZONE)).isoformat()
        result.append(item)
    return result


def load_observation_metadata(
    engine,
    *,
    patient_token: str,
    start_date: date | None = None,
    end_date: date | None = None,
    include_weekends: bool = True,
    environment: str | None = None,
) -> list[dict[str, Any]]:
    """Carrega um registro por intervalo sem converter ausência de observação em falso."""
    init_abc_tables(engine)
    filters = ["s.patient_token = CAST(:patient_token AS UUID)"]
    params: dict[str, Any] = {"patient_token": patient_token}
    if start_date:
        filters.append("(i.inicio AT TIME ZONE :timezone)::date >= :start_date")
        params["start_date"] = start_date
    if end_date:
        filters.append("(i.inicio AT TIME ZONE :timezone)::date <= :end_date")
        params["end_date"] = end_date
    if not include_weekends:
        filters.append("EXTRACT(ISODOW FROM i.inicio AT TIME ZONE :timezone) BETWEEN 1 AND 5")
    if environment:
        filters.append("s.ambiente = :environment")
        params["environment"] = environment
    params["timezone"] = DEFAULT_TIMEZONE

    rows = _query_mappings_with_retry(
        engine,
        text(
            f"""
                SELECT
                    i.id AS intervalo_id,
                    i.sessao_id AS session_id,
                    i.inicio,
                    i.fim,
                    i.status_observacao,
                    i.duracao_planejada_minutos,
                    i.atraso_registro_segundos,
                    i.instrumento_versao,
                    s.ambiente,
                    s.observacao_completa,
                    COUNT(e.id) FILTER (WHERE e.ocorreu IS NULL) AS eventos_nao_informados,
                    COUNT(e.id) FILTER (WHERE e.revisado_humano IS FALSE) AS eventos_sem_revisao
                FROM abc_intervals i
                JOIN abc_sessions s ON s.id = i.sessao_id
                LEFT JOIN abc_interval_events e ON e.intervalo_id = i.id
                WHERE {' AND '.join(filters)}
                GROUP BY i.id, i.sessao_id, i.inicio, i.fim, i.status_observacao,
                         i.duracao_planejada_minutos, i.atraso_registro_segundos,
                         i.instrumento_versao, s.ambiente, s.observacao_completa
                ORDER BY i.inicio
            """
        ),
        params,
    )
    return [_serialize_row(row) for row in rows]


def build_abc_printable_summary(
    *,
    patient_name: str,
    records: Iterable[dict[str, Any]],
    intervals: Iterable[dict[str, Any]],
    temporal_candidates: Iterable[dict[str, Any]] = (),
    anonymize_patient: bool = False,
    include_candidate_chains: bool = True,
    reviewed_chains_only: bool = False,
    generated_by: str = "Usuário local",
    logic_version: str = "abc-print-summary-v1",
) -> dict[str, Any]:
    """Monta o contrato auditável usado pela prévia e pelos relatórios imprimíveis."""
    # Os registros legados são oficiais: normalize a cópia usada na análise e no
    # resumo, mantendo os valores originais nos campos de auditoria.
    record_rows = normalize_abc_records(records)
    interval_rows = list(intervals)
    temporal_rows_source = list(temporal_candidates)
    analysis = build_abc_analysis(record_rows)
    quality_audit = audit_abc_data_quality(record_rows, interval_rows)
    exposure = compute_exposure_summary(record_rows, interval_rows)
    dataset_hash = analysis_run_hash(
        record_rows,
        {
            "methodology_version": METHODOLOGY_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
            "logic_version": logic_version,
        },
    )
    statuses = Counter(str(item.get("status_observacao") or "unknown") for item in interval_rows)
    observed = statuses.get("observed", 0)
    not_observed = statuses.get("not_observed", 0)
    invalid = statuses.get("invalid", 0)
    not_applicable = statuses.get("not_applicable", 0)
    total_intervals = len(interval_rows)
    coverage = observed / total_intervals if total_intervals else 0.0
    session_ids = {str(item.get("session_id")) for item in interval_rows if item.get("session_id")}
    observed_minutes = sum(
        max(0, int(item.get("duracao_planejada_minutos") or 0))
        for item in interval_rows
        if item.get("status_observacao") == "observed"
    )
    missing_events = sum(int(item.get("eventos_nao_informados") or 0) for item in interval_rows)
    unreviewed_events = sum(int(item.get("eventos_sem_revisao") or 0) for item in interval_rows)
    retroactive = sum(
        1 for item in interval_rows if int(item.get("atraso_registro_segundos") or 0) > 0
    )
    versions = sorted({str(item.get("instrumento_versao")) for item in interval_rows if item.get("instrumento_versao")})
    occurrence_interval_ids = {
        str(item.get("intervalo_id")) for item in record_rows if item.get("intervalo_id")
    }

    antecedents = Counter(str(item.get("antecedente") or "Não informado") for item in record_rows)
    behaviors = Counter(str(item.get("comportamento") or "Não informado") for item in record_rows)
    consequences = Counter(str(item.get("consequencia") or "Não informado") for item in record_rows)

    def top_value(counter: Counter) -> dict[str, Any]:
        if not counter:
            return {"nome": "Não disponível", "quantidade": 0}
        name, count = counter.most_common(1)[0]
        return {"nome": name, "quantidade": count}

    def enrich_associations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for item in rows:
            exposed = int(item.get("total_exposto") or 0)
            conditional = float(item.get("probabilidade_condicional") or 0.0)
            baseline = float(item.get("probabilidade_baseline") or 0.0)
            quality = "adequada" if exposed >= 20 else "limitada" if exposed >= 8 else "inicial"
            result.append(
                {
                    **item,
                    "exposicoes": exposed,
                    "ocorrencias_conjuntas": int(item.get("suporte") or 0),
                    "diferenca_risco": conditional - baseline,
                    "qualidade_estimativa": quality,
                }
            )
        return result

    visible_statuses = {"accepted"} if reviewed_chains_only or not include_candidate_chains else {"accepted", "candidate"}
    candidates = []
    seen_transitions: set[str] = set()
    for item in temporal_rows_source:
        if str(item.get("validation_status") or "candidate") not in visible_statuses:
            continue
        if item.get("rejection_reason") == "superseded_detection":
            continue
        transition_key = str(
            item.get("transition_id")
            or "|".join(
                str(item.get(key) or "")
                for key in ("from_behavior_event_id", "from_interval_id", "to_behavior_event_id", "to_interval_id", "rule_version")
            )
        )
        if transition_key in seen_transitions:
            continue
        seen_transitions.add(transition_key)
        candidates.append(item)
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for item in candidates:
        key = (
            str(item.get("origin_behavior_code") or "?"),
            str(item.get("from_consequence_code") or "?"),
            str(item.get("to_antecedent_code") or "?"),
            str(item.get("next_behavior_code") or "?"),
        )
        grouped.setdefault(key, []).append(item)
    temporal_rows = []
    for key, items in grouped.items():
        deltas = [float(item.get("delta_seconds")) for item in items if item.get("delta_seconds") is not None]
        confidences = [float(item.get("chain_confidence")) for item in items if item.get("chain_confidence") is not None]
        accepted = all(item.get("validation_status") == "accepted" for item in items)
        mean_confidence = sum(confidences) / len(confidences) if confidences else None
        chain_sessions = {
            str(session_id)
            for item in items
            for session_id in (item.get("from_session_id"), item.get("to_session_id"))
            if session_id
        }
        chain_days = {
            str(item.get("completed_at") or item.get("destination_start_ts") or "")[:10]
            for item in items
            if item.get("completed_at") or item.get("destination_start_ts")
        }
        sorted_deltas = sorted(deltas)
        q1 = sorted_deltas[max(0, int((len(sorted_deltas) - 1) * 0.25))] if sorted_deltas else None
        q3 = sorted_deltas[max(0, int((len(sorted_deltas) - 1) * 0.75))] if sorted_deltas else None
        stability_criteria = {
            "minimum_occurrences": len(items) >= 3,
            "minimum_sessions": len(chain_sessions) >= 2,
            "minimum_days": len(chain_days) >= 2,
            "not_concentrated_single_session": len(chain_sessions) >= 2,
        }
        stable = all(stability_criteria.values()) and (mean_confidence or 0) >= 0.90
        temporal_rows.append(
            {
                "cadeia": f"{key[0]} -> {key[1]} => {key[2]} -> {key[3]}",
                "repeticoes": len(items),
                "sessoes": len(chain_sessions) if chain_sessions else None,
                "dias": len(chain_days),
                "tempo_medio_segundos": sum(deltas) / len(deltas) if deltas else None,
                "tempo_mediano_segundos": sorted_deltas[len(sorted_deltas) // 2] if sorted_deltas else None,
                "tempo_iqr_segundos": (q3 - q1) if q1 is not None and q3 is not None else None,
                "tempo_minimo_segundos": min(deltas) if deltas else None,
                "tempo_maximo_segundos": max(deltas) if deltas else None,
                "confianca_media": mean_confidence,
                "estabilidade_temporal": "estável" if stable else "evidência insuficiente",
                "criterios_estabilidade": stability_criteria,
                "status": "Revisada e aceita" if accepted else "Candidata",
                "revisao_humana": accepted,
            }
        )
    temporal_rows.sort(key=lambda item: (item["repeticoes"], item["confianca_media"] or 0), reverse=True)

    missing_ratio = (not_observed + invalid + not_applicable) / total_intervals if total_intervals else 1.0
    warnings = []
    if total_intervals == 0:
        quality = "insuficiente"
        warnings.append("Não há intervalos no período selecionado.")
    elif coverage >= 0.90 and invalid == 0 and missing_events == 0:
        quality = "adequada"
    elif coverage >= 0.70:
        quality = "limitada"
    else:
        quality = "insuficiente"
    if not_observed:
        warnings.append(f"{not_observed} intervalo(s) não observado(s) foram excluídos dos denominadores de ocorrência.")
    if invalid:
        warnings.append(f"{invalid} intervalo(s) inválido(s) foram excluídos da análise.")
    if missing_events:
        warnings.append(f"{missing_events} valor(es) de evento estão como não informado; nenhum foi convertido em ausência.")
    if unreviewed_events:
        warnings.append(f"{unreviewed_events} evento(s) ainda não possuem revisão humana registrada.")
    if len(versions) > 1:
        warnings.append("Há mais de uma versão do instrumento no período selecionado.")
    if quality_audit["duplicate_identifiers"] or quality_audit["duplicate_exact_rows"]:
        warnings.append(
            f"Foram detectadas {quality_audit['duplicate_identifiers']} duplicidade(s) de identificador e "
            f"{quality_audit['duplicate_exact_rows']} linha(s) clinicamente idêntica(s)."
        )
    if quality_audit["invalid_timestamps"]:
        warnings.append(f"Há {quality_audit['invalid_timestamps']} timestamp(s) inválido(s) ou intervalo(s) com duração não positiva.")
    if quality_audit["overlap_evaluable"] and quality_audit["overlapping_intervals"]:
        warnings.append(f"Há {quality_audit['overlapping_intervals']} sobreposição(ões) temporal(is) entre intervalos da mesma sessão.")
    if not quality_audit["overlap_evaluable"]:
        warnings.append("Sobreposição temporal não avaliável: faltam início e fim explícitos nos intervalos.")
    if not quality_audit["observer_identification_available"]:
        warnings.append("Número de observadores não avaliável: identificador do observador não foi fornecido.")
    if not quality_audit["taxonomy_evaluable"]:
        warnings.append("Categorias fora da taxonomia não são integralmente avaliáveis sem marcador ou versão de taxonomia por registro.")
    if exposure["status"] == "unavailable":
        warnings.append(exposure["warning"])
    elif exposure["opportunities_without_occurrence"] == 0 and observed:
        warnings.append(
            "Todos os intervalos observados com duração possuem evento registrado; a base pode estar condicionada à ocorrência e não estima risco absoluto."
        )
    if int(analysis.get("c2_intenso") or 0) == 0:
        warnings.append("Não há episódios C2 no recorte; conclusões sobre gravidade intensa não possuem suporte empírico.")
    parsed_dates = pd.to_datetime(
        [item.get("data_hora") or item.get("timestamp") for item in record_rows if item.get("data_hora") or item.get("timestamp")],
        errors="coerce",
        utc=True,
    )
    valid_dates = [value for value in parsed_dates if not pd.isna(value)]
    period_days = ((max(valid_dates) - min(valid_dates)).days + 1) if valid_dates else None
    if period_days is not None and period_days < 28:
        warnings.append(f"O período cobre {period_days} dia(s); sazonalidade e estabilidade mensal não foram demonstradas.")
    warnings.append("Generalização para outros pacientes, profissionais, ambientes ou períodos permanece desconhecida.")
    if quality_audit["duplicate_identifiers"] or quality_audit["invalid_timestamps"] or quality_audit["overlapping_intervals"]:
        quality = "limitada" if quality == "adequada" else quality

    top_antecedent = top_value(antecedents)
    top_behavior = top_value(behaviors)
    top_consequence = top_value(consequences)
    session_by_interval = {
        str(item.get("intervalo_id") or item.get("interval_id")): str(item.get("session_id") or item.get("sessao_id") or "")
        for item in interval_rows
        if item.get("intervalo_id") or item.get("interval_id")
    }
    top_behavior_cluster_interval = None
    clustered_outcomes = []
    clustered_sessions = []
    for item in record_rows:
        interval_id = str(item.get("intervalo_id") or item.get("interval_id") or "")
        session_id = str(item.get("session_id") or item.get("sessao_id") or session_by_interval.get(interval_id) or "")
        if not session_id:
            continue
        clustered_outcomes.append(int(str(item.get("comportamento") or "") == str(top_behavior.get("nome") or "")))
        clustered_sessions.append(session_id)
    if len(set(clustered_sessions)) >= 2 and clustered_outcomes:
        top_behavior_cluster_interval = cluster_bootstrap_interval(clustered_outcomes, clustered_sessions)
    top_chain = (analysis.get("cadeias_completas") or [{}])[0]
    if observed:
        descriptive = (
            f"Foram analisados {observed} intervalos observados em {len(session_ids)} sessão(ões). "
            f"O comportamento mais frequente foi '{top_behavior['nome']}', o antecedente mais frequente foi "
            f"'{top_antecedent['nome']}' e a consequência mais frequente foi '{top_consequence['nome']}'."
        )
    else:
        descriptive = (
            "O volume ou a qualidade dos registros não é suficiente para produzir uma interpretação estável. "
            "Recomenda-se ampliar a coleta antes de utilizar estes padrões para decisões clínicas."
        )
    if top_chain.get("cadeia"):
        descriptive += (
            f" A cadeia A-B-C mais frequente foi '{top_chain['cadeia']}', observada "
            f"{int(top_chain.get('suporte') or 0)} vez(es); ela representa um padrão descritivo para revisão clínica."
        )

    return {
        "report_metadata": {
            "generated_at": datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).isoformat(),
            "extracted_at": datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).isoformat(),
            "logic_version": logic_version,
            "generated_by": generated_by,
            "instrument_version": ", ".join(versions) if versions else "Não disponível",
            "methodology_version": METHODOLOGY_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
            "pipeline_version": "abc-report-pipeline-v3",
            "dataset_version": dataset_hash[:12],
            "dataset_hash": dataset_hash,
            "analysis_run_hash": dataset_hash,
            "taxonomy_versions": quality_audit["taxonomy_versions"],
            "source_systems": quality_audit["source_systems"],
            "period_days": period_days,
        },
        "patient": {
            "display_name": "Paciente anonimizado" if anonymize_patient else patient_name,
            "anonymized": anonymize_patient,
        },
        "observation_summary": {
            "sessions": len(session_ids),
            "total_records": len(record_rows),
            "behavior_occurrences": sum(bool(row.get("comportamento")) for row in record_rows),
            "total_intervals": total_intervals,
            "observed_intervals": observed,
            "not_observed_intervals": not_observed,
            "invalid_intervals": invalid,
            "not_applicable_intervals": not_applicable,
            "coverage": min(1.0, max(0.0, coverage)),
            "observed_hours": observed_minutes / 60,
            "occurrences_per_observed_hour": exposure["occurrences_per_hour"],
            "opportunities_without_occurrence": exposure["opportunities_without_occurrence"],
            "occurrence_intervals": len(occurrence_interval_ids),
            "occurrence_percentage": min(1.0, len(occurrence_interval_ids) / observed) if observed else 0.0,
            "missing_percentage": min(1.0, max(0.0, missing_ratio)),
            "exposure": exposure,
        },
        "top_antecedents": [{"nome": name, "quantidade": count} for name, count in antecedents.most_common(10)],
        "top_behaviors": [{"nome": name, "quantidade": count} for name, count in behaviors.most_common(10)],
        "top_consequences": [{"nome": name, "quantidade": count} for name, count in consequences.most_common(10)],
        "most_frequent": {
            "antecedent": top_antecedent,
            "behavior": top_behavior,
            "consequence": top_consequence,
            "chain": top_chain or {},
        },
        "antecedent_behavior_associations": enrich_associations(analysis.get("antecedente_comportamento") or []),
        "behavior_consequence_associations": enrich_associations(analysis.get("comportamento_consequencia") or []),
        "behavior_chains": temporal_rows,
        "data_quality": {
            "status": quality,
            "status_scope": f"{quality} para análise descritiva",
            "predictive_readiness": "insuficiente sem alvo/horizonte e oportunidades positivas e negativas observáveis",
            "coverage": min(1.0, max(0.0, coverage)),
            "duplicates": quality_audit["duplicate_identifiers"] + quality_audit["duplicate_exact_rows"],
            "duplicate_identifiers": quality_audit["duplicate_identifiers"],
            "duplicate_exact_rows": quality_audit["duplicate_exact_rows"],
            "incomplete_times": quality_audit["missing_timestamps"],
            "invalid_timestamps": quality_audit["invalid_timestamps"],
            "overlapping_intervals": quality_audit["overlapping_intervals"],
            "overlap_evaluable": quality_audit["overlap_evaluable"],
            "outside_taxonomy": quality_audit["outside_taxonomy"],
            "taxonomy_evaluable": quality_audit["taxonomy_evaluable"],
            "observer_count": quality_audit["observer_count"],
            "observer_identification_available": quality_audit["observer_identification_available"],
            "missing_record_identifiers": quality_audit["missing_record_identifiers"],
            "environment_distribution": quality_audit["environments"],
            "retroactive_records": retroactive,
            "unreviewed_events": unreviewed_events,
            "missing_event_values": missing_events,
            "censored_chains": sum(item.get("validation_status") == "censored" for item in temporal_rows_source),
            "warnings": warnings,
        },
        "exposure_summary": exposure,
        "top_behavior_session_cluster_interval": top_behavior_cluster_interval,
        "descriptive_summary": descriptive,
        "analysis": analysis,
        "clinical_disclaimer": (
            "As estimativas são descritivas ou preditivas conforme indicado em cada seção. "
            "Associações não demonstram causalidade nem confirmam função comportamental. "
            "Os resultados não substituem avaliação funcional, julgamento clínico ou análise "
            "individualizada conduzida por profissional habilitado."
        ),
    }


def log_abc_report_generation(
    engine,
    *,
    patient_token: str,
    generated_by: str,
    filters: dict[str, Any],
    output_format: str,
    anonymized: bool,
    logic_version: str = "abc-print-summary-v1",
) -> None:
    """Registra metadados de acesso sem persistir o conteúdo clínico do relatório."""
    init_abc_tables(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO abc_report_audit_logs (
                    patient_token, generated_by, filters, output_format, anonymized, logic_version
                ) VALUES (
                    CAST(:patient_token AS UUID), :generated_by, CAST(:filters AS JSONB),
                    :output_format, :anonymized, :logic_version
                )
                """
            ),
            {
                "patient_token": patient_token,
                "generated_by": generated_by[:160],
                "filters": json.dumps(filters, ensure_ascii=False),
                "output_format": output_format[:20],
                "anonymized": anonymized,
                "logic_version": logic_version[:60],
            },
        )


def patient_excel_path(patient_name: str) -> Path:
    safe_name = _safe_file_stem(patient_name)
    return ABC_EXPORT_DIR / f"{safe_name}_abc_logs.xlsx"


def _excel_lock_for(output_path: Path) -> threading.Lock:
    key = str(output_path.resolve()).lower()
    with _ABC_EXCEL_LOCKS_GUARD:
        return _ABC_EXCEL_LOCKS.setdefault(key, threading.Lock())


def _is_valid_xlsx(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size == 0:
            return False
        with zipfile.ZipFile(path) as archive:
            return "[Content_Types].xml" in archive.namelist() and archive.testzip() is None
    except (OSError, zipfile.BadZipFile):
        return False


def _remove_excel_sidecars(output_path: Path) -> None:
    for sidecar in output_path.parent.glob(f"{output_path.name}.inspect*"):
        try:
            sidecar.unlink(missing_ok=True)
        except OSError:
            pass


def latest_patient_excel_path(patient_name: str) -> Path | None:
    safe_name = _safe_file_stem(patient_name)
    candidates = list(ABC_EXPORT_DIR.glob(f"{safe_name}_abc_logs*.xlsx"))
    valid = [path for path in candidates if _is_valid_xlsx(path)]
    return max(valid, key=lambda path: path.stat().st_mtime_ns) if valid else None


def generate_patient_excel(engine, *, patient_token: str, patient_name: str) -> Path:
    init_abc_tables(engine)
    if not ABC_EXCEL_BUILDER.exists():
        raise RuntimeError(f"Gerador Excel não encontrado em {ABC_EXCEL_BUILDER}.")
    if not (ABC_EXCEL_RUNTIME_DIR / "node_modules").exists():
        raise RuntimeError("Dependências do gerador Excel não estão vinculadas em tools/abc_excel_runtime/node_modules.")

    output_path = patient_excel_path(patient_name)
    ABC_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    input_dir = PROJECT_DIR / "storage" / "abc_excel_inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    lock = _excel_lock_for(output_path)

    with lock:
        _remove_excel_sidecars(output_path)
        input_path = input_dir / f"{_safe_file_stem(patient_name)}-{uuid.uuid4().hex}.json"
        payload = {
            "patientName": patient_name,
            "generatedAt": datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).isoformat(),
            "activeRecords": load_patient_records(engine, patient_token),
            "actionLogs": load_action_logs(engine, patient_token),
            "categories": list_categories(engine),
        }
        input_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        node_binary = _node_binary()
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        temporary_output: Path | None = None
        last_detail = "erro desconhecido"
        try:
            for _ in range(2):
                temporary_output = ABC_EXPORT_DIR / (
                    f"__building_{_safe_file_stem(patient_name)}_{uuid.uuid4().hex}.xlsx"
                )
                try:
                    completed = subprocess.run(
                        [str(node_binary), str(ABC_EXCEL_BUILDER), str(input_path), str(temporary_output)],
                        cwd=str(ABC_EXCEL_RUNTIME_DIR),
                        check=False,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=120,
                        creationflags=creation_flags,
                    )
                    last_detail = (completed.stderr or completed.stdout or last_detail)[-1200:]
                except (OSError, subprocess.TimeoutExpired) as exc:
                    last_detail = str(exc)[-1200:]

                if _is_valid_xlsx(temporary_output):
                    break
                _remove_excel_sidecars(temporary_output)
                temporary_output.unlink(missing_ok=True)
                temporary_output = None

            if temporary_output is None or not _is_valid_xlsx(temporary_output):
                raise RuntimeError(f"Falha ao gerar Excel ABC: {last_detail}")

            _remove_excel_sidecars(temporary_output)
            try:
                os.replace(temporary_output, output_path)
                return output_path
            except OSError:
                timestamp = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).strftime("%Y%m%d_%H%M%S")
                fallback = output_path.with_name(
                    f"{output_path.stem}_atualizado_{timestamp}_{uuid.uuid4().hex[:6]}.xlsx"
                )
                os.replace(temporary_output, fallback)
                return fallback
        finally:
            input_path.unlink(missing_ok=True)
            if temporary_output is not None:
                _remove_excel_sidecars(temporary_output)
                temporary_output.unlink(missing_ok=True)


def _node_binary() -> Path:
    configured = os.getenv("SELLAS_NODE_BINARY")
    bundled = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe"
    system_node = shutil.which("node")
    for candidate in (configured, str(bundled), system_node):
        if candidate and Path(candidate).exists():
            return Path(candidate)
    raise RuntimeError("Node.js não encontrado para gerar o Excel ABC.")


def _load_interval_record(conn, patient_token: str, interval_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        text(
            """
            SELECT
                s.id AS session_id,
                i.id AS intervalo_id,
                s.data_inicio AS data_hora,
                s.ambiente,
                MAX(CASE WHEN c.tipo = 'antecedente' AND e.ocorreu IS TRUE THEN c.codigo END) AS antecedente_codigo,
                MAX(CASE WHEN c.tipo = 'antecedente' AND e.ocorreu IS TRUE THEN COALESCE(c.nome_normalizado, c.nome) END) AS antecedente,
                MAX(CASE WHEN c.tipo = 'comportamento' AND e.ocorreu IS TRUE THEN c.codigo END) AS comportamento_codigo,
                MAX(CASE WHEN c.tipo = 'comportamento' AND e.ocorreu IS TRUE THEN COALESCE(c.nome_normalizado, c.nome) END) AS comportamento,
                MAX(CASE WHEN c.tipo = 'comportamento' AND e.ocorreu IS TRUE THEN e.intensidade END) AS intensidade,
                BOOL_OR(c.tipo = 'comportamento' AND e.ocorreu IS TRUE AND COALESCE(e.causou_lesao, FALSE)) AS causou_lesao,
                BOOL_OR(c.tipo = 'comportamento' AND e.ocorreu IS TRUE AND COALESCE(e.houve_sangramento, FALSE)) AS houve_sangramento,
                BOOL_OR(c.tipo = 'comportamento' AND e.ocorreu IS TRUE AND COALESCE(e.direcionado_ponto_vital, FALSE)) AS direcionado_ponto_vital,
                MAX(CASE WHEN c.tipo = 'comportamento' AND e.ocorreu IS TRUE THEN COALESCE(e.funcao_hipotese_normalizada, e.funcao_hipotese) END) AS funcao,
                MAX(CASE WHEN c.tipo = 'consequencia' AND e.ocorreu IS TRUE THEN c.codigo END) AS consequencia_codigo,
                MAX(CASE WHEN c.tipo = 'consequencia' AND e.ocorreu IS TRUE THEN COALESCE(c.nome_normalizado, c.nome) END) AS consequencia
            FROM abc_sessions s
            JOIN abc_intervals i ON i.sessao_id = s.id
            JOIN abc_interval_events e ON e.intervalo_id = i.id
            JOIN abc_categories c ON c.id = e.categoria_id
            WHERE s.patient_token = CAST(:patient_token AS UUID)
              AND i.id = CAST(:interval_id AS UUID)
              AND i.status_observacao = 'observed'
            GROUP BY s.id, i.id, s.data_inicio, s.ambiente
            """
        ),
        {"patient_token": patient_token, "interval_id": interval_id},
    ).mappings().first()
    if not row:
        return None
    item = _serialize_row(row)
    item["data_hora"] = row["data_hora"].astimezone(ZoneInfo(DEFAULT_TIMEZONE)).isoformat()
    return _enrich_record(item)


def _write_action_log(
    conn,
    *,
    patient_token: str,
    action: str,
    snapshot: dict[str, Any],
    interval_id: str | None = None,
    category_id: str | None = None,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO abc_action_logs (
                patient_token, acao, intervalo_id, categoria_id, snapshot
            ) VALUES (
                CAST(:patient_token AS UUID), :acao,
                CAST(:intervalo_id AS UUID), CAST(:categoria_id AS UUID),
                CAST(:snapshot AS JSONB)
            )
            """
        ),
        {
            "patient_token": patient_token,
            "acao": action,
            "intervalo_id": interval_id,
            "categoria_id": category_id,
            "snapshot": json.dumps(snapshot, ensure_ascii=False),
        },
    )


def _category_code(name: str, category_type: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(char for char in normalized if not unicodedata.combining(char))
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ascii_name).strip("_").upper() or "OPCAO"
    prefix = {"antecedente": "ANT", "comportamento": "COM", "consequencia": "CON"}[category_type]
    return f"{prefix}_{slug}"[:80]


def _safe_file_stem(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value))
    ascii_name = "".join(char for char in normalized if not unicodedata.combining(char))
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_name).strip("_-")
    return safe[:100] or "paciente"


def _serialize_row(row) -> dict[str, Any]:
    result = dict(row)
    for key, value in list(result.items()):
        if isinstance(value, uuid.UUID):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
    return result


def _enrich_record(item: dict[str, Any]) -> dict[str, Any]:
    severity = severity_details(item.get("intensidade"))
    item.update(
        {
            "classificacao": severity["codigo"],
            "classificacao_rotulo": severity["rotulo"],
            "indice_perigo": severity["indice_perigo"],
            "funcao": item.get("funcao") or "Não informada",
            "causou_lesao": bool(item.get("causou_lesao")),
            "houve_sangramento": bool(item.get("houve_sangramento")),
            "direcionado_ponto_vital": bool(item.get("direcionado_ponto_vital")),
        }
    )
    return item


def _smoothed_probability(successes: int, trials: int) -> float:
    """Beta(1, 1) posterior mean, equivalent to Laplace smoothing."""
    return beta_posterior_interval(int(successes), int(trials))["estimate"]


def _wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Compatibilidade legada; use apenas para proporções binomiais brutas."""
    if trials <= 0:
        return 0.0, 1.0
    interval = wilson_interval(int(successes), int(trials))
    return interval["lower"], interval["upper"]


def predict_abc_behavior(
    records: Iterable[dict[str, Any]],
    *,
    behavior: str,
    antecedent: str | None = None,
    environment: str | None = None,
    classification: str | None = None,
    function: str | None = None,
    alpha: float = 0.30,
    recent_window: int = 20,
) -> dict[str, Any]:
    rows = sorted(normalize_abc_records(records), key=lambda item: str(item.get("data_hora", "")))
    behavior = normalize_clinical_value(behavior, "comportamento")["normalized_value"]
    antecedent = normalize_clinical_value(antecedent, "antecedente")["normalized_value"] if antecedent else None
    environment = normalize_clinical_value(environment, "ambiente")["normalized_value"] if environment else None
    if not rows:
        return {
            "analysis_mode": "descriptive",
            "estimand": "relative_distribution_among_recorded_episodes",
            "comportamento": behavior,
            "antecedente": antecedent,
            "ambiente": environment,
            "classificacao": classification,
            "funcao": function,
            "estimativa_descritiva": None,
            "probabilidade_prevista": None,
            "qualidade_evidencia": "insuficiente",
            "amostra_contexto": 0,
            "aviso": "Sem registros semelhantes; não foi calculada probabilidade preditiva.",
        }

    def matching_rows(*, use_antecedent: bool, use_environment: bool) -> list[dict[str, Any]]:
        return [
            row
            for row in rows
            if (not use_antecedent or row.get("antecedente") == antecedent)
            and (not use_environment or row.get("ambiente") == environment)
        ]

    def behavior_count(items: list[dict[str, Any]]) -> int:
        return sum(1 for row in items if row.get("comportamento") == behavior)

    baseline_rows = rows
    antecedent_rows = matching_rows(use_antecedent=bool(antecedent), use_environment=False)
    environment_rows = matching_rows(use_antecedent=False, use_environment=bool(environment))
    context_rows = matching_rows(use_antecedent=bool(antecedent), use_environment=bool(environment))

    baseline_probability = behavior_count(baseline_rows) / len(baseline_rows)
    antecedent_probability = behavior_count(antecedent_rows) / len(antecedent_rows) if antecedent_rows else None
    environment_probability = behavior_count(environment_rows) / len(environment_rows) if environment_rows else None
    context_probability = behavior_count(context_rows) / len(context_rows) if context_rows else None

    recent = rows[-max(1, int(recent_window)) :]
    recent_probability = baseline_probability
    alpha = max(0.01, min(1.0, float(alpha)))
    for row in recent:
        occurred = 1.0 if row.get("comportamento") == behavior else 0.0
        recent_probability = alpha * occurred + (1 - alpha) * recent_probability

    context_successes = behavior_count(context_rows)
    descriptive = descriptive_behavior_estimate(
        context_rows,
        behavior=behavior,
        method="frequentist",
    )
    behavior_probability = descriptive.get("estimate")
    interval_low, interval_high = descriptive.get("lower"), descriptive.get("upper")

    normalized_classification = str(classification).strip().upper() if classification else None
    if normalized_classification and normalized_classification not in {"C1", "C2"}:
        raise ValueError("A classificação da previsão deve ser C1 ou C2.")
    clean_function = normalize_clinical_value(function, "funcao")["normalized_value"] if function else None
    behavior_rows = [row for row in rows if row.get("comportamento") == behavior]
    behavior_context_rows = [row for row in context_rows if row.get("comportamento") == behavior]
    factor_basis = behavior_context_rows or behavior_rows
    classification_probability = None
    if normalized_classification:
        classification_probability = (
            sum(1 for row in factor_basis if row.get("classificacao") == normalized_classification),
            len(factor_basis),
        )
        classification_probability = classification_probability[0] / classification_probability[1] if classification_probability[1] else None
    function_probability = None
    if clean_function:
        function_probability = (
            sum(1 for row in factor_basis if row.get("funcao") == clean_function),
            len(factor_basis),
        )
        function_probability = function_probability[0] / function_probability[1] if function_probability[1] else None
    session_count = len({str(row.get("session_id") or row.get("sessao_id")) for row in context_rows if row.get("session_id") or row.get("sessao_id")})
    day_count = len({str(row.get("data_hora", ""))[:10] for row in context_rows if row.get("data_hora")})
    clustered_interval = None
    clustered_rows = [
        row for row in context_rows if row.get("session_id") or row.get("sessao_id")
    ]
    if len({str(row.get("session_id") or row.get("sessao_id")) for row in clustered_rows}) >= 2:
        clustered_interval = cluster_bootstrap_interval(
            [int(row.get("comportamento") == behavior) for row in clustered_rows],
            [str(row.get("session_id") or row.get("sessao_id")) for row in clustered_rows],
        )
    evidence = compute_evidence_quality(
        sample_size=len(context_rows),
        session_count=session_count,
        day_count=day_count,
        missing_count=sum(not row.get("classificacao") or row.get("classificacao") == "NC" for row in context_rows),
        positive_count=context_successes,
        negative_count=0,
    ).to_dict()

    return {
        "analysis_mode": "descriptive",
        "estimand": "relative_distribution_among_recorded_episodes",
        "absolute_risk_available": False,
        "comportamento": behavior,
        "antecedente": antecedent,
        "ambiente": environment,
        "classificacao": normalized_classification,
        "funcao": clean_function,
        "estimativa_descritiva": behavior_probability,
        # Alias de migração para clientes antigos. A UI e os relatórios não devem chamá-lo de previsão.
        "probabilidade_prevista": behavior_probability,
        "legacy_probability_field_deprecated": True,
        "probabilidade_comportamento": behavior_probability,
        "probabilidade_classificacao": classification_probability,
        "probabilidade_funcao": function_probability,
        "probabilidade_baseline": baseline_probability,
        "probabilidade_antecedente": antecedent_probability,
        "probabilidade_ambiente": environment_probability,
        "probabilidade_contexto": context_probability,
        "probabilidade_recente": recent_probability,
        "intervalo_wilson_inferior": interval_low,
        "intervalo_wilson_superior": interval_high,
        "amostra_total": len(rows),
        "amostra_contexto": len(context_rows),
        "sucessos_contexto": context_successes,
        "numerador": context_successes,
        "denominador": len(context_rows),
        "metodo": descriptive.get("method"),
        "nivel_intervalo": descriptive.get("level"),
        "intervalo_agrupado_sessao": clustered_interval,
        "qualidade_evidencia": evidence["overall_quality"],
        "evidence_quality": evidence,
        "alpha": alpha,
        "janela_recente": len(recent),
        "amostra_fatores": len(factor_basis),
        "componentes_descritivos": [
            {"nome": "prevalência histórica entre episódios", "estimativa": baseline_probability, "n": len(baseline_rows)},
            {"nome": "prevalência recente entre episódios", "estimativa": recent_probability, "n": len(recent)},
            {"nome": "antecedente", "estimativa": antecedent_probability, "n": len(antecedent_rows)},
            {"nome": "ambiente", "estimativa": environment_probability, "n": len(environment_rows)},
            {"nome": "contexto conjunto", "estimativa": context_probability, "n": len(context_rows)},
        ],
        "componentes": [],
        "model_version": None,
        "feature_version": None,
        "methodology_version": METHODOLOGY_VERSION,
        "aviso": descriptive["selection_bias_warning"],
    }


def build_abc_analysis(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = sorted(normalize_abc_records(records), key=lambda item: str(item.get("data_hora", "")))
    total = len(rows)
    session_ids = {
        str(row.get("session_id") or row.get("sessao_id") or row.get("atendimento_id"))
        for row in rows
        if row.get("session_id") or row.get("sessao_id") or row.get("atendimento_id")
    }
    behavior_by_day = Counter()
    environment_counts = Counter()
    antecedent_counts = Counter()
    behavior_counts = Counter()
    consequence_counts = Counter()
    antecedent_behavior = Counter()
    behavior_consequence = Counter()
    full_chain_counts = Counter()
    chain_environment_counts = Counter()
    environment_antecedent_counts = Counter()
    environment_antecedent_behavior_counts = Counter()
    severity_counts = Counter()
    function_counts = Counter()
    chain_severity_counts = Counter()
    chain_function_counts = Counter()
    environment_chain_severity_counts = Counter()
    environment_chain_function_counts = Counter()

    for row in rows:
        timestamp = datetime.fromisoformat(str(row["data_hora"]))
        day = timestamp.date().isoformat()
        antecedent = row.get("antecedente") or "Não informado"
        behavior = row.get("comportamento") or "Não informado"
        consequence = row.get("consequencia") or "Não informado"
        environment = row.get("ambiente") or "Não informado"
        classification = row.get("classificacao") or "NC"
        function_name = row.get("funcao") or "Não informada"
        behavior_by_day[(day, behavior)] += 1
        environment_counts[environment] += 1
        antecedent_counts[antecedent] += 1
        behavior_counts[behavior] += 1
        consequence_counts[consequence] += 1
        antecedent_behavior[(antecedent, behavior)] += 1
        behavior_consequence[(behavior, consequence)] += 1
        full_chain_counts[(antecedent, behavior, consequence)] += 1
        chain_environment_counts[(environment, antecedent, behavior, consequence)] += 1
        environment_antecedent_counts[(environment, antecedent)] += 1
        environment_antecedent_behavior_counts[(environment, antecedent, behavior)] += 1
        severity_counts[classification] += 1
        function_counts[function_name] += 1
        chain_severity_counts[(antecedent, behavior, consequence, classification)] += 1
        chain_function_counts[(antecedent, behavior, consequence, function_name)] += 1
        environment_chain_severity_counts[
            (environment, antecedent, behavior, consequence, classification)
        ] += 1
        environment_chain_function_counts[
            (environment, antecedent, behavior, consequence, function_name)
        ] += 1

    def association_rows(pair_counts: Counter, left_counts: Counter, right_counts: Counter, left_key: str, right_key: str) -> list[dict[str, Any]]:
        result = []
        for (left, right), support in pair_counts.items():
            conditional = support / left_counts[left]
            baseline = right_counts[right] / total if total else 0.0
            result.append(
                {
                    left_key: left,
                    right_key: right,
                    "suporte": support,
                    "total_exposto": left_counts[left],
                    "probabilidade_condicional": conditional,
                    "probabilidade_baseline": baseline,
                    "lift": conditional / baseline if baseline else None,
                }
            )
        return sorted(result, key=lambda item: (item["probabilidade_condicional"], item["suporte"]), reverse=True)

    chain_rows = []
    for (antecedent, behavior, consequence), support in full_chain_counts.items():
        chain_key = (antecedent, behavior, consequence)
        exposed_antecedent = antecedent_counts[antecedent]
        exposed_pair = antecedent_behavior[(antecedent, behavior)]
        p_antecedent = antecedent_counts[antecedent] / total if total else 0.0
        p_behavior_given_antecedent = exposed_pair / exposed_antecedent
        p_consequence_given_pair = support / exposed_pair
        joint_probability = support / total if total else 0.0
        independent_baseline = (
            (antecedent_counts[antecedent] / total)
            * (behavior_counts[behavior] / total)
            * (consequence_counts[consequence] / total)
            if total
            else 0.0
        )
        smoothed_chain = (
            _smoothed_probability(antecedent_counts[antecedent], total)
            * _smoothed_probability(exposed_pair, exposed_antecedent)
            * _smoothed_probability(support, exposed_pair)
        )
        c1_count = chain_severity_counts[(*chain_key, "C1")]
        c2_count = chain_severity_counts[(*chain_key, "C2")]
        severity = severity_summary(
            ["C1"] * c1_count + ["C2"] * c2_count + [None] * (support - c1_count - c2_count),
            occurrence_frequency=joint_probability,
            config=SeverityConfig(),
        )
        classified_count = severity["classified_count"]
        danger_index = severity["mean_configured_severity_weight"]
        joint_interval = wilson_interval(support, total) if total else None
        chain_functions = {
            function_name: count
            for (chain_antecedent, chain_behavior, chain_consequence, function_name), count in chain_function_counts.items()
            if (chain_antecedent, chain_behavior, chain_consequence) == chain_key
        }
        predominant_function = max(chain_functions, key=chain_functions.get) if chain_functions else "Não informada"
        chain_rows.append(
            {
                "cadeia": f"{antecedent} -> {behavior} -> {consequence}",
                "antecedente": antecedent,
                "comportamento": behavior,
                "consequencia": consequence,
                "suporte": support,
                "probabilidade_conjunta": joint_probability,
                "numerador_conjunto": support,
                "denominador_conjunto": total,
                "metodo_intervalo": "wilson",
                "intervalo_inferior": joint_interval["lower"] if joint_interval else None,
                "intervalo_superior": joint_interval["upper"] if joint_interval else None,
                "probabilidade_antecedente": p_antecedent,
                "probabilidade_comportamento_dado_antecedente": p_behavior_given_antecedent,
                "probabilidade_consequencia_dada_cadeia_ab": p_consequence_given_pair,
                "probabilidade_cadeia_fatorada": p_antecedent
                * p_behavior_given_antecedent
                * p_consequence_given_pair,
                "probabilidade_suavizada": smoothed_chain,
                "lift_conjunto": joint_probability / independent_baseline if independent_baseline else None,
                "c1_leve": c1_count,
                "c2_intenso": c2_count,
                "nao_classificado": support - classified_count,
                "proporcao_c2": c2_count / classified_count if classified_count else None,
                "peso_medio_gravidade": danger_index,
                "indice_perigo": danger_index,  # alias temporário de compatibilidade
                "indice_risco_exploratorio": severity["exploratory_risk_index"],
                "indice_risco": severity["exploratory_risk_index"],
                "mensagem_gravidade": severity["message"],
                "severity_config": severity["weight_config"],
                "sessoes_unicas": len({str(row.get("session_id") or row.get("sessao_id")) for row in rows if (row.get("antecedente"), row.get("comportamento"), row.get("consequencia")) == chain_key and (row.get("session_id") or row.get("sessao_id"))}),
                "dias_unicos": len({str(row.get("data_hora"))[:10] for row in rows if (row.get("antecedente"), row.get("comportamento"), row.get("consequencia")) == chain_key}),
                "evidence_status": "adequada" if support >= 8 else "evidência insuficiente",
                "funcao_predominante": predominant_function,
            }
        )
    chain_rows.sort(key=lambda item: (item["probabilidade_conjunta"], item["suporte"]), reverse=True)

    chains_by_environment = []
    for (environment, antecedent, behavior, consequence), support in chain_environment_counts.items():
        environment_chain_key = (environment, antecedent, behavior, consequence)
        exposed_antecedent = environment_antecedent_counts[(environment, antecedent)]
        exposed_pair = environment_antecedent_behavior_counts[(environment, antecedent, behavior)]
        c1_count = environment_chain_severity_counts[(*environment_chain_key, "C1")]
        c2_count = environment_chain_severity_counts[(*environment_chain_key, "C2")]
        classified_count = c1_count + c2_count
        environment_probability = support / environment_counts[environment]
        severity = severity_summary(
            ["C1"] * c1_count + ["C2"] * c2_count + [None] * (support - c1_count - c2_count),
            occurrence_frequency=environment_probability,
            config=SeverityConfig(),
        )
        danger_index = severity["mean_configured_severity_weight"]
        environment_functions = {
            function_name: count
            for (
                function_environment,
                function_antecedent,
                function_behavior,
                function_consequence,
                function_name,
            ), count in environment_chain_function_counts.items()
            if (
                function_environment,
                function_antecedent,
                function_behavior,
                function_consequence,
            )
            == environment_chain_key
        }
        predominant_function = max(environment_functions, key=environment_functions.get) if environment_functions else "Não informada"
        chains_by_environment.append(
            {
                "ambiente": environment,
                "cadeia": f"{antecedent} -> {behavior} -> {consequence}",
                "antecedente": antecedent,
                "comportamento": behavior,
                "consequencia": consequence,
                "suporte": support,
                "total_ambiente": environment_counts[environment],
                "probabilidade_no_ambiente": environment_probability,
                "numerador_no_ambiente": support,
                "denominador_no_ambiente": environment_counts[environment],
                "probabilidade_comportamento_dado_antecedente": exposed_pair / exposed_antecedent,
                "probabilidade_consequencia_dada_cadeia_ab": support / exposed_pair,
                "c1_leve": c1_count,
                "c2_intenso": c2_count,
                "nao_classificado": support - classified_count,
                "proporcao_c2": c2_count / classified_count if classified_count else None,
                "peso_medio_gravidade": danger_index,
                "indice_perigo": danger_index,
                "indice_risco_exploratorio": severity["exploratory_risk_index"],
                "indice_risco": severity["exploratory_risk_index"],
                "mensagem_gravidade": severity["message"],
                "funcao_predominante": predominant_function,
            }
        )
    chains_by_environment.sort(
        key=lambda item: (item["ambiente"], item["probabilidade_no_ambiente"], item["suporte"]),
        reverse=True,
    )

    risk_map = [
        {
            "ambiente": item["ambiente"],
            "cadeia": item["cadeia"],
            "comportamento": item["comportamento"],
            "funcao": item["funcao_predominante"],
            "suporte": item["suporte"],
            "probabilidade_ocorrencia": item["probabilidade_no_ambiente"],
            "frequencia_observada": item["probabilidade_no_ambiente"],
            "peso_medio_gravidade": item["peso_medio_gravidade"],
            "indice_perigo": item["indice_perigo"],
            "indice_risco": item["indice_risco"],
            "c1_leve": item["c1_leve"],
            "c2_intenso": item["c2_intenso"],
            "nao_classificado": item["nao_classificado"],
            "classificacao_predominante": (
                "C2" if item["c2_intenso"] > item["c1_leve"] else "C1"
                if item["c1_leve"] > 0
                else "NC"
            ),
        }
        for item in chains_by_environment
    ]

    timeline = []
    for row in rows[-500:]:
        timestamp = datetime.fromisoformat(str(row["data_hora"]))
        timeline.append(
            {
                "data_hora": timestamp.isoformat(),
                "data": timestamp.date().isoformat(),
                "hora": timestamp.strftime("%H:%M:%S"),
                "ambiente": row.get("ambiente") or "Nao informado",
                "classificacao": row.get("classificacao_rotulo") or "Não classificado",
                "funcao": row.get("funcao") or "Não informada",
                "peso_medio_gravidade": severity_details(row.get("intensidade")).get("indice_perigo"),
                "indice_perigo": severity_details(row.get("intensidade")).get("indice_perigo"),
                "cadeia": (
                    f"{row.get('antecedente') or 'Não informado'} -> "
                    f"{row.get('comportamento') or 'Não informado'} -> "
                    f"{row.get('consequencia') or 'Não informado'}"
                ),
            }
        )

    records_by_addition = sorted(
        rows,
        key=lambda item: str(item.get("criado_em") or item.get("data_hora") or ""),
        reverse=True,
    )
    management_records = []
    for row in records_by_addition:
        timestamp = datetime.fromisoformat(str(row["data_hora"]))
        management_records.append(
            {
                key: value
                for key, value in {
                    **row,
                    "data": timestamp.date().isoformat(),
                    "hora": timestamp.strftime("%H:%M:%S"),
                }.items()
                if key != "normalization_audit"
            }
        )
    recent = management_records[:50]

    day_count = len({str(row.get("data_hora"))[:10] for row in rows if row.get("data_hora")})
    evidence = compute_evidence_quality(
        sample_size=total,
        session_count=len(session_ids),
        day_count=day_count,
        missing_count=severity_counts.get("NC", 0),
        positive_count=total,
        negative_count=0,
    ).to_dict()
    return {
        "analysis_mode": "descriptive",
        "methodology_version": METHODOLOGY_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "evidence_rule_version": EVIDENCE_RULE_VERSION,
        "total_registros": total,
        "sessoes_unicas": len(session_ids),
        "dias_unicos": day_count,
        "comportamentos_distintos": len(behavior_counts),
        "ambientes_distintos": len(environment_counts),
        "c1_leve": severity_counts.get("C1", 0),
        "c2_intenso": severity_counts.get("C2", 0),
        "nao_classificados": severity_counts.get("NC", 0),
        "serie_temporal": [
            {"data": day, "comportamento": behavior, "quantidade": count}
            for (day, behavior), count in sorted(behavior_by_day.items())
        ],
        "por_ambiente": [
            {"ambiente": environment, "quantidade": count}
            for environment, count in environment_counts.most_common()
        ],
        "antecedente_comportamento": association_rows(
            antecedent_behavior, antecedent_counts, behavior_counts, "antecedente", "comportamento"
        ),
        "comportamento_consequencia": association_rows(
            behavior_consequence, behavior_counts, consequence_counts, "comportamento", "consequencia"
        ),
        "cadeias_completas": chain_rows,
        "cadeias_por_ambiente": chains_by_environment,
        "mapa_risco": risk_map,
        "heatmap_cadeias_ambiente": [
            {
                "ambiente": item["ambiente"],
                "cadeia": item["cadeia"],
                "probabilidade": item["probabilidade_no_ambiente"],
                "suporte": item["suporte"],
            }
            for item in chains_by_environment
        ],
        "linha_tempo": timeline,
        "antecedentes_observados": sorted(antecedent_counts),
        "comportamentos_observados": sorted(behavior_counts),
        "consequencias_observadas": sorted(consequence_counts),
        "ambientes_observados": sorted(environment_counts),
        "funcoes_observadas": sorted(function_counts),
        "classificacoes_observadas": sorted(severity_counts),
        "por_classificacao": [
            {"classificacao": classification, "quantidade": count}
            for classification, count in severity_counts.most_common()
        ],
        "por_funcao": [
            {"funcao": function_name, "quantidade": count}
            for function_name, count in function_counts.most_common()
        ],
        "registros_recentes": recent,
        "registros_para_gestao": management_records,
        "evidence_quality": evidence,
        "aviso": (
            "A base de episódios não contém todas as oportunidades negativas observáveis. "
            "As frequências e associações são descritivas; não estimam risco absoluto nem confirmam causa ou função."
        ),
    }
