"""Реквізити документа й структурні одиниці

Revision ID: b5f1c7a92d63
Revises: a7c3e5b21d84
Create Date: 2026-08-26

Переносить у постійну схему те, що перевірено в `andriy_test`: реквізити
документа колонками і структурні одиниці замість 1200-символьних вікон.

## Реквізити -- колонками, а не в pipeline_meta

Жоден із 41 нормативного документа не мав назви: вони прийшли з пайплайну,
який залишив UUID. Без цих полів не працює ні цитування джерела («назва, № від
дата»), ні тест «знайшли правильний документ», ні запит за номером.

Чому колонками, а не в JSON і не в YAML-шапці файла: чинність і домен мусять
відсікатися ВСЕРЕДИНІ SQL-запиту разом із векторним пошуком, а не пост-фільтром
поверх результатів -- інакше кількість результатів і час відповіді самі
розкажуть, що щось відфільтровано, і `LIMIT` почне брехати. По YAML-файлах
такого запиту не напишеш.

`identifier_key` -- нормалізований ключ для ПОШУКУ ЗА НОМЕРОМ. Він потрібен
окремо від `doc_identifier`, бо запит `НД ТЗІ 2.5-004-99` через FTS
безнадійний: Postgres токенізує номер як 'нд' 'тзі' '2.5' '-004' '-99', і
AND-запит по цьому дає 85 фрагментів -- нуль розрізнювальної здатності. Гірше:
текст цього номера є у 6 документах, бо стандарти цитують один одного, а Є цим
номером лише один. Ідентифікатор -- ключ, а не запит.

## Одиниці -- замість вікон, і чому це не косметика

Наявні `document_chunks` ріжуться по 1200 символів. Заміряно: 87% із них
починаються з СЕРЕДИНИ РЕЧЕННЯ. Причина -- `chunk_text` ріже по абзацах, але в
цьому корпусі порожніх рядків між абзацами немає, тому весь документ стає одним
«абзацом» і ріжеться жорстко кожні 1000 символів.

Структурні одиниці, які не розрізані за довжиною, не починаються з середини
речення НІ РАЗУ (0 з 9572). І в них є адреса: `Стаття 26 / 5`, `4.1/4.1.2` --
без неї не написати «Джерело: ..., стаття 12» і не поставити тест
«процитовано правильний пункт».

`document_chunks` ЗАЛИШАЄТЬСЯ. Одиниці індексуються за 0.5 хв проти 5.5 хв у
кусків (одиниці коротші), тому тримати обидві нарізки для порівняння майже
безкоштовно.

### Чому `base_label` окремою колонкою

Одиниця, довша за 2000 символів, ріжеться на частини. Шукаємо по частинах
(вони влазять у вікно моделі -- 512 токенів це ~2100 символів українською), а
цитуємо цілу логічну одиницю: `min(char_start)..max(char_end)` по всіх частинах
з тією самою `base_label`. Без цієї колонки такий запит доводилось би робити
відрізанням суфікса від мітки на льоту.

### `from_length_split` -- про чесність відповіді

Частина, породжена обрізанням за довжиною, а не структурою, не є «пунктом 3.4»
-- вона фрагмент пункту 3.4. Прапорець дає відповіді сказати правду замість
вигаданої адреси. Заміряно: таких 21% одиниць.

## Групи дублікатів

У корпусі той самий акт лежить двічі: 199/216, 201/224, 205/222, 237/238. Це не
теорія -- на питанні про рапорт ОБА місця топ-2 пішли на ту саму цитату з пари
237/238, витіснивши документи, які могли б відповісти.

Видаляти не можна: провенанс тримається на обох документах пари. Тому
групування -- обидва лишаються, у видачі група рахується як один результат.

## Тригер на tsv, а не заповнення в застосунку

Так само, як для `document_chunks` у міграції a7c3e5b21d84: колонку, яку легко
забути заповнити, гірша за трохи магії в базі. Причина, чому tsvector саме
збережений, а не вираз в ORDER BY: `ts_rank(to_tsvector(...))` перелематизовував
Hunspell-ом тисячі фрагментів на кожен запит -- 8.3 с на корпусі з 8599
фрагментів проти 0.19 с зі збереженою колонкою.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b5f1c7a92d63'
down_revision: Union[str, Sequence[str], None] = 'a7c3e5b21d84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ── реквізити документа ────────────────────────────────────────────────
    op.execute("""
        ALTER TABLE documents
            ADD COLUMN doc_identifier   text,
            ADD COLUMN doc_title        text,
            ADD COLUMN issue_date       date,
            ADD COLUMN identifier_key   text,
            ADD COLUMN identity_source  text,
            ADD COLUMN identity_confidence numeric(3,2)
    """)
    op.execute("""
        ALTER TABLE documents ADD CONSTRAINT documents_identity_source_check
            CHECK (identity_source IS NULL
                   OR identity_source IN ('header', 'text', 'manual'))
    """)
    # Пошук за номером -- рівність по нормалізованому ключу, тому btree.
    op.execute("CREATE INDEX documents_identifier_key_idx ON documents (identifier_key)")
    op.execute("""
        COMMENT ON COLUMN documents.identifier_key IS
        'Нормалізований ідентифікатор для пошуку ЗА НОМЕРОМ: складені '
        'конфузабли (латинська I -> кирилична І), знято пробіли й уніфіковано '
        'тире. Через FTS такий запит не працює: Postgres токенізує '
        '«НД ТЗІ 2.5-004-99» як 4 окремі леми і дає 85 збігів'
    """)
    op.execute("""
        COMMENT ON COLUMN documents.identity_source IS
        'header -- з YAML-шапки вивантаження (source_file); text -- витягнуто '
        'з голови чи підписного блоку самого тексту; manual -- поставлено '
        'людиною. Заміряно, що джерела ДОПОВНЮЮТЬ одне одного: шапка знає '
        'номер у 7 документів, де в тексті його немає, а текст -- у 2, де '
        'файл названо руками'
    """)

    # ── структурні одиниці ─────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE document_units (
            id                bigserial PRIMARY KEY,
            document_id       bigint NOT NULL
                                  REFERENCES documents (id) ON DELETE CASCADE,
            ord               integer NOT NULL,
            label             text NOT NULL,
            base_label        text NOT NULL,
            parent_label      text,
            char_start        integer NOT NULL,
            char_end          integer NOT NULL,
            from_length_split boolean NOT NULL DEFAULT false,
            text              text NOT NULL,
            tsv               tsvector,
            embedding         vector(384),
            embedding_model   text,
            created_at        timestamptz NOT NULL DEFAULT now(),
            UNIQUE (document_id, ord),
            CHECK (char_end > char_start)
        )
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION document_units_tsv_update() RETURNS trigger AS $$
        BEGIN
            NEW.tsv := to_tsvector('ukrainian', coalesce(NEW.text, ''));
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER document_units_tsv_trg
            BEFORE INSERT OR UPDATE OF text ON document_units
            FOR EACH ROW EXECUTE FUNCTION document_units_tsv_update()
    """)

    op.execute("CREATE INDEX document_units_tsv_idx ON document_units USING GIN (tsv)")
    op.execute("CREATE INDEX document_units_doc_idx ON document_units (document_id)")
    # Цитата збирається запитом «усі частини цієї логічної одиниці», тому
    # складений індекс саме по (document_id, base_label).
    op.execute("CREATE INDEX document_units_base_idx ON document_units (document_id, base_label)")
    op.execute("""
        CREATE INDEX document_units_emb_idx ON document_units
            USING hnsw (embedding vector_cosine_ops)
    """)

    op.execute("""
        COMMENT ON COLUMN document_units.base_label IS
        'Мітка логічної одиниці без суфікса частини. Шукаємо по частинах (вони '
        'влазять у 512 токенів моделі), а цитуємо цілу одиницю: '
        'min(char_start)..max(char_end) по всіх частинах із цією base_label'
    """)
    op.execute("""
        COMMENT ON COLUMN document_units.from_length_split IS
        'true -- одиницю породило обрізання за довжиною, а не структура. Про '
        'таку не можна написати «пункт 3.4»: вона ФРАГМЕНТ пункту 3.4. '
        'Заміряно 21% одиниць'
    """)
    op.execute("""
        COMMENT ON COLUMN document_units.char_start IS
        'Зсув у documents.text_content. Різання йде по СИРОМУ тексту саме '
        'тому: будь-яке очищення змінює довжину, після чого зсув указує не '
        'туди, куди думає, і цитату вже не показати в оригіналі'
    """)

    # ── групи дублікатів ───────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE document_groups (
            document_id  bigint PRIMARY KEY
                             REFERENCES documents (id) ON DELETE CASCADE,
            canonical_id bigint NOT NULL
                             REFERENCES documents (id) ON DELETE CASCADE,
            reason       text NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX document_groups_canonical_idx ON document_groups (canonical_id)")
    op.execute("""
        COMMENT ON TABLE document_groups IS
        'Групи документів-дублікатів: той самий акт, вивантажений двічі '
        '(199/216, 201/224, 205/222, 237/238). Не видаляємо жодного -- '
        'провенанс тримається на обох; у видачі група рахується як один '
        'результат. Канонічний -- той, у кого є ідентифікатор'
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS document_groups")
    op.execute("DROP TRIGGER IF EXISTS document_units_tsv_trg ON document_units")
    op.execute("DROP FUNCTION IF EXISTS document_units_tsv_update()")
    op.execute("DROP TABLE IF EXISTS document_units")
    op.execute("DROP INDEX IF EXISTS documents_identifier_key_idx")
    op.execute("""
        ALTER TABLE documents
            DROP CONSTRAINT IF EXISTS documents_identity_source_check,
            DROP COLUMN IF EXISTS doc_identifier,
            DROP COLUMN IF EXISTS doc_title,
            DROP COLUMN IF EXISTS issue_date,
            DROP COLUMN IF EXISTS identifier_key,
            DROP COLUMN IF EXISTS identity_source,
            DROP COLUMN IF EXISTS identity_confidence
    """)
