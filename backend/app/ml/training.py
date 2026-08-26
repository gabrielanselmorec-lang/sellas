from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from backend.app.ml.abstention import evaluate_abstention, prediction_audit_id, probability_uncertainty
from backend.app.ml.features import CATEGORICAL_FEATURES, NUMERIC_FEATURES, FeatureConfig, baseline_event_rate, build_feature_frame, split_xy
from backend.app.ml.drift import reference_stats
from backend.app.ml.metrics import calibration_summary, compute_operational_metrics
from backend.app.ml.model_store import ModelVersionMetadata, create_version_id, load_latest_model, save_model_version
from backend.app.ml.validation import evaluate_slices, grouped_event_rates, temporal_split
from backend.app.services.note_extraction import extraction_to_feature_rows
from backend.app.services.storage import load_note_extractions
from backend.app.utils.risk import classify_risk


@dataclass
class TrainedModelBundle:
    patient_id: str | None
    behavior_name: str
    model_name: str
    model: Any
    metrics: dict[str, Any]
    trained_at: str
    feature_frame: pd.DataFrame | None
    metadata: dict[str, Any]


class HistoricalBaselineModel:
    def fit(self, y: pd.Series) -> "HistoricalBaselineModel":
        self.probability_ = float(y.mean()) if len(y) else 0.0
        return self

    def predict_proba(self, x: pd.DataFrame) -> np.ndarray:
        p = getattr(self, "probability_", 0.0)
        return np.column_stack([np.full(len(x), 1 - p), np.full(len(x), p)])


class CalibratedProbabilityModel:
    def __init__(self, base_model: Any, calibrator: Any) -> None:
        self.base_model = base_model
        self.calibrator = calibrator

    def predict_proba(self, x: pd.DataFrame) -> np.ndarray:
        raw_probability = self.base_model.predict_proba(x)[:, 1].reshape(-1, 1)
        return self.calibrator.predict_proba(raw_probability)


MODEL_REGISTRY: dict[str, TrainedModelBundle] = {}


