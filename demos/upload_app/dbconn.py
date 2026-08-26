# -*- coding: utf-8 -*-
"""Одне місце, де складається рядок підключення до бази — і воно ЧИТАЄ.

Нащо цей файл існує. Блок 8 перевірки (26.08) знайшов три копії `_read_env` і
`_dsn` — у `chat_gradio/db.py`, `chat_gradio/tiers.py` і `verify_catalog.py`.
Копії вже розійшлися: у приладі звірки не було `connect_timeout`, тобто при
впалій базі він мовчав би ~4 хвилини.

І одразу — головна пастка, на якій я обпеклась у тій самій правці. Перша версія
цього модуля віддавала `DATABASE_URL` як є, якщо змінна виставлена. А вона
виставлена завжди: служба апки читає `.env`, де стоїть URL **користувача, який
ПИШЕ** (`milidoc_app`). Наслідок, заміряний блоком 6: важкий запит із яруса
вільного SQL виконувався **171.9 с** замість обіцяних 5, бо разом із URL
зникали і `statement_timeout`, і `default_transaction_read_only`, і
`connect_timeout`. Тобто моя ж правка «прибрати дублювання» тихо знесла три
запобіжники й перевела чат на пишучого користувача.

Правило, яке з цього виходить і яке тримає цей файл:

**рядок підключення для читання НІКОЛИ не збирається з готового URL.** Він
завжди складається з частин: readonly-користувач, `connect_timeout`,
`default_transaction_read_only=on`, `statement_timeout`. Немає пароля
readonly-користувача — це помилка з поясненням, а не тихий перехід на того, хто
має право писати.

Різниця між чатом і приладами лишається одним параметром — довжиною
`statement_timeout`, і вона усвідомлена: людина в чаті чекає, прилад звірки
рахує по всьому корпусу.
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
#: довше (важкі звірки по всьому корпусу).
STATEMENT_TIMEOUT_MS_CHAT = 5000
STATEMENT_TIMEOUT_MS_TOOLS = 15000


class ReadOnlyCredentialsMissing(RuntimeError):
    """Немає даних readonly-користувача. Свідомо помилка, а не фолбек.

    Фолбеком тут був би пишучий користувач із `.env` -- саме те, що зламало
    read-only 26.08. Краще гучна помилка на старті, ніж чат, який має право
    писати в чужу базу."""


def read_env(path=None):
    """-> словник із `.env` у корені репозиторію (порожній, якщо файла немає).

    Свідомо без залежності на `python-dotenv`: апка й прилади мусять читати
    один і той самий файл однаково, навіть якщо запущені різними
    інтерпретаторами (venv апки, venv-ml приладів)."""
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


def _parts_from_url(url):
    """Розібрати URL на частини -- потрібне лише щоб дістати хост і порт.

    Користувача з URL НЕ беремо: у `.env` там пишучий користувач."""
    from urllib.parse import urlparse
    parsed = urlparse(url.replace("postgresql+psycopg://", "postgresql://"))
    return {"POSTGRES_HOST": parsed.hostname or "localhost",
            "POSTGRES_PORT": str(parsed.port or 5432),
            "APP_DB_NAME": (parsed.path or "/milidoc").lstrip("/")}


def dsn(statement_timeout_ms=STATEMENT_TIMEOUT_MS_CHAT, env=None):
    """-> рядок підключення ДЛЯ ЧИТАННЯ, завжди з трьома запобіжниками."""
    env = dict(env or read_env())
    # З URL беремо лише адресу бази (хост, порт, назву) -- і лише якщо їх
    # немає в оточенні окремо. Користувача й пароль -- ніколи.
    url = os.environ.get("APP_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if url:
        for key, value in _parts_from_url(url).items():
            env.setdefault(key, value)
    for key in ("POSTGRES_HOST", "POSTGRES_PORT", "APP_DB_NAME",
                "READONLY_DB_USER", "READONLY_DB_PASSWORD"):
        if os.environ.get(key):
            env[key] = os.environ[key]

    user = env.get("READONLY_DB_USER", "milidoc_readonly")
    password = env.get("READONLY_DB_PASSWORD", "")
    if not password:
        raise ReadOnlyCredentialsMissing(
            "немає READONLY_DB_PASSWORD (шукав у .env і в оточенні). "
            "Підключатись пишучим користувачем із DATABASE_URL не буду: "
            "апка й чат працюють із базою лише на читання")
    return (f"host={env.get('POSTGRES_HOST', 'localhost')} "
            f"port={env.get('POSTGRES_PORT', '5433')} "
            f"dbname={env.get('APP_DB_NAME', 'milidoc')} "
            f"user={user} password={password} "
            f"connect_timeout={CONNECT_TIMEOUT_S} "
            f"options='-c default_transaction_read_only=on "
            f"-c statement_timeout={statement_timeout_ms}'")
