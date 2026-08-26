"""criando estrutura inicial

Revision ID: 563592740d7d
Revises:
Create Date: 2026-04-20 13:50:03.141834

Tabelas cobertas (definidas em app/data/models.py):
  patients, programs, program_records, program_target_records,
  interfering_behaviors, interfering_records, program_library

Inclui também as colunas `evolution` que eram adicionadas em runtime
pela função ensure_clinical_evolution_columns() em api.py.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "563592740d7d"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "patients",
        sa.Column("id", sa.String(), primary_key=True, nullable=False),
        sa.Column("name_hash", sa.String(), nullable=True, unique=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=True),
    )
    op.create_index("ix_patients_name_hash", "patients", ["name_hash"])

    op.create_table(
        "programs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("patient_id", sa.String(), sa.ForeignKey("patients.id"), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("objective", sa.String(), nullable=True),
    )
    op.create_index("ix_programs_patient_id", "programs", ["patient_id"])

    op.create_table(
        "program_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("program_id", sa.Integer(), sa.ForeignKey("programs.id"), nullable=True),
        sa.Column("date", sa.String(), nullable=True),
        sa.Column("therapist", sa.String(), nullable=True),
        sa.Column("phase", sa.String(), nullable=True),
        sa.Column("success_rate", sa.Float(), nullable=True),
        sa.Column("independent_rate", sa.Float(), nullable=True),
        sa.Column("prompt_rate", sa.Float(), nullable=True),
        sa.Column("evolution", sa.Text(), nullable=True),
    )
    op.create_index("ix_program_records_program_id", "program_records", ["program_id"])
    op.create_index("ix_program_records_date", "program_records", ["date"])

    op.create_table(
        "program_target_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("program_id", sa.Integer(), sa.ForeignKey("programs.id"), nullable=True),
        sa.Column("target_name", sa.String(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=True),
        sa.Column("independent_rate", sa.Float(), nullable=True),
        sa.Column("prompt_rate", sa.Float(), nullable=True),
        sa.Column("success_rate", sa.Float(), nullable=True),
        sa.Column("date", sa.String(), nullable=True),
        sa.Column("prompt_type", sa.String(), nullable=True),
        sa.Column("evolution", sa.Text(), nullable=True),
    )
    op.create_index("ix_program_target_records_program_id", "program_target_records", ["program_id"])
    op.create_index("ix_program_target_records_date", "program_target_records", ["date"])

    op.create_table(
        "interfering_behaviors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("patient_id", sa.String(), sa.ForeignKey("patients.id"), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
    )
    op.create_index("ix_interfering_behaviors_patient_id", "interfering_behaviors", ["patient_id"])

    op.create_table(
        "interfering_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("behavior_id", sa.Integer(), sa.ForeignKey("interfering_behaviors.id"), nullable=True),
        sa.Column("date", sa.String(), nullable=True),
        sa.Column("therapist", sa.String(), nullable=True),
        sa.Column("count", sa.Integer(), nullable=True),
        sa.Column("rate", sa.Float(), nullable=True),
        sa.Column("evolution", sa.Text(), nullable=True),
    )
    op.create_index("ix_interfering_records_behavior_id", "interfering_records", ["behavior_id"])
    op.create_index("ix_interfering_records_date", "interfering_records", ["date"])

    op.create_table(
        "program_library",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=True, unique=True),
        sa.Column("objective_template", sa.String(), nullable=True),
        sa.Column("mastery_threshold_percent", sa.Float(), nullable=True),
        sa.Column("mastery_days", sa.Integer(), nullable=True),
        sa.Column("suggested_targets", sa.String(), nullable=True),
    )
    op.create_index("ix_program_library_name", "program_library", ["name"])


def downgrade() -> None:
    op.drop_table("program_library")
    op.drop_table("interfering_records")
    op.drop_table("interfering_behaviors")
    op.drop_table("program_target_records")
    op.drop_table("program_records")
    op.drop_table("programs")
    op.drop_table("patients")
