# -*- coding: utf-8 -*-
"""Питання про ПРАВИЛО не має ставати підрахунком ЛЮДЕЙ.

Два дефекти, знайдені фінальним адверсарним проходом 25.08, і обидва псували
правдивість:

  1. «За скільки днів подавати рапорт на відпустку?» → **«7 осіб у
     відпустці»**, ще й із блоком джерела, тобто з виглядом правдивої
     відповіді. Причина механічна: у питанні є «скільк» і «відпустку», отже
     правила бачили підрахунок стану. Питання ж про ПРОЦЕДУРУ.
  2. «Які накази та інструкції зберігаються в системі?» → «у базі поки немає
     нормативних документів», **при 41 такому документі**. Нормативні питання
     каталог не впізнавав зовсім і вони їхали на стару дорогу «довідник», яка
     на нашій базі мертва (`db.search_reference` завжди []). Система
     заперечувала те, що сама ж мала.

Тест сторожить обидві половини: нормативні питання йдуть у нормативні шаблони,
а питання про дані лишаються питаннями про дані.

Запуск:
    python -m pytest demos/upload_app/tests/test_normative_gate.py -q
"""
import pytest

from demos.upload_app.chat_gradio import app as chat_app

tiers = chat_app.tier_chat


NORMATIVE = [
    ("За скільки днів подавати рапорт на відпустку?", "normative_search"),
    ("Який порядок подачі рапорту?", "normative_search"),
    ("Яка процедура оформлення відпустки?", "normative_search"),
    ("Що каже наказ про самовільне залишення частини?", "normative_search"),
    ("Які накази та інструкції зберігаються в системі?", "normative_list"),
    ("Перелічи нормативні акти", "normative_list"),
]

#: Запобіжник проти перегину: гейт нормативки НЕ має з'їдати питання про дані.
#: Це та сама пара перевірок, що в гейті підрозділів -- бо перегин в інший бік
#: означав би, що чат відмовляє на найпростішому питанні.
DATA = [
    ("Скільком зараз у відпустці?", "count_by_state_on_date"),
    ("Скільком у відпустці 6 травня 2026?", "count_by_state_on_date"),
    ("Хто зараз у відрядженні?", "list_by_state"),
    ("Скільки документів у базі?", "documents_count"),
    ("Скільки непідтверджених фактів?", "unconfirmed_count"),
]


@pytest.mark.parametrize("question,expected", NORMATIVE)
def test_normative_questions_go_to_normative_templates(question, expected):
    route = tiers.rules_route(question)
    assert route, f"«{question}» не впізнано взагалі"
    assert route[0] == expected, (question, route[0])


@pytest.mark.parametrize("question,expected", DATA)
def test_data_questions_stay_data(question, expected):
    route = tiers.rules_route(question)
    assert route, f"«{question}» не впізнано взагалі"
    assert route[0] == expected, (question, route[0])


def test_normative_search_carries_the_question_as_parameter():
    """Текст питання йде ПАРАМЕТРОМ у пошук, а не в SQL: інакше це була б
    інʼєкція через звичайне питання."""
    route = tiers.rules_route("Яка процедура оформлення відпустки?")
    assert route[1].get("query") == "Яка процедура оформлення відпустки?"


def test_normative_templates_answer_before_the_old_road():
    """Нормативні шаблони мусять відбирати питання в старої дороги «довідник»
    -- інакше повернеться «у базі немає нормативних документів» при 41
    документі."""
    assert "normative_list" in chat_app._STATE_TEMPLATES
    assert "normative_search" in chat_app._STATE_TEMPLATES


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
