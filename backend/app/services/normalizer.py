from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from backend.app.services.anonymizer import anonymize_record


CANONICAL_FIELDS = {
    "patient_id": ["patient_id", "paciente_id", "client_id"],
    "patient_name": ["patient_name", "paciente_nome", "client_name"],
    "session_id": ["session_id", "sessao_id", "appointment_id"],
    "date": ["date", "data", "session_date"],
    "start_time": ["start_time", "hora_inicio", "started_at"],
    "end_time": ["end_time", "hora_fim", "ended_at"],
    "behavior_id": ["behavior_id", "comportamento_id"],
    "behavior_name": ["behavior_name", "comportamento", "behavior"],
    "behavior_category": ["behavior_category", "categoria"],
    "frequency": ["frequency", "frequencia", "count", "ocorrencias"],
    "duration": ["duration", "duracao", "duration_seconds"],
    "intensity": ["intensity", "intensidade"],
    "antecedent": ["antecedent", "antecedente"],
    "consequence": ["consequence", "consequencia"],
    "hypothesized_function": ["hypothesized_function", "funcao_hipotetizada", "function"],
    "environment": ["environment", "ambiente"],
    "therapist_id": ["therapist_id", "aplicador_id", "professional_id"],
    "therapist_name": ["therapist_name", "terapeuta_nome", "professional_name"],
    "intervention_plan_id": ["intervention_plan_id", "plano_intervencao_id"],
    "strategies_used": ["strategies_used", "estrategias_utilizadas"],
    "prompt_level": ["prompt_level", "nivel_prompt"],
    "independence_score": ["independence_score", "indice_independencia"],
    "notes": ["notes", "observacoes", "anotacoes"],
    "authored_at": ["authored_at", "note_authored_at", "data_anotacao", "created_at", "criado_em"],
    "recorded_at": ["recorded_at", "created_at", "criado_em"],
    "effective_from": ["effective_from", "valid_from", "vigente_desde", "date", "data"],
    "effective_to": ["effective_to", "valid_to", "vigente_ate"],
    "note_scope": ["note_scope", "escopo_nota"],
    "clinical_plan_ref": ["clinical_plan_ref", "intervention_plan_id", "plano_intervencao_id"],
    "created_at": ["created_at", "criado_em"],
    "updated_at": ["updated_at", "atualizado_em"],
}


def _first_present(raw: dict[str, Any], names: list[str]) -> Any:
    for name in names:
        if name in raw:
            return raw[name]
    return None


def normalize_record(raw: dict[str, Any], field_map: dict[str, str] | None = None) -> dict[str, Any]:
    """Map a bHave/raw API record into the internal schema."""
    field_map = field_map or {}
    normalized: dict[str, Any] = {}
    for canonical, aliases in CANONICAL_FIELDS.items():
        explicit = field_map.get(canonical)
        normalized[canonical] = raw.get(explicit) if explicit else _first_present(raw, aliases)

    normalized["date"] = _normalize_date(normalized.get("date"))
    normalized["authored_at"] = _normalize_datetime(normalized.get("authored_at"))
    normalized["recorded_at"] = _normalize_datetime(normalized.get("recorded_at"))
    normalized["effective_from"] = _normalize_datetime(normalized.get("effective_from")) or normalized["date"]
    normalized["effective_to"] = _normalize_datetime(normalized.get("effective_to"))
    normalized["note_scope"] = normalized.get("note_scope") or "unknown"
    normalized["frequency"] = _to_float(normalized.get("frequency"), default=0.0)
    normalized["duration"] = _to_float(normalized.get("duration"), default=0.0)
    normalized["intensity"] = _to_float(normalized.get("intensity"), default=0.0)
    normalized["prompt_level"] = _to_float(normalized.get("prompt_level"), default=0.0)
    normalized["independence_score"] = _to_float(normalized.get("independence_score"), default=0.0)
    normalized["strategies_used"] = _normalize_list(normalized.get("strategies_used"))
    return anonymize_record(normalized)


def normalize_records(records: list[dict[str, Any]], field_map: dict[str, str] | None = None) -> list[dict[str, Any]]:
    return [normalize_record(record, field_map=field_map) for record in records]


def records_to_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=list(CANONICAL_FIELDS))
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for column in ["frequency", "duration", "intensity", "prompt_level", "independence_score"]:
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)
    return df


def _normalize_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    parsed = pd.to_datetime(text, errors="coerce", format="%Y-%m-%d")
    if pd.isna(parsed):
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def _normalize_datetime(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    if "/" in text:
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    else:
        parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.isoformat()


def _to_float(value: Any, default: float) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(value)]
