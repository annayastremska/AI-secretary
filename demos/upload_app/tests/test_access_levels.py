# -*- coding: utf-8 -*-
"""Два рівні доступу: гість за посиланням із QR, оператор за паролем.

Чому тут тести, а не «подивились очима». Рівень доступу — єдине, що відділяє
«будь-хто з посиланням» від «той, хто може писати в спільну базу». Помилка тут
не виглядає як помилка: сторінка відкривається, чат відповідає, і те, що гість
раптом може підтверджувати факти, видно лише коли вже підтвердив.

Перевіряємо чотири твердження, і кожне ламається окремо:
  1. без нічого — не пускає;
  2. ключ у посиланні — пускає, але як ГОСТЯ;
  3. гість НЕ може записати в базу;
  4. пароль сильніший за ключ: оператор лишається оператором.
"""
import importlib

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-guest-token-0123456789"
USER, PASSWORD = "demo", "test-operator-pass"


@pytest.fixture()
def app_mod(monkeypatch):
    """Апка з УВІМКНЕНИМ гейтом. Гейт читає оточення на імпорті, тому модуль
    перезавантажується: інакше тест міряв би конфігурацію, якої немає в проді
    (локально гейт вимкнений, і все дозволено)."""
    monkeypatch.setenv("APP_BASIC_USER", USER)
    monkeypatch.setenv("APP_BASIC_PASS", PASSWORD)
    monkeypatch.setenv("APP_GUEST_TOKEN", TOKEN)
    from demos.upload_app import app as mod
    importlib.reload(mod)
    yield mod
    # Повертаємо модуль у стан без гейта, щоб не зачепити інші тести.
    monkeypatch.delenv("APP_BASIC_USER", raising=False)
    monkeypatch.delenv("APP_BASIC_PASS", raising=False)
    monkeypatch.delenv("APP_GUEST_TOKEN", raising=False)
    importlib.reload(mod)


def test_no_credentials_no_entry(app_mod):
    with TestClient(app_mod.app) as client:
        assert client.get("/stats").status_code == 401


def test_link_key_lets_in_as_guest(app_mod):
    with TestClient(app_mod.app) as client:
        resp = client.get("/stats", params={"k": TOKEN})
        assert resp.status_code == 200
        # Ключ переїхав у cookie: далі він не потрібен в адресі й не поїде в
        # скопійованому посиланні.
        assert app_mod.ACCESS_COOKIE in resp.cookies or \
            client.cookies.get(app_mod.ACCESS_COOKIE) == TOKEN
        # І наступна сторінка вже без ключа в адресі.
        assert client.get("/stats").status_code == 200


def test_wrong_key_is_not_a_key(app_mod):
    with TestClient(app_mod.app) as client:
        assert client.get("/stats", params={"k": "не той ключ"}).status_code \
            == 401


def test_guest_cannot_write_to_the_database(app_mod):
    """Головне твердження цієї зміни. Гість бачить обробку, але факт у
    спільну базу не потрапляє — і відповідь це КАЖЕ, а не глушить."""
    with TestClient(app_mod.app) as client:
        client.get("/stats", params={"k": TOKEN})          # стали гостем
        resp = client.post("/api/jobs/whatever/commit")
        assert resp.status_code == 403
        text = resp.json()["error"]
        assert "оператор" in text
        # Чесність формулювання: поля справді витягнуті, і про це сказано.
        assert "по-справжньому" in text


def test_password_beats_the_link(app_mod):
    """Пароль сильніший за ключ: людина з паролем лишається оператором навіть
    із гостьовою cookie від попереднього заходу за QR."""
    with TestClient(app_mod.app) as client:
        client.get("/stats", params={"k": TOKEN})          # спершу гість
        resp = client.post("/api/jobs/nonexistent/commit",
                           auth=(USER, PASSWORD))
        # 404 означає «пустили як оператора, але такої задачі немає» -- саме
        # це нам і потрібно: не 403.
        assert resp.status_code == 404, resp.text


def test_guest_entry_is_off_when_not_configured(monkeypatch):
    """Без ключа в оточенні гостьового входу немає зовсім: ні cookie, ні
    маршруту QR. Демо-механізм не має вмикатися сам."""
    monkeypatch.setenv("APP_BASIC_USER", USER)
    monkeypatch.setenv("APP_BASIC_PASS", PASSWORD)
    monkeypatch.delenv("APP_GUEST_TOKEN", raising=False)
    from demos.upload_app import app as mod
    importlib.reload(mod)
    try:
        with TestClient(mod.app) as client:
            assert client.get("/stats", params={"k": TOKEN}).status_code == 401
            assert client.get("/static/qr-guest.png",
                              auth=(USER, PASSWORD)).status_code == 404
    finally:
        monkeypatch.delenv("APP_BASIC_USER", raising=False)
        monkeypatch.delenv("APP_BASIC_PASS", raising=False)
        importlib.reload(mod)
