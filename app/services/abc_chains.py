from __future__ import annotations

import math
import threading
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.services.abc_closed import _is_transient_database_error
from app.services.abc_methodology import deterministic_transition_id


CHAIN_INTERPRETATION = (
    "Hipotese descritiva de cadeia consequencia->antecedente; "
    "nao determina causa ou funcao comportamental."
)

DEFAULT_CHAIN_CONFIG = {
    "interval_minutes": 5,
    "max_lag_seconds": 300,
    "min_confidence": 0.90,
    "require_observed_end": True,
    "allow_cross_session_chain": False,
    "require_same_environment": False,
    "include_same_interval_transition": False,
    "break_on_not_observed": True,
    "minimum_valid_intervals": 20,
    "chain_min_repetitions": 3,
    "use_transition_ontology": True,
    # Compatibilidade auditavel: o formulario historico criou uma sessao por clique.
    "legacy_contiguous_sessions": True,
}

_ABC_CHAIN_TABLES_READY = False
_ABC_CHAIN_TABLES_INIT_LOCK = threading.Lock()


def init_abc_chain_tables(engine) -> None:
    global _ABC_CHAIN_TABLES_READY
    if _ABC_CHAIN_TABLES_READY:
        return
    with _ABC_CHAIN_TABLES_INIT_LOCK:
        if _ABC_CHAIN_TABLES_READY:
            return
        _initialize_abc_chain_tables(engine)
        _ABC_CHAIN_TABLES_READY = True


