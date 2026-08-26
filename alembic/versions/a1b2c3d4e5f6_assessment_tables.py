"""tabelas de avaliacao (assessment)

Revision ID: a1b2c3d4e5f6
Revises: 563592740d7d
Create Date: 2026-07-03

Tabelas cobertas:
  assessment_protocols, assessment_items, assessment_scores,
  assessment_item_program_links

Antes desta migration, essas tabelas eram criadas em runtime pela função
init_assessment_tables() em api.py. Esta migration assume o controle do schema.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "563592740d7d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assessment_protocols",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("code", sa.String(80), unique=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "assessment_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("protocol_id", sa.Integer(), sa.ForeignKey("assessment_protocols.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_code", sa.String(20), nullable=False),
        sa.Column("source_sheet", sa.String(100), nullable=True),
        sa.Column("category_code", sa.String(8), nullable=False),
        sa.Column("category_name", sa.String(200), nullable=False),
        sa.Column("item_number", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("max_points", sa.Integer(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("protocol_id", "item_code", name="uq_assessment_items_protocol_item"),
        sa.CheckConstraint("max_points BETWEEN 1 AND 10", name="chk_assessment_items_max_points"),
    )
    op.create_index("ix_assessment_items_protocol_category", "assessment_items", ["protocol_id", "category_code", "item_number"])

    op.create_table(
        "assessment_scores",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("patient_id", sa.String(), sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("assessment_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assessment_date", sa.Date(), nullable=False, server_default=sa.func.current_date()),
        sa.Column("points", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(40), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("patient_id", "item_id", "assessment_date", name="uq_assessment_scores_patient_item_date"),
        sa.CheckConstraint("points >= 0", name="chk_assessment_scores_points"),
    )
    op.create_index("ix_assessment_scores_patient_date", "assessment_scores", ["patient_id", "assessment_date"])

    op.create_table(
        "assessment_item_program_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("assessment_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("program_library_name", sa.String(200), nullable=False),
        sa.Column("auto_points", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("item_id", "program_library_name", name="uq_assessment_links_item_program"),
        sa.CheckConstraint("auto_points IS NULL OR auto_points >= 0", name="chk_assessment_links_auto_points"),
    )


def downgrade() -> None:
    op.drop_table("assessment_item_program_links")
    op.drop_table("assessment_scores")
    op.drop_table("assessment_items")
    op.drop_table("assessment_protocols")
