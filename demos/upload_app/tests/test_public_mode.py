# -*- coding: utf-8 -*-
"""Публічний режим: показуємо обробку, але не пускаємо запис у базу.

Нащо. На демо сайт відкривають за посиланням і QR-кодом, тобто кнопку
«підтвердити» бачить будь-хто в залі. Один натиск — і в базі зʼявляється чужий
документ, а цифри на екрані розходяться з тими, які щойно назвали зі сцени.
Це не гіпотеза: 26.08 я зробила це собі сама, перевіряючи крайні випадки, і
три сміттєвих документи в живій базі довелось видаляти Андрію.

Обробка при цьому лишається видимою: файл кладеться, кроки й витягнуті поля
показуються. Блокується рівно останній крок, і відповідь каже, чому.

## Чому цей файл переписаний 27.08

Аудит чесності тестів показав, що він **на сервері не перевіряв нічого й
казав «passed»**. Дві причини, і обидві варті записи:

1. пароль брався з оточення (`APP_PASSWORD`, якого в нас навіть немає — змінна
   зветься `APP_BASIC_PASS`), із запасним `demo`. Локально гейт вимкнений,
   тому запит проходив. На сервері пароль інший → **401**;
2. а перевірка казала `assert resp.status_code in (401, 403)`, тобто **«нас не
   пустили» вважалось успіхом**. Твердження файла — «один натиск і в базі
   чужий документ» — не стереглось нічим.

Тепер тест **ставить пароль сам** (взірець узятий із `test_access_levels.py`)
і чекає РІВНО той код, який мусить бути. Жодних «або 401, або 403»: два різні
результати не можуть обидва бути правильною відповіддю на одне питання.
"""
import importlib

import pytest
from fastapi.testclient import TestClient

#: Пароль ставить сам тест — і саме тому файл працює на будь-якій машині.
TEST_USER, TEST_PASS = "public-mode-user", "public-mode-pass"


@pytest.fixture(autouse=True)
def _own_gate(monkeypatch):
    """Своє оточення, і прибирання після себе.

    `monkeypatch`, а не `os.environ[...] = ...`: попередня версія лишала
    `APP_PUBLIC_MODE` в оточенні після себе, і наступні файли працювали з ним.
    """
    monkeypatch.setenv("APP_BASIC_USER", TEST_USER)
    monkeypatch.setenv("APP_BASIC_PASS", TEST_PASS)
    monkeypatch.delenv("APP_GUEST_TOKEN", raising=False)
    yield


def _client(public, monkeypatch):
    monkeypatch.setenv("APP_PUBLIC_MODE", "1" if public else "0")
    from demos.upload_app import app as upapp
    importlib.reload(upapp)
    return TestClient(upapp.app), upapp


def test_commit_is_blocked_in_public_mode(monkeypatch):
    client, upapp = _client(True, monkeypatch)
    assert upapp.PUBLIC_MODE is True
    resp = client.post("/api/jobs/whatever/commit", auth=(TEST_USER, TEST_PASS))
    # РІВНО 403, а не «403 або 401». 403 стоїть ПЕРЕД перевіркою існування
    # задачі навмисно: у публічному режимі відповідь не залежить від того, чи
    # вгадав хтось номер задачі.
    assert resp.status_code == 403, resp.text
    assert "заблокован" in resp.json()["error"], resp.json()
    # І відповідь мусить бути ЧЕСНОЮ: сказати, що поля витягнуті справді, а
    # не вдавати збій. Інакше людина вирішить, що система не працює.
    assert "по-справжньому" in resp.json()["error"]


def test_commit_works_when_public_mode_is_off(monkeypatch):
    client, upapp = _client(False, monkeypatch)
    assert upapp.PUBLIC_MODE is False
    resp = client.post("/api/jobs/nope/commit", auth=(TEST_USER, TEST_PASS))
    # РІВНО 404: пустили, дійшли до звичайної перевірки задачі, задачі немає.
    # 401 тут означав би, що тест не дійшов до перевірки взагалі.
    assert resp.status_code == 404, resp.text


def test_wrong_password_is_401_not_403(monkeypatch):
    """Окремо: щоб «не пустили» ніколи більше не читалось як «заблокували».

    Саме змішування цих двох і зробило файл беззубим: 401 означає «ми не
    впізнали того, хто прийшов», 403 — «впізнали й не дозволили». Це різні
    твердження, і тест мусить їх розрізняти.
    """
    client, _upapp = _client(True, monkeypatch)
    resp = client.post("/api/jobs/whatever/commit", auth=("хтось", "не той"))
    assert resp.status_code == 401, resp.text