def _initialize_abc_chain_tables(engine) -> None:
    statements = (
        "CREATE EXTENSION IF NOT EXISTS pgcrypto",
        "ALTER TABLE abc_interval_events ADD COLUMN IF NOT EXISTS onset_ts TIMESTAMPTZ",
        "ALTER TABLE abc_interval_events ADD COLUMN IF NOT EXISTS offset_ts TIMESTAMPTZ",
        "ALTER TABLE abc_intervals ADD COLUMN IF NOT EXISTS indice_intervalo INTEGER",
        """
        DO $$
        DECLARE current_definition TEXT;
        BEGIN
            SELECT pg_get_constraintdef(oid)
              INTO current_definition
              FROM pg_constraint
             WHERE conrelid = 'abc_intervals'::regclass
               AND conname = 'chk_abc_intervals_status';
            IF current_definition IS NULL OR position('partial' IN current_definition) = 0 THEN
                IF current_definition IS NOT NULL THEN
                    ALTER TABLE abc_intervals DROP CONSTRAINT chk_abc_intervals_status;
                END IF;
                ALTER TABLE abc_intervals ADD CONSTRAINT chk_abc_intervals_status CHECK (
                    status_observacao IN ('observed', 'partial', 'not_observed', 'not_applicable', 'invalid')
                );
            END IF;
        END $$
        """,
        """
        CREATE TABLE IF NOT EXISTS abc_chain_configs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            config_version VARCHAR(30) NOT NULL,
            config JSONB NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (config_version)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS abc_transition_rules (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            from_consequence_code VARCHAR(80) NOT NULL,
            to_antecedent_code VARCHAR(80) NOT NULL,
            relation_type VARCHAR(30) NOT NULL DEFAULT 'mapped',
            active BOOLEAN NOT NULL DEFAULT TRUE,
            rule_version VARCHAR(30) NOT NULL DEFAULT '1',
            rationale TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (from_consequence_code, to_antecedent_code, rule_version),
            CONSTRAINT chk_abc_transition_relation CHECK (relation_type IN ('exact', 'mapped', 'clinical_review'))
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS abc_chain_candidates (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            transition_id VARCHAR(64) NOT NULL,
            patient_token UUID NOT NULL,
            from_interval_id UUID NOT NULL REFERENCES abc_intervals(id) ON DELETE CASCADE,
            to_interval_id UUID NOT NULL REFERENCES abc_intervals(id) ON DELETE CASCADE,
            from_behavior_event_id UUID REFERENCES abc_interval_events(id) ON DELETE SET NULL,
            from_consequence_event_id UUID REFERENCES abc_interval_events(id) ON DELETE SET NULL,
            to_antecedent_event_id UUID REFERENCES abc_interval_events(id) ON DELETE SET NULL,
            to_behavior_event_id UUID REFERENCES abc_interval_events(id) ON DELETE SET NULL,
            from_consequence_code VARCHAR(80),
            to_antecedent_code VARCHAR(80),
            next_behavior_code VARCHAR(80),
            origin_behavior_code VARCHAR(80),
            delta_seconds INTEGER NOT NULL,
            same_session BOOLEAN NOT NULL DEFAULT FALSE,
            session_relation VARCHAR(40) NOT NULL DEFAULT 'same_session',
            chain_confidence NUMERIC(5,4),
            validation_status VARCHAR(20) NOT NULL DEFAULT 'candidate',
            rejection_reason VARCHAR(120),
            rule_type VARCHAR(30),
            rule_version VARCHAR(30),
            config_version VARCHAR(30) NOT NULL,
            origin_end_ts TIMESTAMPTZ NOT NULL,
            destination_start_ts TIMESTAMPTZ NOT NULL,
            completed_at TIMESTAMPTZ NOT NULL,
            environment VARCHAR(160),
            reviewed_by VARCHAR(160),
            reviewed_at TIMESTAMPTZ,
            review_note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (transition_id),
            UNIQUE (from_interval_id, to_interval_id, from_consequence_code, to_antecedent_code),
            CONSTRAINT chk_abc_chain_status CHECK (validation_status IN ('candidate', 'accepted', 'rejected', 'censored')),
            CONSTRAINT chk_abc_chain_distinct_interval CHECK (from_interval_id <> to_interval_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_abc_chain_candidates_patient_time ON abc_chain_candidates (patient_token, completed_at)",
        "CREATE INDEX IF NOT EXISTS ix_abc_chain_candidates_status ON abc_chain_candidates (validation_status)",
        "ALTER TABLE abc_chain_candidates ADD COLUMN IF NOT EXISTS transition_id VARCHAR(64)",
        """
        UPDATE abc_chain_candidates
           SET transition_id = encode(
               digest(
                   patient_token::text || E'\\x1f' ||
                   COALESCE(from_behavior_event_id::text, from_interval_id::text) || E'\\x1f' ||
                   COALESCE(to_behavior_event_id::text, to_interval_id::text) || E'\\x1f' ||
                   COALESCE(rule_version, config_version),
                   'sha256'
               ), 'hex'
           )
         WHERE transition_id IS NULL
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_abc_chain_candidates_transition_id ON abc_chain_candidates (transition_id)",
        """
        CREATE TABLE IF NOT EXISTS abc_chain_stats (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            patient_token UUID NOT NULL,
            from_consequence_code VARCHAR(80) NOT NULL,
            to_antecedent_code VARCHAR(80) NOT NULL,
            next_behavior_code VARCHAR(80) NOT NULL,
            n_exposures INTEGER NOT NULL,
            n_transitions INTEGER NOT NULL,
            n_chain_behavior INTEGER NOT NULL,
            p_transition NUMERIC(10,6),
            p_behavior_given_chain NUMERIC(10,6),
            baseline_probability NUMERIC(10,6),
            difference_in_risk NUMERIC(10,6),
            lift NUMERIC(12,6),
            odds_ratio NUMERIC(14,6),
            risk_ratio NUMERIC(14,6),
            phi NUMERIC(10,6),
            ci_low NUMERIC(10,6),
            ci_high NUMERIC(10,6),
            fisher_exact_pvalue NUMERIC(12,8),
            stability_score NUMERIC(10,6),
            evidence_quality VARCHAR(40),
            insufficient_sample BOOLEAN NOT NULL DEFAULT TRUE,
            config_version VARCHAR(30) NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (patient_token, from_consequence_code, to_antecedent_code, next_behavior_code, config_version)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS abc_chain_review_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            chain_candidate_id UUID NOT NULL REFERENCES abc_chain_candidates(id) ON DELETE CASCADE,
            previous_status VARCHAR(20) NOT NULL,
            new_status VARCHAR(20) NOT NULL,
            reviewed_by VARCHAR(160) NOT NULL,
            review_note TEXT,
            reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        "ALTER TABLE abc_chain_configs ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE abc_transition_rules ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE abc_chain_candidates ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE abc_chain_stats ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE abc_chain_review_logs ENABLE ROW LEVEL SECURITY",
    )
    for attempt in range(4):
        try:
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL lock_timeout = '5s'"))
                conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('sellas:abc-chain-schema:v3'))"))
                for statement in statements:
                    conn.execute(text(statement))
                conn.execute(
                    text(
                        """
                        INSERT INTO abc_chain_configs (config_version, config, active)
                        VALUES ('1', CAST(:config AS JSONB), TRUE)
                        ON CONFLICT (config_version) DO NOTHING
                        """
                    ),
                    {"config": _json_dumps(DEFAULT_CHAIN_CONFIG)},
                )
                conn.execute(
                    text(
                        """
                        UPDATE abc_interval_events e
                        SET onset_ts = COALESCE(e.onset_ts, i.inicio),
                            offset_ts = COALESCE(e.offset_ts, i.inicio)
                        FROM abc_intervals i
                        WHERE i.id = e.intervalo_id
                          AND e.ocorreu IS TRUE
                          AND (e.onset_ts IS NULL OR e.offset_ts IS NULL)
                        """
                    )
                )
            return
        except Exception as exc:
            if attempt >= 3 or not _is_transient_database_error(exc):
                raise
            time.sleep(0.25 * (2**attempt))


def normalize_chain_config(config: dict[str, Any] | None) -> dict[str, Any]:
    result = {**DEFAULT_CHAIN_CONFIG, **(config or {})}
    result["interval_minutes"] = max(1, int(result["interval_minutes"]))
    result["max_lag_seconds"] = max(0, int(result["max_lag_seconds"]))
    result["min_confidence"] = min(1.0, max(0.0, float(result["min_confidence"])))
    result["minimum_valid_intervals"] = max(1, int(result["minimum_valid_intervals"]))
    result["chain_min_repetitions"] = max(1, int(result["chain_min_repetitions"]))
    for key in (
        "require_observed_end",
        "allow_cross_session_chain",
        "require_same_environment",
        "include_same_interval_transition",
        "break_on_not_observed",
        "use_transition_ontology",
        "legacy_contiguous_sessions",
    ):
        result[key] = bool(result[key])
    return result


def get_active_chain_config(engine) -> dict[str, Any]:
    init_abc_chain_tables(engine)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT config_version, config, created_at
                FROM abc_chain_configs
                WHERE active = TRUE
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
        ).mappings().first()
    if not row:
        raise RuntimeError("Configuracao de cadeias ABC nao encontrada.")
    return {
        "config_version": row["config_version"],
        "config": normalize_chain_config(dict(row["config"])),
        "created_at": _iso(row["created_at"]),
    }


def list_transition_rules(engine, *, active_only: bool = False) -> list[dict[str, Any]]:
    init_abc_chain_tables(engine)
    where = "WHERE active = TRUE" if active_only else ""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT id, from_consequence_code, to_antecedent_code, relation_type,
                       active, rule_version, rationale, created_at
                FROM abc_transition_rules
                {where}
                ORDER BY active DESC, from_consequence_code, to_antecedent_code
                """
            )
        ).mappings().all()
    return [_serialize(row) for row in rows]


