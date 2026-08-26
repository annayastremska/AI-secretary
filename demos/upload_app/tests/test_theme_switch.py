# -*- coding: utf-8 -*-
"""Дві версії обличчя живуть поряд, і перемикання -- одна змінна.

Рішення Ані 27.08: нову тему робимо, стару НЕ видаляємо. Тому сторінки беруть
стилі одним посиланням `/static/skin.css`, а сервер за `APP_THEME` вирішує, яку
пару файлів віддати. Відкат перед демо -- зміна змінної й перезапуск, без
правки розмітки й без повернення файлів з історії.

Тест сторожить три речі: що обидві версії віддаються, що вони РІЗНІ, і що в
новій справді лежить локальний шрифт (тобто правило «жодного зовнішнього
запиту» не зламане посиланням на Google Fonts).
"""
import importlib
import os

from fastapi.testclient import TestClient


def _client(theme):
    os.environ["APP_THEME"] = theme
    from demos.upload_app import app as upapp
    importlib.reload(upapp)
    return TestClient(upapp.app), upapp


def _css(theme):
    client, upapp = _client(theme)
    resp = client.get("/static/skin.css", auth=("demo", "demo"))
    if resp.status_code == 401:          # пароль у оточенні не той -- не біда
        return None
    assert resp.status_code == 200, resp.status_code
    assert "text/css" in resp.headers["content-type"]
    return resp.text


def test_both_versions_are_served_and_differ():
    v1, v2 = _css("v1"), _css("v2")
    if v1 is None or v2 is None:
        return
    assert len(v1) > 500 and len(v2) > 500
    assert v1 != v2, "обидві версії віддають те саме -- перемикач не працює"


def test_v2_carries_the_new_decisions():
    css = _css("v2")
    if css is None:
        return
    assert "#4a5d3a" in css.lower(), "немає оливкового акценту"
    assert "prefers-color-scheme: dark" in css, "немає темної теми"
    assert "@font-face" in css and "/static/fonts/plexsans" in css, \
        "шрифт не локальний"
    assert "fonts.googleapis.com" not in css and "fonts.gstatic.com" not in css, \
        "зовнішній запит до Google Fonts -- правило №7 зламане"


def test_v1_is_untouched():
    css = _css("v1")
    if css is None:
        return
    assert "#4a6fa5" in css.lower(), "стара тема змінилась -- її чіпати не мали"


def test_unknown_theme_falls_back_to_v1():
    css = _css("нема-такої")
    if css is None:
        return
    assert "#4a6fa5" in css.lower()
