"""abc closed interval tables

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "abc_instrument_versions",
        sa.Column("id", sa.String(80), primary_key=True, nullable=False),
        sa.Column("codigo", sa.String(80), nullable=False),
        sa.Column("nome", sa.String(200), nullable=False),
        sa.Column("versao", sa.String(30), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("metadados", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("codigo", "versao", name="uq_abc_instrument_codigo_versao"),
    )

    op.create_table(
        "abc_sessions",
        sa.Column("id", sa.String(80), primary_key=True, nullable=False),
        sa.Column("patient_token", sa.String(120), nullable=False),
        sa.Column("service_id", sa.String(120), nullable=True),
        sa.Column("data_inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_fim", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("observacao_completa", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("instrumento_versao", sa.String(30), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("data_fim > data_inicio", name="chk_abc_sessions_periodo"),
    )
    op.create_index("ix_abc_sessions_patient_inicio", "abc_sessions", ["patient_token", "data_inicio"])

    op.create_table(
        "abc_intervals",
        sa.Column("id", sa.String(80), primary_key=True, nullable=False),
        sa.Column("sessao_id", sa.String(80), sa.ForeignKey("abc_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fim", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("duracao_planejada_minutos", sa.Integer(), nullable=False),
        sa.Column("status_observacao", sa.String(30), nullable=False),
        sa.Column("atraso_registro_segundos", sa.Integer(), nullable=True),
        sa.Column("observador_token", sa.String(120), nullable=True),
        sa.Column("instrumento_versao", sa.String(30), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("sessao_id", "inicio", name="uq_abc_intervals_sessao_inicio"),
        sa.CheckConstraint("fim > inicio", name="chk_abc_intervals_periodo"),
        sa.CheckConstraint("duracao_planejada_minutos > 0", name="chk_abc_intervals_duracao"),
    )
    op.create_index("ix_abc_intervals_inicio", "abc_intervals", ["inicio"])

    op.create_table(
        "abc_categories",
        sa.Column("id", sa.String(80), primary_key=True, nullable=False),
        sa.Column("codigo", sa.String(80), nullable=False),
        sa.Column("nome", sa.String(200), nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("definicao_operacional", sa.Text(), nullable=True),
        sa.Column("versao", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("ativa", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("service_id", sa.String(120), nullable=True),
        sa.Column("organization_id", sa.String(120), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("codigo", "versao", name="uq_abc_categories_codigo_versao"),
        sa.CheckConstraint("tipo IN ('antecedente', 'comportamento', 'consequencia')", name="chk_abc_categories_tipo"),
    )

    op.create_table(
        "abc_interval_events",
        sa.Column("id", sa.String(80), primary_key=True, nullable=False),
        sa.Column("intervalo_id", sa.String(80), sa.ForeignKey("abc_intervals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("categoria_id", sa.String(80), sa.ForeignKey("abc_categories.id"), nullable=False),
        sa.Column("ocorreu", sa.Boolean(), nullable=True),
        sa.Column("frequencia", sa.Integer(), nullable=True),
        sa.Column("duracao_segundos", sa.Integer(), nullable=True),
        sa.Column("intensidade", sa.Integer(), nullable=True),
        sa.Column("confianca_registro", sa.Numeric(4, 3), nullable=True),
        sa.Column("fonte", sa.String(40), nullable=False, server_default="registro_fechado"),
        sa.Column("revisado_humano", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("intervalo_id", "categoria_id", name="uq_abc_event_intervalo_categoria"),
        sa.CheckConstraint("frequencia IS NULL OR frequencia >= 0", name="chk_abc_event_frequencia"),
        sa.CheckConstraint("duracao_segundos IS NULL OR duracao_segundos >= 0", name="chk_abc_event_duracao"),
        sa.CheckConstraint("intensidade IS NULL OR intensidade BETWEEN 0 AND 5", name="chk_abc_event_intensidade"),
    )


def downgrade() -> None:
    op.drop_table("abc_interval_events")
    op.drop_table("abc_categories")
    op.drop_table("abc_intervals")
    op.drop_table("abc_sessions")
    op.drop_table("abc_instrument_versions")
