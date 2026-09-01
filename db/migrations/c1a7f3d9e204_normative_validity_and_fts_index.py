"""normative validity + FTS GIN index

Revision ID: c1a7f3d9e204
Revises: 02ca65e93d52
Create Date: 2026-08-22

Дві речі, обидві потрібні для дороги Б (нормативка).

1. Стан чинності документа. `documents.status` -- це стадія КОНВЕЄРА
   (uploaded/text_ready/.../failed), і плутати з нею чинність нормативного
   акта не можна: скасований наказ цілком успішно пройшов конвеєр. Тому
   окремі колонки.

   Deny-by-default: дефолт `unknown`, і пошук по нормативці бере ЛИШЕ
   `current`. Документ, про чинність якого ми не знаємо, не цитується --
   за нашим же дизайном (`normative-docs-subsystem.md` §4: «система ніколи
   не позначає документ чинним самостійно») і за банківським правилом
   «фрагмент без мітки вважається недоступним».

2. GIN-індекс під український FTS. Міграція 571e2be57951 створила словник і
   конфігурацію `ukrainian`, але індексу не створила -- у міграціях їх узагалі
   було нуль, тобто пошук ішов послідовним сканом. На 8 документах непомітно,
   на 40 з кодексом і законами по 500 тис. символів -- уже ні.

   Плюс індекси на зовнішні ключі, яких теж не було.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c1a7f3d9e204'
down_revision: Union[str, Sequence[str], None] = '02ca65e93d52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        ALTER TABLE documents
            ADD COLUMN validity TEXT NOT NULL DEFAULT 'unknown'
                CHECK (validity IN ('current', 'probably_superseded',
                                    'superseded', 'cancelled', 'unknown')),
            ADD COLUMN validity_source TEXT
                CHECK (validity_source IN ('declared', 'inferred', 'manual')),
            ADD COLUMN superseded_by_doc_id BIGINT REFERENCES documents(id)
    """)

    # Чому саме так, а не тригером: `probably_superseded` -- це стан, у якому
    # система ЩЕ НЕ ЗНАЄ, чи документ замінено (той самий номер, новіша дата).
    # Такий документ пошук бачить, але відповідь мусить несити попередження.
    # Різницю між "не знаємо" і "знаємо, що скасовано" стирати не можна.
    op.execute("""
        COMMENT ON COLUMN documents.validity IS
        'Чинність нормативного акта (НЕ стадія конвеєра -- то documents.status). '
        'unknown = не встановлено, у пошук не потрапляє (deny by default)'
    """)
    op.execute("""
        COMMENT ON COLUMN documents.validity_source IS
        'Звідки стан: declared -- у тексті прямо написано, inferred -- '
        'виведено з номера й дати (не факт), manual -- підтвердила людина'
    """)

    # Український FTS: те, для чого 571e2be57951 створювала конфігурацію.
    op.execute("""
        CREATE INDEX documents_text_fts_idx ON documents
            USING GIN (to_tsvector('ukrainian', coalesce(text_content, '')))
    """)

    # Часткові індекси під найчастіші фільтри дороги Б.
    op.execute("""
        CREATE INDEX documents_normative_current_idx ON documents (id)
            WHERE domain = 'normative' AND validity = 'current'
    """)

    # Зовнішні ключі -- у міграціях не було жодного індексу.
    for table, col in [
        ("facts", "object_id"),
        ("facts", "dimension_id"),
        ("facts", "source_doc_id"),
        ("object_aliases", "object_id"),
        ("review_queue", "document_id"),
        ("review_log", "fact_id"),
        ("documents", "type_id"),
        ("documents", "source_id"),
        ("objects", "kind_id"),
    ]:
        op.execute(f"CREATE INDEX {table}_{col}_idx ON {table} ({col})")

    # Пошук факту на дату -- найгарячіший запит дороги А.
    op.execute("CREATE INDEX facts_validity_idx ON facts (valid_from, valid_to)")
    op.execute("CREATE INDEX documents_checksum_idx ON documents (checksum)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS documents_checksum_idx")
    op.execute("DROP INDEX IF EXISTS facts_validity_idx")
    for table, col in [
        ("facts", "object_id"), ("facts", "dimension_id"),
        ("facts", "source_doc_id"), ("object_aliases", "object_id"),
        ("review_queue", "document_id"), ("review_log", "fact_id"),
        ("documents", "type_id"), ("documents", "source_id"),
        ("objects", "kind_id"),
    ]:
        op.execute(f"DROP INDEX IF EXISTS {table}_{col}_idx")
    op.execute("DROP INDEX IF EXISTS documents_normative_current_idx")
    op.execute("DROP INDEX IF EXISTS documents_text_fts_idx")
    op.execute("""
        ALTER TABLE documents
            DROP COLUMN IF EXISTS superseded_by_doc_id,
            DROP COLUMN IF EXISTS validity_source,
            DROP COLUMN IF EXISTS validity
    """)
