"""Metodologia auditável para análises descritivas e preditivas do ABC.

Este módulo não conhece HTML, DOCX ou PDF. Ele centraliza validações, fórmulas,
normalização e contratos que precisam ser reproduzíveis em qualquer superfície.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
from scipy.stats import beta as beta_distribution
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    log_loss,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


METHODOLOGY_VERSION = "abc-methodology-v3"
NORMALIZATION_VERSION = "abc-normalization-v1"
SEVERITY_RULE_VERSION = "abc-severity-v1"
EVIDENCE_RULE_VERSION = "abc-evidence-v1"
DEFAULT_CLINICAL_DISCLAIMER = (
    "As estimativas são descritivas ou preditivas conforme indicado em cada seção. "
    "Associações não demonstram causalidade nem confirmam função comportamental. "
    "Os resultados não substituem avaliação funcional, julgamento clínico ou análise "
    "individualizada conduzida por profissional habilitado."
)

POST_TARGET_FIELDS = frozenset(
    {
        "consequencia",
        "consequence",
        "classificacao",
        "classification",
        "gravidade",
        "severity",
        "funcao",
        "function",
        "duracao_final",
        "end_duration",
        "manejo",
        "management",
        "anotacao_pos_evento",
        "post_event_note",
        "end_ts",
        "offset_ts",
    }
)
IDENTIFIER_FIELDS = frozenset(
    {
        "id",
        "patient_id",
        "paciente_id",
        "patient_token",
        "session_id",
        "sessao_id",
        "interval_id",
        "intervalo_id",
        "event_id",
        "evento_id",
    }
)


@dataclass(frozen=True)
class ForecastConfig:
    target_behavior: str
    prediction_unit: str
    prediction_horizon: str
    reference_datetime: str
    environment_filter: str | None
    patient_id: str
    training_start: str | None
    training_end: str | None
    validation_start: str | None
    validation_end: str | None
    model_version: str = "logistic-l2-v1"
    feature_version: str = "abc-features-v1"
    interval_level: float = 0.95
    threshold: float = 0.50
    threshold_rule: str = "configurado; não otimizado no teste final"
    random_seed: int = 20260720

    def validate(self) -> None:
        required = {
            "target_behavior": self.target_behavior,
            "prediction_unit": self.prediction_unit,
            "prediction_horizon": self.prediction_horizon,
            "reference_datetime": self.reference_datetime,
            "patient_id": self.patient_id,
        }
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            raise ValueError(f"Configuração preditiva incompleta: {', '.join(missing)}.")
        if not 0 < float(self.interval_level) < 1:
            raise ValueError("O nível do intervalo deve estar entre 0 e 1.")
        if not 0 < float(self.threshold) < 1:
            raise ValueError("O limiar de classificação deve estar entre 0 e 1.")


@dataclass(frozen=True)
class SeverityConfig:
    weights: dict[str, float] = field(default_factory=lambda: {"C1": 0.20, "C2": 1.00})
    version: str = SEVERITY_RULE_VERSION
    rationale: str = "Pesos internos configurados para sumarização exploratória; não são escala clínica validada."

    def validate(self) -> None:
        if not self.weights:
            raise ValueError("A configuração de gravidade não possui pesos.")
        for key, value in self.weights.items():
            if not str(key).strip() or not math.isfinite(float(value)) or float(value) < 0:
                raise ValueError("Os pesos configurados de gravidade devem ser finitos e não negativos.")


@dataclass(frozen=True)
class EvidenceQuality:
    sample_size_score: float
    session_coverage_score: float
    day_coverage_score: float
    missing_data_score: float
    class_balance_score: float
    temporal_validation_score: float
    calibration_score: float
    external_validity_score: float
    overall_quality: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    rule_version: str = EVIDENCE_RULE_VERSION

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["reasons"] = list(self.reasons)
        result["warnings"] = list(self.warnings)
        return result


def validate_binomial_counts(successes: int, trials: int) -> tuple[int, int]:
    if isinstance(successes, bool) or isinstance(trials, bool):
        raise ValueError("k e n devem ser números inteiros, não booleanos.")
    if not isinstance(successes, (int, np.integer)) or not isinstance(trials, (int, np.integer)):
        raise ValueError("k e n devem ser números inteiros.")
    k, n = int(successes), int(trials)
    if n <= 0:
        raise ValueError("n deve ser maior que zero.")
    if k < 0 or k > n:
        raise ValueError("k deve satisfazer 0 <= k <= n.")
    return k, n


def wilson_interval(successes: int, trials: int, confidence_level: float = 0.95) -> dict[str, Any]:
    k, n = validate_binomial_counts(successes, trials)
    if not 0 < confidence_level < 1:
        raise ValueError("O nível de confiança deve estar entre 0 e 1.")
    # 1,95996398454 para 95%; evita uma dependência conceitual de um estimador composto.
    from scipy.stats import norm

    z = float(norm.ppf(1 - (1 - confidence_level) / 2))
    estimate = k / n
    denominator = 1 + z * z / n
    center = (estimate + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(estimate * (1 - estimate) / n + z * z / (4 * n * n)) / denominator
    lower = min(estimate, max(0.0, center - margin))
    upper = max(estimate, min(1.0, center + margin))
    _validate_interval(estimate, lower, upper)
    return {
        "numerator": k,
        "denominator": n,
        "method": "wilson",
        "method_label": f"Proporção observada e IC de Wilson de {confidence_level * 100:.0f}%",
        "interval_kind": "confidence",
        "level": confidence_level,
        "estimate": estimate,
        "lower": lower,
        "upper": upper,
    }


def beta_posterior_interval(
    successes: int,
    trials: int,
    *,
    alpha: float = 1.0,
    beta: float = 1.0,
    credibility_level: float = 0.95,
) -> dict[str, Any]:
    k, n = validate_binomial_counts(successes, trials)
    if alpha <= 0 or beta <= 0:
        raise ValueError("Os parâmetros alpha e beta da prior devem ser positivos.")
    if not 0 < credibility_level < 1:
        raise ValueError("O nível de credibilidade deve estar entre 0 e 1.")
    posterior_alpha = k + float(alpha)
    posterior_beta = n - k + float(beta)
    estimate = posterior_alpha / (posterior_alpha + posterior_beta)
    tail = (1 - credibility_level) / 2
    lower = float(beta_distribution.ppf(tail, posterior_alpha, posterior_beta))
    upper = float(beta_distribution.ppf(1 - tail, posterior_alpha, posterior_beta))
    _validate_interval(estimate, lower, upper)
    return {
        "numerator": k,
        "denominator": n,
        "method": "beta_posterior",
        "method_label": f"Estimativa bayesiana e intervalo de credibilidade de {credibility_level * 100:.0f}%",
        "interval_kind": "credible",
        "level": credibility_level,
        "estimate": estimate,
        "lower": lower,
        "upper": upper,
        "prior_alpha": float(alpha),
        "prior_beta": float(beta),
        "posterior_alpha": posterior_alpha,
        "posterior_beta": posterior_beta,
    }


def _validate_interval(estimate: float, lower: float, upper: float) -> None:
    values = (estimate, lower, upper)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("A estimativa e os limites precisam ser finitos.")
    if not 0 <= lower <= estimate <= upper <= 1:
        raise ValueError("O intervalo calculado é incoerente com a estimativa.")


def descriptive_behavior_estimate(
    records: Iterable[dict[str, Any]],
    *,
    behavior: str,
    antecedent: str | None = None,
    environment: str | None = None,
    method: str = "frequentist",
    confidence_level: float = 0.95,
    prior_alpha: float = 1.0,
    prior_beta: float = 1.0,
) -> dict[str, Any]:
    normalized = normalize_abc_records(records)
    context = [
        row
        for row in normalized
        if (not antecedent or row.get("antecedente") == normalize_clinical_value(antecedent, "antecedente")["normalized_value"])
        and (not environment or row.get("ambiente") == normalize_clinical_value(environment, "ambiente")["normalized_value"])
    ]
    n = len(context)
    k = sum(row.get("comportamento") == normalize_clinical_value(behavior, "comportamento")["normalized_value"] for row in context)
    if n == 0:
        return {
            "analysis_mode": "descriptive",
            "estimand": "relative_distribution_among_recorded_episodes",
            "numerator": 0,
            "denominator": 0,
            "estimate": None,
            "lower": None,
            "upper": None,
            "method": method,
            "absolute_risk_available": False,
            "selection_bias_warning": "Não há registros semelhantes no contexto selecionado.",
        }
    interval = (
        wilson_interval(k, n, confidence_level)
        if method == "frequentist"
        else beta_posterior_interval(k, n, alpha=prior_alpha, beta=prior_beta, credibility_level=confidence_level)
    )
    return {
        "analysis_mode": "descriptive",
        "estimand": "relative_distribution_among_recorded_episodes",
        **interval,
        "absolute_risk_available": False,
        "selection_bias_warning": (
            "A base contém episódios ABC registrados, mas não todas as oportunidades observáveis sem o comportamento. "
            "A estimativa descreve a distribuição relativa entre episódios registrados e não o risco absoluto do próximo evento."
        ),
    }


def normalize_clinical_value(value: Any, field_name: str) -> dict[str, Any]:
    raw_value = value
    if value is None or not str(value).strip():
        return {
            "raw_value": raw_value,
            "normalized_value": "Não informado",
            "normalization_rule": "missing_to_not_informed",
            "normalization_version": NORMALIZATION_VERSION,
        }
    collapsed = " ".join(str(value).strip().split())
    folded = _fold(collapsed)
    known = {
        ("antecedente", "do nada"): "Antecedente não identificado no registro",
        ("consequencia", "manejo fisico"): "Manejo físico",
        ("funcao", "fuga ou esquiva"): "Fuga/esquiva",
        ("funcao", "fuga/esquiva"): "Fuga/esquiva",
        ("funcao", "nao identificada"): "Não identificada",
        ("funcao", "nao informado"): "Não identificada",
    }
    normalized = known.get((field_name, folded))
    if normalized is not None:
        rule = f"known_alias:{field_name}:{folded}"
    elif folded in {"nao informado", "nao informada", "nao identificado", "nao identificada", "n/a", "na"}:
        normalized = "Não informado" if field_name != "funcao" else "Não identificada"
        rule = "null_equivalent"
    else:
        normalized = collapsed[:1].upper() + collapsed[1:] if collapsed else collapsed
        rule = "trim_whitespace_and_capitalize" if normalized != raw_value else "identity"
    return {
        "raw_value": raw_value,
        "normalized_value": normalized,
        "normalization_rule": rule,
        "normalization_version": NORMALIZATION_VERSION,
    }


def normalize_abc_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    fields = ("antecedente", "comportamento", "consequencia", "ambiente", "funcao")
    for source in records:
        item = dict(source)
        audit: dict[str, dict[str, Any]] = {}
        for field_name in fields:
            normalized = normalize_clinical_value(item.get(field_name), field_name)
            item[field_name] = normalized["normalized_value"]
            audit[field_name] = normalized
        item["normalization_audit"] = audit
        item["normalization_version"] = NORMALIZATION_VERSION
        result.append(item)
    return result


def severity_summary(
    classifications: Iterable[Any],
    *,
    occurrence_frequency: float | None = None,
    config: SeverityConfig | None = None,
) -> dict[str, Any]:
    cfg = config or SeverityConfig()
    cfg.validate()
    counts = {code: 0 for code in cfg.weights}
    unclassified = 0
    for raw in classifications:
        code = str(raw or "").strip().upper()
        if code in counts:
            counts[code] += 1
        else:
            unclassified += 1
    classified = sum(counts.values())
    mean_weight = (
        sum(counts[code] * float(cfg.weights[code]) for code in counts) / classified
        if classified
        else None
    )
    exploratory_risk = (
        float(occurrence_frequency) * mean_weight
        if occurrence_frequency is not None and mean_weight is not None
        else None
    )
    message = (
        f"Todos os registros classificados nesta cadeia foram C1; peso médio configurado: {mean_weight:.2f}."
        if classified and counts.get("C1") == classified and mean_weight is not None
        else "O peso médio configurado resume a gravidade registrada; não é probabilidade de dano."
    )
    return {
        "counts": counts,
        "classified_count": classified,
        "unclassified_count": unclassified,
        "mean_configured_severity_weight": mean_weight,
        "exploratory_risk_index": exploratory_risk,
        "weight_config": {"weights": dict(cfg.weights), "version": cfg.version, "rationale": cfg.rationale},
        "message": message,
    }


def compute_evidence_quality(
    *,
    sample_size: int,
    session_count: int,
    day_count: int,
    missing_count: int,
    positive_count: int | None = None,
    negative_count: int | None = None,
    temporal_validated: bool = False,
    calibrated: bool = False,
    external_validated: bool = False,
) -> EvidenceQuality:
    n = max(0, int(sample_size))
    missing_ratio = min(1.0, max(0.0, missing_count / n)) if n else 1.0
    sample_score = min(1.0, n / 30)
    session_score = min(1.0, max(0, session_count) / 5)
    day_score = min(1.0, max(0, day_count) / 5)
    missing_score = 1 - missing_ratio
    if positive_count is None or negative_count is None or positive_count + negative_count == 0:
        balance_score = 0.0
    else:
        prevalence = positive_count / (positive_count + negative_count)
        balance_score = min(1.0, 2 * min(prevalence, 1 - prevalence))
    temporal_score = 1.0 if temporal_validated else 0.0
    calibration_score = 1.0 if calibrated else 0.0
    external_score = 1.0 if external_validated else 0.0
    scores = (sample_score, session_score, day_score, missing_score, balance_score, temporal_score, calibration_score, external_score)
    overall = sum(scores) / len(scores)
    quality = "adequada" if overall >= 0.75 else "limitada" if overall >= 0.45 else "inicial"
    reasons: list[str] = []
    warnings: list[str] = []
    reasons.append(f"{n} registros distribuídos em {session_count} sessão(ões) e {day_count} dia(s).")
    if session_count < 3:
        warnings.append("Quantidade de registros concentrada em poucas sessões.")
    if not temporal_validated:
        warnings.append("Sem validação fora da amostra em ordem temporal.")
    if missing_ratio > 0.10:
        warnings.append("Proporção relevante de gravidade ou campos clínicos não classificados.")
    if positive_count is not None and negative_count == 0:
        warnings.append("Não há oportunidades negativas observáveis; risco absoluto não é estimável.")
    if temporal_validated and not calibrated:
        warnings.append("Modelo sem evidência suficiente de calibração.")
    if not external_validated:
        warnings.append("Validade externa não avaliada.")
    return EvidenceQuality(
        sample_size_score=sample_score,
        session_coverage_score=session_score,
        day_coverage_score=day_score,
        missing_data_score=missing_score,
        class_balance_score=balance_score,
        temporal_validation_score=temporal_score,
        calibration_score=calibration_score,
        external_validity_score=external_score,
        overall_quality=quality,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


def deterministic_transition_id(
    patient_id: Any,
    previous_event_id: Any,
    next_event_id: Any,
    rule_version: str,
) -> str:
    values = [str(patient_id or ""), str(previous_event_id or ""), str(next_event_id or ""), str(rule_version or "")]
    if any(not value for value in values):
        raise ValueError("patient_id, previous_event_id, next_event_id e rule_version são obrigatórios.")
    return hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()


def build_temporal_transitions(
    events: Iterable[dict[str, Any]],
    *,
    max_gap_seconds: int,
    rule_version: str,
    require_same_session: bool = True,
    require_same_environment: bool = False,
) -> list[dict[str, Any]]:
    if max_gap_seconds < 0:
        raise ValueError("O gap máximo deve ser não negativo.")
    rows = sorted((dict(item) for item in events), key=lambda item: _as_timestamp(item.get("start_ts")))
    result: dict[str, dict[str, Any]] = {}
    for previous, current in zip(rows, rows[1:], strict=False):
        if str(previous.get("patient_id")) != str(current.get("patient_id")):
            continue
        if str(previous.get("event_id")) == str(current.get("event_id")):
            continue
        if require_same_session and str(previous.get("session_id")) != str(current.get("session_id")):
            continue
        if require_same_environment and str(previous.get("environment")) != str(current.get("environment")):
            continue
        previous_end = _as_timestamp(previous.get("end_ts"))
        current_start = _as_timestamp(current.get("start_ts"))
        gap = int((current_start - previous_end).total_seconds())
        if gap < 0:
            continue
        if gap > max_gap_seconds:
            continue
        transition_id = deterministic_transition_id(
            current.get("patient_id"), previous.get("event_id"), current.get("event_id"), rule_version
        )
        result[transition_id] = {
            "transition_id": transition_id,
            "patient_id": current.get("patient_id"),
            "previous_event_id": previous.get("event_id"),
            "next_event_id": current.get("event_id"),
            "session_id": current.get("session_id"),
            "environment": current.get("environment"),
            "gap_seconds": gap,
            "rule_version": rule_version,
        }
    return list(result.values())


def aggregate_temporal_transitions(
    transitions: Iterable[dict[str, Any]],
    *,
    min_occurrences: int = 3,
    min_sessions: int = 2,
    min_days: int = 2,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    seen_ids: set[str] = set()
    for item in transitions:
        transition_id = str(item.get("transition_id") or "")
        if transition_id and transition_id in seen_ids:
            continue
        if transition_id:
            seen_ids.add(transition_id)
        key = (str(item.get("transition") or item.get("previous_label") or "?"), str(item.get("next_label") or "?"))
        grouped.setdefault(key, []).append(dict(item))
    rows: list[dict[str, Any]] = []
    for key, items in grouped.items():
        gaps = sorted(float(item.get("gap_seconds") or item.get("delta_seconds") or 0) for item in items)
        sessions = {str(item.get("session_id") or item.get("from_session_id") or "") for item in items if item.get("session_id") or item.get("from_session_id")}
        days = {
            _as_timestamp(item.get("timestamp") or item.get("completed_at")).date().isoformat()
            for item in items
            if item.get("timestamp") or item.get("completed_at")
        }
        stable = len(items) >= min_occurrences and len(sessions) >= min_sessions and len(days) >= min_days
        rows.append(
            {
                "transition": f"{key[0]} -> {key[1]}",
                "count": len(items),
                "unique_sessions": len(sessions),
                "unique_days": len(days),
                "environments": sorted({str(item.get("environment")) for item in items if item.get("environment")}),
                "median_gap_seconds": float(np.median(gaps)),
                "iqr_gap_seconds": float(np.percentile(gaps, 75) - np.percentile(gaps, 25)),
                "min_gap_seconds": min(gaps),
                "max_gap_seconds": max(gaps),
                "status": "estável" if stable else "evidência insuficiente",
                "stability_criteria": {
                    "minimum_occurrences": len(items) >= min_occurrences,
                    "minimum_sessions": len(sessions) >= min_sessions,
                    "minimum_days": len(days) >= min_days,
                },
            }
        )
    return sorted(rows, key=lambda item: (item["count"], item["unique_sessions"], item["unique_days"]), reverse=True)


def validate_feature_columns(feature_columns: Sequence[str]) -> None:
    normalized = {_fold(name).replace(" ", "_") for name in feature_columns}
    leaking = sorted(normalized & POST_TARGET_FIELDS)
    identifiers = sorted(normalized & IDENTIFIER_FIELDS)
    if leaking:
        raise ValueError(f"Features posteriores ao alvo são proibidas: {', '.join(leaking)}.")
    if identifiers:
        raise ValueError(f"Identificadores não podem ser usados como features: {', '.join(identifiers)}.")


def chronological_session_split(
    frame: pd.DataFrame,
    *,
    timestamp_col: str,
    session_col: str,
    test_fraction: float = 0.25,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if frame.empty:
        raise ValueError("Não é possível dividir uma base vazia.")
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction deve estar entre 0 e 1.")
    data = frame.copy()
    data[timestamp_col] = pd.to_datetime(data[timestamp_col], errors="raise", utc=True)
    sessions = (
        data.groupby(session_col, as_index=False)[timestamp_col]
        .min()
        .sort_values(timestamp_col, kind="stable")
    )
    if len(sessions) < 2:
        raise ValueError("São necessárias ao menos duas sessões para separação temporal.")
    split_at = max(1, min(len(sessions) - 1, int(math.floor(len(sessions) * (1 - test_fraction)))))
    train_sessions = set(sessions.iloc[:split_at][session_col])
    test_sessions = set(sessions.iloc[split_at:][session_col])
    train = data[data[session_col].isin(train_sessions)].sort_values(timestamp_col).copy()
    test = data[data[session_col].isin(test_sessions)].sort_values(timestamp_col).copy()
    if train_sessions & test_sessions:
        raise RuntimeError("Uma sessão foi compartilhada entre treino e teste.")
    if train[timestamp_col].max() >= test[timestamp_col].min():
        raise ValueError("A separação cronológica não é estrita entre treino e teste.")
    return train, test, {
        "training_start": train[timestamp_col].min().isoformat(),
        "training_end": train[timestamp_col].max().isoformat(),
        "validation_start": test[timestamp_col].min().isoformat(),
        "validation_end": test[timestamp_col].max().isoformat(),
        "training_sessions": len(train_sessions),
        "validation_sessions": len(test_sessions),
    }


def fit_validated_logistic_model(
    frame: pd.DataFrame,
    *,
    target_col: str,
    timestamp_col: str,
    session_col: str,
    feature_columns: Sequence[str],
    config: ForecastConfig,
) -> dict[str, Any]:
    config.validate()
    validate_feature_columns(feature_columns)
    required = {target_col, timestamp_col, session_col, *feature_columns}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Colunas ausentes para o modelo: {', '.join(missing)}.")
    data = frame.copy()
    if "opportunity_observed" in data.columns:
        data = data[data["opportunity_observed"] == True].copy()  # noqa: E712
    if data[target_col].isna().any():
        raise ValueError("O alvo clínico não pode ser imputado.")
    values = set(int(value) for value in data[target_col].unique())
    if not values <= {0, 1}:
        raise ValueError("O alvo binário deve conter apenas 0 e 1.")
    if values != {0, 1}:
        raise ValueError("O modelo requer positivos e negativos observáveis; não crie negativos artificiais.")
    train, test, periods = chronological_session_split(
        data, timestamp_col=timestamp_col, session_col=session_col, test_fraction=0.25
    )
    if train[target_col].nunique() < 2 or test[target_col].nunique() < 2:
        raise ValueError("Treino e validação precisam conter as duas classes para métricas preditivas confiáveis.")
    numeric = [name for name in feature_columns if pd.api.types.is_numeric_dtype(train[name])]
    categorical = [name for name in feature_columns if name not in numeric]
    transformers = []
    if numeric:
        transformers.append(
            ("numeric", Pipeline([("imputer", SimpleImputer(strategy="median", add_indicator=True)), ("scale", StandardScaler())]), numeric)
        )
    if categorical:
        transformers.append(
            ("categorical", Pipeline([("imputer", SimpleImputer(strategy="most_frequent")), ("onehot", OneHotEncoder(handle_unknown="ignore"))]), categorical)
        )
    model = Pipeline(
        [
            ("preprocess", ColumnTransformer(transformers, remainder="drop")),
            ("classifier", LogisticRegression(C=1.0, max_iter=2000, random_state=config.random_seed)),
        ]
    )
    model.fit(train[list(feature_columns)], train[target_col].astype(int))
    probabilities = model.predict_proba(test[list(feature_columns)])[:, 1]
    baseline_probability = float(train[target_col].mean())
    metrics = binary_probability_metrics(test[target_col].to_numpy(), probabilities, threshold=config.threshold)
    baselines = _predictive_baselines(
        train,
        test,
        target_col=target_col,
        feature_columns=feature_columns,
        threshold=config.threshold,
    )
    baseline_entry = min(baselines, key=lambda item: item["metrics"]["brier_score"])
    baseline_metrics = baseline_entry["metrics"]
    gain = metrics["brier_score"] < baseline_metrics["brier_score"] and metrics["log_loss"] <= baseline_metrics["log_loss"]
    drift = compute_drift_report(train, test, feature_columns=feature_columns, target_col=target_col)
    explanation = explain_logistic_prediction(model, test[list(feature_columns)].iloc[[-1]], float(probabilities[-1]))
    backtest = rolling_origin_backtest(
        data,
        target_col=target_col,
        timestamp_col=timestamp_col,
        session_col=session_col,
        feature_columns=feature_columns,
        model_template=model,
        threshold=config.threshold,
    )
    run_hash = analysis_run_hash(
        data.to_dict(orient="records"),
        {
            "methodology_version": METHODOLOGY_VERSION,
            "model_version": config.model_version,
            "feature_version": config.feature_version,
            "features": list(feature_columns),
            "model_parameters": {"penalty": "l2", "C": 1.0, "max_iter": 2000, "random_seed": config.random_seed},
            "threshold": config.threshold,
            "periods": periods,
        },
    )
    return {
        "analysis_mode": "predictive",
        "target": config.target_behavior,
        "prediction_unit": config.prediction_unit,
        "prediction_horizon": config.prediction_horizon,
        "model": "Regressão logística regularizada L2",
        "model_version": config.model_version,
        "feature_version": config.feature_version,
        "model_parameters": {"penalty": "l2", "C": 1.0, "max_iter": 2000, "random_seed": config.random_seed},
        "features": list(feature_columns),
        "periods": periods,
        "training_positives": int(train[target_col].sum()),
        "training_negatives": int((1 - train[target_col]).sum()),
        "validation_positives": int(test[target_col].sum()),
        "validation_negatives": int((1 - test[target_col]).sum()),
        "training_prevalence": float(train[target_col].mean()),
        "validation_prevalence": float(test[target_col].mean()),
        "metrics": metrics,
        "baseline": baseline_entry,
        "baselines": baselines,
        "reliable_gain_over_baseline": gain,
        "conclusion": (
            "O modelo apresentou ganho preditivo fora da amostra sobre a referência histórica."
            if gain
            else "O modelo não apresentou ganho preditivo confiável sobre a referência histórica."
        ),
        "threshold": config.threshold,
        "threshold_rule": config.threshold_rule,
        "operational_evaluation": metrics["operational"],
        "drift": drift,
        "individual_explanation": explanation,
        "rolling_origin_backtest": backtest,
        "analysis_run_hash": run_hash,
        "generalization_status": "desconhecida fora do paciente, dos ambientes e do período avaliados",
    }


def binary_probability_metrics(y_true: Sequence[int], probabilities: Sequence[float], *, threshold: float) -> dict[str, Any]:
    y = np.asarray(y_true, dtype=int)
    p = np.clip(np.asarray(probabilities, dtype=float), 1e-12, 1 - 1e-12)
    if len(y) == 0 or len(y) != len(p):
        raise ValueError("Desfechos e probabilidades devem ter o mesmo tamanho não vazio.")
    predicted = (p >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, predicted, labels=[0, 1]).ravel()
    calibration = calibration_metrics(y, p)
    return {
        "brier_score": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "pr_auc": float(average_precision_score(y, p)) if len(np.unique(y)) == 2 else None,
        "roc_auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
        "sensitivity": _safe_div(tp, tp + fn),
        "specificity": _safe_div(tn, tn + fp),
        "positive_predictive_value": _safe_div(tp, tp + fp),
        "negative_predictive_value": _safe_div(tn, tn + fn),
        "accuracy": _safe_div(tp + tn, tp + tn + fp + fn),
        "precision": _safe_div(tp, tp + fp),
        "recall": _safe_div(tp, tp + fn),
        "false_positive_rate": _safe_div(fp, fp + tn),
        "false_negative_rate": _safe_div(fn, fn + tp),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "operational": {
            "threshold": float(threshold),
            "alerts": int(tp + fp),
            "alert_rate": _safe_div(tp + fp, len(y)),
            "false_alerts": int(fp),
            "missed_events": int(fn),
            "detected_events": int(tp),
            "evaluated_opportunities": int(len(y)),
        },
        **calibration,
    }


def calibration_metrics(y_true: Sequence[int], probabilities: Sequence[float], bins: int = 10) -> dict[str, Any]:
    y = np.asarray(y_true, dtype=float)
    p = np.clip(np.asarray(probabilities, dtype=float), 1e-6, 1 - 1e-6)
    logit = np.log(p / (1 - p))
    design = np.column_stack([np.ones(len(logit)), logit])
    try:
        # Ajuste logístico simples por Newton-Raphson para intercepto e inclinação de calibração.
        coefficients = np.zeros(2)
        for _ in range(50):
            fitted = 1 / (1 + np.exp(-(design @ coefficients)))
            weights = np.clip(fitted * (1 - fitted), 1e-8, None)
            gradient = design.T @ (y - fitted)
            hessian = -(design.T * weights) @ design
            step = np.linalg.solve(hessian, gradient)
            coefficients -= step
            if np.max(np.abs(step)) < 1e-8:
                break
        intercept, slope = map(float, coefficients)
    except (np.linalg.LinAlgError, FloatingPointError):
        intercept, slope = None, None
    edges = np.linspace(0, 1, bins + 1)
    assignments = np.minimum(np.digitize(p, edges[1:-1]), bins - 1)
    ece = 0.0
    curve = []
    for index in range(bins):
        mask = assignments == index
        if not mask.any():
            continue
        predicted_mean = float(p[mask].mean())
        observed_mean = float(y[mask].mean())
        count = int(mask.sum())
        ece += count / len(y) * abs(predicted_mean - observed_mean)
        curve.append({"predicted": predicted_mean, "observed": observed_mean, "n": count})
    return {"calibration_intercept": intercept, "calibration_slope": slope, "expected_calibration_error": float(ece), "calibration_curve": curve}


def analysis_run_hash(records: Iterable[dict[str, Any]], metadata: dict[str, Any] | None = None) -> str:
    """Hash reproduzível da entrada analítica e dos parâmetros."""
    rows = [dict(item) for item in records]
    rows.sort(
        key=lambda item: (
            str(item.get("timestamp") or item.get("data_hora") or item.get("start_ts") or ""),
            str(item.get("session_id") or item.get("sessao_id") or ""),
            str(item.get("event_id") or item.get("evento_id") or item.get("intervalo_id") or ""),
        )
    )
    payload = {"records": rows, "metadata": metadata or {}}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cluster_bootstrap_interval(
    outcomes: Sequence[int | bool],
    clusters: Sequence[Any],
    *,
    level: float = 0.95,
    resamples: int = 2000,
    seed: int = 20260720,
) -> dict[str, Any]:
    """IC por reamostragem de sessões, mantendo episódios correlacionados no mesmo bloco."""
    y = np.asarray(outcomes, dtype=float)
    cluster_values = np.asarray([str(value) for value in clusters], dtype=object)
    if len(y) == 0 or len(y) != len(cluster_values):
        raise ValueError("Desfechos e sessões devem ter o mesmo tamanho não vazio.")
    if not 0 < level < 1 or resamples < 100:
        raise ValueError("Nível e número de reamostragens inválidos.")
    unique = np.unique(cluster_values)
    if len(unique) < 2:
        raise ValueError("O intervalo agrupado requer pelo menos duas sessões.")
    grouped = {cluster: y[cluster_values == cluster] for cluster in unique}
    rng = np.random.default_rng(seed)
    estimates = np.empty(resamples, dtype=float)
    for index in range(resamples):
        sampled = rng.choice(unique, size=len(unique), replace=True)
        values = np.concatenate([grouped[cluster] for cluster in sampled])
        estimates[index] = float(values.mean())
    alpha = (1 - level) / 2
    return {
        "estimate": float(y.mean()),
        "lower": float(np.quantile(estimates, alpha)),
        "upper": float(np.quantile(estimates, 1 - alpha)),
        "level": float(level),
        "method": "cluster_bootstrap_by_session",
        "cluster_count": int(len(unique)),
        "resamples": int(resamples),
        "seed": int(seed),
        "independence_assumption": "episódios podem ser correlacionados dentro da sessão",
    }


def audit_abc_data_quality(
    records: Iterable[dict[str, Any]],
    intervals: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Auditoria conservadora: só classifica como erro o que é verificável nos campos disponíveis."""
    record_rows = [dict(item) for item in records]
    interval_rows = [dict(item) for item in intervals]

    def first_value(item: dict[str, Any], names: Sequence[str]) -> Any:
        return next((item.get(name) for name in names if item.get(name) not in (None, "")), None)

    identifiers = [first_value(item, ("event_id", "evento_id", "intervalo_id", "interval_id")) for item in record_rows]
    nonempty_ids = [str(value) for value in identifiers if value not in (None, "")]
    duplicate_identifiers = len(nonempty_ids) - len(set(nonempty_ids))
    exact_keys = [
        tuple(str(item.get(key) or "") for key in ("data_hora", "session_id", "ambiente", "antecedente", "comportamento", "consequencia"))
        for item in record_rows
    ]
    duplicate_exact_rows = len(exact_keys) - len(set(exact_keys))

    invalid_timestamps = 0
    missing_timestamps = 0
    for item in record_rows:
        value = first_value(item, ("data_hora", "timestamp", "start_ts", "onset_ts"))
        if value in (None, ""):
            missing_timestamps += 1
            continue
        try:
            pd.to_datetime(value, errors="raise", utc=True)
        except Exception:
            invalid_timestamps += 1

    overlap_count = 0
    overlap_evaluable = False
    grouped_intervals: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] = {}
    for item in interval_rows:
        start = first_value(item, ("start_ts", "onset_ts", "inicio_ts", "data_hora_inicio"))
        end = first_value(item, ("end_ts", "offset_ts", "fim_ts", "data_hora_fim"))
        session = first_value(item, ("session_id", "sessao_id"))
        if not start or not end or not session:
            continue
        try:
            start_ts = pd.to_datetime(start, errors="raise", utc=True)
            end_ts = pd.to_datetime(end, errors="raise", utc=True)
        except Exception:
            invalid_timestamps += 1
            continue
        overlap_evaluable = True
        if end_ts <= start_ts:
            invalid_timestamps += 1
            continue
        grouped_intervals.setdefault(str(session), []).append((start_ts, end_ts))
    for values in grouped_intervals.values():
        values.sort(key=lambda pair: pair[0])
        previous_end = None
        for start, end in values:
            if previous_end is not None and start < previous_end:
                overlap_count += 1
            previous_end = max(previous_end, end) if previous_end is not None else end

    observers = {
        str(first_value(item, ("observer_id", "observador_token", "observador_id", "created_by")))
        for item in [*record_rows, *interval_rows]
        if first_value(item, ("observer_id", "observador_token", "observador_id", "created_by"))
    }
    environments = Counter(str(item.get("ambiente") or item.get("environment") or "Não informado") for item in record_rows)
    source_systems = sorted(
        {
            str(first_value(item, ("source_system", "sistema_origem")))
            for item in [*record_rows, *interval_rows]
            if first_value(item, ("source_system", "sistema_origem"))
        }
    )
    taxonomy_versions = sorted(
        {
            str(first_value(item, ("taxonomy_version", "versao_taxonomia", "instrumento_versao")))
            for item in [*record_rows, *interval_rows]
            if first_value(item, ("taxonomy_version", "versao_taxonomia", "instrumento_versao"))
        }
    )
    outside_taxonomy = sum(
        bool(item.get("fora_taxonomia")) or item.get("taxonomy_valid") is False
        for item in record_rows
    )
    return {
        "duplicate_identifiers": int(duplicate_identifiers),
        "duplicate_exact_rows": int(duplicate_exact_rows),
        "invalid_timestamps": int(invalid_timestamps),
        "missing_timestamps": int(missing_timestamps),
        "overlapping_intervals": int(overlap_count),
        "overlap_evaluable": overlap_evaluable,
        "outside_taxonomy": int(outside_taxonomy),
        "taxonomy_evaluable": any("taxonomy_valid" in item or "fora_taxonomia" in item for item in record_rows),
        "observer_count": int(len(observers)),
        "observer_identification_available": bool(observers),
        "missing_record_identifiers": int(len(record_rows) - len(nonempty_ids)),
        "environments": dict(sorted(environments.items())),
        "source_systems": source_systems,
        "taxonomy_versions": taxonomy_versions,
    }


