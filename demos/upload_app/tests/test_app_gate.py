"""Одна апка, дві сторінки, один гейт (задача 1.3) + збірка (критерії етапу).

Перевіряється без бази й без моделі: імпорт модулів, побудова Gradio Blocks,
і те, що Basic-auth middleware закриває ОБИДВІ сторінки — і /, і змонтований
Gradio-чат під /chat (він обслуговується тією самою FastAPI, тож HTTP-
middleware обгортає і його; цей тест — доказ, що чат не монтується повз гейт).
"""
import base64

from fastapi.testclient import TestClient

import demos.upload_app.app as upapp
import demos.upload_app.chat_gradio.app as chat_app


def test_import_and_blocks_build():
    """Критерій готовності: модуль імпортується, Blocks будується."""
    demo = chat_app.build_blocks()
    assert demo is not None
    assert type(demo).__name__ == "Blocks"


def _auth(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_gate_covers_both_pages(monkeypatch):
    monkeypatch.setattr(upapp, "BASIC_USER", "demo")
    monkeypatch.setattr(upapp, "BASIC_PASS", "secret")
    client = TestClient(upapp.app)

    # без пароля -- 401 на обох сторінках і на API
    for path in ("/", "/chat", "/api/jobs/nope"):
        r = client.get(path, follow_redirects=True)
        assert r.status_code == 401, f"{path}: очікували 401, є {r.status_code}"
        assert r.headers.get("www-authenticate", "").startswith("Basic")

    # неправильний пароль -- теж 401
    r = client.get("/chat", headers=_auth("demo", "wrong"),
                   follow_redirects=True)
    assert r.status_code == 401

    # з паролем -- обидві сторінки відкриваються (один вхід на всю апку)
    r = client.get("/", headers=_auth("demo", "secret"))
    assert r.status_code == 200
    r = client.get("/chat", headers=_auth("demo", "secret"),
                   follow_redirects=True)
    assert r.status_code == 200
    assert "gradio" in r.text.lower()


def test_gate_off_when_unset(monkeypatch):
    """Локальний режим (127.0.0.1, змінні не виставлені) — без гейта; на
    зовнішньому інтерфейсі це блокує явна перевірка в __main__ апки."""
    monkeypatch.setattr(upapp, "BASIC_USER", "")
    monkeypatch.setattr(upapp, "BASIC_PASS", "")
    client = TestClient(upapp.app)
    assert client.get("/").status_code == 200