def train_models(
    records: list[dict],
    patient_id: str | None,
    behavior_name: str,
    feature_config: FeatureConfig | None = None,
) -> dict[str, Any]:
    feature_config = feature_config or FeatureConfig()
    note_rows = extraction_to_feature_rows(load_note_extractions(patient_id=patient_id))
    frame = build_feature_frame(records, patient_id=patient_id, behavior_name=behavior_name, config=feature_config, note_feature_rows=note_rows)
    if len(frame) < 12:
        raise ValueError("Dados insuficientes para treino. Sincronize mais sessoes ou amplie o intervalo.")

    x, y = split_xy(frame)
    if y.nunique() < 2:
        raise ValueError("O comportamento-alvo precisa ter ocorrencias e nao ocorrencias para treinar modelos supervisionados.")

    try:
        from sklearn.compose import ColumnTransformer
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import (
            accuracy_score,
            average_precision_score,
            brier_score_loss,
            confusion_matrix,
            f1_score,
            precision_score,
            recall_score,
            roc_auc_score,
        )
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder, StandardScaler
    except ImportError as exc:
        raise RuntimeError("scikit-learn e joblib precisam estar instalados para treinar o MVP.") from exc

    train_frame, test_frame, temporal_info = temporal_split(frame)
    x_train, y_train = split_xy(train_frame)
    x_test, y_test = split_xy(test_frame)

    def preprocessor():
        return ColumnTransformer(
            transformers=[
                ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), NUMERIC_FEATURES),
                ("cat", Pipeline([("imputer", SimpleImputer(strategy="most_frequent")), ("onehot", OneHotEncoder(handle_unknown="ignore"))]), CATEGORICAL_FEATURES),
            ],
            remainder="drop",
        )

    candidates = {
        "baseline_historico": HistoricalBaselineModel().fit(y_train),
        "regressao_logistica": lambda: Pipeline(
            [("preprocess", preprocessor()), ("model", LogisticRegression(max_iter=1000, class_weight="balanced"))]
        ),
        "random_forest": lambda: Pipeline(
            [
                ("preprocess", preprocessor()),
                (
                    "model",
                    RandomForestClassifier(
                        n_estimators=240,
                        min_samples_leaf=3,
                        class_weight="balanced_subsample",
                        random_state=42,
                    ),
                ),
            ]
        ),
    }

    xgboost_status: dict[str, Any] = {"available": False, "enabled": False, "reason": "xgboost nao instalado"}
    try:
        from xgboost import XGBClassifier  # type: ignore

        candidates["xgboost"] = lambda: Pipeline(
            [
                ("preprocess", preprocessor()),
                (
                    "model",
                    XGBClassifier(
                        objective="binary:logistic",
                        eval_metric="logloss",
                        learning_rate=0.06,
                        max_depth=3,
                        n_estimators=160,
                        subsample=0.9,
                        colsample_bytree=0.9,
                        random_state=42,
                    ),
                ),
            ]
        )
        xgboost_status = {"available": True, "enabled": True, "objective": "binary:logistic"}
    except Exception:
        pass

    results: dict[str, Any] = {}
    calibration: dict[str, Any] = {}
    trained_models: dict[str, Any] = {"baseline_historico": candidates["baseline_historico"]}
    for name, model in candidates.items():
        if name != "baseline_historico":
            model, calibration[name] = _fit_with_probability_calibration(model(), x_train, y_train)
            trained_models[name] = model
        else:
            calibration[name] = {"method": "historical_rate", "calibrated": False}
        probabilities = model.predict_proba(x_test)[:, 1]
        predictions = (probabilities >= 0.5).astype(int)
        results[name] = {
            "accuracy": _safe_metric(accuracy_score, y_test, predictions),
            "precision": _safe_metric(precision_score, y_test, predictions, zero_division=0),
            "recall": _safe_metric(recall_score, y_test, predictions, zero_division=0),
            "f1_score": _safe_metric(f1_score, y_test, predictions, zero_division=0),
            "roc_auc": _safe_auc(roc_auc_score, y_test, probabilities),
            "pr_auc": _safe_auc(average_precision_score, y_test, probabilities),
            "brier_score": _safe_metric(brier_score_loss, y_test, probabilities),
            "confusion_matrix": confusion_matrix(y_test, predictions).tolist(),
            "calibration": calibration[name],
            "calibration_summary": calibration_summary(y_test, probabilities),
            "operational": compute_operational_metrics(
                y_test,
                probabilities,
                threshold=0.5,
                exposure_hours=float(pd.to_numeric(test_frame.get("coverage_minutes"), errors="coerce").fillna(0).sum() / 60),
            ),
        }

    selected_name = _select_model(results)
    key = registry_key(patient_id, behavior_name)
    trained_at = datetime.now(timezone.utc).isoformat()
    version_id = create_version_id(behavior_name, patient_id)
    validation = {
        "temporal_split": temporal_info,
        "train_slices": grouped_event_rates(train_frame),
        "test_slices": evaluate_slices(
            test_frame,
            trained_models[selected_name].predict_proba(x_test)[:, 1],
            (trained_models[selected_name].predict_proba(x_test)[:, 1] >= 0.5).astype(int),
        ),
        "target_contract": {
            "landmark_table": True,
            "censored_rows_excluded": True,
            "as_of_features": True,
            "note_temporal_filter": "authored_at <= landmark_ts and note_scope in pre_session/in_session_live",
        },
    }
    metadata = ModelVersionMetadata(
        version_id=version_id,
        patient_id=patient_id,
        behavior_name=behavior_name,
        selected_model=selected_name,
        trained_at=trained_at,
        artifact_path=f"{version_id}.joblib",
        samples=int(len(frame)),
        event_rate=float(y.mean()),
        metrics=results,
        validation=validation,
        calibration=calibration[selected_name],
        reference_stats=reference_stats(train_frame),
    )
    save_model_version(metadata, trained_models[selected_name])
    bundle = TrainedModelBundle(
        patient_id=patient_id,
        behavior_name=behavior_name,
        model_name=selected_name,
        model=trained_models[selected_name],
        metrics=results,
        trained_at=trained_at,
        feature_frame=frame,
        metadata=metadata.__dict__,
    )
    MODEL_REGISTRY[key] = bundle
    return {
        "model_version_id": version_id,
        "patient_id": patient_id,
        "behavior_name": behavior_name,
        "selected_model": selected_name,
        "trained_at": bundle.trained_at,
        "samples": int(len(frame)),
        "event_rate": float(y.mean()),
        "baseline_probability": baseline_event_rate(frame),
        "target_definition": target_definition(feature_config),
        "feature_config": feature_config.__dict__,
        "xgboost": xgboost_status,
        "count_model_experimental": poisson_count_probability(float(frame["frequency"].mean())),
        "calibration": calibration[selected_name],
        "validation": validation,
        "metrics": results,
        "feature_importance": feature_importance(trained_models[selected_name]),
    }


