-- Схема БД під пайплайн екстракції. Відповідає трьохшаровій структурі,
-- погодженій у context/project-expectations.md розд. 4:
--   documents -- реєстр документів (кожен файл і його тип)
--   subjects  -- реєстр об'єктів (люди/техніка), з псевдонімами
--   facts     -- ОДНА спільна таблиця станів для всіх вимірів
-- Новий тип документа додає рядок у довідник, а не нову таблицю -- тому
-- логіка підрахунків не змінюється з кожним новим бланком.
--
-- НЕ ПЕРЕВІРЕНО на живому PostgreSQL -- у середовищі розробки немає сервера.

CREATE TABLE IF NOT EXISTS documents (
    id            UUID PRIMARY KEY,
    -- UNIQUE саме тут, а не перевіркою в коді: ручний експорт людиною з
    -- АСКОД/Армія+ означає, що той самий документ рано чи пізно завантажать
    -- двічі, а подвійні факти подвоюють підрахунки -- головну цінність системи.
    file_hash     TEXT NOT NULL UNIQUE,
    domain        TEXT,
    template      TEXT,
    -- confirmed | needs_review | unresolved | duplicate
    status        TEXT NOT NULL,
    storage_key   TEXT,
    uploaded_at   TIMESTAMPTZ NOT NULL,
    -- повна шапка (subject, facts, provenance) як вона пішла в MinIO --
    -- щоб запис можна було перерахувати після виправлення схеми
    meta          JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS documents_status_idx   ON documents (status);
CREATE INDEX IF NOT EXISTS documents_template_idx ON documents (template);

CREATE TABLE IF NOT EXISTS subjects (
    id                 BIGSERIAL PRIMARY KEY,
    kind               TEXT NOT NULL,            -- person | vehicle | unit | ...
    attributes         JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_document_id UUID REFERENCES documents (id),
    -- Ототожнення осіб НЕ робиться автоматично за ПІБ: новий документ дає
    -- чернетковий subject, а об'єднання підтверджує людина (див.
    -- context/open-questions.md). Автоматичне вгадування "той самий Іваненко"
    -- -- саме той клас тихої помилки, від якого застерігає розд. 7
    -- architecture-proposal.md.
    confirmed          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Історія псевдонімів: те саме ім'я/позивний записані по-різному в різних
-- документах (вимога з project-expectations.md розд. 4б).
CREATE TABLE IF NOT EXISTS subject_aliases (
    id                 BIGSERIAL PRIMARY KEY,
    subject_id         BIGINT NOT NULL REFERENCES subjects (id) ON DELETE CASCADE,
    alias              TEXT NOT NULL,
    source_document_id UUID REFERENCES documents (id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS facts (
    id                        BIGSERIAL PRIMARY KEY,
    subject_id                BIGINT REFERENCES subjects (id),
    fact_type                 TEXT NOT NULL,     -- -> dictionaries/fact_type_registry.yaml
    value_code                TEXT,
    date_start                DATE,
    date_end                  DATE,              -- NULL = ще діє
    -- Підтверджений = усі КРИТИЧНІ поля витягнуто. Шаблони запитів читають
    -- лише confirmed = TRUE (project-expectations.md розд. 6), тому цей
    -- прапорець напряму впливає на всі підрахунки.
    confirmed                 BOOLEAN NOT NULL DEFAULT FALSE,
    status                    TEXT NOT NULL DEFAULT 'current',
    superseded_by_document_id UUID REFERENCES documents (id),
    source_document_id        UUID NOT NULL REFERENCES documents (id),
    additional_info           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS facts_type_dates_idx ON facts (fact_type, date_start, date_end);
CREATE INDEX IF NOT EXISTS facts_subject_idx    ON facts (subject_id);
CREATE INDEX IF NOT EXISTS facts_confirmed_idx  ON facts (confirmed);

-- Черга ручної роботи: непідтверджені записи + 5%-вибірка серед
-- підтверджених (architecture-proposal.md розд. 3 -- інакше рівень помилки
-- системи лишається невідомим, а не нульовим).
CREATE TABLE IF NOT EXISTS review_queue (
    id          BIGSERIAL PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    -- needs_review | random_audit | unresolved_template
    reason      TEXT NOT NULL,
    resolved_at TIMESTAMPTZ,
    resolved_by TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS review_queue_open_idx ON review_queue (resolved_at) WHERE resolved_at IS NULL;
