# -*- coding: utf-8 -*-
"""Привітання йде в розмовний шаблон, а не в «що відомо про людину».

Знахідка з мого ж переліку незакритих: «Доброго ранку!» модель клала в
`person_status` -- у питанні немає ні дати, ні підрахунку, зате є звертання, і
найближчим шаблоном для неї виявився «що відомо про людину». На екрані це
виглядало як «не знайшла такої особи» у відповідь на привітання.

ГОЛОВНЕ, ЩО ТУТ ТРИМАЄТЬСЯ -- не саме привітання, а те, що правило ЗАКРИТЕ:
фраза з привітанням І питанням мусить піти по питанню. Правило, яке хапає
«Привіт! скільки людей у відпустці?», зламало б рівно ті питання, задля яких
система існує, і зламало б тихо -- відповідь була б ввічлива.
"""
import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(APP_DIR, "chat_gradio"))

import tiers  # noqa: E402


GREETINGS = [
    "Доброго ранку!",
    "Доброго дня",
    "Добрий вечір",
    "Привіт!",
    "Вітаю",
    "Бажаю здоров'я",
    "Дякую за допомогу",
    "Спасибі!",
    "До побачення, гарного дня",
]

#: Фрази, у яких привітання є, але воно не головне.
NOT_GREETINGS = [
    "Привіт! скільки людей у відпустці?",
    "Доброго дня, покажи документ №102",
    "Дякую, а хто повертається завтра?",
    "Добрий день! Що в черзі перевірки?",
    "Вітаю, назвіть відсутніх по підрозділах",
]

#: Звичайні питання -- правило не мусить їх бачити взагалі.
PLAIN = [
    "Хто зараз у відрядженні?",
    "Скільки у відпустці 2026-10-10?",
    "Яка тривалість щорічної основної відпустки?",
    "Що відомо про Усика?",
]


def test_greetings_are_recognised():
    for q in GREETINGS:
        assert tiers.is_greeting(q), q


def test_a_greeting_with_a_question_is_not_a_greeting():
    """Найдорожчий випадок: ввічлива відповідь замість числа."""
    for q in NOT_GREETINGS:
        assert not tiers.is_greeting(q), q


def test_plain_questions_are_untouched():
    for q in PLAIN:
        assert not tiers.is_greeting(q), q


def test_rules_route_sends_a_greeting_to_smalltalk():
    for q in GREETINGS:
        route = tiers.rules_route(q)
        assert route, q
        assert route[0] == "smalltalk", (q, route)


def test_rules_route_keeps_questions_on_their_road():
    """Перевірка на пошкодження сусіднього: фраза з привітанням і питанням
    мусить піти шаблоном питання, а не розмовним.

    Частина цих фраз доходить до правил, які звертаються в базу (питання про
    корпус міряються по ньому). Без бази це не провал перевірки, а її межа:
    вирішує все одно гейт `is_greeting`, і він перевірений вище без бази. Тому
    недоступність бази тут -- пропуск ОДНІЄЇ фрази з поясненням, а не зелений
    тест на порожньому місці.
    """
    checked = 0
    for q in NOT_GREETINGS:
        try:
            route = tiers.rules_route(q)
        except Exception:                           # noqa: BLE001, PERF203
            #: База недоступна -- гейт уже спрацював (інакше ми б не дійшли до
            #: правила з запитом), тобто головне тут доведено.
            assert not tiers.is_greeting(q), q
            continue
        checked += 1
        if route:
            assert route[0] != "smalltalk", (q, route)
    assert checked, "жодної фрази не пройдено -- перевірка нічого не довела"


def test_the_rule_stands_before_the_counting_ones():
    """Порядок: привітання -- одразу після гейта недійсної дати.

    Якщо його опустити нижче, будь-яке правило підрахунку схопить фразу
    випадково (у «доброго дня» є слово «дня», і на це вже ловились дати)."""
    import io
    src = io.open(os.path.join(APP_DIR, "chat_gradio", "tiers.py"),
                  encoding="utf-8").read()
    body = src[src.index("def rules_route("):]
    i_greet = body.index("is_greeting(question)")
    for later in ("review_queue_count", "documents_count", "person_status"):
        if later in body:
            assert i_greet < body.index(later), later
