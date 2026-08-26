from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app.services.abc_chains as abc_chains

from app.services.abc_chains import (
    calculate_chain_statistics,
    chain_features_from_candidates,
    detect_chain_candidates,
    normalize_chain_config,
    transition_match,
)


UTC = timezone.utc


def test_chain_schema_initialization_runs_only_once(monkeypatch):
    calls = []
    monkeypatch.setattr(abc_chains, "_ABC_CHAIN_TABLES_READY", False)
    monkeypatch.setattr(abc_chains, "_initialize_abc_chain_tables", lambda engine: calls.append(engine))

    engine = object()
    abc_chains.init_abc_chain_tables(engine)
    abc_chains.init_abc_chain_tables(engine)

    assert calls == [engine]


def episode(
    index: int,
    *,
    second: int | None = None,
    session: str = "session-1",
    status: str = "observed",
    antecedent: str | None = "PAUSA",
    antecedent_occurred: bool | None = True,
    behavior: str | None = "CHORO",
    consequence: str | None = "PAUSA",
    environment: str = "Sala",
    created_offset: int = 0,
) -> dict:
    start = datetime(2026, 7, 14, 10, 0, tzinfo=UTC) + timedelta(seconds=second if second is not None else index * 300)
    return {
        "interval_id": f"interval-{index}",
        "session_id": session,
        "service_id": "service-1",
        "patient_token": "patient-1",
        "environment": environment,
        "timezone": "America/Sao_Paulo",
        "start_ts": start,
        "end_ts": start + timedelta(minutes=5),
        "status": status,
        "antecedent_event_id": f"a-{index}" if antecedent else None,
        "antecedent_code": antecedent,
        "antecedent_occurred": antecedent_occurred,
        "antecedent_onset_ts": start,
        "antecedent_confidence": 1.0,
        "antecedent_reviewed": True,
        "behavior_event_id": f"b-{index}" if behavior else None,
        "behavior_code": behavior,
        "behavior_onset_ts": start,
        "behavior_offset_ts": start,
        "behavior_confidence": 1.0,
        "behavior_reviewed": True,
        "consequence_event_id": f"c-{index}" if consequence else None,
        "consequence_code": consequence,
        "consequence_offset_ts": start,
        "consequence_confidence": 1.0,
        "consequence_reviewed": True,
        "interval_created_at": start + timedelta(seconds=created_offset),
        "event_created_at": start + timedelta(seconds=created_offset),
    }


def config(**overrides) -> dict:
    return normalize_chain_config(
        {
            "max_lag_seconds": 300,
            "minimum_valid_intervals": 1,
            "chain_min_repetitions": 1,
            "legacy_contiguous_sessions": False,
            **overrides,
        }
    )


def valid(candidates: list[dict]) -> list[dict]:
    return [item for item in candidates if item["validation_status"] in {"candidate", "accepted"}]


def test_same_interval_consequence_never_predicts_same_episode_behavior():
    origin = episode(1, second=0)
    destination = episode(2, second=1)
    destination["interval_id"] = origin["interval_id"]
    result = detect_chain_candidates([origin, destination], [], config())
    assert result[0]["validation_status"] == "rejected"
    assert result[0]["rejection_reason"] == "same_interval"


def test_lag_accepts_300_seconds_and_does_not_accept_301():
    accepted = detect_chain_candidates([episode(1, second=0), episode(2, second=300)], [], config())
    rejected = detect_chain_candidates([episode(1, second=0), episode(2, second=301)], [], config())
    assert valid(accepted)[0]["delta_seconds"] == 300
    assert valid(rejected) == []


def test_null_is_censored_and_false_is_confirmed_absence():
    unknown = detect_chain_candidates(
        [episode(1, second=0), episode(2, second=30, antecedent=None, antecedent_occurred=None)],
        [],
        config(),
    )
    absent = detect_chain_candidates(
        [episode(1, second=0), episode(2, second=30, antecedent=None, antecedent_occurred=False)],
        [],
        config(),
    )
    assert unknown[0]["rejection_reason"] == "antecedent_unknown"
    assert unknown[0]["validation_status"] == "censored"
    assert absent == []


def test_not_observed_interval_breaks_chain():
    gap = episode(2, second=60, status="not_observed", antecedent=None, behavior=None, consequence=None)
    result = detect_chain_candidates(
        [episode(1, second=0), gap, episode(3, second=120)],
        [],
        config(break_on_not_observed=True),
    )
    assert result[0]["validation_status"] == "censored"
    assert "not_observed" in result[0]["rejection_reason"]


def test_partial_origin_is_not_used_when_observed_end_is_required():
    result = detect_chain_candidates(
        [episode(1, second=0, status="partial"), episode(2, second=30)],
        [],
        config(require_observed_end=True),
    )
    assert result == []


def test_exact_and_mapped_transition_rules_are_distinct():
    exact = transition_match("PAUSA", "PAUSA", [], use_ontology=True)
    mapped = transition_match(
        "REDIRECIONAMENTO",
        "DEMANDA",
        [
            {
                "from_consequence_code": "REDIRECIONAMENTO",
                "to_antecedent_code": "DEMANDA",
                "relation_type": "mapped",
                "rule_version": "2",
                "active": True,
            }
        ],
        use_ontology=True,
    )
    missing = transition_match("REDIRECIONAMENTO", "DEMANDA", [], use_ontology=True)
    assert exact == (True, "exact", "builtin-exact")
    assert mapped == (True, "mapped", "2")
    assert missing == (False, None, None)


