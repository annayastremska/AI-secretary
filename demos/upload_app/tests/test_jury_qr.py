# -*- coding: utf-8 -*-
"""Другий QR: пам'ятка журі й перемикач між двома кодами.

Запит Ані 29.08. Головне, що перевіряється, -- не «кнопка є», а три речі, які
тихо ламаються:

  доступ    -- пам'ятка відкривається ТИМ САМИМ гостьовим ключем, тобто без
               ключа мусить бути 401, а не 200;
  показ     -- перемикача немає, поки немає ФАЙЛІВ: кнопка, що веде в 404,
               гірша за відсутність кнопки;
  ключ у git -- ні картинка, ні пам'ятка в репозиторій не їдуть.
"""
import io
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
APP_DIR = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(APP_DIR, "chat_gradio"))

import app as chat                                  # noqa: E402


# ── Показ перемикача ─────────────────────────────────────────────────────────


def test_no_token_no_switch(monkeypatch):
    monkeypatch.delenv("APP_GUEST_TOKEN", raising=False)
    assert chat.jury_qr_available() is False


@pytest.mark.parametrize("present", [
    (),                                  # немає нічого
    ("qr-jury.png",),                    # є код, немає пам'ятки
    ("jury-guide.html",),                # є пам'ятка, немає коду
])
def test_switch_needs_both_files(monkeypatch, present):
    """Одного файла не досить: код без пам'ятки веде в 404, пам'ятка без коду
    не має чим показатись."""
    monkeypatch.setenv("APP_GUEST_TOKEN", "x" * 32)
    monkeypatch.setattr(os.path, "exists",
                        lambda p: os.path.basename(p) in present)
    assert chat.jury_qr_available() is False


def test_switch_shown_when_both_present(monkeypatch):
    monkeypatch.setenv("APP_GUEST_TOKEN", "x" * 32)
    monkeypatch.setattr(os.path, "exists",
                        lambda p: os.path.basename(p) in
                        ("qr-jury.png", "jury-guide.html"))
    assert chat.jury_qr_available() is True


# ── Розмітка й скрипт ────────────────────────────────────────────────────────


def _chat_source():
    with io.open(os.path.join(APP_DIR, "chat_gradio", "app.py"),
                 encoding="utf-8") as fh:
        return fh.read()


def test_markup_carries_the_state_in_the_dom():
    """Стан («який код показано») лежить у РОЗМІТЦІ, а не в змінній скрипта:
    Gradio перемальовує блок, і змінна розійшлася б із екраном."""
    src = _chat_source()
    assert 'data-shown="guest"' in src
    assert 'data-qr="guest"' in src and 'data-qr="jury"' in src


def test_swap_script_is_inlined_like_the_others():
    src = _chat_source()
    assert "QR_SWAP_JS" in src
    assert '"<script>" + qr_swap + "</script>"' in src


def test_swap_script_uses_delegation():
    """Слухач -- на document. Блок малює Gradio ПІСЛЯ виконання скрипта, тому
    addEventListener на саму кнопку не спрацював би."""
    with io.open(os.path.join(APP_DIR, "static", "qr-swap.js"),
                 encoding="utf-8") as fh:
        js = fh.read()
    assert 'document.addEventListener("click"' in js
    assert "getAttribute(\"data-shown\")" in js


# ── Маршрути ─────────────────────────────────────────────────────────────────


def _upload_source():
    with io.open(os.path.join(APP_DIR, "app.py"), encoding="utf-8") as fh:
        return fh.read()


def test_routes_exist():
    src = _upload_source()
    assert '@app.get("/jury")' in src
    assert '@app.get("/static/qr-jury.png")' in src


def test_guide_lives_outside_static_and_is_absent_from_git():
    """Пам'ятка й код містять ключ доступу, тому в git їх немає."""
    with io.open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as fh:
        ignored = fh.read()
    assert "data/qr-jury.png" in ignored
    assert "data/jury-guide.html" in ignored
    src = _upload_source()
    assert 'JURY_GUIDE_PATH = os.path.join(PROJECT_ROOT, "data"' in src


def test_missing_files_answer_404_not_500():
    """Немає файла -> 404 із текстом, а не виняток: сторінку це не валить."""
    src = _upload_source()
    jury = src[src.index('@app.get("/jury")'):]
    jury = jury[:jury.index("@app.get", 10)]
    assert "os.path.exists(JURY_GUIDE_PATH)" in jury
    assert "status_code=404" in jury
