# db — сховище

Власник: Андрій.

Два сховища, різні задачі:

| Що | Чим | Що зберігає |
|---|---|---|
| Об'єкти | MinIO | самі файли документів (скани, фото) |
| Факти | Postgres | витягнуті поля у структурованому вигляді |

## Що тут лежить

- `migrations/` — 11 Alembic-міграцій: реєстр документів/об'єктів/фактів,
  функції `resolve_or_create_object`/`next_document_for_processing`,
  `dimensions.validity_model` (ranged / current_state / permanent_event),
  Ukrainian FTS (Hunspell), `unit` у `object_kinds`, `new_person` у
  `review_queue.queue_type`.
- `init/` — bootstrap користувачів/бази при першому старті контейнера.
- `seeds/` — генератор синтетичного реєстру особового складу.
- `tsearch_data/` — Hunspell-словник української (`brown-uk/dict_uk` через
  `wooorm/dictionaries`, GPLv3 — на радар юриста) для FTS.
- `scripts/load_ai_secretary_output.py` — CLI-завантажувач виводу пайплайна
  Ані (`data/output/documents/**/*.md`) у цю БД.
- `scripts/start-docker-safe.ps1` — обхід відомого бага Docker Desktop на
  Windows (docker/desktop-feedback#460): стартує стек, автоматично чистить
  застряглі AF_UNIX socket-файли після неохайного вимкнення.

Запуск локально: `cp .env.example .env` (заповнити значення) →
`docker compose up -d` → `alembic upgrade head`.

Логіка мапінгу виводу Ані на цю схему — `airflow/plugins/ai_secretary_loader.py`
(той самий код, що й у `airflow/dags/load_ai_secretary_output_dag.py`, щоб не
дублювати між CLI і Airflow). Контракт із пайплайном — `docs/contracts/2026-08-11_database-handoff.md`.

## Для команди відповіді (чат/UI)

[`README_for_chatbot_team.md`](README_for_chatbot_team.md) — огляд схеми
під запити (не під мапінг виводу пайплайна): три шари таблиць, чому
`facts.status='confirmed'` обов'язково фільтрувати, FTS-приклад, read-only
доступ.

## Дампи в git не їдуть

Схема потрібна в репо, дані — ні. Дамп може містити витягнуті реальні дані,
тому `dumps/`, `*.dump`, `backup*.sql` заблоковані в `.gitignore`.

## Чернетки видно окремо

База мусить розрізняти підтверджений факт і чернетку. Відповідь користувачу
показує кількість непідтверджених — тобто це не службова позначка, а частина
результату.
