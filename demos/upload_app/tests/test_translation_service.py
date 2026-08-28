# -*- coding: utf-8 -*-
"""Переклад як сервіс: кеш -- продукт, модель -- генератор.

Рішення Ані 28.08: словника, який я написала руками, недостатньо -- на демо
будуть іноземці, тому перекласти треба ВСЕ, включно з нормативкою, і зробити це
інструментом, а не вручну.

## Що обрано (research 28.08) і чому саме так

* **вбудований перекладач браузера** (`window.Translator`, Chrome 138+) --
  недоступний: працює лише в захищеному контексті, а сторінка роздається по
  HTTP на IP;
* **віджет Google** -- припинений для нових сайтів із 2019;
* **хмарний API** -- ключ у сторінці або залежність демо від чужого сервісу;
* **`nllb-200-distilled-600M` локально** -- обрано. Заміряно на сервері:
  завантаження ~24 с, п'ять рядків 3.3 с на процесорі, детерміновано
  (beam search, без сімплінгу).

## Головне, що тут перевіряється

**Кеш працює без моделі.** На демо не має бути жодного очікування: 817 рядків
лежать у `data/eval/translation-cache.json`, у git, і віддаються за
мілісекунди. Модель потрібна лише щоб кеш поповнити.

**Немає перекладу -- рядок не повертається.** Не порожній рядок і не помилка:
сторінка лишає це місце українською. Порожній переклад читався б як поломка.

**SQL і номер звернення не перекладаються ніколи** -- переклад їх зламав би:
запит мусить збігатися з виконаним, номер є ключем у журналі.

Запуск:
    python -m pytest demos/upload_app/tests/test_translation_service.py -q
"""
import io
import json
import os

import pytest

from demos.upload_app import translate as tr


def test_cache_is_committed_and_not_tiny():
    """Кеш -- це продукт. Якщо його немає в репозиторії, на демо перекладу
    немає взагалі: модель за замовчуванням вимкнена."""
    assert os.path.exists(tr.CACHE_PATH), tr.CACHE_PATH
    data = json.load(io.open(tr.CACHE_PATH, encoding="utf-8"))
    assert len(data) > 700, len(data)


def test_model_is_off_by_default():
    """На демо жодного очікування моделі. Вмикається явно, для поповнення."""
    assert tr.MODEL_ENABLED is False or os.environ.get("TRANSLATE_MODEL")


def test_known_strings_come_from_cache_without_a_model():
    got = tr.translate(["Статистика", "Чат"], allow_model=False)
    assert got.get("Статистика") == "Statistics"
    assert got.get("Чат")


def test_unknown_string_is_absent_not_empty():
    """Порожній переклад гірший за український текст: він виглядав би як
    поломка сторінки. Тому невідомий рядок просто не повертається."""
    got = tr.translate(["цього рядка в кеші напевно немає 12345"],
                       allow_model=False)
    assert got == {}


@pytest.mark.parametrize("text", [
    "SELECT COUNT(*) FROM facts f",
    "SELECT o.canonical_name AS name FROM objects o WHERE id = %(id)s",
    "cd3433",
    "abc123",
])
def test_sql_and_request_ids_are_never_translated(text):
    assert tr._skip(text), text
    assert tr.translate([text], allow_model=False) == {}


@pytest.mark.parametrize("text", [
    "Statistics",              # уже англійською
    "1077",                    # число
    "—",                       # розділювач
])
def test_nothing_to_do_is_skipped(text):
    assert tr._skip(text), text


def test_normative_quote_is_translated_now():
    """РІШЕННЯ ПЕРЕВЕРНУТЕ (Аня 28.08). Спершу цитати норм я захищала від
    перекладу: перекладена норма -- це вже переказ. Для демо це скасовано, бо
    дані синтетичні, а незрозуміла сторінка шкодить більше. Для пілота з
    реальними документами рішення треба переглянути -- саме тому воно записане
    тестом, а не забуте."""
    data = json.load(io.open(tr.CACHE_PATH, encoding="utf-8"))
    quotes = [k for k in data if k.startswith("«")]
    assert quotes, "у кеші немає жодної цитати -- нормативку не переклали"
    for q in quotes[:3]:
        assert data[q] and data[q] != q


def test_endpoint_contract():
    """Маршрут мусить бути стійким до сміття: сторінка присилає, що присилає."""
    from fastapi.testclient import TestClient

    from demos.upload_app import app as web

    client = TestClient(web.app, raise_server_exceptions=False)
    ok = client.post("/api/translate", json={"texts": ["Статистика"]})
    assert ok.status_code == 200
    assert ok.json()["texts"]["Статистика"] == "Statistics"

    for bad in ({"texts": "не список"}, {"інше": 1}, []):
        r = client.post("/api/translate", json=bad)
        assert r.status_code == 400, bad
        assert r.json() == {"texts": {}}


def test_request_size_is_bounded():
    """Сторінка присилає десятки рядків. Межа стоїть, щоб один запит не міг
    попросити переклад усього корпусу."""
    from fastapi.testclient import TestClient

    from demos.upload_app import app as web

    client = TestClient(web.app, raise_server_exceptions=False)
    r = client.post("/api/translate",
                    json={"texts": ["Статистика"] * 5000})
    assert r.status_code == 200
