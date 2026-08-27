# -*- coding: utf-8 -*-
"""Блоки F і D: дві речі в питанні, і слово «чинний».

## Блок F — «перший параметр виграє»

п. 16–17 звіту Дениса: чотири різні формулювання порівняння дали чотири рази ту
саму половину відповіді, і жодного слова про те, що друга половина відкинута.
Він же звів це до правила: «береться перший параметр, решта відкидається без
жодного слова».

**Декомпозиції запиту тут немає й до демо не буде** — це окрема робота, і той
самий провал відкритий у процедурному шляху (Андрій, розділ 8.6 його контексту).
Прибрано МОВЧАННЯ: тиха відповідь на половину питання гірша за уточнення, бо
перевірити її людина не може.

**Окремо — регресія, яку породила моя ж правка блоку A.** «Дві дати = період»
перетворило «порівняй 1 і 2 вересня» на діапазон 01..02, тобто на відповідь про
ТРЕТЄ питання, якого ніхто не ставив. До правки відповідь була про 1 вересня,
після — про період; обидві неправильні, але друга гірша, бо правдоподібніша.
Тому порівняння тепер відділене від діапазону словами, а не датами.

## Блок D — слово «чинний»

п. 10: №118 анульований, №131 виданий замість нього, а чат називав чинними
обидва. Причина: `facts.status = 'confirmed'` означає «факт витягнуто
впевнено», а слово «чинний» читається як «документ не скасовано». Осі
анулювання в базі немає взагалі — пайплайн її не витягує, і це моя зона.
Тому чат не може сказати «чинний» правдиво й не мусить казати цього, поки поля
немає.

Запуск:
    python -m pytest demos/upload_app/tests/test_two_entities_and_validity.py -q
"""
import datetime

import pytest

from demos.upload_app.chat_gradio import app as chat_app
from demos.upload_app.chat_gradio import db as chat_db

tiers = chat_app.tier_chat


@pytest.fixture(autouse=True)
def roster(monkeypatch):
    monkeypatch.setattr(tiers, "subdivisions", lambda: ["1-ша механізована рота"])


# ── Блок F. Порівняння -- не діапазон ─────────────────────────────────────

@pytest.mark.parametrize("question", [
    "Порівняй, скільком осіб у відпустці 2026-09-01 і 2026-09-02",
    "Відсутніх стало більше чи менше з 2026-08-26 до 2026-09-02?",
    "У 1 роті більше, ніж у 2?",
    "Чим відрізняються документи №131 і №118?",
])
def test_comparison_is_recognised(question):
    assert tiers.is_comparison(question), question


@pytest.mark.parametrize("question", [
    "Хто був у відрядженні з 2026-05-10 по 2026-10-10?",
    "Скільком осіб у відпустці 2026-10-10?",
    "Хто у відпустці протягом серпня 2026?",
])
def test_plain_questions_are_not_comparisons(question):
    """ПЕРЕВІРКА НА ПОБІЧНУ ШКОДУ: справжній діапазон не має ставати
    «порівнянням» -- інакше блок A зламається назад."""
    assert not tiers.is_comparison(question), question


def test_comparison_of_two_dates_is_not_turned_into_a_period():
    """Регресія від блоку A, знайдена своєю ж перевіркою.

    «Порівняй 1 і 2 вересня» НЕ мусить ставати періодом 01..02: це відповідь на
    питання, якого ніхто не ставив, і вона правдоподібніша за попередню
    помилку, тобто небезпечніша.
    """
    on_date, date_from, date_to = tiers.extract_dates(
        "Порівняй, скільком осіб у відпустці 2026-09-01 і 2026-09-02")
    assert on_date == datetime.date(2026, 9, 1)
    assert date_from is None and date_to is None


def test_real_period_still_becomes_a_period():
    """І навпаки -- «з X по Y» лишається періодом."""
    on_date, date_from, date_to = tiers.extract_dates(
        "Хто був у відрядженні з 2026-05-10 по 2026-10-10?")
    assert on_date is None
    assert (date_from, date_to) == (datetime.date(2026, 5, 10),
                                    datetime.date(2026, 10, 10))


def test_note_says_which_of_the_two_was_answered():
    lines = tiers.two_entities_note(
        "Порівняй, скільком у відпустці 2026-09-01 і 2026-09-02",
        "дата 2026-09-01")
    assert lines and "⚠️" in lines[0]
    assert "2026-09-01" in lines[0]
    assert "дві речі" in lines[0]


def test_no_note_on_a_plain_question():
    """Побічна шкода: попередження не має з'являтись у кожній відповіді."""
    assert tiers.two_entities_note("Скільком осіб у відпустці 2026-10-10?",
                                   "дата 2026-10-10") == []


def test_answered_about_uses_human_words():
    """У рядку для людини не мусить бути `on_date` -- це та сама внутрішня
    кухня, на яку скаржився Денис (п. 25)."""
    label = chat_app._answered_about({"on_date": "2026-09-01"})
    assert label == "дата 2026-09-01"
    assert "on_date" not in label
    assert chat_app._answered_about({}) == "перше зі згаданого"


def test_note_reaches_the_catalog_answer(monkeypatch):
    """Наскрізь: рядок мусить дійти до тексту відповіді, а не лишитись у
    функції, яку ніхто не кличе."""
    monkeypatch.setattr(tiers, "rules_route",
                        lambda q: ("count_by_state_on_date",
                                   {"dims": ["leave"], "on_date": "2026-09-01"}))
    monkeypatch.setattr(tiers, "run_template",
                        lambda tid, p: ("Доповідаю: 12 осіб у відпустці.", ["ш"]))
    out = chat_app._catalog_tier(
        "Порівняй, скільком у відпустці 2026-09-01 і 2026-09-02")
    assert "дві речі для порівняння" in out, out


# ── Блок D. «Чинний» ──────────────────────────────────────────────────────

def test_confirmed_is_no_longer_called_valid():
    """Головне твердження блоку D."""
    assert chat_db.STATUS_LABEL["confirmed"] == "підтверджений"
    assert "чинний" not in chat_db.STATUS_LABEL.values()


def test_unconfirmed_label_unchanged():
    """Друга мітка правильна й не чіпалась: чернетка -- це «ще не враховано»,
    а не «більше не діє»."""
    assert "чернетка" in chat_db.STATUS_LABEL["unconfirmed"]


def test_code_compares_status_code_not_the_human_label():
    """ПЕРЕВІРКА НА ПОБІЧНУ ШКОДУ, і саме вона робить перейменування безпечним.

    П'ять місць у коді порівнювали `r["status"] == "чинний"` -- тобто підпис для
    людини. Перейменувати підпис і лишити порівняння означало б зламати логіку
    мовчки: усі факти стали б «не підтвердженими». Тому порівняння переведені
    на КОД (`fact_status == 'confirmed'`), який є контрактом бази.
    """
    import inspect
    src = inspect.getsource(chat_app)
    assert '== "чинний"' not in src, "лишилось порівняння з підписом"
    assert '!= "чинний"' not in src, "лишилось порівняння з підписом"
