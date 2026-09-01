"""core_tables

Revision ID: 60ea874484ed
Revises: 1283dc745daa
Create Date: 2026-08-05 14:05:03.109256

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '60ea874484ed'
down_revision: Union[str, Sequence[str], None] = '1283dc745daa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE TABLE documents (
            id BIGSERIAL PRIMARY KEY,
            type_id INTEGER REFERENCES document_types(id),
            source_id INTEGER REFERENCES sources(id),
            source_kind TEXT NOT NULL CHECK (source_kind IN ('electronic', 'photo')),
            status TEXT NOT NULL DEFAULT 'uploaded'
                CHECK (status IN ('uploaded', 'text_ready', 'classified', 'extracted', 'failed')),
            raw_uri TEXT,
            text_content TEXT,
            uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            processed_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ,
            checksum TEXT
        )
    """)

    # objects — спільний "якір": id + вид об'єкта. Поля, специфічні для виду
    # (людина/техніка/завдання), розбиті по окремих таблицях нижче (1:1 на objects.id),
    # бо набори полів надто різні, щоб тримати їх в одній широкій таблиці.
    op.execute("""
        CREATE TABLE objects (
            id BIGSERIAL PRIMARY KEY,
            kind_id INTEGER NOT NULL REFERENCES object_kinds(id),
            canonical_name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Статичні поля з офіційної форми "Штатної книжки" — ідентичність людини,
    # яка не змінюється (чи змінюється вкрай рідко) протягом служби.
    # Звання й посада НЕ тут: у формі це історія з датами й номерами наказів
    # ("Наказ на присвоєння останнього звання", "Індекси посад, які обіймав(ла)")
    # — тобто це "стан", що змінюється в часі, і йому місце в facts/dimensions,
    # а не статична колонка.
    op.execute("""
        CREATE TABLE people (
            object_id BIGINT PRIMARY KEY REFERENCES objects(id),
            service_id TEXT,
            last_name TEXT NOT NULL,
            first_name TEXT NOT NULL,
            patronymic TEXT,
            rnokpp TEXT,
            id_document_type TEXT,
            id_document_series TEXT,
            id_document_number TEXT,
            birth_date DATE,
            birth_place TEXT,
            gender TEXT,
            education TEXT,
            service_type TEXT,               -- вид служби (контракт/мобілізований/строкова тощо)
            contract_start_date DATE,
            contract_end_date DATE,
            conscription_period TEXT,        -- період призову, якщо не фіксована дата (базова військова служба)
            enrollment_date DATE,             -- дата зарахування до списків частини
            enrollment_order_date DATE,       -- наказ про зарахування до списків
            enrollment_order_number TEXT,
            arrived_from TEXT,                -- звідки прибув
            service_entry_date DATE,          -- коли призваний(а)/прийнятий(а) на військову службу
            service_entry_authority TEXT,     -- ким призваний(а)/прийнятий(а)
            relatives_info TEXT,
            additional_info TEXT
        )
    """)

    op.execute("""
        CREATE TABLE equipment (
            object_id BIGINT PRIMARY KEY REFERENCES objects(id),
            inventory_number TEXT,
            equipment_type TEXT,
            model TEXT
        )
    """)

    op.execute("""
        CREATE TABLE tasks (
            object_id BIGINT PRIMARY KEY REFERENCES objects(id),
            task_type TEXT,
            description TEXT
        )
    """)

    # UNIQUE(alias) — запобіжник від гонок при одночасному резолві псевдонімів
    # з різних джерел (синтетика + фото-потік пишуть паралельно).
    op.execute("""
        CREATE TABLE object_aliases (
            id BIGSERIAL PRIMARY KEY,
            object_id BIGINT NOT NULL REFERENCES objects(id),
            alias TEXT UNIQUE NOT NULL,
            first_seen_in_doc BIGINT REFERENCES documents(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE facts (
            id BIGSERIAL PRIMARY KEY,
            object_id BIGINT NOT NULL REFERENCES objects(id),
            dimension_id INTEGER NOT NULL REFERENCES dimensions(id),
            value TEXT,
            valid_from DATE,
            valid_to DATE,
            source_doc_id BIGINT REFERENCES documents(id),
            confidence NUMERIC,
            status TEXT NOT NULL DEFAULT 'unconfirmed'
                CHECK (status IN ('confirmed', 'unconfirmed', 'rejected')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            version INTEGER NOT NULL DEFAULT 1,
            CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
        )
    """)

    op.execute("""
        CREATE TABLE review_queue (
            id BIGSERIAL PRIMARY KEY,
            document_id BIGINT REFERENCES documents(id),
            fact_id BIGINT REFERENCES facts(id),
            queue_type TEXT NOT NULL
                CHECK (queue_type IN ('unknown_type', 'unconfirmed_fact', 'handwritten', 'qa_sample')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            assigned_to TEXT,
            resolved_at TIMESTAMPTZ,
            resolution TEXT
        )
    """)

    # review_log — INSERT-only аудит: хто, коли, старе → нове значення
    op.execute("""
        CREATE TABLE review_log (
            id BIGSERIAL PRIMARY KEY,
            fact_id BIGINT NOT NULL REFERENCES facts(id),
            changed_by TEXT NOT NULL,
            changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            old_value TEXT,
            new_value TEXT,
            action TEXT NOT NULL
        )
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE review_log")
    op.execute("DROP TABLE review_queue")
    op.execute("DROP TABLE facts")
    op.execute("DROP TABLE object_aliases")
    op.execute("DROP TABLE tasks")
    op.execute("DROP TABLE equipment")
    op.execute("DROP TABLE people")
    op.execute("DROP TABLE objects")
    op.execute("DROP TABLE documents")
