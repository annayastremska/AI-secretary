"""documents checksum unique

Revision ID: 2135e0e3ae2a
Revises: 9337860eac54
Create Date: 2026-08-10 19:22:42.149439

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2135e0e3ae2a'
down_revision: Union[str, Sequence[str], None] = '9337860eac54'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Дедуплікація за checksum досі жила лише на рівні застосунку (SELECT
    # перед INSERT у завантажувачі) -- без DB-обмеження паралельні прогони
    # (напр. ручний CLI-запуск і Airflow DAG одночасно) могли створити два
    # documents-рядки з однаковим вмістом. NULL-и не конфліктують між собою
    # за стандартною семантикою UNIQUE у Postgres, тож це не заважає
    # документам без відомого хешу.
    op.create_unique_constraint("documents_checksum_key", "documents", ["checksum"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("documents_checksum_key", "documents", type_="unique")