def create_transition_rule(
    engine,
    *,
    from_consequence_code: str,
    to_antecedent_code: str,
    relation_type: str,
    rule_version: str,
    rationale: str | None,
    active: bool = True,
) -> dict[str, Any]:
    if relation_type not in {"exact", "mapped", "clinical_review"}:
        raise ValueError("Tipo de relacao de transicao invalido.")
    init_abc_chain_tables(engine)
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO abc_transition_rules (
                    from_consequence_code, to_antecedent_code, relation_type,
                    active, rule_version, rationale
                ) VALUES (:from_code, :to_code, :relation, :active, :version, :rationale)
                ON CONFLICT (from_consequence_code, to_antecedent_code, rule_version)
                DO UPDATE SET relation_type = EXCLUDED.relation_type,
                              active = EXCLUDED.active,
                              rationale = EXCLUDED.rationale
                RETURNING *
                """
            ),
            {
                "from_code": from_consequence_code,
                "to_code": to_antecedent_code,
                "relation": relation_type,
                "active": active,
                "version": rule_version,
                "rationale": rationale,
            },
        ).mappings().one()
    return _serialize(row)


def transition_match(
    consequence_code: str | None,
    antecedent_code: str | None,
    rules: Iterable[dict[str, Any]],
    *,
    use_ontology: bool,
) -> tuple[bool, str | None, str | None]:
    if not consequence_code or not antecedent_code:
        return False, None, None
    if consequence_code == antecedent_code:
        return True, "exact", "builtin-exact"
    if not use_ontology:
        return False, None, None
    for rule in rules:
        if not rule.get("active", True):
            continue
        if (
            rule.get("from_consequence_code") == consequence_code
            and rule.get("to_antecedent_code") == antecedent_code
        ):
            return True, str(rule.get("relation_type") or "mapped"), str(rule.get("rule_version") or "1")
    return False, None, None


def detect_chain_candidates(
    episodes: Iterable[dict[str, Any]],
    rules: Iterable[dict[str, Any]],
    config: dict[str, Any],
    *,
    landmark_ts: datetime | str | None = None,
) -> list[dict[str, Any]]:
    cfg = normalize_chain_config(config)
    landmark = _as_utc(landmark_ts) if landmark_ts else None
    ordered = sorted((dict(item) for item in episodes), key=lambda item: _as_utc(item["start_ts"]))
    candidates: list[dict[str, Any]] = []

    for origin_index, origin in enumerate(ordered):
        if origin.get("status") not in {"observed", "partial"}:
            continue
        if cfg["require_observed_end"] and (
            origin.get("status") != "observed"
            or origin.get("session_observation_complete") is False
        ):
            continue
        if not origin.get("behavior_code") or not origin.get("consequence_code"):
            continue
        origin_end = _as_utc(origin.get("consequence_offset_ts") or origin.get("end_ts"))
        if landmark and (
            origin_end > landmark
            or _created_after_landmark(origin, landmark)
        ):
            continue

        for destination_index in range(origin_index + 1, len(ordered)):
            destination = ordered[destination_index]
            destination_start = _as_utc(destination.get("antecedent_onset_ts") or destination.get("start_ts"))
            delta = int((destination_start - origin_end).total_seconds())
            if delta > cfg["max_lag_seconds"]:
                break
            if delta < 0:
                continue

            base = _candidate_base(origin, destination, origin_end, destination_start, delta)
            if origin.get("interval_id") == destination.get("interval_id"):
                if not cfg["include_same_interval_transition"]:
                    candidates.append({**base, "validation_status": "rejected", "rejection_reason": "same_interval"})
                break

            if destination.get("status") in {"not_observed", "invalid"}:
                candidates.append(
                    {
                        **base,
                        "validation_status": "censored",
                        "rejection_reason": f"observation_gap:{destination.get('status')}",
                    }
                )
                if cfg["break_on_not_observed"]:
                    break
                continue

            if destination.get("status") not in {"observed", "partial"}:
                continue
            if destination.get("antecedent_occurred") is None:
                candidates.append({**base, "validation_status": "censored", "rejection_reason": "antecedent_unknown"})
                break
            if destination.get("antecedent_occurred") is False or not destination.get("antecedent_code"):
                continue
            if not destination.get("behavior_code"):
                candidates.append({**base, "validation_status": "censored", "rejection_reason": "next_behavior_unknown"})
                break

            gap_status = _gap_status(ordered[origin_index + 1 : destination_index], cfg)
            if gap_status:
                candidates.append({**base, "validation_status": "censored", "rejection_reason": gap_status})
                break

            same_session = str(origin.get("session_id")) == str(destination.get("session_id"))
            legacy_contiguous = _legacy_contiguous(origin, destination, cfg, delta)
            if not same_session and not legacy_contiguous and not cfg["allow_cross_session_chain"]:
                candidates.append({**base, "validation_status": "rejected", "rejection_reason": "cross_session_blocked"})
                break
            if cfg["require_same_environment"] and origin.get("environment") != destination.get("environment"):
                candidates.append({**base, "validation_status": "rejected", "rejection_reason": "cross_environment_blocked"})
                break
            base["same_session"] = same_session
            base["session_relation"] = (
                "same_session" if same_session else "legacy_contiguous" if legacy_contiguous else "cross_session_allowed"
            )

            matched, rule_type, rule_version = transition_match(
                origin.get("consequence_code"),
                destination.get("antecedent_code"),
                rules,
                use_ontology=cfg["use_transition_ontology"],
            )
            if not matched:
                candidates.append({**base, "validation_status": "rejected", "rejection_reason": "transition_rule_missing"})
                break

            confidence = min(
                _event_confidence(origin, "behavior"),
                _event_confidence(origin, "consequence"),
                _event_confidence(destination, "antecedent"),
                _event_confidence(destination, "behavior"),
            )
            base.update({"chain_confidence": confidence, "rule_type": rule_type, "rule_version": rule_version})
            if confidence < cfg["min_confidence"]:
                candidates.append({**base, "validation_status": "rejected", "rejection_reason": "low_confidence"})
                break

            completed_at = _as_utc(destination.get("behavior_offset_ts") or destination.get("end_ts"))
            base["completed_at"] = completed_at
            if landmark and (
                completed_at > landmark
                or _created_after_landmark(destination, landmark)
            ):
                candidates.append({**base, "validation_status": "censored", "rejection_reason": "after_landmark"})
                break

            candidates.append({**base, "validation_status": "candidate", "rejection_reason": None})
            break
    return candidates


def calculate_chain_statistics(
    episodes: Iterable[dict[str, Any]],
    candidates: Iterable[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    cfg = normalize_chain_config(config)
    observed = [item for item in episodes if item.get("status") in {"observed", "partial"}]
    valid = [item for item in candidates if item.get("validation_status") in {"candidate", "accepted"}]
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    pair_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in valid:
        key = (item["from_consequence_code"], item["to_antecedent_code"], item["next_behavior_code"])
        grouped[key].append(item)
        pair_groups[key[:2]].append(item)

    behavior_totals = Counter(item.get("behavior_code") for item in observed if item.get("behavior_code"))
    stats = []
    for (from_code, to_code, behavior_code), rows in sorted(grouped.items()):
        exposures = sum(1 for item in observed if item.get("consequence_code") == from_code)
        transitions = len(pair_groups[(from_code, to_code)])
        chain_behavior = len(rows)
        baseline_n = max(0, len(observed) - transitions)
        baseline_behavior = max(0, behavior_totals[behavior_code] - chain_behavior)
        n11 = chain_behavior
        n10 = max(0, transitions - chain_behavior)
        n01 = baseline_behavior
        n00 = max(0, baseline_n - baseline_behavior)
        p_transition = _safe_div(transitions, exposures)
        p_chain = _safe_div(chain_behavior, transitions)
        p_base = _safe_div(behavior_totals[behavior_code], len(observed))
        risk_difference = None if p_chain is None or p_base is None else p_chain - p_base
        lift = _safe_div(p_chain, p_base)
        odds_ratio, odds_ci = _odds_ratio_ci(n11, n10, n01, n00)
        risk_ratio, risk_ci = _risk_ratio_ci(n11, n10, n01, n00)
        ci_low, ci_high = _wilson_interval(chain_behavior, transitions)
        sessions = len({str(item.get("from_session_id")) for item in rows})
        months = len({_as_utc(item["completed_at"]).strftime("%Y-%m") for item in rows})
        periods = len({_as_utc(item["completed_at"]).date().isoformat() for item in rows})
        repetition = min(1.0, chain_behavior / cfg["chain_min_repetitions"])
        stability = (repetition * min(1.0, sessions / 2) * min(1.0, max(months, periods) / 2)) ** (1 / 3)
        insufficient = (
            exposures < cfg["minimum_valid_intervals"]
            or chain_behavior < cfg["chain_min_repetitions"]
            or sessions < 2
        )
        stats.append(
            {
                "from_consequence_code": from_code,
                "to_antecedent_code": to_code,
                "next_behavior_code": behavior_code,
                "n_exposures": exposures,
                "n_transitions": transitions,
                "n_chain_behavior": chain_behavior,
                "p_transition": p_transition,
                "p_behavior_given_chain": p_chain,
                "baseline_probability": p_base,
                "difference_in_risk": risk_difference,
                "lift": lift,
                "odds_ratio": odds_ratio,
                "odds_ratio_ci_low": odds_ci[0],
                "odds_ratio_ci_high": odds_ci[1],
                "risk_ratio": risk_ratio,
                "risk_ratio_ci_low": risk_ci[0],
                "risk_ratio_ci_high": risk_ci[1],
                "phi": _phi(n11, n10, n01, n00),
                "ci_low": ci_low,
                "ci_high": ci_high,
                "fisher_exact_pvalue": _fisher_exact_two_sided(n11, n10, n01, n00),
                "stability_score": stability,
                "sessions_observed": sessions,
                "months_observed": months,
                "periods_observed": periods,
                "evidence_quality": "insufficient" if insufficient else "exploratory_stable",
                "insufficient_sample": insufficient,
                "continuity_correction": any(value == 0 for value in (n11, n10, n01, n00)),
            }
        )
    return stats


def load_chain_episodes(engine, patient_token: str) -> list[dict[str, Any]]:
    init_abc_chain_tables(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT i.id AS interval_id, i.sessao_id AS session_id, s.service_id,
                       s.patient_token, s.ambiente AS environment, s.timezone,
                       s.observacao_completa AS session_observation_complete,
                       i.inicio AS start_ts, i.fim AS end_ts, i.status_observacao AS status,
                       i.criado_em AS interval_created_at,
                       MAX(e.id::text) FILTER (WHERE c.tipo='antecedente' AND e.ocorreu IS TRUE) AS antecedent_event_id,
                       MAX(c.codigo) FILTER (WHERE c.tipo='antecedente' AND e.ocorreu IS TRUE) AS antecedent_code,
                       MAX(c.nome) FILTER (WHERE c.tipo='antecedente' AND e.ocorreu IS TRUE) AS antecedent_name,
                       MAX(e.onset_ts) FILTER (WHERE c.tipo='antecedente' AND e.ocorreu IS TRUE) AS antecedent_onset_ts,
                       MAX(e.confianca_registro) FILTER (WHERE c.tipo='antecedente' AND e.ocorreu IS TRUE) AS antecedent_confidence,
                       BOOL_OR(e.revisado_humano) FILTER (WHERE c.tipo='antecedente' AND e.ocorreu IS TRUE) AS antecedent_reviewed,
                       MAX(e.id::text) FILTER (WHERE c.tipo='comportamento' AND e.ocorreu IS TRUE) AS behavior_event_id,
                       MAX(c.codigo) FILTER (WHERE c.tipo='comportamento' AND e.ocorreu IS TRUE) AS behavior_code,
                       MAX(c.nome) FILTER (WHERE c.tipo='comportamento' AND e.ocorreu IS TRUE) AS behavior_name,
                       MAX(e.onset_ts) FILTER (WHERE c.tipo='comportamento' AND e.ocorreu IS TRUE) AS behavior_onset_ts,
                       MAX(e.offset_ts) FILTER (WHERE c.tipo='comportamento' AND e.ocorreu IS TRUE) AS behavior_offset_ts,
                       MAX(e.confianca_registro) FILTER (WHERE c.tipo='comportamento' AND e.ocorreu IS TRUE) AS behavior_confidence,
                       BOOL_OR(e.revisado_humano) FILTER (WHERE c.tipo='comportamento' AND e.ocorreu IS TRUE) AS behavior_reviewed,
                       MAX(e.id::text) FILTER (WHERE c.tipo='consequencia' AND e.ocorreu IS TRUE) AS consequence_event_id,
                       MAX(c.codigo) FILTER (WHERE c.tipo='consequencia' AND e.ocorreu IS TRUE) AS consequence_code,
                       MAX(c.nome) FILTER (WHERE c.tipo='consequencia' AND e.ocorreu IS TRUE) AS consequence_name,
                       MAX(e.offset_ts) FILTER (WHERE c.tipo='consequencia' AND e.ocorreu IS TRUE) AS consequence_offset_ts,
                       MAX(e.confianca_registro) FILTER (WHERE c.tipo='consequencia' AND e.ocorreu IS TRUE) AS consequence_confidence,
                       BOOL_OR(e.revisado_humano) FILTER (WHERE c.tipo='consequencia' AND e.ocorreu IS TRUE) AS consequence_reviewed,
                       MAX(e.criado_em) FILTER (WHERE e.ocorreu IS TRUE) AS event_created_at
                FROM abc_sessions s
                JOIN abc_intervals i ON i.sessao_id = s.id
                LEFT JOIN abc_interval_events e ON e.intervalo_id = i.id
                LEFT JOIN abc_categories c ON c.id = e.categoria_id
                WHERE s.patient_token = CAST(:patient_token AS UUID)
                GROUP BY i.id, i.sessao_id, s.service_id, s.patient_token, s.ambiente,
                         s.timezone, s.observacao_completa, i.inicio, i.fim,
                         i.status_observacao, i.criado_em
                ORDER BY i.inicio
                """
            ),
            {"patient_token": patient_token},
        ).mappings().all()
    result = []
    for row in rows:
        item = dict(row)
        item["antecedent_occurred"] = True if item.get("antecedent_event_id") else None
        for key in ("interval_id", "session_id", "service_id", "patient_token", "antecedent_event_id", "behavior_event_id", "consequence_event_id"):
            if item.get(key) is not None:
                item[key] = str(item[key])
        result.append(item)
    return result


