"""facts source_field, confidence already existed

Revision ID: 07936af7080d
Revises: 571e2be57951
Create Date: 2026-08-13 13:24:46.415393

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '07936af7080d'
down_revision: Union[str, Sequence[str], None] = '571e2be57951'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # AI-secretary тепер віддає facts[].source_field для похідних фактів
    # (ім'я поля схеми, з якого факт побудований) -- дозволяє колись точково
    # пов'язати review_queue з конкретним фактом замість документа цілком
    # (для багатопольового основного факту однозначного джерела все ще нема,
    # але для похідних тепер є). facts.confidence НЕ додається тут -- ця
    # колонка існує з першої-таки миграції (60ea874484ed), просто досі не
    # заповнювалась завантажувачем.
    op.add_column("facts", sa.Column("source_field", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("facts", "source_field")
