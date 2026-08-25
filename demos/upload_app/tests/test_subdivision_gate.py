# -*- coding: utf-8 -*-
"""Питання про підрозділ: тепер ВІДПОВІДЬ, а не відмова — але не всяка.

Історія цього файлу — про те, як правда змінюється разом із даними.

**Було до 25.08:** зв'язку особа→підрозділ у базі не існувало, тому єдиною
чесною відповіддю була відмова. Цей тест її й сторожив: гейт відбирав такі
питання в підрахунків, бо число «по всій частині» на питання про роту —
неправда з виглядом правди.

**Стало 25.08:** Андрій залив штатку — 300 осіб зі `service_id` і вимір
`subdivision` (по 90 у трьох ротах, 15 у взводі забезпечення, 15 в управлінні
батальйону). У ту саму хвилину наша відмова перестала бути чесною: система
заперечувала б власні дані.

Тому тест перевернутий, і в ньому три випадки замість одного:
  1. підрозділ названий і він У ШТАТЦІ → рахуємо по ньому;
  2. названий, але такого немає → **чесна відмова**, не нуль (нуль читався б
     як «там нікого», а насправді немає підрозділу);
  3. не названий («по підрозділах») → розклад по всіх.

Запуск:
    python -m pytest demos/upload_app/tests/test_subdivision_gate.py -q
"""
import pytest

from demos.upload_app.chat_gradio import app as chat_app

tiers = chat_app.tier_chat

#: Штатка на сервері. У тестах бази немає, тому перелік підставляється --
#: інакше `extract_subdivision` не має з чим порівнювати.
ROSTER = ["1-ша механізована рота", "2-га механізована рота",
          "3-тя механізована рота", "Взвод забезпечення",
          "Управління батальйону"]


@pytest.fixture(autouse=True)
def roster(monkeypatch):
    monkeypatch.setattr(tiers, "subdivisions", lambda: ROSTER)


KNOWN = [
    "Скільки людей у 2 роті зараз у відпустці?",
    "Скільком у другій роті у відпустці?",
    "Скільком у 1-й механізованій роті у відрядженні?",
    "Скільком у взводі забезпечення у відпустці сьогодні?",
    "Хто відсутній у 3 роті?",
]


@pytest.mark.parametrize("question", KNOWN)
def test_known_subdivision_is_counted(question):
    """ГОЛОВНЕ: підрозділ зі штатки більше не отримує відмову."""
    route = tiers.rules_route(question)
    assert route, question
    tid, params = route
    assert tid == "count_by_state_in_subdivision", (question, tid)
    # параметри мусять бути ПОВНІ: без dims/on_date шаблон не виконається, і
    # питання тихо поїде на стару дорогу просити дату (так і було в першій
    # спробі -- перевірено на сервері)
    for key in ("dims", "on_date", "subdivision"):
        assert key in params, (question, key, params)


UNKNOWN = [
    "Скільки людей у 7 роті у відпустці?",
    "Скільком у 9-й роті у відрядженні?",
]


@pytest.mark.parametrize("question", UNKNOWN)
def test_unknown_subdivision_is_refused_not_zeroed(question):
    """Підрозділ, якого в штатці немає, мусить давати ВІДМОВУ. Нуль тут був би
    неправдою: немає самого підрозділу, а не людей у ньому."""
    route = tiers.rules_route(question)
    assert route and route[0] == "subdivision_unknown", (question, route)


@pytest.mark.parametrize("question", [
    "Покажи відсутніх по підрозділах",
    "Скільком у відпустці по ротах?",
])
def test_breakdown_when_subdivision_not_named(question):
    route = tiers.rules_route(question)
    assert route and route[0] == "subdivision_breakdown", (question, route)


#: ЗАПОБІЖНИК ПРОТИ ПЕРЕГИНУ. Гейт не має чіпати питання, де підрозділ не
#: згадується, і не має ковтати нормативні питання зі словом «підрозділ».
NOT_SUBDIVISION = [
    ("Скільком зараз у відпустці?", "count_by_state_on_date"),
    ("Хто зараз у відрядженні?", "list_by_state"),
    ("Яка процедура оформлення відпустки у підрозділі?", "normative_search"),
]


@pytest.mark.parametrize("question,expected", NOT_SUBDIVISION)
def test_gate_does_not_eat_other_questions(question, expected):
    route = tiers.rules_route(question)
    assert route and route[0] == expected, (question, route)


def test_unknown_subdivision_refusal_names_the_real_ones():
    """Відмова мусить сказати, які підрозділи Є -- інакше людина не знає, як
    перепитати."""
    t = tiers._CATALOG["subdivision_unknown"]
    assert t.get("blocked") is True
    refusal = (t.get("refusal") or "").lower()
    assert "рота" in refusal or "роти" in refusal
    assert "взвод" in refusal


def test_subdivision_templates_have_sql_and_draft_query():
    """Шаблони підрахунку мусять мати і основний запит, і окремий на чернетки:
    правило «чернетка не факт» діє і в розрізі підрозділу."""
    for tid in ("count_by_state_in_subdivision",):
        t = tiers._CATALOG[tid]
        assert t.get("sql") and t.get("sql_unconfirmed"), tid
    assert tiers._CATALOG["subdivision_breakdown"].get("sql")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
