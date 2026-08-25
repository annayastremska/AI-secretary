"""tsvector фрагмента -- колонкою, а не виразом

Revision ID: a7c3e5b21d84
Revises: f2b9c4a1e805
Create Date: 2026-08-24

Знайдено заміром на реальному корпусі (8599 фрагментів, 8.5 млн символів):
лексична гілка гібридного пошуку виконувалась **8.3 секунди**.

EXPLAIN ANALYZE показав, що винне не знаходження, а РАНЖУВАННЯ. Індекс
відбирає кандидатів за мілісекунди (Bitmap Index Scan ~1 мс), а далі
`ts_rank(to_tsvector('ukrainian', ch.text), ...)` у ORDER BY перелематизує
Hunspell-ом кожен зі 5189 відібраних фрагментів заново. Український Hunspell
із повним словником на 336 тис. рядків -- дорога операція, і на кожен запит
вона робилась тисячі разів.

На восьми документах цього не побачити: там і кандидатів десятки. Тому воно й
не вилізло локально.

Виправлення: тримати tsvector збереженою колонкою. Тоді і збіг, і ранг беруть
готове значення.

Чому не GENERATED ALWAYS: `to_tsvector(regconfig, text)` -- STABLE, а не
IMMUTABLE (конфігурація пошуку може змінитись), і Postgres не дозволяє її в
генерованій колонці. Тому звичайна колонка + тригер.

Тригер, а не заповнення в застосунку: фрагменти пише не лише наш скрипт, і
колонка, яку легко забути заповнити, гірша за трохи магії в базі. При зміні
словника колонку треба перерахувати -- як і індекс раніше (див. коментар про
REINDEX у 571e2be57951).
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'a7c3e5b21d84'
down_revision: Union[str, Sequence[str], None] = 'f2b9c4a1e805'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE document_chunks ADD COLUMN tsv tsvector")

    op.execute("""
        CREATE OR REPLACE FUNCTION document_chunks_tsv_update() RETURNS trigger AS $$
        BEGIN
            NEW.tsv := to_tsvector('ukrainian', coalesce(NEW.text, ''));
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER document_chunks_tsv_trg
            BEFORE INSERT OR UPDATE OF text ON document_chunks
            FOR EACH ROW EXECUTE FUNCTION document_chunks_tsv_update()
    """)

    # Заповнюємо наявні. UPDATE зачепить тригер, тому пишемо напряму.
    op.execute("UPDATE document_chunks SET tsv = to_tsvector('ukrainian', coalesce(text, ''))")

    # Індекс тепер по колонці, а не по виразу -- старий стає непотрібним.
    op.execute("CREATE INDEX document_chunks_tsv_idx ON document_chunks USING GIN (tsv)")
    op.execute("DROP INDEX IF EXISTS document_chunks_fts_idx")

    op.execute("""
        COMMENT ON COLUMN document_chunks.tsv IS
        'Готовий tsvector тексту фрагмента. Потрібен саме збереженим: '
        'ts_rank по виразу перелематизовував Hunspell-ом тисячі фрагментів '
        'на кожен запит (8.3 с на корпусі з 8599 фрагментів). '
        'При зміні словника -- UPDATE document_chunks SET tsv = to_tsvector(...)'
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("""
        CREATE INDEX document_chunks_fts_idx ON document_chunks
            USING GIN (to_tsvector('ukrainian', text))
    """)
    op.execute("DROP INDEX IF EXISTS document_chunks_tsv_idx")
    op.execute("DROP TRIGGER IF EXISTS document_chunks_tsv_trg ON document_chunks")
    op.execute("DROP FUNCTION IF EXISTS document_chunks_tsv_update()")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS tsv")
