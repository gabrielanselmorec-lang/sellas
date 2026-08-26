"""Regressão metodológica do relatório ABC v2 com dados exclusivamente sintéticos."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from app.services.abc_methodology import (
    ForecastConfig,
    analysis_run_hash,
    aggregate_temporal_transitions,
    audit_abc_data_quality,
    beta_posterior_interval,
    build_temporal_transitions,
    chronological_session_split,
    cluster_bootstrap_interval,
    compute_exposure_summary,
    compute_evidence_quality,
    descriptive_behavior_estimate,
    deterministic_transition_id,
    fit_validated_logistic_model,
    normalize_abc_records,
    normalize_clinical_value,
    severity_summary,
    validate_binomial_counts,
    validate_feature_columns,
    wilson_interval,
)


@pytest.mark.parametrize("k,n", [(0, 10), (10, 10)])
def test_wilson_extremes_are_bounded_and_reproducible(k, n):
    result = wilson_interval(k, n)
    assert result["estimate"] == k / n
    assert 0 <= result["lower"] <= result["estimate"] <= result["upper"] <= 1
    assert result["numerator"] == k and result["denominator"] == n


def test_beta_posterior_known_values_and_credible_label():
    result = beta_posterior_interval(3, 5, alpha=1, beta=1)
    assert result["estimate"] == 4 / 7
    assert result["posterior_alpha"] == 4
    assert result["posterior_beta"] == 3
    assert result["interval_kind"] == "credible"
    assert 0 <= result["lower"] <= result["estimate"] <= result["upper"] <= 1


@pytest.mark.parametrize("k,n", [(-1, 2), (3, 2), (0, 0), (1.5, 2), (True, 2)])
def test_invalid_binomial_counts_fail_early(k, n):
    with pytest.raises(ValueError):
        validate_binomial_counts(k, n)


def test_normalization_preserves_raw_and_handles_known_legacy_values():
    antecedent = normalize_clinical_value("  Do   nada ", "antecedente")
    consequence = normalize_clinical_value("Manejo fisíco", "consequencia")
    assert antecedent["raw_value"] == "  Do   nada "
    assert antecedent["normalized_value"] == "Antecedente não identificado no registro"
    assert consequence["normalized_value"] == "Manejo físico"
    assert antecedent["normalization_version"]


def test_normalize_records_keeps_audit_for_every_clinical_field():
    result = normalize_abc_records(
        [{"antecedente": "Do nada", "comportamento": " choro ", "consequencia": "Manejo fisíco", "ambiente": None, "funcao": "fuga ou esquiva"}]
    )[0]
    assert result["antecedente"] == "Antecedente não identificado no registro"
    assert result["funcao"] == "Fuga/esquiva"
    assert result["normalization_audit"]["comportamento"]["raw_value"] == " choro "


def test_descriptive_estimate_does_not_claim_absolute_risk():
    records = [
        {"antecedente": "Demanda", "comportamento": behavior, "consequencia": "Pausa", "ambiente": "Sala"}
        for behavior in ("Choro", "Choro", "Grito")
    ]
    result = descriptive_behavior_estimate(records, behavior="Choro")
    assert result["analysis_mode"] == "descriptive"
    assert result["estimate"] == 2 / 3
    assert result["numerator"] == 2 and result["denominator"] == 3
    assert result["absolute_risk_available"] is False
    assert "não o risco absoluto" in result["selection_bias_warning"]


def test_severity_counts_unclassified_and_never_calls_weight_probability():
    result = severity_summary(["C1", "C1", None], occurrence_frequency=0.5)
    assert result["classified_count"] == 2
    assert result["unclassified_count"] == 1
    assert result["mean_configured_severity_weight"] == 0.20
    assert result["exploratory_risk_index"] == 0.10
    assert "peso médio configurado: 0.20" in result["message"]


def test_evidence_quality_exposes_scores_reasons_and_warnings():
    quality = compute_evidence_quality(
        sample_size=20,
        session_count=1,
        day_count=2,
        missing_count=5,
        positive_count=20,
        negative_count=0,
    ).to_dict()
    assert quality["overall_quality"] in {"inicial", "limitada", "adequada"}
    assert quality["reasons"]
    assert any("oportunidades negativas" in warning for warning in quality["warnings"])
    assert quality["temporal_validation_score"] == 0


def test_transition_id_is_deterministic_and_rule_versioned():
    first = deterministic_transition_id("p", "e1", "e2", "v1")
    assert first == deterministic_transition_id("p", "e1", "e2", "v1")
    assert first != deterministic_transition_id("p", "e1", "e2", "v2")
    assert len(first) == 64


def test_temporal_transition_deduplicates_and_respects_gap_and_session():
    events = [
        {"patient_id": "p", "event_id": "e1", "session_id": "s1", "environment": "Sala", "start_ts": "2026-07-01T10:00:00Z", "end_ts": "2026-07-01T10:00:05Z"},
        {"patient_id": "p", "event_id": "e2", "session_id": "s1", "environment": "Sala", "start_ts": "2026-07-01T10:00:15Z", "end_ts": "2026-07-01T10:00:20Z"},
        {"patient_id": "p", "event_id": "e3", "session_id": "s2", "environment": "Sala", "start_ts": "2026-07-01T10:00:25Z", "end_ts": "2026-07-01T10:00:30Z"},
    ]
    rows = build_temporal_transitions(events, max_gap_seconds=15, rule_version="v1")
    assert len(rows) == 1
    assert rows[0]["gap_seconds"] == 10


def test_negative_and_excessive_gaps_are_rejected():
    overlap = [
        {"patient_id": "p", "event_id": "e1", "session_id": "s", "start_ts": "2026-07-01T10:00:00Z", "end_ts": "2026-07-01T10:00:20Z"},
        {"patient_id": "p", "event_id": "e2", "session_id": "s", "start_ts": "2026-07-01T10:00:10Z", "end_ts": "2026-07-01T10:00:30Z"},
    ]
    assert build_temporal_transitions(overlap, max_gap_seconds=5, rule_version="v1") == []
    overlap[1]["start_ts"] = "2026-07-01T10:01:00Z"
    assert build_temporal_transitions(overlap, max_gap_seconds=5, rule_version="v1") == []


def test_aggregate_transition_requires_occurrences_sessions_and_days():
    rows = [
        {"transition_id": f"t{i}", "transition": "A", "next_label": "B", "gap_seconds": i + 1, "session_id": f"s{i % 2}", "completed_at": f"2026-07-0{1 + i % 2}T10:00:00Z", "environment": "Sala"}
        for i in range(4)
    ]
    result = aggregate_temporal_transitions(rows)[0]
    assert result["count"] == 4
    assert result["status"] == "estável"
    assert result["iqr_gap_seconds"] >= 0


def test_leakage_and_identifier_columns_are_blocked():
    with pytest.raises(ValueError, match="posteriores"):
        validate_feature_columns(["ambiente", "consequencia"])
    with pytest.raises(ValueError, match="Identificadores"):
        validate_feature_columns(["ambiente", "patient_id"])


def test_chronological_split_never_shares_sessions_or_future():
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-07-01", periods=8, freq="D", tz="UTC"),
            "session_id": [f"s{i // 2}" for i in range(8)],
            "target": [0, 1] * 4,
        }
    )
    train, test, periods = chronological_session_split(frame, timestamp_col="timestamp", session_col="session_id")
    assert set(train.session_id).isdisjoint(set(test.session_id))
    assert train.timestamp.max() < test.timestamp.min()
    assert periods["training_end"] < periods["validation_start"]


def _predictive_frame() -> pd.DataFrame:
    rows = []
    for session in range(16):
        for opportunity in range(2):
            rows.append(
                {
                    "timestamp": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=session, minutes=opportunity),
                    "session_id": f"s{session}",
                    "opportunity_observed": True,
                    "target": int((session + opportunity) % 3 == 0),
                    "ambiente": "Sala" if session % 2 else "Casa",
                    "hora": 9 + opportunity,
                }
            )
    return pd.DataFrame(rows)


def _config() -> ForecastConfig:
    return ForecastConfig(
        target_behavior="Choro",
        prediction_unit="próxima oportunidade com demanda",
        prediction_horizon="uma oportunidade",
        reference_datetime="2026-07-20T12:00:00Z",
        environment_filter=None,
        patient_id="sintetico",
        training_start=None,
        training_end=None,
        validation_start=None,
        validation_end=None,
    )


def test_predictive_model_has_temporal_holdout_baseline_and_calibration():
    result = fit_validated_logistic_model(
        _predictive_frame(),
        target_col="target",
        timestamp_col="timestamp",
        session_col="session_id",
        feature_columns=["ambiente", "hora"],
        config=_config(),
    )
    assert result["analysis_mode"] == "predictive"
    assert result["baseline"]["name"]
    assert "brier_score" in result["metrics"]
    assert "expected_calibration_error" in result["metrics"]
    assert len(result["baselines"]) >= 4
    assert result["operational_evaluation"]["evaluated_opportunities"] > 0
    assert result["rolling_origin_backtest"]["fold_count"] > 0
    assert result["drift"]["status"] in {"estável", "monitorar", "mudança material"}
    assert result["individual_explanation"]["top_contributions"]
    assert len(result["analysis_run_hash"]) == 64
    assert result["conclusion"]


def test_predictive_model_refuses_single_class_and_no_observable_negatives():
    frame = _predictive_frame()
    frame["target"] = 1
    with pytest.raises(ValueError, match="positivos e negativos observáveis"):
        fit_validated_logistic_model(
            frame,
            target_col="target",
            timestamp_col="timestamp",
            session_col="session_id",
            feature_columns=["ambiente", "hora"],
            config=_config(),
        )


def test_fixed_seed_makes_metrics_reproducible():
    kwargs = dict(
        frame=_predictive_frame(),
        target_col="target",
        timestamp_col="timestamp",
        session_col="session_id",
        feature_columns=["ambiente", "hora"],
        config=_config(),
    )
    first = fit_validated_logistic_model(**kwargs)
    second = fit_validated_logistic_model(**kwargs)
    assert first["metrics"] == second["metrics"]
    assert math.isfinite(first["metrics"]["brier_score"])


def test_cluster_bootstrap_is_reproducible_and_session_aware():
    outcomes = [1, 1, 0, 0, 1, 0]
    sessions = ["s1", "s1", "s2", "s2", "s3", "s3"]
    first = cluster_bootstrap_interval(outcomes, sessions, resamples=500)
    second = cluster_bootstrap_interval(outcomes, sessions, resamples=500)
    assert first == second
    assert first["cluster_count"] == 3
    assert 0 <= first["lower"] <= first["upper"] <= 1
    with pytest.raises(ValueError, match="duas sessões"):
        cluster_bootstrap_interval([1, 0], ["s1", "s1"])


def test_quality_audit_detects_only_verifiable_problems():
    records = [
        {"event_id": "e1", "data_hora": "2026-07-01T10:00:00Z", "ambiente": "Sala", "taxonomy_valid": True, "observer_id": "o1"},
        {"event_id": "e1", "data_hora": "inválido", "ambiente": "Sala", "taxonomy_valid": False, "observer_id": "o1"},
    ]
    intervals = [
        {"session_id": "s1", "start_ts": "2026-07-01T10:00:00Z", "end_ts": "2026-07-01T10:05:00Z"},
        {"session_id": "s1", "start_ts": "2026-07-01T10:04:00Z", "end_ts": "2026-07-01T10:09:00Z"},
    ]
    audit = audit_abc_data_quality(records, intervals)
    assert audit["duplicate_identifiers"] == 1
    assert audit["invalid_timestamps"] == 1
    assert audit["overlapping_intervals"] == 1
    assert audit["outside_taxonomy"] == 1
    assert audit["observer_count"] == 1


def test_exposure_summary_uses_observed_time_and_negative_opportunities():
    records = [{"intervalo_id": "i1", "data_hora": "2026-07-01T10:00:00Z", "ambiente": "Sala", "comportamento": "Choro"}]
    intervals = [
        {"intervalo_id": "i1", "status_observacao": "observed", "duracao_planejada_minutos": 30},
        {"intervalo_id": "i2", "status_observacao": "observed", "duracao_planejada_minutos": 30, "ambiente": "Sala"},
        {"intervalo_id": "i3", "status_observacao": "not_observed", "duracao_planejada_minutos": 60},
    ]
    exposure = compute_exposure_summary(records, intervals)
    assert exposure["observed_hours"] == 1
    assert exposure["occurrences_per_hour"] == 1
    assert exposure["opportunities_without_occurrence"] == 1
    assert exposure["by_environment"]["Sala"]["observed_hours"] == 1


def test_analysis_run_hash_is_order_and_key_stable_but_parameter_sensitive():
    rows = [{"event_id": "e2", "timestamp": "2026-01-02"}, {"event_id": "e1", "timestamp": "2026-01-01"}]
    first = analysis_run_hash(rows, {"b": 2, "a": 1})
    second = analysis_run_hash(list(reversed(rows)), {"a": 1, "b": 2})
    assert first == second
    assert first != analysis_run_hash(rows, {"a": 2, "b": 2})
