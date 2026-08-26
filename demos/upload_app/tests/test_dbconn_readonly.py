# -*- coding: utf-8 -*-
"""Рядок підключення для читання мусить мати три запобіжники — завжди.

Знайдено блоком 6 перевірки (26.08), і це була МОЯ регресія з блоку 8. Перша
версія `dbconn.dsn()` віддавала `DATABASE_URL` як є, якщо змінна виставлена. А
вона виставлена завжди: служба апки читає `.env`, де стоїть URL користувача,
який ПИШЕ. Разом із URL зникали `statement_timeout`,
`default_transaction_read_only` і `connect_timeout`.

Заміряно на живому сервері: важкий запит із яруса вільного SQL виконувався
171.9 секунди замість обіцяних 5. Тобто правка «прибрати дублювання» знесла три
запобіжники й перевела чат на пишучого користувача.

Тест сторожить рівно це.
"""
import pytest

from demos.upload_app import dbconn

ENV = {"POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5433",
       "APP_DB_NAME": "milidoc", "READONLY_DB_USER": "milidoc_readonly",
       "READONLY_DB_PASSWORD": "secret"}


def test_three_guards_are_always_there():
    got = dbconn.dsn(env=ENV)
    assert "default_transaction_read_only=on" in got
    assert "statement_timeout=5000" in got
    assert f"connect_timeout={dbconn.CONNECT_TIMEOUT_S}" in got
    assert "user=milidoc_readonly" in got


def test_database_url_never_replaces_the_dsn(monkeypatch):
    """URL пишучого користувача не має підмінити рядок підключення."""
    monkeypatch.setenv("DATABASE_URL",
                       "postgresql+psycopg://milidoc_app:pw@localhost:5433/milidoc")
    got = dbconn.dsn(env=ENV)
    assert "milidoc_app" not in got, "узяли пишучого користувача з URL"
    assert "user=milidoc_readonly" in got
    assert "default_transaction_read_only=on" in got
    assert "statement_timeout=5000" in got


def test_url_gives_only_the_address(monkeypatch):
    """З URL можна взяти хост, порт і назву бази -- більше нічого."""
    monkeypatch.setenv("DATABASE_URL",
                       "postgresql+psycopg://milidoc_app:pw@db.example:6000/other")
    env = {k: v for k, v in ENV.items() if k.startswith("READONLY")}
    got = dbconn.dsn(env=env)
    assert "host=db.example" in got and "port=6000" in got
    assert "dbname=other" in got
    assert "milidoc_app" not in got


def test_missing_readonly_password_is_an_error_not_a_fallback(monkeypatch):
    """Тихий перехід на пишучого користувача -- саме те, що зламалось 26.08."""
    monkeypatch.setenv("DATABASE_URL",
                       "postgresql+psycopg://milidoc_app:pw@localhost:5433/milidoc")
    with pytest.raises(dbconn.ReadOnlyCredentialsMissing):
        dbconn.dsn(env={"READONLY_DB_USER": "milidoc_readonly",
                        "READONLY_DB_PASSWORD": ""})


def test_tools_get_a_longer_statement_timeout():
    got = dbconn.dsn(dbconn.STATEMENT_TIMEOUT_MS_TOOLS, env=ENV)
    assert "statement_timeout=15000" in got
    assert "default_transaction_read_only=on" in got