def detect_and_persist_chains(
    engine,
    *,
    patient_token: str,
    overrides: dict[str, Any] | None = None,
    landmark_ts: datetime | None = None,
) -> dict[str, Any]:
    active = get_active_chain_config(engine)
    config = normalize_chain_config({**active["config"], **(overrides or {})})
    episodes = load_chain_episodes(engine, patient_token)
    rules = list_transition_rules(engine, active_only=True)
    candidates = detect_chain_candidates(episodes, rules, config, landmark_ts=landmark_ts)
    deduplicated: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        previous_id = candidate.get("from_behavior_event_id") or candidate.get("from_interval_id")
        next_id = candidate.get("to_behavior_event_id") or candidate.get("to_interval_id")
        transition_id = deterministic_transition_id(
            patient_token,
            previous_id,
            next_id,
            str(candidate.get("rule_version") or active["config_version"]),
        )
        candidate["transition_id"] = transition_id
        deduplicated[transition_id] = candidate
    candidates = list(deduplicated.values())
    stats = calculate_chain_statistics(episodes, candidates, config)

    with engine.begin() as conn:
        current_keys = {
            (
                str(item["from_interval_id"]),
                str(item["to_interval_id"]),
                item.get("from_consequence_code"),
                item.get("to_antecedent_code"),
            )
            for item in candidates
            if item["from_interval_id"] != item["to_interval_id"]
        }
        previous_rows = conn.execute(
            text(
                """
                SELECT id, from_interval_id, to_interval_id,
                       from_consequence_code, to_antecedent_code
                FROM abc_chain_candidates
                WHERE patient_token = CAST(:patient_token AS UUID)
                  AND reviewed_at IS NULL
                """
            ),
            {"patient_token": patient_token},
        ).mappings().all()
        superseded_ids = [
            row["id"]
            for row in previous_rows
            if (
                str(row["from_interval_id"]),
                str(row["to_interval_id"]),
                row["from_consequence_code"],
                row["to_antecedent_code"],
            )
            not in current_keys
        ]
        for candidate_id in superseded_ids:
            conn.execute(
                text(
                    """
                    UPDATE abc_chain_candidates
                    SET validation_status = 'censored',
                        rejection_reason = 'superseded_detection',
                        updated_at = now()
                    WHERE id = :candidate_id
                    """
                ),
                {"candidate_id": candidate_id},
            )
        for candidate in candidates:
            if candidate["from_interval_id"] == candidate["to_interval_id"]:
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO abc_chain_candidates (
                        transition_id, patient_token, from_interval_id, to_interval_id,
                        from_behavior_event_id, from_consequence_event_id,
                        to_antecedent_event_id, to_behavior_event_id,
                        from_consequence_code, to_antecedent_code, next_behavior_code,
                        origin_behavior_code, delta_seconds, same_session, session_relation,
                        chain_confidence, validation_status, rejection_reason,
                        rule_type, rule_version, config_version, origin_end_ts,
                        destination_start_ts, completed_at, environment
                    ) VALUES (
                        :transition_id, CAST(:patient_token AS UUID), CAST(:from_interval_id AS UUID), CAST(:to_interval_id AS UUID),
                        CAST(:from_behavior_event_id AS UUID), CAST(:from_consequence_event_id AS UUID),
                        CAST(:to_antecedent_event_id AS UUID), CAST(:to_behavior_event_id AS UUID),
                        :from_consequence_code, :to_antecedent_code, :next_behavior_code,
                        :origin_behavior_code, :delta_seconds, :same_session, :session_relation,
                        :chain_confidence, :validation_status, :rejection_reason,
                        :rule_type, :rule_version, :config_version, :origin_end_ts,
                        :destination_start_ts, :completed_at, :environment
                    )
                    ON CONFLICT (from_interval_id, to_interval_id, from_consequence_code, to_antecedent_code)
                    DO UPDATE SET
                        delta_seconds = EXCLUDED.delta_seconds,
                        same_session = EXCLUDED.same_session,
                        session_relation = EXCLUDED.session_relation,
                        chain_confidence = EXCLUDED.chain_confidence,
                        validation_status = CASE
                            WHEN abc_chain_candidates.reviewed_at IS NOT NULL THEN abc_chain_candidates.validation_status
                            ELSE EXCLUDED.validation_status
                        END,
                        rejection_reason = CASE
                            WHEN abc_chain_candidates.reviewed_at IS NOT NULL THEN abc_chain_candidates.rejection_reason
                            ELSE EXCLUDED.rejection_reason
                        END,
                        rule_type = EXCLUDED.rule_type,
                        rule_version = EXCLUDED.rule_version,
                        config_version = EXCLUDED.config_version,
                        completed_at = EXCLUDED.completed_at,
                        updated_at = now()
                    """
                ),
                {**candidate, "patient_token": patient_token, "config_version": active["config_version"]},
            )
        conn.execute(
            text("DELETE FROM abc_chain_stats WHERE patient_token = CAST(:patient_token AS UUID)"),
            {"patient_token": patient_token},
        )
        for item in stats:
            conn.execute(
                text(
                    """
                    INSERT INTO abc_chain_stats (
                        patient_token, from_consequence_code, to_antecedent_code, next_behavior_code,
                        n_exposures, n_transitions, n_chain_behavior, p_transition,
                        p_behavior_given_chain, baseline_probability, difference_in_risk,
                        lift, odds_ratio, risk_ratio, phi, ci_low, ci_high,
                        fisher_exact_pvalue, stability_score, evidence_quality,
                        insufficient_sample, config_version
                    ) VALUES (
                        CAST(:patient_token AS UUID), :from_consequence_code, :to_antecedent_code, :next_behavior_code,
                        :n_exposures, :n_transitions, :n_chain_behavior, :p_transition,
                        :p_behavior_given_chain, :baseline_probability, :difference_in_risk,
                        :lift, :odds_ratio, :risk_ratio, :phi, :ci_low, :ci_high,
                        :fisher_exact_pvalue, :stability_score, :evidence_quality,
                        :insufficient_sample, :config_version
                    )
                    """
                ),
                {**item, "patient_token": patient_token, "config_version": active["config_version"]},
            )
    counts = Counter(item["validation_status"] for item in candidates)
    return {
        "patient_token": patient_token,
        "config_version": active["config_version"],
        "config": config,
        "episodes_examined": len(episodes),
        "counts": {key: counts.get(key, 0) for key in ("candidate", "accepted", "rejected", "censored")},
        "candidates": [_serialize(item) for item in candidates],
        "stats": stats,
        "interpretation": CHAIN_INTERPRETATION,
    }


def list_chain_candidates(
    engine,
    *,
    patient_token: str,
    status: str | None = None,
) -> list[dict[str, Any]]:
    init_abc_chain_tables(engine)
    params: dict[str, Any] = {"patient_token": patient_token}
    status_sql = ""
    if status:
        status_sql = "AND validation_status = :status"
        params["status"] = status
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT * FROM abc_chain_candidates
                WHERE patient_token = CAST(:patient_token AS UUID) {status_sql}
                ORDER BY completed_at DESC
                """
            ),
            params,
        ).mappings().all()
    return [_serialize(row) for row in rows]


