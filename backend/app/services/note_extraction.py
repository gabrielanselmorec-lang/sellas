from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any


BEHAVIOR_TERMS: dict[str, list[str]] = {
    "agressao": ["agressao", "agressividade", "bater", "morder", "empurrar", "chutar", "puxar cabelo"],
    "autolesao": ["autolesao", "bater em si mesmo", "se morder", "se arranhar"],
    "fuga": ["fuga", "evasao", "sair correndo", "fugir"],
    "esquiva": ["esquiva", "recusa", "recusa de demanda"],
    "choro": ["choro", "chorou", "choramingo"],
    "grito": ["grito", "gritos", "gritou"],
    "birra": ["birra", "crise", "comportamento disruptivo", "desregulacao"],
    "jogar_objetos": ["jogar objetos", "arremessar objetos"],
    "destruicao": ["destruir materiais", "destruicao", "quebrar materiais"],
    "oposicao": ["oposicao", "comportamento inadequado"],
    "estereotipia": ["estereotipia excessiva"],
    "comportamento_problema": ["comportamento problema", "comportamento-problema"],
}

NEGATION_TERMS = [
    "nao apresentou",
    "nao houve",
    "nao ocorreram",
    "antes de apresentar",
    "sem",
    "ausencia de",
    "sem sinais de",
]

GENERAL_ABSENCE_TERMS = [
    "sem comportamento problema",
    "sem comportamento-problema",
    "sem intercorrencias",
    "nao houve crise",
    "nao apresentou comportamento",
    "boa regulacao",
]

AMBIGUOUS_TERMS = ["possivel", "pareceu", "quase", "tentou", "inicio de", "iniciou"]

INTENSITY_TERMS = {
    "leve": 1,
    "baixo": 1,
    "moderado": 2,
    "intenso": 3,
    "intensa": 3,
    "intensamente": 3,
    "grave": 3,
    "severo": 3,
    "forte": 3,
    "alto": 3,
}

NUMBER_WORDS = {
    "uma": 1,
    "um": 1,
    "duas": 2,
    "dois": 2,
    "tres": 3,
    "quatro": 4,
    "cinco": 5,
}

ANTECEDENT_TERMS = {
    "demanda": ["demanda", "solicitacao", "instrucao", "atividade dificil", "tarefa de mesa"],
    "negacao_acesso": ["negacao de acesso", "acesso negado", "negado acesso"],
    "retirada_item": ["retirada do tablet", "retirada de item", "retirado o brinquedo"],
    "transicao": ["transicao"],
    "espera": ["espera"],
    "mudanca_rotina": ["mudanca de rotina"],
    "frustracao": ["frustracao"],
    "barulho": ["barulho"],
    "correcao": ["correcao"],
    "redirecionamento": ["redirecionamento", "redirecionado", "redirecionada"],
}

CONSEQUENCE_TERMS = {
    "atencao": ["atencao"],
    "fuga_demanda": ["fuga da demanda"],
    "acesso_item": ["acesso a item", "acesso ao brinquedo"],
    "pausa": ["pausa"],
    "redirecionamento": ["redirecionamento", "redirecionado", "redirecionada"],
    "bloqueio": ["bloqueio"],
    "ajuda_fisica": ["ajuda fisica"],
    "ajuda_verbal": ["ajuda verbal"],
    "demanda_mantida": ["demanda mantida"],
}

CONTEXT_TERMS = {
    "sala": ["sala"],
    "escola": ["escola"],
    "casa": ["casa"],
    "clinica": ["clinica"],
    "mesa": ["mesa", "atividade de mesa", "tarefa de mesa"],
    "banheiro": ["banheiro"],
    "refeicao": ["refeicao"],
    "transicao": ["transicao"],
    "grupo": ["grupo"],
    "individual": ["individual"],
    "atividade_academica": ["atividade academica"],
    "brincadeira": ["brincadeira"],
    "atividade_motora": ["atividade motora"],
    "chegada": ["chegada", "inicio da sessao"],
    "saida": ["saida", "fim da sessao"],
}


