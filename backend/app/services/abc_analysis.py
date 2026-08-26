from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

OBSERVED_STATUSES = {"observed"}

DEFAULT_CATEGORIES = [
    {"codigo": "PEDIDO_NEGADO", "nome": "Pedido negado", "tipo": "antecedente", "definicao_operacional": "Acesso ou pedido negado."},
    {"codigo": "DEMANDA", "nome": "Demanda apresentada", "tipo": "antecedente", "definicao_operacional": "Instrucao ou tarefa apresentada."},
    {"codigo": "AGRESSAO_FISICA", "nome": "Agressao fisica", "tipo": "comportamento", "definicao_operacional": "Contato fisico agressivo observavel."},
    {"codigo": "CHORO", "nome": "Choro", "tipo": "comportamento", "definicao_operacional": "Choro audivel ou visivel."},
    {"codigo": "PAUSA", "nome": "Pausa", "tipo": "consequencia", "definicao_operacional": "Interrupcao temporaria da demanda."},
    {"codigo": "ATENCAO", "nome": "Atencao social", "tipo": "consequencia", "definicao_operacional": "Atencao social contingente registrada."},
]


def floor_timestamp_to_interval(timestamp: datetime, interval_minutes: int = 5) -> datetime:
    if not isinstance(interval_minutes, int) or interval_minutes <= 0:
        raise ValueError("interval_minutes deve ser um inteiro positivo")
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    utc_timestamp = timestamp.astimezone(timezone.utc)
    interval_seconds = interval_minutes * 60
    floored_seconds = math.floor(utc_timestamp.timestamp() / interval_seconds) * interval_seconds
    return datetime.fromtimestamp(floored_seconds, tz=timezone.utc)


