"""chunks + embeddings (pgvector)

Revision ID: d4e8b1c60937
Revises: c1a7f3d9e204
Create Date: 2026-08-22

Гібридний пошук вимагає фрагментів, а не документів цілком, і ось чому це
не «просто ще одна таблиця»:

* **Ембеддинг цілого документа неможливий фізично.** У нас документи до
  520 тис. символів, а вікно енкодера -- 512 токенів. Тобто вектор -- це
  завжди вектор ФРАГМЕНТА, і питання «різати чи не різати» не стоїть.
* Наш дизайн (`normative-docs-subsystem.md` §5.2) відмовлявся від чанкінгу з
  правильного побоювання: «можна витягнути крок 3 без кроку 1». Банк
  розв'язує це не відмовою, а **parent chunk**: шукаємо по дрібному, у
  контекст даємо батьківський. Тому тут є і `document_id` (батько), і
  `char_start`/`char_end` -- щоб цитату можна було довести, а не переказати.
* FTS теж переноситься на фрагменти. Інакше RRF зливав би різні одиниці:
  ранг документа проти рангу фрагмента -- це не порівнянні списки.

Розмірність 384 -- під `multilingual-e5-small`. Вона зафіксована в типі
колонки, тож зміна енкодера на інший розмір = нова міграція + переіндексація
всього корпусу. Це не незручність, а властивість: банківський документ
окремо вимагає, щоб запит кодувався ТИМ САМИМ енкодером, що індексував
корпус, інакше векторний пошук просто неправильний.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd4e8b1c60937'
down_revision: Union[str, Sequence[str], None] = 'c1a7f3d9e204'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 384


def upgrade() -> None:
    """Upgrade schema."""
    # Розширення створює init-скрипт під суперюзером (db/init/001_users_and_db.sh):
    # `CREATE EXTENSION` застосунковому користувачу не дозволений. Тут лише
    # перевіряємо й падаємо з поясненням, а не з «permission denied».
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
                RAISE EXCEPTION 'Розширення vector відсутнє. Створити під '
                    'суперюзером: CREATE EXTENSION vector; (у свіжій базі це '
                    'робить db/init/001_users_and_db.sh). Образ мусить бути '
                    'pgvector/pgvector, не postgres:alpine.';
            END IF;
        END $$
    """)

    op.execute(f"""
        CREATE TABLE document_chunks (
            id BIGSERIAL PRIMARY KEY,
            document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            ord INTEGER NOT NULL,
            text TEXT NOT NULL,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            -- Назва моделі лежить поруч із вектором навмисно: інакше через
            -- місяць неможливо сказати, чим саме проіндексовано, і чи можна
            -- порівнювати ці вектори з новими.
            embedding_model TEXT,
            embedding vector({EMBEDDING_DIM}),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (document_id, ord),
            CHECK (char_end > char_start)
        )
    """)

    op.execute("CREATE INDEX document_chunks_document_id_idx ON document_chunks (document_id)")

    # Лексична гілка гібриду -- по тексту фрагмента, тією ж українською
    # конфігурацією, що й документи.
    op.execute("""
        CREATE INDEX document_chunks_fts_idx ON document_chunks
            USING GIN (to_tsvector('ukrainian', text))
    """)

    # HNSW, не IVFFlat: IVFFlat вимагає навчання на вже наповненій таблиці й
    # деградує, якщо будувати його на порожній. HNSW будується інкрементально
    # і не має цієї пастки -- для корпусу, який ми доливаємо, це важливіше за
    # трохи вищу вартість вставки.
    #
    # vector_cosine_ops: e5 нормалізує вектори, тож косинус -- правильна
    # метрика (і саме її очікує сама модель).
    op.execute("""
        CREATE INDEX document_chunks_embedding_idx ON document_chunks
            USING hnsw (embedding vector_cosine_ops)
    """)

    op.execute("""
        COMMENT ON COLUMN document_chunks.embedding_model IS
        'Яким енкодером порахований вектор. Порівнювати вектори різних '
        'моделей не можна -- при зміні енкодера переіндексувати весь корпус'
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS document_chunks")
    # Саме розширення не прибираємо: ним може користуватись щось інше.
