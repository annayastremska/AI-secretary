# -*- coding: utf-8 -*-
"""Ворожі питання (блок 3 перевірки): питання не має ЗВУЖУВАТИСЬ мовчки.

Прогін 26.08 на живому сервері дав шість дефектів одного типу — чат відповідав
на **інше, вужче питання**, і робив це впевнено: з датою зрізу, джерелом і
номером звернення. Саме такі відповіді найгірші: перевірити їх людина не може.

  1. «Хто НЕ у відпустці зараз?» -> перелік тих, хто У відпустці;
  2. «Хто був у відпустці рік тому?» -> сьогоднішній перелік («рік тому» тихо
     ставало сьогодні);
  3. «У якій роті найбільше відсутніх?» -> відмова «такого підрозділу немає»;
  4. «У 1 роті більше, ніж у 2?» -> цифра лише по 1-й роті;
  5. «У 2 і 3 роті разом» -> цифра лише по 3-й роті;
  6. «У відпустці і у відрядженні» -> лише відпустка.

Запуск:
    python -m pytest demos/upload_app/tests/test_tricky_questions.py -q
"""
import datetime

import pytest

from demos.upload_app.chat_gradio import app as chat_app

tiers = chat_app.tier_chat

ROSTER = ["1-ша механізована рота", "2-га механізована рота",
          "3-тя механізована рота", "Взвод забезпечення",
          "Управління батальйону"]


@pytest.fixture(autouse=True)
def roster(monkeypatch):
    monkeypatch.setattr(tiers, "subdivisions", lambda: ROSTER)


@pytest.mark.parametrize("question", [
    "Хто НЕ у відпустці зараз?",
    "Скільком не у відрядженні 30 серпня 2026?",
    "Скільки людей не відсутні сьогодні?",
])
def test_negation_is_not_answered_with_its_opposite(question):
    tid, params = tiers.rules_route(question)
    assert tid == "count_not_in_state", (question, tid)
    assert "dims" in params and "on_date" in params


@pytest.mark.parametrize("question,expected_days_ago", [
    ("Хто був у відпустці рік тому?", 365),
    ("Скільком у відпустці місяць тому?", 30),
    ("Хто був у відпустці тиждень тому?", 7),
    ("Хто був у відпустці вчора?", 1),
])
def test_relative_dates_are_not_silently_today(question, expected_days_ago):
    on_date, date_from, _ = tiers.extract_dates(question)
    got = on_date or date_from
    want = datetime.date.today() - datetime.timedelta(days=expected_days_ago)
    assert got == want, (question, got)


@pytest.mark.parametrize("question", [
    "У якій роті найбільше відсутніх 30 серпня 2026?",
    "У 1 роті більше відсутніх, ніж у 2?",
    "Скільком осіб у 2 і 3 роті разом у відпустці 30 серпня?",
    "Порівняй роти за відсутніми",
])
def test_comparison_between_subdivisions_gives_the_breakdown(question):
    tid, params = tiers.rules_route(question)
    assert tid == "subdivision_breakdown", (question, tid)
    assert "on_date" in params and "dims" in params


def test_one_named_subdivision_still_goes_to_that_subdivision():
    """Правка на порівняння не мусила зламати звичайне питання про роту."""
    tid, params = tiers.rules_route("Скільки людей у 2 роті зараз у відпустці?")
    assert tid == "count_by_state_in_subdivision", tid
    assert params["subdivision"]


def test_two_states_in_one_question_count_both():
    assert tiers.extract_state("Скільком у відпустці і у відрядженні?") == "absent"
    assert tiers.extract_state("Скільком у відпустці?") == "leave"
    assert tiers.extract_state("Хто у відрядженні?") == "deployment"


def test_negated_answer_says_both_numbers_and_the_limit(monkeypatch):
    monkeypatch.setattr(tiers, "_run_template_sql",
                        lambda sql, params: [{"total": 303, "in_state": 12,
                                              "n": 1}])
    monkeypatch.setattr(tiers, "_people_total", lambda: 303)
    text, _ = tiers.run_template("count_not_in_state",
                                 {"dims": ["leave"], "state": "leave",
                                  "on_date": "2026-08-30"})
    assert "291" in text                      # 303 - 12
    assert "303" in text and "12" in text     # віднімання сказане вголос
    assert "ЗА ШТАТКОЮ" in text               # межа названа
    assert "Переліку не даю" in text