def ensure_default_categories(categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if categories:
        return categories
    now = _now()
    return [
        {
            "id": _id("cat"),
            "versao": 1,
            "ativa": True,
            "service_id": None,
            "organization_id": None,
            "criado_em": now,
            **item,
        }
        for item in DEFAULT_CATEGORIES
    ]


def create_instrument(payload: dict[str, Any], instruments: list[dict[str, Any]]) -> dict[str, Any]:
    item = {"id": _id("inst"), "criado_em": _now(), **payload}
    instruments.append(item)
    return item


def create_category(payload: dict[str, Any], categories: list[dict[str, Any]]) -> dict[str, Any]:
    for category in categories:
        if category.get("codigo") == payload.get("codigo") and int(category.get("versao", 1)) == int(payload.get("versao", 1)):
            raise ValueError("Categoria ABC duplicada para codigo e versao.")
    item = {"id": _id("cat"), "criado_em": _now(), **payload}
    categories.append(item)
    return item


def create_session(payload: dict[str, Any], sessions: list[dict[str, Any]]) -> dict[str, Any]:
    start = _parse_dt(payload.get("data_inicio"))
    end = _parse_dt(payload.get("data_fim"))
    if end <= start:
        raise ValueError("data_fim deve ser posterior a data_inicio.")
    item = {
        "id": _id("sess"),
        "data_inicio": start.isoformat(),
        "data_fim": end.isoformat(),
        "criado_em": _now(),
        **{k: v for k, v in payload.items() if k not in {"data_inicio", "data_fim"}},
    }
    sessions.append(item)
    return item


def create_interval(payload: dict[str, Any], session_id: str, sessions: list[dict[str, Any]], intervals: list[dict[str, Any]]) -> dict[str, Any]:
    if not any(item.get("id") == session_id for item in sessions):
        raise ValueError("Sessao ABC nao encontrada.")
    interval_minutes = int(payload.get("interval_minutes") or 5)
    start = floor_timestamp_to_interval(_parse_dt(payload.get("inicio")), interval_minutes)
    end = _parse_dt(payload.get("fim")) if payload.get("fim") else start + timedelta(minutes=interval_minutes)
    if end <= start:
        raise ValueError("fim deve ser posterior ao inicio.")
    if any(item.get("sessao_id") == session_id and item.get("inicio") == start.isoformat() for item in intervals):
        raise ValueError("Intervalo ABC duplicado para sessao e inicio.")
    item = {
        "id": _id("int"),
        "sessao_id": session_id,
        "inicio": start.isoformat(),
        "fim": end.astimezone(timezone.utc).isoformat(),
        "duracao_planejada_minutos": interval_minutes,
        "criado_em": _now(),
        **{k: v for k, v in payload.items() if k not in {"inicio", "fim"}},
    }
    intervals.append(item)
    return item


def create_event(payload: dict[str, Any], interval_id: str, intervals: list[dict[str, Any]], categories: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    if not any(item.get("id") == interval_id for item in intervals):
        raise ValueError("Intervalo ABC nao encontrado.")
    category = resolve_category(payload, categories)
    if any(item.get("intervalo_id") == interval_id and item.get("categoria_id") == category["id"] for item in events):
        raise ValueError("Categoria duplicada no mesmo intervalo.")
    item = {
        **payload,
        "id": _id("evt"),
        "intervalo_id": interval_id,
        "categoria_id": category["id"],
        "categoria_codigo": category["codigo"],
        "categoria_tipo": category["tipo"],
        "criado_em": _now(),
    }
    events.append(item)
    return item


def resolve_category(payload: dict[str, Any], categories: list[dict[str, Any]]) -> dict[str, Any]:
    category_id = payload.get("categoria_id")
    category_code = payload.get("categoria_codigo")
    for category in categories:
        if category_id and category.get("id") == category_id:
            return category
        if category_code and category.get("codigo") == category_code and category.get("ativa", True):
            return category
    raise ValueError("Categoria ABC nao encontrada.")


def conditional_probability_analysis(
    sessions: list[dict[str, Any]],
    intervals: list[dict[str, Any]],
    categories: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    patient_token: str | None = None,
    periodo_inicio: Any = None,
    periodo_fim: Any = None,
    include_weekends: bool = True,
    minimum_valid_intervals: int = 10,
    pair_type: str = "antecedente_comportamento",
) -> dict[str, Any]:
    categories = ensure_default_categories(categories)
    valid_intervals = _valid_intervals(sessions, intervals, patient_token, periodo_inicio, periodo_fim, include_weekends)
    event_map = _event_value_map(events)
    if pair_type == "comportamento_consequencia":
        left_type, right_type = "comportamento", "consequencia"
    else:
        left_type, right_type = "antecedente", "comportamento"
    left_categories = [c for c in categories if c.get("tipo") == left_type and c.get("ativa", True)]
    right_categories = [c for c in categories if c.get("tipo") == right_type and c.get("ativa", True)]
    rows = []
    for left in left_categories:
        for right in right_categories:
            rows.append(_pair_metrics(left, right, valid_intervals, event_map, minimum_valid_intervals, pair_type))
    return {
        "tipo": pair_type,
        "periodo": {
            "inicio": _dt_or_none(periodo_inicio),
            "fim": _dt_or_none(periodo_fim),
            "include_weekends": include_weekends,
        },
        "intervalos_observados_total": len(valid_intervals),
        "metricas": rows,
        "aviso": "Associacoes descritivas nao confirmam causa ou funcao comportamental.",
    }


def timeline(
    sessions: list[dict[str, Any]],
    intervals: list[dict[str, Any]],
    categories: list[dict[str, Any]],
    events: list[dict[str, Any]],
    patient_token: str | None = None,
) -> list[dict[str, Any]]:
    valid_intervals = _valid_intervals(sessions, intervals, patient_token, None, None, True, include_not_observed=True)
    by_interval: dict[str, list[dict[str, Any]]] = {}
    category_by_id = {item["id"]: item for item in ensure_default_categories(categories)}
    for event in events:
        category = category_by_id.get(event.get("categoria_id"), {})
        by_interval.setdefault(event["intervalo_id"], []).append(
            {
                "codigo": category.get("codigo", event.get("categoria_codigo")),
                "nome": category.get("nome"),
                "tipo": category.get("tipo", event.get("categoria_tipo")),
                "ocorreu": event.get("ocorreu"),
                "frequencia": event.get("frequencia"),
            }
        )
    return [{**interval, "eventos": by_interval.get(interval["id"], [])} for interval in valid_intervals]


def abc_prediction_rows(intervals: list[dict[str, Any]], events: list[dict[str, Any]], behavior_code: str) -> list[dict[str, Any]]:
    observed = [item for item in sorted(intervals, key=lambda x: x.get("inicio", "")) if item.get("status_observacao") in OBSERVED_STATUSES]
    event_map = _event_value_map(events)
    rows = []
    previous_occurrences: list[int] = []
    for idx, interval in enumerate(observed[:-1]):
        next_interval = observed[idx + 1]
        current_consequence_codes = [
            event.get("categoria_codigo")
            for event in events
            if event.get("intervalo_id") == interval["id"] and event.get("categoria_tipo") == "consequencia" and event.get("ocorreu") is True
        ]
        y_next = event_map.get((next_interval["id"], behavior_code))
        y_current = event_map.get((interval["id"], behavior_code))
        rows.append(
            {
                "intervalo_id": interval["id"],
                "target_intervalo_id": next_interval["id"],
                "Y_t_plus_1": 1 if y_next is True else 0 if y_next is False else None,
                "behavior_current": 1 if y_current is True else 0 if y_current is False else None,
                "recent_3": sum(previous_occurrences[-3:]),
                "recent_6": sum(previous_occurrences[-6:]),
                "recent_12": sum(previous_occurrences[-12:]),
                "consequence_features_allowed_after_interval_close": current_consequence_codes,
                "leakage_guard": "same_interval_consequence_not_used_to_predict_same_interval_behavior",
            }
        )
        previous_occurrences.append(1 if y_current is True else 0)
    return rows


def _pair_metrics(left: dict[str, Any], right: dict[str, Any], intervals: list[dict[str, Any]], event_map: dict[tuple[str, str], bool | None], minimum_valid_intervals: int, pair_type: str) -> dict[str, Any]:
    n11 = n10 = n01 = n00 = missing = 0
    for interval in intervals:
        left_value = event_map.get((interval["id"], left["codigo"]))
        right_value = event_map.get((interval["id"], right["codigo"]))
        if left_value is None or right_value is None:
            missing += 1
            continue
        if left_value and right_value:
            n11 += 1
        elif left_value and not right_value:
            n10 += 1
        elif not left_value and right_value:
            n01 += 1
        else:
            n00 += 1
    n = n11 + n10 + n01 + n00
    p_base = _safe_div(n11 + n01, n)
    p_cond = _safe_div(n11, n11 + n10)
    p_not = _safe_div(n01, n01 + n00)
    risk_diff = None if p_cond is None or p_not is None else p_cond - p_not
    risk_relative = _safe_div(p_cond, p_not)
    lift = _safe_div(p_cond, p_base)
    odds_ratio = _odds_ratio(n11, n10, n01, n00)
    phi = _phi(n11, n10, n01, n00)
    ci = _wilson_interval(n11, n11 + n10)
    quality = _quality(n, n11 + n10, missing, len(intervals), minimum_valid_intervals, ci)
    left_label = "antecedente" if left.get("tipo") == "antecedente" else "comportamento"
    right_label = "comportamento" if right.get("tipo") == "comportamento" else "consequencia"
    return {
        left_label: {"codigo": left["codigo"], "nome": left["nome"]},
        right_label: {"codigo": right["codigo"], "nome": right["nome"]},
        "intervalos_observados": n,
        "intervalos_observados_total": len(intervals),
        "intervalos_com_antecedente" if pair_type != "comportamento_consequencia" else "intervalos_com_comportamento_base": n11 + n10,
        "intervalos_com_comportamento" if pair_type != "comportamento_consequencia" else "intervalos_com_consequencia": n11 + n01,
        "intervalos_com_ambos": n11,
        "dados_ausentes": missing,
        "probabilidade_baseline": p_base,
        "probabilidade_condicional": p_cond,
        "probabilidade_sem_exposicao": p_not,
        "diferenca_risco": risk_diff,
        "risco_relativo": risk_relative,
        "lift": lift,
        "odds_ratio": odds_ratio,
        "phi": phi,
        "intervalo_confianca": {"inferior": ci[0], "superior": ci[1]},
        "qualidade_estimativa": quality,
        "interpretacao": "Associacao descritiva observada; nao determina causa ou funcao.",
    }


def _valid_intervals(
    sessions: list[dict[str, Any]],
    intervals: list[dict[str, Any]],
    patient_token: str | None,
    periodo_inicio: Any,
    periodo_fim: Any,
    include_weekends: bool,
    include_not_observed: bool = False,
) -> list[dict[str, Any]]:
    session_by_id = {item["id"]: item for item in sessions}
    start = pd.to_datetime(periodo_inicio, errors="coerce") if periodo_inicio else None
    end = pd.to_datetime(periodo_fim, errors="coerce") if periodo_fim else None
    result = []
    for interval in intervals:
        session = session_by_id.get(interval.get("sessao_id"))
        if not session:
            continue
        if patient_token and session.get("patient_token") != patient_token:
            continue
        if not include_not_observed and interval.get("status_observacao") not in OBSERVED_STATUSES:
            continue
        timestamp = pd.to_datetime(interval.get("inicio"), errors="coerce")
        if pd.isna(timestamp):
            continue
        if start is not None and not pd.isna(start) and timestamp < start:
            continue
        if end is not None and not pd.isna(end) and timestamp > end:
            continue
        if not include_weekends and timestamp.weekday() >= 5:
            continue
        result.append({**interval, "patient_token": session.get("patient_token")})
    return result


def _event_value_map(events: list[dict[str, Any]]) -> dict[tuple[str, str], bool | None]:
    result = {}
    for event in events:
        result[(event.get("intervalo_id"), event.get("categoria_codigo"))] = event.get("ocorreu")
    return result


def _odds_ratio(n11: int, n10: int, n01: int, n00: int) -> float | None:
    a, b, c, d = map(float, (n11, n10, n01, n00))
    if 0 in {n11, n10, n01, n00}:
        a, b, c, d = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    return _safe_div(a * d, b * c)


def _phi(n11: int, n10: int, n01: int, n00: int) -> float | None:
    denominator = (n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00)
    if denominator <= 0:
        return None
    return float((n11 * n00 - n10 * n01) / math.sqrt(denominator))


def _wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if total <= 0:
        return (None, None)
    p = successes / total
    denominator = 1 + z**2 / total
    center = (p + z**2 / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z**2 / (4 * total)) / total) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


def _quality(n: int, exposure: int, missing: int, total: int, minimum: int, ci: tuple[float | None, float | None]) -> str:
    if n < minimum or exposure < minimum:
        return "amostra_insuficiente"
    if total and missing / total > 0.25:
        return "dados_ausentes_altos"
    if ci[0] is not None and ci[1] is not None and ci[1] - ci[0] > 0.50:
        return "incerta"
    return "adequada"


def _safe_div(numerator: Any, denominator: Any) -> float | None:
    try:
        if denominator in (None, 0):
            return None
        if numerator is None:
            return None
        return float(numerator / denominator)
    except Exception:
        return None


def _parse_dt(value: Any) -> datetime:
    parsed = pd.to_datetime(value, errors="raise", utc=True)
    return parsed.to_pydatetime()


def _dt_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
