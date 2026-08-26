# -*- coding: utf-8 -*-
"""Збій бази не має НАЗАВЖДИ прибирати межі покриття з відповідей.

Знайдено блоком 8 перевірки (26.08). `data_coverage()` кешується на процес --
це правильно, бо покриття це властивість набору даних. Але кешувався і НЕУСПІХ:
один збій бази на старті процесу означав `(None, None)` до перезапуску апки,
тобто нульові відповіді назавжди втрачали рядок «за цю дату даних немає». А це
рівно те попередження, яке відрізняє «нікого не було» від «ми не знаємо».
"""
import datetime

import psycopg

from demos.upload_app.chat_gradio import db


def test_failure_is_not_cached(monkeypatch):
    calls = {"n": 0}

    def flaky(sql, params=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise psycopg.OperationalError("база впала")
        return [{"d_from": datetime.date(2026, 6, 2),
                 "d_to": datetime.date(2026, 10, 10)}]

    monkeypatch.setattr(db, "_COVERAGE", None)
    monkeypatch.setattr(db, "_query", flaky)
    assert db.data_coverage() == (None, None)      # перший раз -- збій
    assert db.data_coverage()[0] == datetime.date(2026, 6, 2)   # другий -- дані
    assert calls["n"] == 2, "після збою запит мусить повторитись"


def test_success_is_cached(monkeypatch):
    calls = {"n": 0}

    def once(sql, params=None):
        calls["n"] += 1
        return [{"d_from": datetime.date(2026, 6, 2),
                 "d_to": datetime.date(2026, 10, 10)}]

    monkeypatch.setattr(db, "_COVERAGE", None)
    monkeypatch.setattr(db, "_query", once)
    db.data_coverage()
    db.data_coverage()
    assert calls["n"] == 1, "успіх кешується -- це властивість набору даних"
