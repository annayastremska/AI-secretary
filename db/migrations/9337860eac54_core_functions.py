"""core_functions

Revision ID: 9337860eac54
Revises: 60ea874484ed
Create Date: 2026-08-05 14:40:39.552054

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9337860eac54'
down_revision: Union[str, Sequence[str], None] = '60ea874484ed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    # resolve_or_create_object — атомарний резолв псевдонімів.
    # Швидкий шлях: псевдонім уже відомий — просто повертаємо object_id.
    # Повільний шлях: створюємо кандидата в objects, намагаємось атомарно
    # закріпити псевдонім за ним через ON CONFLICT (alias) DO NOTHING RETURNING.
    # Якщо між нашим SELECT і INSERT інший паралельний воркер (синтетика чи
    # фото-потік) устиг закріпити той самий псевдонім першим — прибираємо
    # власний "осиротілий" рядок objects і повертаємо id об'єкта-переможця.
    op.execute("""
        CREATE OR REPLACE FUNCTION resolve_or_create_object(
            p_alias TEXT,
            p_kind_code TEXT,
            p_doc_id BIGINT
        ) RETURNS BIGINT AS $$
        DECLARE
            v_object_id BIGINT;
            v_kind_id INTEGER;
            v_new_object_id BIGINT;
        BEGIN
            SELECT object_id INTO v_object_id
            FROM object_aliases
            WHERE alias = p_alias;

            IF v_object_id IS NOT NULL THEN
                RETURN v_object_id;
            END IF;

            SELECT id INTO v_kind_id FROM object_kinds WHERE code = p_kind_code;
            IF v_kind_id IS NULL THEN
                RAISE EXCEPTION 'Unknown object kind: %', p_kind_code;
            END IF;

            INSERT INTO objects (kind_id, canonical_name)
            VALUES (v_kind_id, p_alias)
            RETURNING id INTO v_new_object_id;

            INSERT INTO object_aliases (object_id, alias, first_seen_in_doc)
            VALUES (v_new_object_id, p_alias, p_doc_id)
            ON CONFLICT (alias) DO NOTHING
            RETURNING object_id INTO v_object_id;

            IF v_object_id IS NOT NULL THEN
                RETURN v_object_id;
            END IF;

            -- програли гонку: чужий INSERT закріпив псевдонім першим
            DELETE FROM objects WHERE id = v_new_object_id;

            SELECT object_id INTO v_object_id
            FROM object_aliases
            WHERE alias = p_alias;

            RETURN v_object_id;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # next_document_for_processing — вибір наступного документа для обробки,
    # SELECT ... FOR UPDATE SKIP LOCKED, щоб два DAG/воркери не взяли той самий
    # документ одночасно. Функція лише вибирає й блокує рядок — виклик має
    # відбуватись в одній транзакції з подальшим UPDATE статусу документа,
    # інакше блокування знімається одразу після виклику й SKIP LOCKED не захищає.
    op.execute("""
        CREATE OR REPLACE FUNCTION next_document_for_processing(
            p_worker_id TEXT,
            p_status_from TEXT
        ) RETURNS BIGINT AS $$
        DECLARE
            v_document_id BIGINT;
        BEGIN
            SELECT id INTO v_document_id
            FROM documents
            WHERE status = p_status_from
            ORDER BY uploaded_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1;

            RETURN v_document_id;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION IF EXISTS next_document_for_processing(TEXT, TEXT)")
    op.execute("DROP FUNCTION IF EXISTS resolve_or_create_object(TEXT, TEXT, BIGINT)")
