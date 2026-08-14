# Інструкція для Андрія: що вставити своєму Клоду

Скопіюй усе під лінією нижче (від «Контекст» до кінця файлу) і встав як
перше повідомлення своєму Claude Code в корені цього репозиторію
(`docflow-expertise`). Клод сам прочитає файли, з якими звіряється, —
тобі нічого пояснювати додатково.

---

## Контекст

Проєкт: `docflow-expertise`, капстоун №21, KSE Agentic AI Summer School
2026. Частина проєкту — чат, який відповідає на питання про людей і
документи частини (усі дані вигадані). Зараз чат ходить у SQLite
(`answer/chat/db.py`). Моя задача — написати `db/db_postgres.py`: ту саму
функціональність на Postgres, щоб потім переключити один рядок імпорту в
`answer/chat/app.py`.

Прочитай спочатку три файли в цьому репо, у такому порядку:

1. `docs/contracts/2026-08-14_chat-db-interface.md` — контракт: схема
   трьох таблиць, сім функцій стику, шість наскрізних правил.
2. `answer/chat/db.py` — та сама логіка вже написана на SQLite. Це джерело
   правди по SQL: перенось запити на Postgres майже дослівно (плейсхолдери
   `?` → `%s`, `LEFT JOIN` лишається таким самим, умови на порожні дати
   `!= ''` теж).
3. `db/handoff-notes.md` — що вже підготовлено з мого боку.

Зверни увагу і на `docs/contracts/2026-08-11_database-handoff.md` — це
інший стик (пайплайн Ані ↔ база), не плутай з нашим. Наш — тільки про сім
функцій чату.

## Що вже готово, чіпати не треба

Схема й дані підготовлені і вже один раз перевірені локально (потім
контейнер зупинили, у тебе він не запущений) — у папці `db/local-postgres/`:

- `schema.sql` — DDL, три таблиці (`people`, `absences`, `reference_docs`),
  усі колонки `TEXT`, індекси під усі сім функцій.
- `docker-compose.yml` — `postgres:16-alpine`, порт `5432`, база
  `chat_stand`, користувач `chat_stand`, пароль `chat_stand_local`.
- `load_data.py` — вантажить `answer/chat/data/people.csv`,
  `answer/chat/data/absences.csv`, `answer/chat/data/reference/*.md` у цю
  базу (той самий алгоритм, що й `answer/chat/seed.py` для SQLite).

Підніми базу сам, першим кроком:

```bash
cd db/local-postgres
docker compose up -d
pip install -r requirements.txt
python3 load_data.py
```

Перевір, що вивелось рівно 300 people, 31 absences, 17 reference_docs —
так само, як у `answer/chat/stand.sqlite` (цей файл у git не потрапляє,
він похідний — відтворюється `answer/chat/seed.py`). Якщо цифри інші —
спершу розберись чому, до `db_postgres.py` не переходь.

DSN за замовчуванням: `postgresql://chat_stand:chat_stand_local@localhost:5432/chat_stand`
(можна переозначити через `DATABASE_URL`). Якщо в мене вже є своя локальна
Postgres — підключайся до неї, схема та ж сама, просто прожени `schema.sql`
і `load_data.py` проти неї.

## Завдання

Створи `db/db_postgres.py` із рівно сімома функціями, тими самими назвами,
аргументами й ключами словників, що й у `answer/chat/db.py`:

```python
find_people(subdivision=None, name=None) -> list[dict]
absences_on_date(date, subdivision=None, doc_type=None) -> list[dict]
returning_on_date(date, subdivision=None) -> list[dict]
absences_for_person(name_or_service_id, only_active=True) -> list[dict]
document_by_number(doc_number) -> list[dict]
count_absent_by_subdivision(date) -> list[dict]
search_reference(query, limit=3) -> list[dict]
```

Шість правил з контракту, які має тримати цей файл (деталі й обґрунтування
— в `docs/contracts/2026-08-14_chat-db-interface.md`):

1. Функція повертає дані (list[dict]), не текст для людини.
2. Нічого не знайшли → `[]`. Не виняток, не `None`.
3. Дати — рядки `YYYY-MM-DD`, на вході і на виході.
4. Кожен рядок про документ несе джерело (`doc_number`+`source_file` або
   `doc_title`+`section_number`). Виняток — підсумкові рядки й рядки
   реєстру `people`.
5. Порожнє поле лишається порожнім — нічого не підставляти замість
   незаповненої дати чи звання.
6. Коли документів з одним номером два — віддавати обидва, вибір чинного
   лишається за чатом.

Для `search_reference`: на стенді це простий пошук за словами, у мене вже
є GIN-індекс під `to_tsvector('simple', ...)` у `schema.sql` — онови
реалізацію на `to_tsquery`/`ts_rank`, якщо є час; якщо ні, можна почати з
портованого 1:1 підходу з `db.py` (порахувати збіги слів), і взяти FTS
окремим кроком пізніше. Головне — не зламати `score` та сортування за ним.

Використовуй `psycopg2` (він уже в `db/local-postgres/requirements.txt`)
або `psycopg` 3 — на твій розсуд, аби контракт-функції давали ідентичний
результат.

## Як перевірити, що готово

Не здавай на очі — прожени автоматичний тест:

1. Скопіюй `answer/chat/verify.py` → `db/verify_postgres.py`.
2. У копії зміни рядок `import db` на `import db_postgres as db`.
3. Запусти: `python3 db/verify_postgres.py`.
4. Усі перевірки мають пройти. Якщо щось падає — виправляй
   `db/db_postgres.py`, не сам тест (тест написаний проти контракту, не
   проти реалізації).

Коли тест зелений — постав мені одне повідомлення: «db_postgres.py готовий,
verify_postgres.py проходить». Заміну рядка імпорту в `answer/chat/app.py`
(`import db` → `import db_postgres as db`) зроблю я сам.

## Чого НЕ робити

- Не міняй назви функцій, аргументів чи ключів словників — на них
  зав'язаний увесь `answer/chat/app.py`.
- Не чіпай `answer/chat/app.py`, `answer/chat/db.py`, `answer/chat/seed.py`
  — вони мої.
- Не підставляй значення замість порожніх полів і не «виправляй» зіпсовані
  документи (порожні дати, `date_to` раніше `date_from`) — це навмисно,
  чат сам має це помітити і назвати.
- Код і назви файлів — англійською (правило репо, `CLAUDE.md`); коментарі
  й документація — українською.