def get_chain_candidate(engine, candidate_id: str) -> dict[str, Any] | None:
    init_abc_chain_tables(engine)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM abc_chain_candidates WHERE id = CAST(:id AS UUID)"),
            {"id": candidate_id},
        ).mappings().first()
    return _serialize(row) if row else None


def review_chain_candidate(
    engine,
    *,
    candidate_id: str,
    status: str,
    reviewed_by: str,
    note: str | None,
) -> dict[str, Any]:
    if status not in {"accepted", "rejected"}:
        raise ValueError("A revisao deve aceitar ou rejeitar o candidato.")
    init_abc_chain_tables(engine)
    with engine.begin() as conn:
        current = conn.execute(
            text("SELECT validation_status FROM abc_chain_candidates WHERE id = CAST(:id AS UUID) FOR UPDATE"),
            {"id": candidate_id},
        ).mappings().first()
        if not current:
            raise ValueError("Candidato de cadeia nao encontrado.")
        row = conn.execute(
            text(
                """
                UPDATE abc_chain_candidates
                SET validation_status = :status,
                    rejection_reason = CASE WHEN :status = 'rejected' THEN 'human_review' ELSE NULL END,
                    reviewed_by = :reviewed_by, reviewed_at = now(), review_note = :note,
                    updated_at = now()
                WHERE id = CAST(:id AS UUID)
                RETURNING *
                """
            ),
            {"id": candidate_id, "status": status, "reviewed_by": reviewed_by, "note": note},
        ).mappings().one()
        conn.execute(
            text(
                """
                INSERT INTO abc_chain_review_logs (
                    chain_candidate_id, previous_status, new_status, reviewed_by, review_note
                ) VALUES (CAST(:id AS UUID), :previous, :new, :reviewed_by, :note)
                """
            ),
            {
                "id": candidate_id,
                "previous": current["validation_status"],
                "new": status,
                "reviewed_by": reviewed_by,
                "note": note,
            },
        )
    return _serialize(row)


