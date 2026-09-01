"""ukrainian fts config

Revision ID: 571e2be57951
Revises: 8a667569ba4d
Create Date: 2026-08-11 18:33:24.822979

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '571e2be57951'
down_revision: Union[str, Sequence[str], None] = '8a667569ba4d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Postgres не має словника для української з коробки (лише зразки в
    # tsearch_data/). Файли uk_UA.dict/uk_UA.affix/ukrainian.stop --
    # відкритий Hunspell-словник (brown-uk/dict_uk через wooorm/dictionaries,
    # GPLv3 -- на радар юриста поряд з іншими ліцензійними примітками
    # проєкту) -- змонтовані окремими файлами в docker-compose.yml, щоб не
    # приховати стандартні словники інших мов у тій самій теці.
    op.execute("""
        CREATE TEXT SEARCH DICTIONARY ukrainian_hunspell (
            TEMPLATE = ispell,
            DictFile = uk_ua,
            AffFile = uk_ua,
            StopWords = ukrainian
        )
    """)

    op.execute("CREATE TEXT SEARCH CONFIGURATION ukrainian (COPY = simple)")

    op.execute("""
        ALTER TEXT SEARCH CONFIGURATION ukrainian
            ALTER MAPPING FOR asciiword, asciihword, hword_asciipart,
                               word, hword, hword_part
            WITH ukrainian_hunspell, simple
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TEXT SEARCH CONFIGURATION IF EXISTS ukrainian")
    op.execute("DROP TEXT SEARCH DICTIONARY IF EXISTS ukrainian_hunspell")
