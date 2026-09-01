"""documents domain and pipeline_meta

Revision ID: d765af54fbe6
Revises: 2135e0e3ae2a
Create Date: 2026-08-11 10:25:36.938026

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'd765af54fbe6'
down_revision: Union[str, Sequence[str], None] = '2135e0e3ae2a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # domain -- типізована колонка: часто запитувана ("скільки документів по
    # домену X"), уже рахується AI-secretary на кожному документі, просто
    # раніше нікуди не писалась (найдешевша втрата за аналізом Ані).
    op.add_column("documents", sa.Column("domain", sa.Text(), nullable=True))

    # pipeline_meta -- решта того, що AI-secretary вже рахує, але що не
    # заслуговує на окрему типізовану колонку (рідко читається, форма може
    # змінюватись): identification (anchors/model + бал), field_provenance,
    # unknown_critical_fields, confirmed_empty_fields, warnings, а також
    # зворотнє посилання на бік AI-secretary (ai_secretary_id, storage_key)
    # -- узгоджено з research-insights.md ("типізовані колонки + JSONB для
    # рідкісних полів").
    op.add_column("documents", sa.Column("pipeline_meta", JSONB(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("documents", "pipeline_meta")
    op.drop_column("documents", "domain")
