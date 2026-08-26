"""ABC temporal chains, transition rules and review audit.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("abc_interval_events", sa.Column("onset_ts", sa.DateTime(timezone=True), nullable=True))
    op.add_column("abc_interval_events", sa.Column("offset_ts", sa.DateTime(timezone=True), nullable=True))
    op.add_column("abc_intervals", sa.Column("indice_intervalo", sa.Integer(), nullable=True))
    op.drop_constraint("chk_abc_intervals_status", "abc_intervals", type_="check")
    op.create_check_constraint(
        "chk_abc_intervals_status",
        "abc_intervals",
        "status_observacao IN ('observed','partial','not_observed','not_applicable','invalid')",
    )

    op.create_table(
        "abc_chain_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("config_version", sa.String(30), nullable=False, unique=True),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "abc_transition_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("from_consequence_code", sa.String(80), nullable=False),
        sa.Column("to_antecedent_code", sa.String(80), nullable=False),
        sa.Column("relation_type", sa.String(30), nullable=False, server_default="mapped"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("rule_version", sa.String(30), nullable=False, server_default="1"),
        sa.Column("rationale", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("from_consequence_code", "to_antecedent_code", "rule_version"),
        sa.CheckConstraint("relation_type IN ('exact','mapped','clinical_review')", name="chk_abc_transition_relation"),
    )
    op.create_table(
        "abc_chain_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("patient_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_interval_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("abc_intervals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_interval_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("abc_intervals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_behavior_event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("abc_interval_events.id", ondelete="SET NULL")),
        sa.Column("from_consequence_event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("abc_interval_events.id", ondelete="SET NULL")),
        sa.Column("to_antecedent_event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("abc_interval_events.id", ondelete="SET NULL")),
        sa.Column("to_behavior_event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("abc_interval_events.id", ondelete="SET NULL")),
        sa.Column("from_consequence_code", sa.String(80)),
        sa.Column("to_antecedent_code", sa.String(80)),
        sa.Column("next_behavior_code", sa.String(80)),
        sa.Column("origin_behavior_code", sa.String(80)),
        sa.Column("delta_seconds", sa.Integer(), nullable=False),
        sa.Column("same_session", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("session_relation", sa.String(40), nullable=False, server_default="same_session"),
        sa.Column("chain_confidence", sa.Numeric(5, 4)),
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="candidate"),
        sa.Column("rejection_reason", sa.String(120)),
        sa.Column("rule_type", sa.String(30)),
        sa.Column("rule_version", sa.String(30)),
        sa.Column("config_version", sa.String(30), nullable=False),
        sa.Column("origin_end_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("destination_start_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("environment", sa.String(160)),
        sa.Column("reviewed_by", sa.String(160)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("from_interval_id", "to_interval_id", "from_consequence_code", "to_antecedent_code"),
        sa.CheckConstraint("validation_status IN ('candidate','accepted','rejected','censored')", name="chk_abc_chain_status"),
        sa.CheckConstraint("from_interval_id <> to_interval_id", name="chk_abc_chain_distinct_interval"),
    )
    op.create_index("ix_abc_chain_candidates_patient_time", "abc_chain_candidates", ["patient_token", "completed_at"])
    op.create_index("ix_abc_chain_candidates_status", "abc_chain_candidates", ["validation_status"])
    op.create_table(
        "abc_chain_stats",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("patient_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_consequence_code", sa.String(80), nullable=False),
        sa.Column("to_antecedent_code", sa.String(80), nullable=False),
        sa.Column("next_behavior_code", sa.String(80), nullable=False),
        sa.Column("n_exposures", sa.Integer(), nullable=False),
        sa.Column("n_transitions", sa.Integer(), nullable=False),
        sa.Column("n_chain_behavior", sa.Integer(), nullable=False),
        sa.Column("p_transition", sa.Numeric(10, 6)),
        sa.Column("p_behavior_given_chain", sa.Numeric(10, 6)),
        sa.Column("baseline_probability", sa.Numeric(10, 6)),
        sa.Column("difference_in_risk", sa.Numeric(10, 6)),
        sa.Column("lift", sa.Numeric(12, 6)),
        sa.Column("odds_ratio", sa.Numeric(14, 6)),
        sa.Column("risk_ratio", sa.Numeric(14, 6)),
        sa.Column("phi", sa.Numeric(10, 6)),
        sa.Column("ci_low", sa.Numeric(10, 6)),
        sa.Column("ci_high", sa.Numeric(10, 6)),
        sa.Column("fisher_exact_pvalue", sa.Numeric(12, 8)),
        sa.Column("stability_score", sa.Numeric(10, 6)),
        sa.Column("evidence_quality", sa.String(40)),
        sa.Column("insufficient_sample", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config_version", sa.String(30), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("patient_token", "from_consequence_code", "to_antecedent_code", "next_behavior_code", "config_version"),
    )
    op.create_table(
        "abc_chain_review_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("chain_candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("abc_chain_candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("previous_status", sa.String(20), nullable=False),
        sa.Column("new_status", sa.String(20), nullable=False),
        sa.Column("reviewed_by", sa.String(160), nullable=False),
        sa.Column("review_note", sa.Text()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.execute(
        """
        INSERT INTO abc_chain_configs (config_version, config, active)
        VALUES ('1', '{"interval_minutes":5,"max_lag_seconds":300,"min_confidence":0.9,"require_observed_end":true,"allow_cross_session_chain":false,"include_same_interval_transition":false,"break_on_not_observed":true,"minimum_valid_intervals":20,"chain_min_repetitions":3,"use_transition_ontology":true,"legacy_contiguous_sessions":true}'::jsonb, true)
        ON CONFLICT (config_version) DO NOTHING;
        UPDATE abc_interval_events e
        SET onset_ts = COALESCE(e.onset_ts, i.inicio), offset_ts = COALESCE(e.offset_ts, i.inicio)
        FROM abc_intervals i WHERE i.id = e.intervalo_id AND e.ocorreu IS TRUE;
        """
    )


def downgrade() -> None:
    op.drop_table("abc_chain_review_logs")
    op.drop_table("abc_chain_stats")
    op.drop_index("ix_abc_chain_candidates_status", table_name="abc_chain_candidates")
    op.drop_index("ix_abc_chain_candidates_patient_time", table_name="abc_chain_candidates")
    op.drop_table("abc_chain_candidates")
    op.drop_table("abc_transition_rules")
    op.drop_table("abc_chain_configs")
    op.drop_constraint("chk_abc_intervals_status", "abc_intervals", type_="check")
    op.create_check_constraint(
        "chk_abc_intervals_status",
        "abc_intervals",
        "status_observacao IN ('observed','not_observed','not_applicable','invalid')",
    )
    op.drop_column("abc_intervals", "indice_intervalo")
    op.drop_column("abc_interval_events", "offset_ts")
    op.drop_column("abc_interval_events", "onset_ts")
