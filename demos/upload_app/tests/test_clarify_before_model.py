# -*- coding: utf-8 -*-
"""Уточнення про дату — ОСТАННЯ інстанція, а не перша. Блок B, крок 2.

## Що тут вирішується

п. 1 звіту Дениса: «Скільком осіб у відпустці 2026-10-10?» → відповідь → «А у
відрядженні?» → **«Якої дати?»**. Дата була рядком вище.

Живий прогін 27.08 показав, що історія до маршрутизатора вже доходить (це
правка Андрія, зміряна 8/21 → 18/21), але до нього не доходить ЧЕРГА:
`dispatch_count` віддає `("clarify", …)` замість `None`, і на цьому хід
закінчується. Тобто маршрутизатор, який єдиний читає розмову, не отримує
жодного шансу.

## Порядок, який тут закріплюється

    стара дорога хоче уточнити
      → дати шанс маршрутизаторові з РОЗМОВОЮ (він єдиний її бачить)
        → відповів — відповідаємо
        → не відповів — уточнення лишається

Уточнення **не прибирається**. Воно лишається запобіжником: якщо дати немає ні
в питанні, ні в розмові, система питає, а не підставляє «сьогодні». Це прямо
правило продукту: тиха відповідь на іншу дату гірша за перепитування.

## Чому саме модельний ярус, а не `_extra_tiers` цілком

`_extra_tiers` починається з правил і векторів. На короткій репліці («а у
відрядженні?») вектори могли б віддати найближчий за формулюванням шаблон — і
той порахував би на СЬОГОДНІ, бо дати в репліці немає. Тобто ми замінили б
чесне уточнення на тиху неправду. Розмову читає лише маршрутизатор на моделі,
тому шанс дається саме йому.

Запуск:
    python -m pytest demos/upload_app/tests/test_clarify_before_model.py -q
"""
import pytest

from demos.upload_app.chat_gradio import app as chat_app

tiers = chat_app.tier_chat

HISTORY_WITH_DATE = [
    {"role": "user", "content": "Скільком осіб у відпустці 2026-10-10?"},
    {"role": "assistant", "content": "Доповідаю: 1 особа у відпустці."},
]


@pytest.fixture(autouse=True)
def no_db_no_model(monkeypatch):
    """Ні бази, ні моделі: перевіряємо ПОРЯДОК доріг, а не дані."""
    monkeypatch.setattr(tiers, "subdivisions",
                        lambda: ["1-ша механізована рота", "3-тя механізована рота",
                                 "Взвод забезпечення"])
    monkeypatch.setattr(tiers, "_run_template_sql", lambda sql, p: [])
    monkeypatch.setattr(chat_app.db, "find_people", lambda **kw: [])
    monkeypatch.setattr(chat_app.db, "absences_for_person", lambda *a, **kw: [])
    monkeypatch.setattr(chat_app.db, "coverage_note", lambda date=None: "")
    monkeypatch.setattr(chat_app.db, "data_coverage", lambda: (None, None))


def _clarifies(out):
    return bool(out) and out.startswith(chat_app.CLARIFY_MARK)


# ── Те, що мусить вижити (перевірки ДО правки) ─────────────────────────────

def test_no_history_still_clarifies_instead_of_inventing_today(monkeypatch):
    """ГОЛОВНИЙ запобіжник. Без розмови дату взяти нізвідки -- питаємо.

    Якщо цей тест колись стане червоним, це означає, що система почала
    підставляти «сьогодні» молчки: рівно та поломка, через яку п. 11 звіту
    («разом менше за частину») читається як брехня.
    """
    monkeypatch.setattr(chat_app, "model_available", lambda: False)
    out = chat_app.answer("Хто відсутній?", [])
    assert _clarifies(out) or "дата" in out.lower(), out


def test_model_that_answers_nothing_leaves_the_clarification(monkeypatch):
    """Маршрутизатор не впорався -- уточнення лишається, а не зникає."""
    monkeypatch.setattr(chat_app, "model_available", lambda: True)
    monkeypatch.setattr(tiers, "_get_model", lambda: object())
    monkeypatch.setattr(chat_app, "_model_catalog_tier",
                        lambda q, h=None: None)
    out = chat_app.answer("Хто відсутній?", HISTORY_WITH_DATE)
    assert _clarifies(out), out


def test_model_refusal_also_leaves_the_clarification(monkeypatch):
    """`_MODEL_REFUSED` -- це «питання не про базу», а не відповідь. Уточнення
    лишається: інакше відмова моделі стерла б чесне запитання про дату."""
    monkeypatch.setattr(chat_app, "model_available", lambda: True)
    monkeypatch.setattr(tiers, "_get_model", lambda: object())
    monkeypatch.setattr(chat_app, "_model_catalog_tier",
                        lambda q, h=None: chat_app._MODEL_REFUSED)
    out = chat_app.answer("Хто відсутній?", HISTORY_WITH_DATE)
    assert _clarifies(out), out


def test_broken_model_tier_does_not_break_the_answer(monkeypatch):
    """Ярус упав винятком -- людина однаково мусить отримати уточнення.

    Це не теоретично: саме сьогодні я вклала `NameError` у цю ділянку, і він
    поклав би чат на будь-якому питанні до моделі.
    """
    def boom(q, h=None):
        raise RuntimeError("ярус зламався")

    monkeypatch.setattr(chat_app, "model_available", lambda: True)
    monkeypatch.setattr(tiers, "_get_model", lambda: object())
    monkeypatch.setattr(chat_app, "_model_catalog_tier", boom)
    out = chat_app.answer("Хто відсутній?", HISTORY_WITH_DATE)
    assert _clarifies(out), out


# ── Те, що правка мусить додати ────────────────────────────────────────────

def test_model_with_conversation_gets_the_chance_before_clarifying(monkeypatch):
    """Суть правки: перед уточненням маршрутизатор бачить РОЗМОВУ.

    Перевіряється не лише результат, а й що історія дійшла: без неї ярус не
    міг би дістати дату, і шанс був би формальним.
    """
    seen = {}

    def fake_tier(question, history=None):
        seen["question"], seen["history"] = question, history
        return ("Доповідаю: 3 особи у відрядженні.\nЗріз: на 2026-10-10."
                "\n\n⚠️ узято з попереднього питання: дата")

    monkeypatch.setattr(chat_app, "model_available", lambda: True)
    monkeypatch.setattr(tiers, "_get_model", lambda: object())
    monkeypatch.setattr(chat_app, "_model_catalog_tier", fake_tier)

    # Питання САМЕ ТАКЕ, а не «а у відрядженні?»: перша версія тесту брала
    # коротку репліку й проходила ще ДО правки -- бо вона не доходить до гілки
    # уточнення взагалі (намір із неї не витягується, і хід іде далі сам).
    # Тобто тест був зелений із неправильної причини. «Хто відсутній?» без
    # дати уточнення дає гарантовано -- на ньому й міряємо.
    out = chat_app.answer("Хто відсутній?", HISTORY_WITH_DATE)

    assert not _clarifies(out), "уточнення переважило готову відповідь"
    assert "2026-10-10" in out, out
    assert seen.get("history") == HISTORY_WITH_DATE, "історія не дійшла"
    # І успадковане назване вголос -- без цього відповідь небезпечна
    assert "узято з попереднього питання" in out, out
