"""Normalização auditável e identificação determinística das transições ABC.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""
from alembic import op
import sqlalchemy as sa


revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.add_column("abc_categories", sa.Column("nome_original", sa.String(200), nullable=True))
    op.add_column("abc_categories", sa.Column("nome_normalizado", sa.String(200), nullable=True))
    op.add_column("abc_categories", sa.Column("regra_normalizacao", sa.String(160), nullable=True))
    op.add_column("abc_categories", sa.Column("versao_normalizacao", sa.String(40), nullable=True))
    op.add_column("abc_interval_events", sa.Column("funcao_hipotese_original", sa.String(120), nullable=True))
    op.add_column("abc_interval_events", sa.Column("funcao_hipotese_normalizada", sa.String(120), nullable=True))
    op.add_column("abc_interval_events", sa.Column("versao_normalizacao", sa.String(40), nullable=True))
    op.add_column("abc_chain_candidates", sa.Column("transition_id", sa.String(64), nullable=True))
    op.create_index("ix_abc_chain_candidates_transition_id", "abc_chain_candidates", ["transition_id"], unique=True)

    op.execute(
        """
        UPDATE abc_categories
           SET nome_original = COALESCE(nome_original, nome),
               nome_normalizado = CASE
                   WHEN lower(trim(nome)) IN ('manejo fisíco', 'manejo fisico') THEN 'Manejo físico'
                   WHEN lower(trim(nome)) = 'do nada' THEN 'Antecedente não identificado no registro'
                   ELSE regexp_replace(trim(nome), '\\s+', ' ', 'g')
               END,
               regra_normalizacao = CASE
                   WHEN lower(trim(nome)) IN ('manejo fisíco', 'manejo fisico') THEN 'known_alias:consequencia:manejo fisico'
                   WHEN lower(trim(nome)) = 'do nada' THEN 'known_alias:antecedente:do nada'
                   ELSE 'trim_whitespace'
               END,
               versao_normalizacao = 'abc-normalization-v1'
         WHERE versao_normalizacao IS NULL
        """
    )
    op.execute(
        """
        UPDATE abc_interval_events
           SET funcao_hipotese_original = COALESCE(funcao_hipotese_original, funcao_hipotese),
               funcao_hipotese_normalizada = CASE
                   WHEN funcao_hipotese IS NULL OR trim(funcao_hipotese) = '' THEN 'Não identificada'
                   WHEN lower(trim(funcao_hipotese)) IN ('fuga ou esquiva', 'fuga/esquiva') THEN 'Fuga/esquiva'
                   ELSE regexp_replace(trim(funcao_hipotese), '\\s+', ' ', 'g')
               END,
               versao_normalizacao = 'abc-normalization-v1'
         WHERE versao_normalizacao IS NULL
        """
    )
    op.execute(
        """
        UPDATE abc_chain_candidates
           SET transition_id = encode(
               digest(
                   patient_token::text || E'\\x1f' ||
                   COALESCE(from_behavior_event_id::text, from_interval_id::text) || E'\\x1f' ||
                   COALESCE(to_behavior_event_id::text, to_interval_id::text) || E'\\x1f' ||
                   COALESCE(rule_version, config_version),
                   'sha256'
               ),
               'hex'
           )
         WHERE transition_id IS NULL
        """
    )
    op.alter_column("abc_chain_candidates", "transition_id", nullable=False)


def downgrade() -> None:
    op.drop_index("ix_abc_chain_candidates_transition_id", table_name="abc_chain_candidates")
    op.drop_column("abc_chain_candidates", "transition_id")
    op.drop_column("abc_interval_events", "versao_normalizacao")
    op.drop_column("abc_interval_events", "funcao_hipotese_normalizada")
    op.drop_column("abc_interval_events", "funcao_hipotese_original")
    op.drop_column("abc_categories", "versao_normalizacao")
    op.drop_column("abc_categories", "regra_normalizacao")
    op.drop_column("abc_categories", "nome_normalizado")
    op.drop_column("abc_categories", "nome_original")