def compute_exposure_summary(
    records: Iterable[dict[str, Any]],
    intervals: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Calcula taxas somente com tempo explicitamente observado; nunca inventa exposição ausente."""
    records_rows = [dict(item) for item in records]
    interval_rows = [dict(item) for item in intervals]
    record_by_interval = {
        str(item.get("intervalo_id") or item.get("interval_id")): item
        for item in records_rows
        if item.get("intervalo_id") or item.get("interval_id")
    }
    occurrence_ids = set(record_by_interval)
    total_minutes = 0.0
    observed_ids: set[str] = set()
    by_environment: dict[str, dict[str, float]] = {}
    by_date: dict[str, dict[str, Any]] = {}
    for item in interval_rows:
        if str(item.get("status_observacao") or "") != "observed":
            continue
        interval_id = str(item.get("intervalo_id") or item.get("interval_id") or "")
        duration = float(item.get("duracao_planejada_minutos") or item.get("duration_minutes") or 0)
        if duration <= 0:
            continue
        total_minutes += duration
        if interval_id:
            observed_ids.add(interval_id)
        record = record_by_interval.get(interval_id, {})
        environment = str(item.get("ambiente") or item.get("environment") or record.get("ambiente") or record.get("environment") or "Não informado")
        timestamp = item.get("start_ts") or item.get("onset_ts") or item.get("data_hora_inicio") or record.get("data_hora") or record.get("timestamp")
        date_label = "Não informado"
        if timestamp:
            try:
                date_label = pd.to_datetime(timestamp, errors="raise").strftime("%Y-%m-%d")
            except Exception:
                pass
        env = by_environment.setdefault(environment, {"observed_minutes": 0.0, "occurrence_intervals": 0.0})
        env["observed_minutes"] += duration
        day = by_date.setdefault(date_label, {"observed_minutes": 0.0, "occurrence_intervals": 0, "behaviors": Counter()})
        day["observed_minutes"] += duration
        if interval_id in occurrence_ids:
            env["occurrence_intervals"] += 1
            day["occurrence_intervals"] += 1
            behavior = str(record.get("comportamento") or "Não informado")
            day["behaviors"][behavior] += 1
    observed_hours = total_minutes / 60
    occurrence_observed = len(observed_ids & occurrence_ids)
    for item in by_environment.values():
        hours = item["observed_minutes"] / 60
        item["observed_hours"] = hours
        item["occurrences_per_hour"] = item["occurrence_intervals"] / hours if hours else None
    serial_days = []
    for date_label, item in sorted(by_date.items()):
        hours = item["observed_minutes"] / 60
        serial_days.append(
            {
                "date": date_label,
                "observed_hours": hours,
                "occurrence_intervals": item["occurrence_intervals"],
                "occurrences_per_hour": item["occurrence_intervals"] / hours if hours else None,
                "behavior_counts": dict(item["behaviors"]),
                "behavior_rates_per_hour": {
                    behavior: count / hours for behavior, count in item["behaviors"].items()
                } if hours else {},
            }
        )
    return {
        "status": "calculable" if observed_hours > 0 else "unavailable",
        "observed_hours": observed_hours,
        "observed_intervals_with_duration": len(observed_ids),
        "occurrence_intervals": occurrence_observed,
        "opportunities_without_occurrence": max(0, len(observed_ids) - occurrence_observed),
        "occurrences_per_hour": occurrence_observed / observed_hours if observed_hours else None,
        "by_environment": by_environment,
        "by_date": serial_days,
        "warning": (
            "Taxas por hora usam somente intervalos explicitamente observados e com duração positiva."
            if observed_hours
            else "Exposição temporal indisponível; contagens não foram convertidas em taxas."
        ),
        "unavailable_denominators": [
            "tempo em demanda",
            "tempo em transição",
        ],
    }


def compute_drift_report(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    feature_columns: Sequence[str],
    target_col: str | None = None,
) -> dict[str, Any]:
    """PSI exploratório entre treino e validação; não substitui monitoramento prospectivo."""
    details = []
    for column in feature_columns:
        train_values = train[column]
        test_values = test[column]
        if pd.api.types.is_numeric_dtype(train_values):
            numeric_train = pd.to_numeric(train_values, errors="coerce").dropna()
            numeric_test = pd.to_numeric(test_values, errors="coerce").dropna()
            if numeric_train.empty or numeric_test.empty:
                psi = None
            else:
                edges = np.unique(np.quantile(numeric_train, np.linspace(0, 1, 6)))
                if len(edges) < 2:
                    psi = 0.0 if numeric_train.iloc[0] == numeric_test.median() else 1.0
                else:
                    edges[0], edges[-1] = -np.inf, np.inf
                    psi = _population_stability_index(
                        np.histogram(numeric_train, bins=edges)[0],
                        np.histogram(numeric_test, bins=edges)[0],
                    )
        else:
            train_counts = train_values.fillna("<ausente>").astype(str).value_counts()
            test_counts = test_values.fillna("<ausente>").astype(str).value_counts()
            categories = sorted(set(train_counts.index) | set(test_counts.index))
            psi = _population_stability_index(
                np.array([train_counts.get(value, 0) for value in categories]),
                np.array([test_counts.get(value, 0) for value in categories]),
            )
        details.append({"feature": column, "psi": psi})
    finite = [float(item["psi"]) for item in details if item["psi"] is not None and math.isfinite(float(item["psi"]))]
    maximum = max(finite) if finite else None
    status = "indisponível" if maximum is None else "estável" if maximum < 0.10 else "monitorar" if maximum < 0.25 else "mudança material"
    prevalence_shift = None
    if target_col and target_col in train and target_col in test:
        prevalence_shift = float(test[target_col].mean() - train[target_col].mean())
    return {
        "status": status,
        "max_psi": maximum,
        "feature_details": details,
        "target_prevalence_shift": prevalence_shift,
        "thresholds": {"stable_below": 0.10, "material_at_or_above": 0.25},
        "scope": "treino versus validação cronológica; generalização externa não avaliada",
    }


def explain_logistic_prediction(model: Pipeline, row: pd.DataFrame, probability: float, top_n: int = 8) -> dict[str, Any]:
    preprocess = model.named_steps["preprocess"]
    classifier = model.named_steps["classifier"]
    transformed = preprocess.transform(row)
    values = transformed.toarray()[0] if hasattr(transformed, "toarray") else np.asarray(transformed)[0]
    names = list(preprocess.get_feature_names_out())
    coefficients = classifier.coef_[0]
    contributions = sorted(
        (
            {"feature": str(name), "log_odds_contribution": float(value * coefficient)}
            for name, value, coefficient in zip(names, values, coefficients, strict=False)
        ),
        key=lambda item: abs(item["log_odds_contribution"]),
        reverse=True,
    )[:top_n]
    return {
        "predicted_probability": float(probability),
        "intercept_log_odds": float(classifier.intercept_[0]),
        "top_contributions": contributions,
        "interpretation": "Contribuições em log-odds para a última oportunidade de validação; não representam causalidade.",
    }


def rolling_origin_backtest(
    data: pd.DataFrame,
    *,
    target_col: str,
    timestamp_col: str,
    session_col: str,
    feature_columns: Sequence[str],
    model_template: Pipeline,
    threshold: float,
) -> dict[str, Any]:
    ordered = data.sort_values(timestamp_col).copy()
    sessions = list(dict.fromkeys(ordered[session_col].astype(str)))
    if len(sessions) < 6:
        return {"status": "insuficiente", "fold_count": 0, "reason": "menos de seis sessões para backtesting progressivo"}
    minimum_training_sessions = max(4, len(sessions) // 2)
    outcomes: list[int] = []
    probabilities: list[float] = []
    folds = []
    for cut in range(minimum_training_sessions, len(sessions)):
        train_sessions = set(sessions[:cut])
        test_session = sessions[cut]
        fold_train = ordered[ordered[session_col].astype(str).isin(train_sessions)]
        fold_test = ordered[ordered[session_col].astype(str) == test_session]
        if fold_train[target_col].nunique() < 2 or fold_test.empty:
            continue
        fold_model = clone(model_template)
        fold_model.fit(fold_train[list(feature_columns)], fold_train[target_col].astype(int))
        fold_probabilities = fold_model.predict_proba(fold_test[list(feature_columns)])[:, 1]
        outcomes.extend(fold_test[target_col].astype(int).tolist())
        probabilities.extend(fold_probabilities.tolist())
        folds.append(
            {
                "test_session": test_session,
                "training_end": str(pd.to_datetime(fold_train[timestamp_col], utc=True).max()),
                "test_start": str(pd.to_datetime(fold_test[timestamp_col], utc=True).min()),
                "n": int(len(fold_test)),
            }
        )
    if not outcomes or len(set(outcomes)) < 2:
        return {"status": "insuficiente", "fold_count": len(folds), "reason": "backtesting não reuniu as duas classes"}
    return {
        "status": "avaliado",
        "fold_count": len(folds),
        "metrics": binary_probability_metrics(outcomes, probabilities, threshold=threshold),
        "folds": folds,
    }


def _predictive_baselines(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    target_col: str,
    feature_columns: Sequence[str],
    threshold: float,
) -> list[dict[str, Any]]:
    prevalence = float(train[target_col].mean())
    candidates: list[tuple[str, np.ndarray, float | None]] = [
        ("prevalência histórica de treino", np.full(len(test), prevalence), prevalence),
        ("sempre prever ausência", np.zeros(len(test)), 0.0),
        ("sempre prever ocorrência", np.ones(len(test)), 1.0),
    ]
    for names in (("ambiente", "environment"), ("antecedente", "antecedent")):
        column = next((name for name in names if name in feature_columns and name in train.columns), None)
        if not column:
            continue
        grouped = train.groupby(column, dropna=False)[target_col].mean()
        predictions = test[column].map(grouped).fillna(prevalence).to_numpy(dtype=float)
        candidates.append((f"frequência de treino por {column}", predictions, None))
    return [
        {
            "name": name,
            "probability": probability,
            "metrics": binary_probability_metrics(test[target_col].to_numpy(), predictions, threshold=threshold),
        }
        for name, predictions, probability in candidates
    ]


def _population_stability_index(reference_counts: np.ndarray, current_counts: np.ndarray) -> float:
    reference = np.asarray(reference_counts, dtype=float) + 0.5
    current = np.asarray(current_counts, dtype=float) + 0.5
    reference /= reference.sum()
    current /= current.sum()
    return float(np.sum((current - reference) * np.log(current / reference)))


def _fold(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    return re.sub(r"\s+", " ", "".join(char for char in normalized if not unicodedata.combining(char))).strip()


def _as_timestamp(value: Any) -> pd.Timestamp:
    if value is None or value == "":
        raise ValueError("Timestamp obrigatório ausente.")
    return pd.to_datetime(value, errors="raise", utc=True)


def _safe_div(numerator: float, denominator: float) -> float | None:
    return float(numerator / denominator) if denominator else None
