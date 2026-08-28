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


#: Розмовні форми, які знайшов ПРИЛАД, а не я. `measure_router
#: --production-view` показав, що правила ведуть «Розкажи анекдот» і «Розкажи
#: щось цікаве» в `person_status` -- тобто на прохання пожартувати чат
#: відповідав «не знайшла такої особи». А «привіт, є хтось живий?» пролітало
#: моє ж правило, бо `хто` збігалося всередині `хтось`.
#:
#: Обидві дірки мій перший набір не мав за побудовою: у ньому були лише ті
#: фрази, які я сама придумала, а придумати «хтось» у переліку слів-заперечень
#: неможливо -- це видно тільки на чужому наборі.
CHITCHAT = [
    "привіт, є хтось живий?",
    "як ти?",
    "Як справи?",
    "ти бот чи людина?",
    "Розкажи анекдот",
    "Розкажи щось цікаве",
    "Хто ти і що ти вмієш?",
]

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


def test_chitchat_found_by_the_instrument_is_recognised():
    """Форми з заміру маршрутизатора -- окремим тестом, щоб було видно, що їх
    знайшов прилад, а не автор тесту."""
    for q in CHITCHAT:
        assert tiers.is_greeting(q), q


def test_chitchat_goes_to_smalltalk_too():
    for q in CHITCHAT:
        route = tiers.rules_route(q)
        assert route and route[0] == "smalltalk", (q, route)


def test_question_words_match_whole_words_only():
    """Пряма перевірка тієї помилки: `хто` не мусить збігатися в `хтось`.

    Тримається саме на цьому слові, а не на фразі: фразу легко полагодити
    списком винятків, а помилка була в межах слова."""
    assert tiers.is_greeting("привіт, є хтось живий?")
    assert not tiers.is_greeting("привіт, хто у відпустці?")


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
