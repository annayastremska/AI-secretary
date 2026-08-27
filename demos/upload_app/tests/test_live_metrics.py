# -*- coding: utf-8 -*-
"""Живі метрики роботи чата: медіана й найгірші 10% часу відповіді.

Найважливіший тест тут -- `test_chat_and_stats_share_one_module`. Пастка
реальна: чат імпортує сусідні модулі плоско (`import db`), і якби лічильник
теж імпортувався плоско, Python створив би ДВА модулі -- `livemetrics` і
`demos.upload_app.livemetrics` -- із двома різними буферами. Чат рахував би в
один, сторінка читала б інший, і на екрані назавжди лишились би нулі. Ніщо не
впало б: ні виняток, ні падіння тесту -- просто цифра, якої немає. Тому
однаковість модуля перевіряється явно.
"""
import sys

from fastapi.testclient import TestClient

from demos.upload_app import app as app_mod
from demos.upload_app import livemetrics
from demos.upload_app import stats as stats_mod


def setup_function():
    livemetrics.reset()


def test_median_not_mean():
    """Медіана, а не середнє. Наші яруси різняться на два порядки (правила
    0.04 с, модель 45 с), і середнє на такому розкиді не описує нічого: воно
    показало б «11 с» там, де три з чотирьох відповідей були миттєві."""
    for value in (0.04, 0.05, 0.06, 45.0):
        livemetrics.record(value, "правила")
    snap = livemetrics.snapshot()
    assert snap["median_s"] == 0.1        # 0.05/0.06 -> округлення до 0.1
    assert snap["p90_s"] == 45.0          # найгірші 10% видно окремо
    assert snap["n"] == 4 and snap["total"] == 4


def test_window_is_bounded_but_total_is_not():
    """Буфер кільцевий (щоб не рости), але «скільки питань поставили» --
    число за весь час запуску, і воно не мусить скидатись разом із буфером."""
    for _ in range(livemetrics._KEEP + 25):
        livemetrics.record(1.0)
    snap = livemetrics.snapshot()
    assert snap["n"] == livemetrics._KEEP
    assert snap["total"] == livemetrics._KEEP + 25


def test_empty_says_nothing_instead_of_zero():
    """Порожній лічильник віддає None, а не 0. Нуль означав би «відповідаємо
    за нуль секунд» -- це інше твердження, ніж «ще не міряли»."""
    snap = livemetrics.snapshot()
    assert snap["median_s"] is None and snap["p90_s"] is None
    assert snap["n"] == 0


def test_chat_and_stats_share_one_module():
    """Чат і сторінка мусять писати й читати ОДИН буфер."""
    from demos.upload_app.chat_gradio import app as chat_app
    assert chat_app.livemetrics is livemetrics
    assert stats_mod.livemetrics is livemetrics
    # І плоского двійника не існує -- або він той самий об'єкт.
    assert sys.modules.get("livemetrics", livemetrics) is livemetrics


def test_live_endpoint_has_no_db_query(monkeypatch):
    """Маршрут /api/chat-live не мусить чіпати базу: сторінка опитує його
    кожні кілька секунд, а база -- чужа зона, і навмисно вантажити її ми
    домовились не робити."""
    def boom(*a, **kw):
        raise AssertionError("маршрут живих метрик поліз у базу")

    monkeypatch.setattr(stats_mod, "db_counters", boom)
    livemetrics.record(2.0, "каталог шаблонів")
    auth = ((app_mod.BASIC_USER, app_mod.BASIC_PASS)
            if app_mod.BASIC_USER and app_mod.BASIC_PASS else None)
    with TestClient(app_mod.app) as client:
        resp = client.get("/api/chat-live", auth=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["median_s"] == 2.0


def test_road_is_taken_from_the_answer_itself():
    """Дорога читається з готової відповіді, а не визначається вдруге: у
    блоці «джерело» вона вже є, і друге місце розійшлося б із першим."""
    from demos.upload_app.chat_gradio import app as chat_app
    text = ("Доповідаю: 1 особа.<br>дорога: каталог шаблонів "
            "(count_by_state_on_date)<br>звернення: 0a7b1a</details>")
    assert chat_app._road_of(text).startswith("каталог шаблонів")
    assert chat_app._road_of("без блоку джерела") is None


def test_quality_reports_absent_is_not_an_error():
    """Немає файла заміру -> None у полі, а не виняток на сторінці. Сторінка
    покаже прочерк і скаже, чим його заповнити."""
    monkey = stats_mod.ROUTER_REPORT
    try:
        stats_mod.ROUTER_REPORT = "не-існує.json"
        out = stats_mod.chat_quality()
        assert out["router"] is None
        assert out["quotes"]["blocked"]
    finally:
        stats_mod.ROUTER_REPORT = monkey
