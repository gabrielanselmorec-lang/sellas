"""Contratos de coleta e indicadores dos acontecimentos ABC do Sellas."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("America/Sao_Paulo")
INTEGRITY_DDL = (
    "ALTER TABLE abc_intervals ADD COLUMN IF NOT EXISTS metadados JSONB NOT NULL DEFAULT '{}'::jsonb",
    """CREATE TABLE IF NOT EXISTS abc_record_requests (
        patient_token UUID NOT NULL, request_id UUID NOT NULL,
        payload_hash VARCHAR(64) NOT NULL, response JSONB NOT NULL,
        criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (patient_token, request_id))""",
    "ALTER TABLE abc_record_requests ENABLE ROW LEVEL SECURITY",
)


class DuplicateRecordError(ValueError):
    pass


def local_timestamp(value: Any) -> datetime:
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp.replace(tzinfo=TIMEZONE) if stamp.tzinfo is None else stamp.astimezone(TIMEZONE)


def has_behavior(row: dict[str, Any]) -> bool:
    if row.get("ocorreu") is False:
        return False
    value = str(row.get("comportamento") or "").strip().casefold()
    return value not in {"", "não informado", "não informada", "nao informado", "não ocorreu", "n/a"}


def record_metadata(row: dict[str, Any]) -> dict[str, Any]:
    """Reconhece o formulário legado sem reescrever os acontecimentos originais."""
    metadata = dict(row.get("metadados") or {})
    kind = metadata.get("record_kind") or ("episode" if row.get("registro_fechado") else "observation")
    metadata["record_kind"] = kind
    for key in ("observador", "fonte_referencia", "atendimento_referencia"):
        metadata.setdefault(key, "")
    metadata.setdefault("precisao_horario", "legacy" if row.get("registro_fechado") else "second")
    return metadata


def filter_records(records: Iterable[dict[str, Any]], *, start_date=None, end_date=None,
                   environment=None, behavior=None, classification=None, include_weekends=True) -> list[dict[str, Any]]:
    if start_date and end_date and start_date > end_date:
        raise ValueError("A data inicial não pode ser posterior à data final.")
    result = []
    for row in records:
        stamp = local_timestamp(row["data_hora"])
        if start_date and stamp.date() < start_date or end_date and stamp.date() > end_date:
            continue
        if not include_weekends and stamp.weekday() >= 5:
            continue
        if environment and row.get("ambiente") != environment:
            continue
        if behavior and row.get("comportamento") != behavior:
            continue
        if classification and row.get("classificacao") != classification:
            continue
        result.append(row)
    return result


def temporal_candidates_in_scope(candidates, interval_ids):
    return [item for item in candidates if str(item.get("from_interval_id")) in interval_ids
            and str(item.get("to_interval_id")) in interval_ids]


def build_time_summary(records: Iterable[dict[str, Any]], bin_minutes: int = 60) -> dict[str, Any]:
    if bin_minutes not in {15, 30, 60}:
        raise ValueError("Use faixas de 15, 30 ou 60 minutos.")
    rows = [row for row in records if has_behavior(row)]
    bins, months, weekdays = defaultdict(list), defaultdict(list), defaultdict(list)
    missing = approximate = legacy = invalid = 0
    stamps = []
    for row in rows:
        try:
            stamp = local_timestamp(row["data_hora"])
        except (ValueError, TypeError, KeyError):
            invalid += 1
            continue
        stamps.append(stamp)
        months[stamp.strftime("%Y-%m")].append(row)
        weekdays[stamp.weekday()].append(row)
        precision = row.get("precisao_horario", "legacy")
        if precision == "unknown":
            missing += 1
            continue
        approximate += precision == "approximate"
        legacy += precision == "legacy"
        minute = (stamp.hour * 60 + stamp.minute) // bin_minutes * bin_minutes
        bins[minute].append(row)
    known = sum(map(len, bins.values()))

    def details(items):
        return {"quantidade": len(items), "percentual_total": len(items) / len(rows) if rows else 0,
                "dias": len({local_timestamp(r["data_hora"]).date() for r in items}),
                "respostas": sum(int(r.get("frequencia") or 1) for r in items)}

    return {"faixa_minutos": bin_minutes, "total_registros": len(rows), "horarios_validos": known,
            "sem_horario": missing, "horarios_invalidos": invalid, "horarios_aproximados": approximate,
            "precisao_legada": legacy, "dias": len({s.date() for s in stamps}),
            "inicio": min(stamps).date().isoformat() if stamps else None,
            "fim": max(stamps).date().isoformat() if stamps else None,
            "por_horario": [{"inicio": f"{minute // 60:02d}:{minute % 60:02d}",
                              "fim": f"{(minute + bin_minutes - 1) // 60:02d}:{(minute + bin_minutes - 1) % 60:02d}",
                              **details(items), "percentual_horarios_validos": len(items) / known if known else 0}
                             for minute, items in sorted(bins.items())],
            "por_mes": [{"mes": month, **details(items)} for month, items in sorted(months.items())],
            "por_dia_semana": [{"dia": day, "nome": ("Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo")[day],
                                **details(items)} for day, items in sorted(weekdays.items())],
            "aviso": "Contagens de episódios registrados. Dias sem registro não representam ausência observada. "
                     "Horários aproximados e legados não sustentam conclusões sobre segundos."}


def attach_exposure_to_time_summary(summary: dict[str, Any], exposure: dict[str, Any]) -> dict[str, Any]:
    months = {row["mes"]: dict(row) for row in summary.get("por_mes", [])}
    for day in exposure.get("by_date", []):
        if len(day.get("date", "")) != 10:
            continue
        month = day["date"][:7]
        item = months.setdefault(month, {"mes": month, "quantidade": 0, "dias": 0, "respostas": 0, "percentual_total": 0})
        item["horas_observadas"] = item.get("horas_observadas", 0) + day["observed_hours"]
        item["dias_observados"] = item.get("dias_observados", 0) + 1
        item["episodios_no_tempo_observado"] = item.get("episodios_no_tempo_observado", 0) + day.get("episodes", 0)
    for row in months.values():
        hours = row.get("horas_observadas")
        row.setdefault("horas_observadas", None)
        row.setdefault("dias_observados", None)
        row["episodios_por_hora_observada"] = row.get("episodios_no_tempo_observado", 0) / hours if hours and exposure.get("status") == "calculable" else None
    return {**summary, "por_mes": [months[key] for key in sorted(months)]}


def answer_time_question(question: str, analysis: dict[str, Any]) -> dict[str, Any] | None:
    import unicodedata
    folded = ''.join(c for c in unicodedata.normalize('NFKD', question.lower()) if not unicodedata.combining(c))
    if not any(word in folded for word in ("horario", "que hora", "qual hora", "quais horas", "faixa de hora")):
        return None
    def fold(value):
        return ''.join(c for c in unicodedata.normalize('NFKD', str(value).lower()) if not unicodedata.combining(c))
    rows = analysis.get("registros_para_gestao") or []
    requested = [name for name in analysis.get("comportamentos_observados", [])
                 if fold(name) in folded or (fold(name).split() and fold(name).split()[0] in folded.split())]
    if requested and rows:
        rows = [r for r in rows if r.get("comportamento") in requested]
        summary = build_time_summary(rows)
    else:
        summary = analysis.get("distribuicao_temporal") or {}
    behavior_label = f" para {', '.join(requested)}" if requested else ""

    bins = summary.get("por_horario") or []
    if not bins:
        response = "Não há acontecimentos com horário informado no recorte selecionado dos ABCs do Sellas."
    else:
        maximum = max(item["quantidade"] for item in bins)
        peaks = [item for item in bins if item["quantidade"] == maximum]
        labels = ", ".join(f"{item['inicio']}–{item['fim']}" for item in peaks)
        response = (f"Nos ABCs do Sellas{behavior_label}, a maior concentração foi em {labels}: {maximum} registro(s) em cada faixa "
                    f"({maximum / summary['total_registros']:.1%} do total). "
                    f"Foram analisados {summary['total_registros']} episódios, em {summary['dias']} dia(s), "
                    f"entre {summary['inicio']} e {summary['fim']}, no horário de Brasília. "
                    f"Sem horário: {summary['sem_horario']}; aproximados: {summary['horarios_aproximados']}; "
                    f"precisão legada não confirmada: {summary['precisao_legada']}. "
                    "Isso descreve os registros disponíveis; não estima risco por hora sem tempo real de observação.")
    return {"resposta": response, "fontes": [], "modo": "calculo_abc_sellas", "indicadores_abc": summary}
