# -*- coding: utf-8 -*-
"""Публічний режим: показуємо обробку, але не пускаємо запис у базу.

Нащо. На демо сайт відкривають за посиланням і QR-кодом, тобто кнопку
«підтвердити» бачить будь-хто в залі. Один натиск — і в базі зʼявляється чужий
документ, а цифри на екрані розходяться з тими, які щойно назвали зі сцени.
Це не гіпотеза: 26.08 я зробила це собі сама, перевіряючи крайні випадки, і
три сміттєвих документи в живій базі досі чекають видалення.

Обробка при цьому лишається видимою: файл кладеться, кроки й витягнуті поля
показуються. Блокується рівно останній крок, і відповідь каже, чому.
"""
import importlib
import os

from fastapi.testclient import TestClient


def _client(public):
    os.environ["APP_PUBLIC_MODE"] = "1" if public else "0"
    from demos.upload_app import app as upapp
    importlib.reload(upapp)
    return TestClient(upapp.app), upapp


def test_commit_is_blocked_in_public_mode():
    client, upapp = _client(True)
    assert upapp.PUBLIC_MODE is True
    resp = client.post("/api/jobs/whatever/commit",
                       auth=("demo", os.environ.get("APP_PASSWORD", "demo")))
    # 403 (заблоковано) стоїть ПЕРЕД перевіркою існування задачі: у публічному
    # режимі відповідь не залежить від того, чи вгадав хтось номер задачі
    assert resp.status_code in (401, 403)
    if resp.status_code == 403:
        assert "запис у базу заблокований" in resp.json()["error"]


def test_commit_works_when_public_mode_is_off():
    client, upapp = _client(False)
    assert upapp.PUBLIC_MODE is False
    resp = client.post("/api/jobs/nope/commit",
                       auth=("demo", os.environ.get("APP_PASSWORD", "demo")))
    # без публічного режиму доходимо до звичайної перевірки задачі
    assert resp.status_code in (401, 404)