def normalize_note_text(text: str | None) -> str:
    raw = text or ""
    normalized = unicodedata.normalize("NFKD", raw)
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    lowered = without_accents.lower()
    lowered = re.sub(r"[^\w\s.,;:%/-]", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def adapt_appointment_note(raw: dict[str, Any], field_map: dict[str, str] | None = None) -> dict[str, Any]:
    field_map = field_map or {}

    def first(*names: str) -> Any:
        for name in names:
            mapped = field_map.get(name, name)
            if mapped in raw and raw[mapped] not in (None, ""):
                return raw[mapped]
        return None

    note_text = first("notes", "clinical_notes", "behavioral_notes", "session_summary", "evolution_notes") or ""
    return {
        "patient_id": first("patient_id", "client_id", "paciente_id"),
        "patient_name": first("patient_name", "client_name", "paciente_nome"),
        "appointment_id": first("appointment_id", "session_id", "atendimento_id"),
        "appointment_date": first("appointment_date", "date", "session_date", "data"),
        "appointment_start_time": first("appointment_start_time", "start_time", "started_at"),
        "appointment_end_time": first("appointment_end_time", "end_time", "ended_at"),
        "professional_id": first("professional_id", "therapist_id", "aplicador_id"),
        "professional_name": first("professional_name", "therapist_name", "aplicador_nome"),
        "authored_at": first("authored_at", "note_authored_at", "created_at", "criado_em"),
        "recorded_at": first("recorded_at", "created_at", "criado_em"),
        "note_scope": first("note_scope", "escopo_nota"),
        "raw_note_text": str(note_text),
        "created_at": first("created_at", "criado_em"),
        "updated_at": first("updated_at", "atualizado_em"),
    }


def extract_appointment_note(note: dict[str, Any]) -> dict[str, Any]:
    raw_text = str(note.get("raw_note_text") or "")
    cleaned = normalize_note_text(raw_text)
    behaviors = _extract_behaviors(cleaned)
    antecedents = _extract_tags(cleaned, ANTECEDENT_TERMS)
    consequences = _extract_tags(cleaned, CONSEQUENCE_TERMS)
    contexts = _extract_tags(cleaned, CONTEXT_TERMS)
    general_absence = any(term in cleaned for term in GENERAL_ABSENCE_TERMS)
    ambiguous = any(term in cleaned for term in AMBIGUOUS_TERMS)
    binary = {f"behavior_{item['behavior_name']}_occurred": item["occurred"] for item in behaviors if item["occurred"] is not None}
    if general_absence:
        binary["general_problem_behavior_occurred"] = 0
    confidence = _confidence(behaviors, general_absence, ambiguous)
    requires_review = ambiguous or confidence < 0.70 or any(item.get("possible_behavior") for item in behaviors)
    now = datetime.now(timezone.utc).isoformat()
    return {
        "patient_id": note.get("patient_id"),
        "appointment_id": note.get("appointment_id"),
        "appointment_date": note.get("appointment_date"),
        "authored_at": note.get("authored_at"),
        "recorded_at": note.get("recorded_at"),
        "note_scope": note.get("note_scope") or "unknown",
        "raw_note_text": raw_text,
        "raw_text_hash": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        "cleaned_note_text": cleaned,
        "extracted_behaviors": behaviors,
        "binary_occurrences": binary,
        "extracted_antecedents": antecedents,
        "extracted_consequences": consequences,
        "extracted_context": contexts,
        "extracted_intensity": _extract_intensity(cleaned),
        "extracted_frequency": _extract_frequency(cleaned),
        "extracted_duration": _extract_duration_minutes(cleaned),
        "extraction_confidence": confidence,
        "extraction_method": "rules_pt_br_v1",
        "requires_human_review": requires_review,
        "human_confirmed": False,
        "human_corrected": False,
        "created_at": note.get("created_at") or now,
        "updated_at": now,
    }


def extraction_to_feature_rows(extractions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for extraction in extractions:
        source = _human_corrected_source(extraction)
        context_tags = [_feature_context_name(tag) for tag in source.get("extracted_context", [])]
        for behavior in source.get("extracted_behaviors", []):
            rows.append(
                {
                    "patient_id": extraction.get("patient_id"),
                    "session_id": extraction.get("appointment_id"),
                    "appointment_id": extraction.get("appointment_id"),
                    "date": extraction.get("appointment_date"),
                    "authored_at": extraction.get("authored_at"),
                    "recorded_at": extraction.get("recorded_at"),
                    "note_scope": extraction.get("note_scope") or "unknown",
                    "low_temporal_confidence": not bool(extraction.get("authored_at")),
                    "behavior_name": behavior.get("behavior_name"),
                    "occurrence_from_note": _occurrence_value(behavior.get("occurred")),
                    "note_extracted_frequency": behavior.get("frequency") or source.get("extracted_frequency") or 0,
                    "note_extracted_intensity": behavior.get("intensity") or source.get("extracted_intensity") or 0,
                    "note_extracted_duration": behavior.get("duration_minutes") or source.get("extracted_duration") or 0,
                    "note_extraction_confidence": source.get("extraction_confidence") or 0,
                    "note_requires_human_review": int(bool(source.get("requires_human_review"))),
                    **{f"note_antecedent_{tag}": 1 for tag in source.get("extracted_antecedents", [])},
                    **{f"note_consequence_{tag}": 1 for tag in source.get("extracted_consequences", [])},
                    **{f"note_context_{tag}": 1 for tag in context_tags},
                }
            )
    return rows


def _human_corrected_source(extraction: dict[str, Any]) -> dict[str, Any]:
    correction = extraction.get("correction") or {}
    corrected = correction.get("corrected_extraction") or {}
    if extraction.get("human_corrected") and corrected:
        if "extracted_behaviors" in corrected:
            return {**extraction, **corrected, "requires_human_review": False}
        if corrected.get("behavior_name"):
            behavior = {
                "behavior_name": corrected.get("behavior_name"),
                "occurred": corrected.get("behavior_occurred", corrected.get("occurred")),
                "frequency": corrected.get("frequency"),
                "intensity": corrected.get("intensity"),
                "duration_minutes": corrected.get("duration_minutes"),
            }
            return {
                **extraction,
                "extracted_behaviors": [behavior],
                "extracted_antecedents": corrected.get("antecedents", extraction.get("extracted_antecedents", [])),
                "extracted_consequences": corrected.get("consequences", extraction.get("extracted_consequences", [])),
                "extracted_context": corrected.get("context_tags", extraction.get("extracted_context", [])),
                "extraction_confidence": 1.0,
                "requires_human_review": False,
            }
    return extraction


def _feature_context_name(tag: str) -> str:
    return {
        "chegada": "inicio_sessao",
        "saida": "fim_sessao",
        "mesa": "atividade_mesa",
    }.get(tag, tag)


def _extract_behaviors(cleaned: str) -> list[dict[str, Any]]:
    behaviors: list[dict[str, Any]] = []
    for behavior_name, terms in BEHAVIOR_TERMS.items():
        matches = []
        for term in terms:
            for match in re.finditer(rf"\b{re.escape(term)}\b", cleaned):
                context = cleaned[max(0, match.start() - 55): match.end() + 55]
                negated = _is_negated(context, term)
                possible = any(marker in context for marker in AMBIGUOUS_TERMS)
                matches.append({"term": term, "negated": negated, "possible": possible, "context": context})
        if not matches:
            continue
        clear_positive = any((not match["negated"]) and (not match["possible"]) for match in matches)
        if all(match["negated"] for match in matches):
            occurred = 0
        elif any(match["possible"] for match in matches) and not clear_positive:
            occurred = None
        else:
            occurred = 1 if clear_positive or any(not match["negated"] for match in matches) else 0
        behaviors.append(
            {
                "behavior_name": behavior_name,
                "matched_terms": sorted({match["term"] for match in matches}),
                "occurred": occurred,
                "possible_behavior": any(match["possible"] for match in matches),
                "frequency": _extract_frequency(" ".join(match["context"] for match in matches)),
                "intensity": _extract_intensity(" ".join(match["context"] for match in matches)),
                "duration_minutes": _extract_duration_minutes(" ".join(match["context"] for match in matches)),
            }
        )
    return behaviors


def _is_negated(context: str, term: str) -> bool:
    if f"sem {term}" in context:
        return True
    term_index = context.find(term)
    prefix = context[:term_index] if term_index >= 0 else context
    return any(marker in prefix[-38:] for marker in NEGATION_TERMS)


def _extract_frequency(text: str) -> Any:
    digit = re.search(r"\b(\d+)\s*(vez|vezes|episodios|ocorrencias)\b", text)
    if digit:
        return int(digit.group(1))
    for word, value in NUMBER_WORDS.items():
        if re.search(rf"\b{word}\s+(vez|vezes|episodios|ocorrencias)\b", text):
            return value
    if "varias vezes" in text:
        return "multiple"
    if "muitas vezes" in text:
        return "high_frequency"
    return None


def _extract_intensity(text: str) -> int | None:
    found = [value for term, value in INTENSITY_TERMS.items() if re.search(rf"\b{re.escape(term)}\b", text)]
    return max(found) if found else None


def _extract_duration_minutes(text: str) -> float | None:
    match = re.search(r"(por|cerca de|aproximadamente|durou)\s*(\d+)\s*(min|mins|minutos)", text)
    if match:
        return float(match.group(2))
    if "poucos segundos" in text:
        return 0.5
    if "toda a sessao" in text:
        return 60.0
    return None


def _extract_tags(text: str, taxonomy: dict[str, list[str]]) -> list[str]:
    tags = []
    for tag, terms in taxonomy.items():
        if any(re.search(rf"\b{re.escape(term)}\b", text) for term in terms):
            tags.append(tag)
    return sorted(tags)


def _confidence(behaviors: list[dict[str, Any]], general_absence: bool, ambiguous: bool) -> float:
    if ambiguous:
        return 0.60
    if general_absence and not behaviors:
        return 0.95
    if behaviors and any(item.get("occurred") is None for item in behaviors):
        return 0.55
    if behaviors:
        return 0.90
    return 0.45


def _occurrence_value(value: Any) -> int:
    if value is None:
        return 0
    return int(bool(value))
