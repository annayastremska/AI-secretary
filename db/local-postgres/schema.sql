-- Схема Postgres, 1:1 з таблицями stand.sqlite (див. ../seed.py і ../ДОМОВЛЕНІСТЬ-З-БАЗОЮ.md).
-- Навмисно всі поля TEXT, як у SQLite-стенді: контракт вимагає, щоб зіпсовані
-- документи (порожні дати, date_to раніше date_from) лежали як є, не відкидались
-- типізацією колонки. db_postgres.py сам вирішує, чи парсити дати всередині запитів.

CREATE TABLE IF NOT EXISTS people (
    service_id      TEXT PRIMARY KEY,
    full_name       TEXT NOT NULL,
    rank            TEXT NOT NULL DEFAULT '',
    position_title  TEXT NOT NULL DEFAULT '',
    subdivision     TEXT NOT NULL DEFAULT '',
    phone           TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS absences (
    doc_number      TEXT NOT NULL,          -- не унікальний, буває два документи з одним номером
    doc_date        TEXT NOT NULL DEFAULT '',
    doc_type        TEXT NOT NULL DEFAULT '',
    service_id      TEXT NOT NULL DEFAULT '',  -- порожньо = людини немає в реєстрі people
    person_name_raw TEXT NOT NULL DEFAULT '',
    date_from       TEXT NOT NULL DEFAULT '',  -- ISO YYYY-MM-DD рядком або '' — не підставляти
    date_to         TEXT NOT NULL DEFAULT '',
    reason          TEXT NOT NULL DEFAULT '',
    place           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT '',  -- 'чинний' / 'скасований' / 'чернетка'
    superseded_by   TEXT NOT NULL DEFAULT '',
    source_file     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS reference_docs (
    doc_title       TEXT NOT NULL,
    section_number  TEXT NOT NULL,
    section_title   TEXT NOT NULL,
    text            TEXT NOT NULL,
    source_note     TEXT NOT NULL   -- обов'язковий, ТЗ corp-study#52
);

-- Індекси під сім функцій стику (find_people, absences_on_date, returning_on_date,
-- absences_for_person, document_by_number, count_absent_by_subdivision).
CREATE INDEX IF NOT EXISTS idx_people_subdivision   ON people (subdivision);
CREATE INDEX IF NOT EXISTS idx_people_full_name     ON people (full_name);
CREATE INDEX IF NOT EXISTS idx_absences_service_id  ON absences (service_id);
CREATE INDEX IF NOT EXISTS idx_absences_doc_number  ON absences (doc_number);
CREATE INDEX IF NOT EXISTS idx_absences_dates       ON absences (date_from, date_to);
CREATE INDEX IF NOT EXISTS idx_absences_person_raw  ON absences (person_name_raw);

-- Пошук по reference_docs: search_reference на стенді — прості слова;
-- у бойовій версії тут Postgres FTS. Індекс під tsvector — щоб було готово
-- одразу, коли Андрій перейде з LIKE на to_tsquery.
CREATE INDEX IF NOT EXISTS idx_reference_fts ON reference_docs
    USING GIN (to_tsvector('simple', doc_title || ' ' || section_title || ' ' || text));