def approve_current_chain_candidates(
    engine,
    *,
    patient_token: str,
    reviewed_by: str,
    rationale: str,
) -> dict[str, Any]:
    """Versiona regras ausentes e aceita apenas candidatos temporalmente válidos atuais."""
    current = list_chain_candidates(engine, patient_token=patient_token)
    missing_pairs = sorted(
        {
            (item.get("from_consequence_code"), item.get("to_antecedent_code"))
            for item in current
            if item.get("rejection_reason") == "transition_rule_missing"
            and item.get("from_consequence_code")
            and item.get("to_antecedent_code")
        }
    )
    for from_code, to_code in missing_pairs:
        create_transition_rule(
            engine,
            from_consequence_code=str(from_code),
            to_antecedent_code=str(to_code),
            relation_type="clinical_review",
            rule_version="1",
            rationale=rationale,
            active=True,
        )

    detection = detect_and_persist_chains(engine, patient_token=patient_token)
    with engine.begin() as conn:
        candidates = conn.execute(
            text(
                """
                SELECT id, validation_status
                FROM abc_chain_candidates
                WHERE patient_token = CAST(:patient_token AS UUID)
                  AND validation_status = 'candidate'
                FOR UPDATE
                """
            ),
            {"patient_token": patient_token},
        ).mappings().all()
        for candidate in candidates:
            conn.execute(
                text(
                    """
                    INSERT INTO abc_chain_review_logs (
                        chain_candidate_id, previous_status, new_status,
                        reviewed_by, review_note
                    ) VALUES (
                        :candidate_id, :previous_status, 'accepted',
                        :reviewed_by, :review_note
                    )
                    """
                ),
                {
                    "candidate_id": candidate["id"],
                    "previous_status": candidate["validation_status"],
                    "reviewed_by": reviewed_by,
                    "review_note": rationale,
                },
            )
        if candidates:
            conn.execute(
                text(
                    """
                    UPDATE abc_chain_candidates
                    SET validation_status = 'accepted',
                        rejection_reason = NULL,
                        reviewed_by = :reviewed_by,
                        reviewed_at = now(),
                        review_note = :review_note,
                        updated_at = now()
                    WHERE patient_token = CAST(:patient_token AS UUID)
                      AND validation_status = 'candidate'
                    """
                ),
                {
                    "patient_token": patient_token,
                    "reviewed_by": reviewed_by,
                    "review_note": rationale,
                },
            )
    remaining = list_chain_candidates(engine, patient_token=patient_token)
    counts = Counter(item["validation_status"] for item in remaining)
    return {
        "patient_token": patient_token,
        "rules_created_or_confirmed": len(missing_pairs),
        "accepted_now": len(candidates),
        "counts": {key: counts.get(key, 0) for key in ("candidate", "accepted", "rejected", "censored")},
        "detection": detection["counts"],
        "interpretation": CHAIN_INTERPRETATION,
    }