def predict_next_session(
    records: list[dict],
    patient_id: str | None,
    behavior_name: str,
    feature_config: FeatureConfig | None = None,
) -> dict[str, Any]:
    feature_config = feature_config or FeatureConfig()
    key = registry_key(patient_id, behavior_name)
    if key not in MODEL_REGISTRY:
        stored = load_latest_model(patient_id, behavior_name)
        if stored:
            model, metadata = stored
            MODEL_REGISTRY[key] = TrainedModelBundle(
                patient_id=patient_id,
                behavior_name=behavior_name,
                model_name=metadata["selected_model"],
                model=model,
                metrics=metadata.get("metrics", {}),
                trained_at=metadata.get("trained_at", ""),
                feature_frame=None,
                metadata=metadata,
            )
        else:
            train_models(records, patient_id=patient_id, behavior_name=behavior_name, feature_config=feature_config)
    bundle = MODEL_REGISTRY[key]
    note_rows = extraction_to_feature_rows(load_note_extractions(patient_id=patient_id))
    frame = build_feature_frame(records, patient_id=patient_id, behavior_name=behavior_name, config=feature_config, note_feature_rows=note_rows)
    if frame.empty:
        raise ValueError("Nao ha historico suficiente para gerar previsao.")
    latest = frame.tail(1)
    x_latest, _ = split_xy(latest)
    probability = float(bundle.model.predict_proba(x_latest)[:, 1][0])
    factors = explain_prediction(bundle, x_latest)
    latest_row = latest.iloc[0]
    baseline = round(baseline_event_rate(frame), 4)
    model_version_id = bundle.metadata.get("version_id")
    landmark_ts = latest_row.get("landmark_ts")
    abstention = evaluate_abstention(latest_row, observed_sessions=len(frame))
    uncertainty = probability_uncertainty(probability, len(frame))
    audit_id = prediction_audit_id(patient_id, behavior_name, landmark_ts, model_version_id)
    clinical_plan_ref = latest_row.get("clinical_plan_ref") or latest_row.get("intervention_plan_id")
    return {
        "patient_id": patient_id,
        "patient_token": patient_id,
        "behavior_name": behavior_name,
        "behavior_code": behavior_name,
        "landmark_ts": str(landmark_ts) if landmark_ts is not None else None,
        "prediction_window": feature_config.prediction_window,
        "horizon": feature_config.prediction_window,
        "horizon_sessions": feature_config.horizon_sessions,
        "target_definition": target_definition(feature_config),
        "probability": round(probability, 4),
        "risk_probability": round(probability, 4),
        "risk": classify_risk(probability),
        "baseline_probability": baseline,
        "personal_baseline": baseline,
        "uncertainty": uncertainty,
        "data_quality": abstention["data_quality"],
        "abstain": abstention["abstain"],
        "abstain_reason": abstention["abstain_reason"],
        "model": bundle.model_name,
        "model_version_id": model_version_id,
        "model_version": model_version_id,
        "clinical_plan_ref": clinical_plan_ref,
        "audit_id": audit_id,
        "calibration": bundle.metadata.get("calibration", {}),
        "trained_at": bundle.trained_at,
        "top_factors": factors,
        "associated_factors": factors,
        "clinical_factor_summary": clinical_factor_summary(factors),
        "governance": abstention["governance"],
        "clinical_disclaimer": "Esta previsao e uma ferramenta de apoio e nao substitui a avaliacao clinica do analista do comportamento.",
    }


def explain_prediction(bundle: TrainedModelBundle, x_latest: pd.DataFrame, top_n: int = 6) -> list[dict[str, Any]]:
    row = x_latest.iloc[0]
    readable = [
        ("Frequencia recente nas ultimas sessoes", row.get("frequency_recent_3", 0)),
        ("Media movel de frequencia", row.get("frequency_moving_avg", 0)),
        ("Intensidade media recente", row.get("intensity_recent_avg", 0)),
        ("Ambiente atual", row.get("environment", "")),
        ("Antecedente associado", row.get("antecedent", "")),
        ("Funcao hipotetizada", row.get("hypothesized_function", "")),
        ("Sessoes desde a ultima ocorrencia", row.get("sessions_since_last_occurrence", 0)),
    ]
    return [
        {"factor": factor, "value": _json_value(value)}
        for factor, value in readable[:top_n]
    ]


def target_definition(config: FeatureConfig) -> dict[str, Any]:
    return {
        "symbol": "Y_t,h",
        "positive_class": "1 se o comportamento-alvo ocorrer na janela futura h",
        "negative_class": "0 se o comportamento-alvo nao ocorrer na janela futura h",
        "prediction_window": config.prediction_window,
        "horizon_sessions": config.horizon_sessions,
        "mvp_default": config.prediction_window == "next_session" and config.horizon_sessions == 1,
        "landmark_contract": "features as-of ate t; alvo futuro em h; linhas censuradas excluidas do treino",
        "negative_class_rule": "0 somente quando ha cobertura observacional suficiente no horizonte",
    }


def poisson_count_probability(lambda_value: float) -> dict[str, Any]:
    lambda_value = max(float(lambda_value), 0.0)
    return {
        "status": "experimental",
        "model": "Poisson",
        "lambda": lambda_value,
        "probability_at_least_one": float(1 - np.exp(-lambda_value)),
        "equation": "P(Y >= 1) = 1 - exp(-lambda)",
    }


