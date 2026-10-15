"""Metadados de episódios e proteção contra reenvio, sem alterar dados originais.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
"""
from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE abc_intervals ADD COLUMN IF NOT EXISTS metadados JSONB NOT NULL DEFAULT '{}'::jsonb")
    op.execute("""CREATE TABLE IF NOT EXISTS abc_record_requests (
        patient_token UUID NOT NULL, request_id UUID NOT NULL,
        payload_hash VARCHAR(64) NOT NULL, response JSONB NOT NULL,
        criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(patient_token, request_id))""")
    op.execute("ALTER TABLE abc_record_requests ENABLE ROW LEVEL SECURITY")


def downgrade():
    raise RuntimeError("Reversão automática bloqueada para preservar os metadados de coleta. Restaure uma cópia validada do banco se necessário.")
