"""facts additional_info jsonb

Revision ID: 8a667569ba4d
Revises: 349d428a0094
Create Date: 2026-08-11 18:08:36.311715

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = '8a667569ba4d'
down_revision: Union[str, Sequence[str], None] = '349d428a0094'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Поля, специфічні для конкретного шаблону (посада, мета відрядження,
    # номер наказу тощо -- AI-secretary кладе їх у fact["additional_info"],
    # структура різна для кожного template/db_target: additional_info).
    # Досі просто відкидались завантажувачем. JSONB, не типізовані колонки:
    # набір полів плаває від шаблону до шаблону (research-insights.md,
    # "типізовані колонки + JSONB для рідкісних полів" -- уникати EAV).
    op.add_column("facts", sa.Column("additional_info", JSONB(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("facts", "additional_info")
