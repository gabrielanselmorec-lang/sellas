"""ABC severity, function hypothesis and edit audit

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("abc_interval_events", sa.Column("causou_lesao", sa.Boolean(), nullable=True))
    op.add_column("abc_interval_events", sa.Column("houve_sangramento", sa.Boolean(), nullable=True))
    op.add_column("abc_interval_events", sa.Column("direcionado_ponto_vital", sa.Boolean(), nullable=True))
    op.add_column("abc_interval_events", sa.Column("funcao_hipotese", sa.String(120), nullable=True))
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.abc_action_logs') IS NOT NULL THEN
                ALTER TABLE abc_action_logs DROP CONSTRAINT IF EXISTS chk_abc_action_logs_acao;
                ALTER TABLE abc_action_logs ADD CONSTRAINT chk_abc_action_logs_acao CHECK (
                    acao IN ('registro_adicionado', 'registro_editado', 'registro_removido', 'categoria_criada')
                );
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_column("abc_interval_events", "funcao_hipotese")
    op.drop_column("abc_interval_events", "direcionado_ponto_vital")
    op.drop_column("abc_interval_events", "houve_sangramento")
    op.drop_column("abc_interval_events", "causou_lesao")