def list_chain_stats(engine, *, patient_token: str) -> list[dict[str, Any]]:
    init_abc_chain_tables(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT * FROM abc_chain_stats
                WHERE patient_token = CAST(:patient_token AS UUID)
                ORDER BY insufficient_sample, lift DESC NULLS LAST, n_chain_behavior DESC
                """
            ),
            {"patient_token": patient_token},
        ).mappings().all()
    return [_serialize(row) for row in rows]


def build_transition_matrix(engine, *, patient_token: str) -> list[dict[str, Any]]:
    candidates = list_chain_candidates(engine, patient_token=patient_token)
    valid = [item for item in candidates if item["validation_status"] in {"candidate", "accepted"}]
    totals = Counter(item["from_consequence_code"] for item in valid)
    cells = Counter((item["from_consequence_code"], item["to_antecedent_code"]) for item in valid)
    return [
        {
            "from_consequence_code": from_code,
            "to_antecedent_code": to_code,
            "count": count,
            "probability": _safe_div(count, totals[from_code]),
        }
        for (from_code, to_code), count in sorted(cells.items())
    ]


def build_chain_timeline(engine, *, patient_token: str) -> list[dict[str, Any]]:
    episodes = load_chain_episodes(engine, patient_token)
    return [
        {
            "interval_id": item["interval_id"],
            "session_id": item["session_id"],
            "start_ts": _iso(item["start_ts"]),
            "end_ts": _iso(item["end_ts"]),
            "status": item["status"],
            "environment": item.get("environment"),
            "antecedent_code": item.get("antecedent_code"),
            "behavior_code": item.get("behavior_code"),
            "consequence_code": item.get("consequence_code"),
        }
        for item in episodes
    ]


def chain_features_from_candidates(
    candidates: Iterable[dict[str, Any]],
    *,
    landmark_ts: datetime | str,
    session_id: str | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    landmark = _as_utc(landmark_ts)
    safe = []
    for item in candidates:
        if item.get("validation_status") != "accepted":
            continue
        completed = _as_utc(item["completed_at"])
        created = _as_utc(item.get("created_at") or completed)
        reviewed = _as_utc(item["reviewed_at"]) if item.get("reviewed_at") else None
        if completed <= landmark and created <= landmark and reviewed and reviewed <= landmark:
            safe.append(item)
    safe.sort(key=lambda item: _as_utc(item["completed_at"]))
    recent_hour = [item for item in safe if _as_utc(item["completed_at"]) >= landmark - timedelta(hours=1)]
    recent_week = [item for item in safe if _as_utc(item["completed_at"]) >= landmark - timedelta(days=7)]
    same_session = [item for item in safe if session_id and str(item.get("to_session_id")) == str(session_id)]
    same_context = [item for item in safe if environment and item.get("environment") == environment]
    last = safe[-1] if safe else None
    return {
        "landmark_ts": landmark.isoformat(),
        "recent_chain_count_1h": len(recent_hour),
        "recent_chain_count_1session": len(same_session),
        "chain_exposure_rate_7d": len(recent_week) / 7.0,
        "last_chain_delta_seconds": last.get("delta_seconds") if last else None,
        "last_chain_type": (
            f"{last.get('from_consequence_code')}->{last.get('to_antecedent_code')}->{last.get('next_behavior_code')}"
            if last
            else None
        ),
        "chain_lift_personal_baseline": last.get("lift") if last else None,
        "chain_stability_recent": last.get("stability_score") if last else None,
        "time_since_last_chain": int((landmark - _as_utc(last["completed_at"])).total_seconds()) if last else None,
        "chain_count_same_context": len(same_context),
        "chain_count_same_activity": 0,
        "resolved_chain_count": len(safe),
        "leakage_guard": "completed_created_and_reviewed_at_or_before_landmark",
    }


def get_chain_features(
    engine,
    *,
    patient_token: str,
    landmark_ts: datetime,
    session_id: str | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    candidates = list_chain_candidates(engine, patient_token=patient_token)
    stats_by_type = {
        (item["from_consequence_code"], item["to_antecedent_code"], item["next_behavior_code"]): item
        for item in list_chain_stats(engine, patient_token=patient_token)
    }
    for item in candidates:
        stat = stats_by_type.get((item.get("from_consequence_code"), item.get("to_antecedent_code"), item.get("next_behavior_code")))
        if stat:
            item["lift"] = stat.get("lift")
            item["stability_score"] = stat.get("stability_score")
    return chain_features_from_candidates(
        candidates,
        landmark_ts=landmark_ts,
        session_id=session_id,
        environment=environment,
    )


def _candidate_base(
    origin: dict[str, Any],
    destination: dict[str, Any],
    origin_end: datetime,
    destination_start: datetime,
    delta: int,
) -> dict[str, Any]:
    return {
        "from_interval_id": str(origin.get("interval_id")),
        "to_interval_id": str(destination.get("interval_id")),
        "from_session_id": str(origin.get("session_id")),
        "to_session_id": str(destination.get("session_id")),
        "from_behavior_event_id": _str_or_none(origin.get("behavior_event_id")),
        "from_consequence_event_id": _str_or_none(origin.get("consequence_event_id")),
        "to_antecedent_event_id": _str_or_none(destination.get("antecedent_event_id")),
        "to_behavior_event_id": _str_or_none(destination.get("behavior_event_id")),
        "origin_behavior_code": origin.get("behavior_code"),
        "from_consequence_code": origin.get("consequence_code"),
        "to_antecedent_code": destination.get("antecedent_code"),
        "next_behavior_code": destination.get("behavior_code"),
        "origin_end_ts": origin_end,
        "destination_start_ts": destination_start,
        "completed_at": _as_utc(destination.get("behavior_offset_ts") or destination.get("end_ts")),
        "delta_seconds": delta,
        "same_session": str(origin.get("session_id")) == str(destination.get("session_id")),
        "session_relation": "same_session",
        "chain_confidence": None,
        "rule_type": None,
        "rule_version": None,
        "environment": destination.get("environment"),
    }


def _legacy_contiguous(origin: dict[str, Any], destination: dict[str, Any], cfg: dict[str, Any], delta: int) -> bool:
    if not cfg.get("legacy_contiguous_sessions"):
        return False
    if origin.get("service_id") or destination.get("service_id"):
        return False
    if origin.get("environment") != destination.get("environment"):
        return False
    timezone_name = destination.get("timezone") or origin.get("timezone") or "America/Sao_Paulo"
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc
    origin_day = _as_utc(origin["start_ts"]).astimezone(tz).date()
    destination_day = _as_utc(destination["start_ts"]).astimezone(tz).date()
    return origin_day == destination_day and 0 <= delta <= cfg["max_lag_seconds"]


def _gap_status(gaps: Iterable[dict[str, Any]], cfg: dict[str, Any]) -> str | None:
    if not cfg["break_on_not_observed"]:
        return None
    for item in gaps:
        if item.get("status") in {"not_observed", "invalid"}:
            return f"observation_gap:{item.get('status')}"
    return None


def _event_confidence(item: dict[str, Any], prefix: str) -> float:
    value = item.get(f"{prefix}_confidence")
    if value is not None:
        return min(1.0, max(0.0, float(value)))
    return 1.0 if item.get(f"{prefix}_reviewed", False) else 0.0


def _created_after_landmark(item: dict[str, Any], landmark: datetime) -> bool:
    for key in ("event_created_at", "interval_created_at"):
        if item.get(key) and _as_utc(item[key]) > landmark:
            return True
    return False


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return float(numerator) / float(denominator)


def _wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def _corrected_cells(a: int, b: int, c: int, d: int) -> tuple[float, float, float, float]:
    if 0 in (a, b, c, d):
        return a + 0.5, b + 0.5, c + 0.5, d + 0.5
    return float(a), float(b), float(c), float(d)


def _odds_ratio_ci(a: int, b: int, c: int, d: int) -> tuple[float | None, tuple[float | None, float | None]]:
    aa, bb, cc, dd = _corrected_cells(a, b, c, d)
    odds = aa * dd / (bb * cc)
    se = math.sqrt(1 / aa + 1 / bb + 1 / cc + 1 / dd)
    return odds, (math.exp(math.log(odds) - 1.96 * se), math.exp(math.log(odds) + 1.96 * se))


def _risk_ratio_ci(a: int, b: int, c: int, d: int) -> tuple[float | None, tuple[float | None, float | None]]:
    aa, bb, cc, dd = _corrected_cells(a, b, c, d)
    exposed = aa / (aa + bb)
    unexposed = cc / (cc + dd)
    rr = exposed / unexposed
    se = math.sqrt(1 / aa - 1 / (aa + bb) + 1 / cc - 1 / (cc + dd))
    return rr, (math.exp(math.log(rr) - 1.96 * se), math.exp(math.log(rr) + 1.96 * se))


def _phi(a: int, b: int, c: int, d: int) -> float | None:
    denominator = math.sqrt((a + b) * (c + d) * (a + c) * (b + d))
    return None if denominator == 0 else (a * d - b * c) / denominator


def _fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    row1, row2, col1 = a + b, c + d, a + c
    total = row1 + row2
    if total == 0:
        return 1.0

    def probability(x: int) -> float:
        return math.comb(col1, x) * math.comb(total - col1, row1 - x) / math.comb(total, row1)

    lower = max(0, row1 - (total - col1))
    upper = min(row1, col1)
    observed = probability(a)
    return min(1.0, sum(probability(x) for x in range(lower, upper + 1) if probability(x) <= observed + 1e-12))


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise ValueError("Timestamp temporal ausente ou invalido.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _serialize(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key, value in list(data.items()):
        if isinstance(value, (datetime, uuid.UUID)):
            data[key] = str(value) if isinstance(value, uuid.UUID) else value.isoformat()
        elif hasattr(value, "as_tuple"):
            data[key] = float(value)
    return data


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def _json_dumps(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
