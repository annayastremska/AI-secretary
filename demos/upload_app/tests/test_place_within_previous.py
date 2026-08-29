# -*- coding: utf-8 -*-
"""«Хтось із них у Житомирі?» -- звуження попереднього переліку, не новий запит.

Дефект, знайдений Анею 29.08 живцем: після переліку трьох осіб у відрядженні
питання «хтось із них у житомирі?» пішло звичайною дорогою місця (запит по ВСІЙ
базі) і відповіло про Лемешко з ТРАВНЕВОЮ відпусткою. Питання про тих трьох
лишилось без відповіді, і про підміну предмета не було сказано нічого.

Той самий клас, що «скільки набоїв»: впевнена відповідь не про те.
"""
import datetime
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CHAT = os.path.abspath(os.path.join(HERE, "..", "chat_gradio"))
sys.path.insert(0, CHAT)

import tiers                                        # noqa: E402

CATALOG = os.path.join(ROOT, "demos", "upload_app", "query_catalog.yaml")


# ── Розпізнавання звуження ───────────────────────────────────────────────────


@pytest.mark.parametrize("q", [
    "хтось із них в житомирі?",
    "хто з цих людей у житомирі",
    "серед них є хтось у Рівному",
    "з тих трьох хтось у Житомирі",
])
def test_narrowing_is_recognised(q):
    assert tiers.asks_within_previous(q), q


@pytest.mark.parametrize("q", [
    "хто у житомирі",
    "а хто ще там?",
    "скільки людей у відрядженні",
])
def test_plain_place_question_is_not_narrowing(q):
    """Звичайне питання про пункт мусить лишитись звичайним: інакше нова гілка
    забрала б собі дорогу, яка працює."""
    assert not tiers.asks_within_previous(q), q


# ── Шаблон перетину ──────────────────────────────────────────────────────────


def _template():
    ts = yaml.safe_load(open(CATALOG, encoding="utf-8"))["templates"]
    return [t for t in ts if t["id"] == "list_place_within_state"][0]


def test_template_exists_and_is_internal():
    t = _template()
    assert t["params"] == ["place", "dims", "on_date"]
    # Модель цей шаблон не обирає: його ставить код, коли бачить звуження.
    assert t.get("internal") is True


def test_every_param_is_allowed_in_code():
    """Той самий конструкційний тест, що зловив `subdivision` 25.08 і `place`
    29.08: параметр, якого немає в `_SQL_PARAM_NAMES`, не доїжджає до запиту, і
    відповідь стає тихою відмовою."""
    for p in _template()["params"]:
        assert p in tiers._SQL_PARAM_NAMES, p


@pytest.mark.parametrize("key", ["sql", "sql_unconfirmed"])
def test_both_queries_are_narrowed(key):
    """Звуження мусить бути в ОБОХ запитах. Непідтверджені -- теж перетин: без
    цього чернетки поїхали б із усієї бази, а підтверджені -- лише з переліку."""
    q = _template()[key]
    assert "%(dims)s" in q, key
    assert q.count("%(on_date)s") == 2, key
    assert "%(place)s" in q, key


def test_confirmed_and_unconfirmed_are_separated():
    t = _template()
    assert "f.status = 'confirmed'" in t["sql"]
    assert "f.status = 'unconfirmed'" in t["sql_unconfirmed"]


# ── Подача відповіді ─────────────────────────────────────────────────────────


def _render(monkeypatch, rows, unconfirmed=(), place="м. Житомир"):
    """Рендер окремою функцією не винесений -- він усередині `run_template`,
    тому підмінюємо саме читання з бази. Заглушка розрізняє два запити за
    словом `unconfirmed` у самому SQL: інакше чернетки й підтверджені
    поїхали б однаковим переліком, і тест не побачив би підміни."""
    def fake(sql, params):
        # Знаменник («усього в реєстрі») читається тим самим шляхом, тому
        # заглушка мусить відповісти й на нього -- інакше падає не гілка, а
        # склад відповіді.
        if "FROM people" in sql:
            return [{"n": 303}]
        return list(unconfirmed) if "'unconfirmed'" in sql else list(rows)
    monkeypatch.setattr(tiers, "_run_template_sql", fake)
    text, _src = tiers.run_template(
        "list_place_within_state",
        {"place": place, "dims": ["deployment_location"],
         "on_date": "2026-08-30"})
    return text


def test_empty_result_says_none_of_them(monkeypatch):
    """«У Житомирі нікого немає» і «нікого З НИХ немає» -- різні твердження.
    Перше заперечує наявність людей у пункті взагалі, і це була б неправда."""
    text = _render(monkeypatch, [])
    assert "нікого з них" in text.lower()
    assert "м. Житомир" in text


def test_listed_result_names_the_narrowing(monkeypatch):
    rows = [{"name": "Гавриш Адам Станіславович", "dim": "deployment_location",
             "place": "м. Житомир", "valid_from": datetime.date(2026, 8, 26),
             "valid_to": datetime.date(2026, 8, 31), "source_doc_id": 33,
             "doc_number": "207"}]
    text = _render(monkeypatch, rows)
    assert "із цього переліку" in text
    assert "Гавриш" in text


def test_answer_still_states_the_as_of_date(monkeypatch):
    """Правило продукту: зріз є в КОЖНІЙ відповіді. Один раз я його вже
    зняла, замінивши загальний табличний рендер своєю гілкою."""
    assert "Зріз:" in _render(monkeypatch, [])
    assert "Зріз:" in _render(monkeypatch, [{"name": "Х", "dim": "deployment_location",
                                "place": "м. Житомир", "valid_from": None,
                                "valid_to": None, "source_doc_id": 1,
                                "doc_number": None}])