def test_mapped_rule_detects_complete_chain():
    origin = episode(1, second=0, consequence="REDIRECIONAMENTO")
    destination = episode(2, second=45, antecedent="DEMANDA", behavior="AGRESSAO_FISICA")
    rules = [
        {
            "from_consequence_code": "REDIRECIONAMENTO",
            "to_antecedent_code": "DEMANDA",
            "relation_type": "clinical_review",
            "rule_version": "1",
            "active": True,
        }
    ]
    result = valid(detect_chain_candidates([origin, destination], rules, config()))
    assert result[0]["rule_type"] == "clinical_review"
    assert result[0]["next_behavior_code"] == "AGRESSAO_FISICA"


def test_timezone_conversion_and_session_crossing_midnight_keep_order():
    origin = episode(1)
    origin.update(
        start_ts="2026-07-15T02:59:30Z",
        consequence_offset_ts="2026-07-15T02:59:30Z",
        event_created_at="2026-07-15T02:59:30Z",
        interval_created_at="2026-07-15T02:59:30Z",
    )
    destination = episode(2)
    destination.update(
        start_ts="2026-07-14T23:00:00-04:00",
        antecedent_onset_ts="2026-07-14T23:00:00-04:00",
        behavior_offset_ts="2026-07-14T23:00:00-04:00",
        event_created_at="2026-07-14T23:00:00-04:00",
        interval_created_at="2026-07-14T23:00:00-04:00",
    )
    result = valid(detect_chain_candidates([origin, destination], [], config()))
    assert result[0]["delta_seconds"] == 30


def test_cross_session_transition_is_blocked_by_default():
    result = detect_chain_candidates(
        [episode(1, second=0, session="one"), episode(2, second=30, session="two")],
        [],
        config(allow_cross_session_chain=False, legacy_contiguous_sessions=False),
    )
    assert result[0]["validation_status"] == "rejected"
    assert result[0]["rejection_reason"] == "cross_session_blocked"


def test_low_confidence_is_rejected():
    destination = episode(2, second=30)
    destination["behavior_confidence"] = 0.7
    result = detect_chain_candidates([episode(1, second=0), destination], [], config(min_confidence=0.9))
    assert result[0]["rejection_reason"] == "low_confidence"


def test_landmark_rejects_retroedited_episode():
    origin = episode(1, second=0)
    destination = episode(2, second=30, created_offset=120)
    landmark = destination["start_ts"] + timedelta(seconds=60)
    result = detect_chain_candidates([origin, destination], [], config(), landmark_ts=landmark)
    assert valid(result) == []


def test_ml_features_require_completion_creation_and_review_before_landmark():
    landmark = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    base = {
        "validation_status": "accepted",
        "completed_at": landmark - timedelta(minutes=20),
        "created_at": landmark - timedelta(minutes=19),
        "reviewed_at": landmark - timedelta(minutes=10),
        "from_consequence_code": "PAUSA",
        "to_antecedent_code": "PAUSA",
        "next_behavior_code": "CHORO",
        "delta_seconds": 20,
        "environment": "Sala",
    }
    after_review = {**base, "reviewed_at": landmark + timedelta(seconds=1)}
    unresolved = {**base, "validation_status": "candidate"}
    features = chain_features_from_candidates([base, after_review, unresolved], landmark_ts=landmark, environment="Sala")
    assert features["resolved_chain_count"] == 1
    assert features["recent_chain_count_1h"] == 1
    assert features["leakage_guard"] == "completed_created_and_reviewed_at_or_before_landmark"


def test_statistics_apply_zero_correction_fisher_or_rr_and_phi():
    episodes = [episode(index, second=index * 20, session=f"session-{index // 5}") for index in range(25)]
    candidates = []
    for index in range(4):
        candidates.append(
            {
                "validation_status": "accepted",
                "from_consequence_code": "PAUSA",
                "to_antecedent_code": "PAUSA",
                "next_behavior_code": "CHORO" if index < 3 else "GRITO",
                "from_session_id": f"session-{index}",
                "completed_at": episodes[index + 1]["start_ts"],
            }
        )
    result = calculate_chain_statistics(episodes, candidates, config(minimum_valid_intervals=1, chain_min_repetitions=3))
    stat = next(item for item in result if item["next_behavior_code"] == "CHORO")
    assert stat["continuity_correction"] is True
    assert math_is_finite(stat["odds_ratio"])
    assert math_is_finite(stat["risk_ratio"])
    assert 0 <= stat["fisher_exact_pvalue"] <= 1
    assert stat["phi"] is not None


def test_temporal_stability_uses_sessions_and_periods():
    episodes = [episode(index, second=index * 20) for index in range(25)]
    candidates = []
    for index, month in enumerate((1, 2, 3), start=1):
        candidates.append(
            {
                "validation_status": "accepted",
                "from_consequence_code": "PAUSA",
                "to_antecedent_code": "PAUSA",
                "next_behavior_code": "CHORO",
                "from_session_id": f"session-{index}",
                "completed_at": datetime(2026, month, 10, tzinfo=UTC),
            }
        )
    stat = calculate_chain_statistics(episodes, candidates, config(minimum_valid_intervals=1, chain_min_repetitions=3))[0]
    assert stat["stability_score"] == pytest.approx(1.0)
    assert stat["insufficient_sample"] is False
    assert stat["months_observed"] == 3


def math_is_finite(value) -> bool:
    return value is not None and float("-inf") < float(value) < float("inf")
