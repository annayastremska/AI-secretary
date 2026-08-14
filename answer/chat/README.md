# Чат — вікно відповіді (тестовий стенд)

Чат, який відповідає на питання про людей і документи частини. Всі дані
вигадані. Дорога Б («нормативка») і дорога А («підрахунок») з `README.md`
кореня репо — тут обидві живуть на SQLite-стенді, поки `db/` не готовий
на Postgres.

## Запуск

```bash
pip install -r answer/chat/requirements.txt
python answer/chat/seed.py   # збирає stand.sqlite з answer/chat/data/
python answer/chat/app.py    # відкриє http://127.0.0.1:7860
```

На Mac можна замість трьох команд двічі клацнути `start-mac.command`.

## Файли

| Файл | Що там |
|---|---|
| `app.py` | вікно чата (Gradio): маршрутизація питання → дорога → відповідь |
| `db.py` | сім функцій стику з базою — реалізація на SQLite для стенду |
| `seed.py` | збирає `stand.sqlite` з `data/` |
| `data/` | синтетичні дані: реєстр людей, документи відсутності, довідники |
| `verify.py` | автоматичний тест: сім функцій `db.py` і крайові випадки |
| `test-questions.md` | питання для ручного тестування, простою мовою |
| `how-it-works.html` | повна інструкція, як усе влаштовано, ~7 хвилин |

Контракт із базою (сім функцій, шість наскрізних правил) —
[`docs/contracts/2026-08-14_chat-db-interface.md`](../../docs/contracts/2026-08-14_chat-db-interface.md).
Підготовка локального Postgres і інструкції для переходу з SQLite —
`db/local-postgres/`, `db/handoff-notes.md`, `db/claude-instructions.md`.

## Перемикання на Postgres

Один рядок у `app.py`:

```python
import db  # noqa: E402
```

міняється на:

```python
import db_postgres as db  # noqa: E402
```

коли `db/db_postgres.py` готовий і проходить `verify.py` (копію з заміненим
імпортом).
