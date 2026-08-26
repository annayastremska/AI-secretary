# -*- coding: utf-8 -*-
"""Одне місце, де складається рядок підключення до бази.

Нащо цей файл існує. Блок 8 перевірки (26.08) знайшов **три копії** `_read_env`
і `_dsn` -- у `chat_gradio/db.py`, `chat_gradio/tiers.py` і `verify_catalog.py`.
Копії вже розійшлися:

* у `verify_catalog.py` **не було `connect_timeout`** -- тобто при впалій базі
  прилад звірки мовчав би ~4 хвилини замість швидкої помилки (рівно та поломка,
  яку ми виправили в чаті 25.08, і виправили лише в двох копіях із трьох);
* лише `verify_catalog.py` знав про змінну `APP_DATABASE_URL`;
* `statement_timeout` різнився (5 с у чаті, 15 с у приладі) -- це якраз
  правильно, тому лишається параметром, а не розбіжністю.

Правило, яке з цього виходить: **умови підключення -- це властивість продукту
(читання, таймаути, режим), а не деталь модуля.** Тому вони тут, в одному
місці, а різницю між викликами видно параметром.

Читання -- жорстко: `default_transaction_read_only=on` у самому DSN, поверх
readonly-користувача. База -- зона Андрія, і апка в неї не пише (CLAUDE.md).
"""
import os

#: Корінь репозиторію: `demos/upload_app/` -> два рівні вгору.
PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

#: Чому 3 секунди. Без `connect_timeout` psycopg перебирає ::1 і 127.0.0.1 без
#: обмеження, і при впалій базі відповідь не приходить ~4 хвилини. Наше правило
#: вимагає РІЗНИХ текстів для «не знайшла» і «база недоступна», а текст, якого
#: немає чотири хвилини, не є ні тим, ні тим (заміряно 25.08).
CONNECT_TIMEOUT_S = 3

#: Скільком часу дозволено запиту. У чаті коротко (людина чекає), у приладах
#: довше (там важкі звірки по всьому корпусу).
STATEMENT_TIMEOUT_MS_CHAT = 5000
STATEMENT_TIMEOUT_MS_TOOLS = 15000


def read_env(path=None):
    """-> словник із `.env` у корені репозиторію (порожній, якщо файла немає).

    Свідомо без залежності на `python-dotenv`: апка й прилади мусять читати
    один і той самий файл однаково, навіть якщо запущені різними інтерпретаторами
    (venv апки, venv-ml приладів)."""
    values = {}
    path = path or os.path.join(PROJECT_ROOT, ".env")
    if not os.path.exists(path):
        return values
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    return values


def dsn(statement_timeout_ms=STATEMENT_TIMEOUT_MS_CHAT):
    """-> рядок підключення ДЛЯ ЧИТАННЯ.

    `APP_DATABASE_URL` (або `DATABASE_URL`) має пріоритет: так прилади ходять у
    базу на сервері без `.env` під рукою. SQLAlchemy-схему `postgresql+psycopg://`
    зводимо до тієї, яку розуміє psycopg."""
    url = os.environ.get("APP_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if url:
        return url.replace("postgresql+psycopg://", "postgresql://")
    env = read_env()
    return (f"host={env.get('POSTGRES_HOST', 'localhost')} "
            f"port={env.get('POSTGRES_PORT', '5433')} "
            f"dbname={env.get('APP_DB_NAME', 'milidoc')} "
            f"user={env.get('READONLY_DB_USER', 'milidoc_readonly')} "
            f"password={env.get('READONLY_DB_PASSWORD', '')} "
            f"connect_timeout={CONNECT_TIMEOUT_S} "
            f"options='-c default_transaction_read_only=on "
            f"-c statement_timeout={statement_timeout_ms}'")
