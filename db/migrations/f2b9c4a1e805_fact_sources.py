"""fact_sources: той самий факт може мати кілька документів-джерел

Revision ID: f2b9c4a1e805
Revises: d4e8b1c60937
Create Date: 2026-08-24

Причина -- п. 3.2 інструкції Ані: у демо-наборі **8 документів існують і в
.docx, і в .pdf** (навмисно, щоб показати обидва шляхи витягу). Пайплайн
обробляє їх як два незалежні документи -- різні файли, різний `file_hash` --
тому в базі з'являються два однакові факти про ту саму особу. Особа при цьому
одна: `resolve_or_create_object` зводить її правильно.

Наслідок, який побачила б комісія: підрахунок і список дають РІЗНІ числа.
Підрахунки написані як `COUNT(DISTINCT f.object_id)` і не страждають, а
список віддає рядок на факт -- «скільком у відпустці» скаже 12, а «хто у
відпустці» покаже 13 рядків, де одне прізвище двічі.

Аня пропонувала два шляхи. Мінімум -- `DISTINCT` у спискових шаблонах;
правильніше -- не створювати другий факт, а долучити другий документ як ще
одне джерело. Робимо друге, і ось чому: `DISTINCT` довелося б додати в кожен
спискових шаблон і в кожен майбутній, тобто ми лікували б КОЖНОГО читача
даних замість самих даних. Одна забута вибірка -- і дубль повертається.

`facts.source_doc_id` лишається як є (первинне джерело, на нього спираються
наявні запити), а ця таблиця тримає ПОВНИЙ перелік документів, що
підтверджують факт, включно з первинним. Обидва документи лишаються в
`documents` -- Аня прямо просила їх не видаляти, на них тримається провенанс.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'f2b9c4a1e805'
down_revision: Union[str, Sequence[str], None] = 'd4e8b1c60937'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE TABLE fact_sources (
            fact_id BIGINT NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
            document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            is_primary BOOLEAN NOT NULL DEFAULT false,
            added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (fact_id, document_id)
        )
    """)
    op.execute("CREATE INDEX fact_sources_document_id_idx ON fact_sources (document_id)")

    # Первинне джерело кожного наявного факту -- щоб таблиця одразу була
    # повною, а не заповнювалась лише новими завантаженнями.
    op.execute("""
        INSERT INTO fact_sources (fact_id, document_id, is_primary)
        SELECT id, source_doc_id, true
          FROM facts
         WHERE source_doc_id IS NOT NULL
        ON CONFLICT DO NOTHING
    """)

    op.execute("""
        COMMENT ON TABLE fact_sources IS
        'Усі документи, що підтверджують факт. Потрібна, бо той самий документ '
        'приходить у docx і pdf як два різні файли: факт один, джерел два. '
        'facts.source_doc_id лишається первинним джерелом'
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS fact_sources")
