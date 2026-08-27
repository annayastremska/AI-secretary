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

import pytest
from fastapi.testclient import TestClient

#: Пароль, який ставить САМ ТЕСТ -- і саме тому він працює на будь-якій машині.
#:
#: Було: тест ходив із `demo/demo` і мав `if status == 401: return None`, а
#: кожна перевірка починалась із `if css is None: return`. Локально гейт
#: вимкнений, тому все працювало. На сервері `.env` виставляє інший пароль --
#: і всі перевірки цього файла мовчки виходили, а звіт казав «passed».
#: Знеструмленими виявились твердження «зі сторінок немає запиту до Google
#: Fonts» і «стару тему не чіпали», тобто рівно те, що ми обіцяємо замовнику.
#: Взірець виправлення лежав у сусідньому файлі: test_access_levels.py ставить
#: пароль сам. Робимо так само.
TEST_USER, TEST_PASS = "theme-test-user", "theme-test-pass"


@pytest.fixture(autouse=True)
def _own_gate(monkeypatch):
    """Своє оточення на кожен тест: свій пароль, і прибирання після себе.

    `monkeypatch`, а не `os.environ[...] = ...`: попередня версія лишала
    `APP_THEME="нема-такої"` в оточенні після себе, і наступні файли
    працювали з ним. Порядкова залежність за побудовою.
    """
    monkeypatch.setenv("APP_BASIC_USER", TEST_USER)
    monkeypatch.setenv("APP_BASIC_PASS", TEST_PASS)
    monkeypatch.delenv("APP_GUEST_TOKEN", raising=False)
    yield


def _client(theme, monkeypatch):
    monkeypatch.setenv("APP_THEME", theme)
    from demos.upload_app import app as upapp
    importlib.reload(upapp)
    return TestClient(upapp.app), upapp


def _css(theme, monkeypatch):
    """Стилі версії `theme`. НЕ повертає None: якщо не пустили -- це провал,
    а не «не біда». Саме та поблажливість і глушила весь файл."""
    client, upapp = _client(theme, monkeypatch)
    resp = client.get("/static/skin.css", auth=(TEST_USER, TEST_PASS))
    assert resp.status_code == 200, (
        f"гейт не пустив тест до стилів (код {resp.status_code}). Тест мусить "
        f"ставити пароль сам, а не сподіватись на оточення машини")
    assert "text/css" in resp.headers["content-type"]
    return resp.text


def test_both_versions_are_served_and_differ(monkeypatch):
    v1, v2 = _css("v1", monkeypatch), _css("v2", monkeypatch)
    assert len(v1) > 500 and len(v2) > 500
    assert v1 != v2, "обидві версії віддають те саме -- перемикач не працює"


def test_v2_carries_the_new_decisions(monkeypatch):
    css = _css("v2", monkeypatch)
    assert "#4a5d3a" in css.lower(), "немає оливкового акценту"
    assert "prefers-color-scheme: dark" in css, "немає темної теми"
    assert "@font-face" in css and "/static/fonts/plexsans" in css, \
        "шрифт не локальний"
    assert "fonts.googleapis.com" not in css and "fonts.gstatic.com" not in css, \
        "зовнішній запит до Google Fonts -- правило №7 зламане"


def test_v1_is_untouched(monkeypatch):
    css = _css("v1", monkeypatch)
    assert "#4a6fa5" in css.lower(), "стара тема змінилась -- її чіпати не мали"


def test_unknown_theme_falls_back_to_v1(monkeypatch):
    css = _css("нема-такої", monkeypatch)
    assert "#4a6fa5" in css.lower()


def test_v3_is_what_we_actually_show(monkeypatch):
    """V3 -- обличчя, яким ми ПОКАЗУЄМО на демо, і до 27.08 воно не
    перевірялось жодним тестом: тести чіпали v1, v2 і невідому версію.

    Стережемо те, що справді вирішено й що легко зламати випадковою правкою:
    зелений акцент у двох темах, обидві теми, локальний шрифт, нуль зовнішніх
    запитів і нуль радіуса з напряму «технічний сан».
    """
    css = _css("v3", monkeypatch)
    assert len(css) > 5000, \
        "у v3 три файли -- короткий вихід означає, що частина не підклеїлась"
    assert "#46682f" in css.lower(), "немає зеленого акценту світлої теми"
    assert "#7a9e5a" in css.lower(), "немає приглушеного зеленого темної теми"
    assert "prefers-color-scheme: dark" in css, "немає темної теми"
    assert '[data-theme="dark"]' in css, "немає явного перемикача теми"
    assert "@font-face" in css and "/static/fonts/plexsans" in css, \
        "шрифт не локальний"
    assert "fonts.googleapis.com" not in css and "fonts.gstatic.com" not in css, \
        "зовнішній запит до Google Fonts -- правило №7 зламане"
    assert "--r-md: 0" in css, "нуль радіуса напряму B зник"