def feature_importance(model: Any, top_n: int = 20) -> list[dict[str, Any]]:
    base_model = getattr(model, "base_model", model)
    if hasattr(base_model, "calibrated_classifiers_") and base_model.calibrated_classifiers_:
        base_model = getattr(base_model.calibrated_classifiers_[0], "estimator", base_model)
    elif hasattr(base_model, "estimator") and base_model.estimator is not None:
        base_model = base_model.estimator
    if not hasattr(base_model, "named_steps"):
        return []
    estimator = base_model.named_steps.get("model")
    names = _feature_names(base_model)
    values = None
    importance_type = ""
    if hasattr(estimator, "feature_importances_"):
        values = estimator.feature_importances_
        importance_type = "feature_importances"
    elif hasattr(estimator, "coef_"):
        values = estimator.coef_[0]
        importance_type = "coeficiente_beta"
    if values is None:
        return []
    rows = [
        {"feature": str(name), "importance": float(value), "absolute_importance": float(abs(value)), "type": importance_type}
        for name, value in zip(names, values)
    ]
    return sorted(rows, key=lambda item: item["absolute_importance"], reverse=True)[:top_n]


def clinical_factor_summary(factors: list[dict[str, Any]]) -> str:
    if not factors:
        return "Nao houve fatores suficientes para explicar a previsao."
    labels = [str(item.get("factor", "")).lower() for item in factors[:4]]
    return "Os fatores que mais influenciaram a previsao foram: " + ", ".join(labels) + "."


def _feature_names(pipeline: Any) -> list[str]:
    try:
        preprocessor = pipeline.named_steps["preprocess"]
        return list(preprocessor.get_feature_names_out())
    except Exception:
        return NUMERIC_FEATURES + CATEGORICAL_FEATURES


def registry_key(patient_id: str | None, behavior_name: str) -> str:
    return f"{patient_id or 'all'}::{behavior_name.lower()}"


def _fit_with_probability_calibration(model: Any, x_train: pd.DataFrame, y_train: pd.Series) -> tuple[Any, dict[str, Any]]:
    try:
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        model.fit(x_train, y_train)
        return model, {"method": "none", "calibrated": False, "reason": "sklearn indisponivel"}

    if len(x_train) < 12:
        model.fit(x_train, y_train)
        return model, {"method": "none", "calibrated": False, "reason": "amostra insuficiente"}

    class_counts = y_train.value_counts()
    if len(x_train) >= 30 and len(class_counts) == 2 and int(class_counts.min()) >= 3:
        method = "isotonic" if len(x_train) >= 120 and int(class_counts.min()) >= 20 else "sigmoid"
        calibrated = CalibratedClassifierCV(model, method=method, cv=3)
        calibrated.fit(x_train, y_train)
        return calibrated, {
            "method": f"calibrated_classifier_cv_{method}",
            "calibrated": True,
            "calibration_samples": int(len(x_train)),
            "cv": 3,
        }

    split_idx = max(1, min(len(x_train) - 1, int(len(x_train) * 0.75)))
    x_fit, x_cal = x_train.iloc[:split_idx], x_train.iloc[split_idx:]
    y_fit, y_cal = y_train.iloc[:split_idx], y_train.iloc[split_idx:]
    if y_fit.nunique() < 2 or y_cal.nunique() < 2:
        model.fit(x_train, y_train)
        return model, {"method": "none", "calibrated": False, "reason": "calibracao sem duas classes"}

    model.fit(x_fit, y_fit)
    raw_probability = model.predict_proba(x_cal)[:, 1].reshape(-1, 1)
    calibrator = LogisticRegression().fit(raw_probability, y_cal)
    return CalibratedProbabilityModel(model, calibrator), {
        "method": "platt_sigmoid_holdout",
        "calibrated": True,
        "calibration_samples": int(len(x_cal)),
    }


def _select_model(results: dict[str, dict[str, Any]]) -> str:
    ranked = sorted(
        results.items(),
        key=lambda item: (
            item[1].get("recall") or 0,
            item[1].get("f1_score") or 0,
            -(item[1].get("brier_score") or 1),
        ),
        reverse=True,
    )
    return ranked[0][0]


def _safe_metric(fn, *args, **kwargs) -> float:
    try:
        value = fn(*args, **kwargs)
        if pd.isna(value):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _safe_auc(fn, y_true, probabilities) -> float | None:
    try:
        if len(set(y_true)) < 2:
            return None
        return float(fn(y_true, probabilities))
    except Exception:
        return None


def _json_value(value: Any) -> Any:
    if isinstance(value, (np.integer, np.floating)):
        return float(value)
    return value
