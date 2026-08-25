# -*- coding: utf-8 -*-
"""Одне питання -- один період, незалежно від того, який ярус його впізнав.

Знахідка аудиту 25.08 (аблацією): дати в чат приходили двома різними
шляхами. Векторний ярус брав їх ПРАВИЛАМИ (`params_for_template` ->
`extract_dates`), а модельний -- тільки з JSON моделі, і коли модель
віддавала null, підставляв «сьогодні». Наслідок, заміряний зі стаб-моделлю:

    «Хто був у відпустці у травні 2026?»
      через векторний ярус -> 2026-05-01 .. 2026-05-31
      через модельний ярус -> сьогодні .. сьогодні

Це не тиха вигадка (зріз дати чат озвучує), але одне й те саме питання
давало різну відповідь залежно від внутрішнього шляху — а такого користувач
пояснити не може. Правка: правила стали проміжним шаром фолбека в
`model_route` — модель лишається головною, але там, де вона змовчала,
правила знають більше за «сьогодні».

Запуск:
    python -m pytest demos/upload_app/tests/test_route_date_agreement.py -q
"""
import datetime

import pytest

from demos.upload_app.chat_gradio import tiers


#: Питання з періодом у тексті, який правила вміють прочитати.
QUESTION = "Хто був у відпустці у травні 2026?"


@pytest.fixture()
def silent_model(monkeypatch):
    """Стаб моделі, яка обрала шаблон правильно, а всі слоти віддала null.

    Це найважливіший, а не екзотичний випадок: 4B-модель на CPU саме так і
    поводиться на питаннях із періодом -- шаблон вона впізнає, дати ні."""
    def _fake(system, user, schema):
        return {"template": "list_by_state", "state": "leave",
                "on_date": None, "date_from": None, "date_to": None,
                "name": None, "doc_number": None}
    monkeypatch.setattr(tiers, "_model_json", _fake)


def test_model_tier_falls_back_to_rule_dates_not_to_today(silent_model):
    tid, params = tiers.model_route(QUESTION)
    assert tid == "list_by_state"
    assert params["date_from"] == datetime.date(2026, 5, 1), params
    assert params["date_to"] == datetime.date(2026, 5, 31), params


def test_both_tiers_agree_on_the_same_question(silent_model):
    """Головне твердження: два яруси -- один період."""
    _, model_params = tiers.model_route(QUESTION)
    vector_params = tiers.params_for_template("list_by_state", QUESTION)
    for key in ("date_from", "date_to"):
        assert model_params[key] == vector_params[key], (
            key, model_params[key], vector_params[key])


def test_model_dates_still_win_over_rules(monkeypatch):
    """Запобіжник проти перегину в інший бік: правила -- ФОЛБЕК, не заміна.
    Коли модель дату віддала, беремо її."""
    def _fake(system, user, schema):
        return {"template": "list_by_state", "state": "leave",
                "on_date": None, "date_from": "2026-05-10",
                "date_to": "2026-05-12", "name": None, "doc_number": None}
    monkeypatch.setattr(tiers, "_model_json", _fake)
    _, params = tiers.model_route(QUESTION)
    assert params["date_from"] == datetime.date(2026, 5, 10)
    assert params["date_to"] == datetime.date(2026, 5, 12)


def test_no_date_in_question_still_means_today(monkeypatch):
    """І там, де дати немає ніде, дефолт лишається тим самим -- сьогодні."""
    def _fake(system, user, schema):
        return {"template": "list_by_state", "state": "leave",
                "on_date": None, "date_from": None, "date_to": None,
                "name": None, "doc_number": None}
    monkeypatch.setattr(tiers, "_model_json", _fake)
    today = datetime.date.today()
    _, params = tiers.model_route("Хто у відпустці?")
    assert params["date_from"] == today and params["date_to"] == today


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
