# -*- coding: utf-8 -*-
"""Питання «хто ще в цьому місті». Розбір звернення 05259c (Аня, 29.08).

ЩО БУЛО. Дві репліки поспіль: «Покажи документ №102», далі «але вони саме в
житомирі?». Друга отримала відповідь на 468 символів, ІЗ ДЖЕРЕЛОМ і БЕЗ
відмови -- тобто система впевнено відповіла про інше. Причина не в моделі: у
каталозі не було жодного параметра «місце», тобто такого зрізу система не
вміла за побудовою, а замість відмови впала в найближчий підрахунок.

ЩО ТУТ ТРИМАЄТЬСЯ. Не сам новий шаблон -- його зламати важко. Тримаються дві
межі, зламати які легко й тихо:

  1. місце розпізнається ЛИШЕ якщо воно справді є в даних. Правило, що хапає
     будь-яке слово після «у/в», почне бачити місто у «у відпустці» й «у
     черзі»;
  2. схожа, але не наявна назва -- це ВІДМОВА, а не найближчий збіг. Те саме
     правило, що для номерів документів: не виправляємо і схожих не
     підставляємо.

Критерії -- `docs/tasks/2026-08-27_acceptance-criteria.md`, розділ 14.
"""
import os
import sys

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(APP_DIR, "chat_gradio"))

import tiers  # noqa: E402


#: Значення, які СПРАВДІ є в базі стенду (перевірено запитом 29.08).
KNOWN = ["м. Житомир", "м. Сухобрід", "м. Кривоярськ", "м. Тихолісся",
         "м. Малий Ясенець", "с. Соснова Гряда", "с. Верхня Тернівка"]


def _lookup(stem):
    """Заглушка звернення в базу: замість запиту -- список KNOWN.

    Так тест не залежить від бази, але перевіряє САМЕ ту логіку, що в проді:
    кандидат зіставляється зі значеннями, а не приймається на віру.
    """
    stem = stem.lower()
    for v in KNOWN:
        if v.lower().replace("м. ", "").replace("с. ", "").startswith(stem):
            return v
    return None


# ── К3: розпізнавання у відмінку ─────────────────────────────────────────────


@pytest.mark.parametrize("question,expected", [
    ("хто ще в житомирі", "м. Житомир"),
    ("але вони саме в житомирі?", "м. Житомир"),
    ("хто у Сухоброді?", "м. Сухобрід"),
    ("є ще хтось у Кривоярську?", "м. Кривоярськ"),
    ("хто в Тихолісся", "м. Тихолісся"),
])
def test_place_is_recognised_in_any_case(question, expected):
    assert tiers.extract_place(question, lookup=_lookup) == expected


def test_the_name_comes_from_the_database_not_from_the_question():
    """К3: у відповіді назва мусить бути такою, як у базі.

    «житомирі» з малої й у місцевому відмінку не має протікати у відповідь:
    людина мусить бачити те, що записано в документі."""
    got = tiers.extract_place("хто ще в житомирі", lookup=_lookup)
    assert got == "м. Житомир", got
    assert "житомирі" not in got


# ── Ш1: не бачити місто там, де його немає ───────────────────────────────────


@pytest.mark.parametrize("question", [
    "хто у відпустці?",
    "що в черзі перевірки?",
    "хто у відрядженні зараз",
    "скільки людей у частині",
    "хто у 2 роті у відпустці?",
    "покажи документ №102",
    "яка тривалість щорічної основної відпустки?",
])
def test_no_place_where_there_is_none(question):
    assert tiers.extract_place(question, lookup=_lookup) is None, question


# ── К7 і Ш3: схоже -- не підставляємо ────────────────────────────────────────


def test_a_similar_but_absent_place_is_not_substituted():
    """К7: «Житомирська» -> не «м. Житомир».

    Тут легко зробити «зручно»: обрізати до кореня й узяти найближче. Але
    зручність тут означає відповідь про інший населений пункт, і людина цього
    не побачить."""
    assert tiers.extract_place("хто у Жмеринці?", lookup=_lookup) is None
    assert tiers.extract_place("хто у Києві?", lookup=_lookup) is None


def test_a_root_shorter_than_four_characters_is_not_matched():
    """Ш3: три літери кореня дають хибні збіги на довгих назвах."""
    assert tiers.extract_place("хто у Мал?", lookup=_lookup) is None


# ── Ш2: підрозділ важливіший за місце ────────────────────────────────────────


def test_subdivision_wins_over_place():
    """Ш2: «у 2 роті» -- це підрозділ, а не населений пункт.

    Перевіряється двома способами, і перший не потребує бази: `extract_place`
    мусить віддати None ще до будь-якого запиту -- гейт підрозділу стоїть у
    ньому першим рядком. Другий спосіб (повна дорога) вимагає бази, тому без
    неї не провалює тест, а пропускається з поясненням: те саме твердження вже
    доведено вище.
    """
    assert tiers.extract_place("Хто у 2 роті у відпустці?",
                               lookup=_lookup) is None
    try:
        route = tiers.rules_route("Хто у 2 роті у відпустці?")
    except Exception:                               # noqa: BLE001
        pytest.skip("без бази повна дорога не міряється; гейт перевірений вище")
    assert route, "правила мусять упізнати підрозділ"
    assert route[0] != "list_by_place", route


def test_the_place_rule_stands_before_the_counting_ones():
    """Порядок правил: місце -- ПЕРЕД підрахунками стану.

    Саме через цей порядок «хто ще в Житомирі» перестає падати в підрахунок по
    всій частині. Якщо правило опустити нижче, дефект 05259c повернеться, і
    повернеться тихо -- відповідь буде виглядати правильною."""
    import io
    src = io.open(os.path.join(APP_DIR, "chat_gradio", "tiers.py"),
                  encoding="utf-8").read()
    body = src[src.index("def rules_route("):]
    i_place = body.index('return "list_by_place"')
    for later in ("count_by_state_on_date", "list_by_state"):
        assert i_place < body.index(later), later


# ── К1, К2, К4: шаблони в каталозі ───────────────────────────────────────────


def test_catalog_has_a_place_template_with_a_place_param():
    import io

    import yaml
    d = yaml.safe_load(io.open(os.path.join(APP_DIR, "query_catalog.yaml"),
                               encoding="utf-8"))
    ids = {t["id"]: t for t in d["templates"]}
    assert "list_by_place" in ids, sorted(ids)
    t = ids["list_by_place"]
    assert "place" in (t.get("params") or []), t.get("params")
    #: К2: обидва види відсутності в одному зрізі.
    sql = t.get("sql") or ""
    assert "leave_place" in sql and "deployment_location" in sql, sql
    #: Чернетки окремим запитом -- як у решти підрахункових шаблонів.
    assert t.get("sql_unconfirmed"), "чернетки мусять рахуватись окремо"


def test_catalog_has_a_refusal_for_an_unknown_place():
    import io

    import yaml
    d = yaml.safe_load(io.open(os.path.join(APP_DIR, "query_catalog.yaml"),
                               encoding="utf-8"))
    ids = {t["id"]: t for t in d["templates"]}
    assert "place_unknown" in ids, sorted(ids)
    t = ids["place_unknown"]
    assert t.get("blocked") is True
    r = t.get("refusal") or ""
    #: К4: причина мусить бути названа саме та -- не «немає даних», а «немає
    #: самого пункту». Нуль тут читався б як «там нікого немає».
    assert "нуль" in r.lower(), r


# ── К5: питання-продовження ──────────────────────────────────────────────────


def test_place_is_a_carried_slot():
    """К5 і продовження розмови: «а хто ще там?» після питання про Житомир.

    Перевіряється не діалог (це живий прогін), а те, що місце внесене в
    перелік слотів. Без цього продовження не працює за побудовою."""
    from chat_gradio import app as chat_app
    assert "place" in chat_app.SLOT_KEYS, chat_app.SLOT_KEYS


# ── Ш4: перелік дозволених параметрів не розходиться з каталогом ─────────────


def test_every_catalog_param_is_allowed_in_sql():
    """КОНСТРУКЦІЙНИЙ тест, а не про місце.

    Двічі поспіль один і той самий провал: параметр оголошений у шаблоні
    (`subdivision` 25.08, `place` 29.08), але не дописаний у
    `_SQL_PARAM_NAMES`. Запит падає з «query parameter missing», виняток
    глушиться дорогою каталогу, і питання тихо їде у відмову -- тобто на екрані
    це виглядає як «чат не розуміє питання», а не як помилка коду.

    Цей тест ловить весь клас: будь-який майбутній параметр, оголошений у
    каталозі й забутий у переліку.
    """
    import io

    import yaml
    d = yaml.safe_load(io.open(os.path.join(APP_DIR, "query_catalog.yaml"),
                               encoding="utf-8"))
    declared = {p for t in d["templates"] for p in (t.get("params") or [])}
    #: `state` -- службове значення дороги, у SQL його немає ніде.
    declared.discard("state")
    missing = sorted(declared - set(tiers._SQL_PARAM_NAMES))
    assert not missing, (
        "оголошені в каталозі, але не дозволені в SQL: " + str(missing)
        + ". Саме через це «хто ще в житомирі» їхало у відмову 29.08.")


def test_place_templates_are_reachable_from_the_early_road():
    """Правило без дороги -- це правило, якого немає.

    Друга половина того самого дефекту: правила впізнавали `list_by_place`, але
    рання дорога каталогу відкрита лише для перелічених шаблонів, а нижче
    маршрут уже поставлений моделлю. Тобто шаблон був, правило було, а дійти до
    людини воно не могло."""
    from chat_gradio import app as chat_app
    for tid in ("list_by_place", "place_unknown"):
        assert tid in chat_app._STATE_TEMPLATES, tid


# ── Короткі репліки-продовження (знахідка Ані 29.08 на живому діалозі) ───────


BARE_FOLLOWUPS = ["а у житомирі", "а у житомирі?", "у Сухоброді?",
                  "а в Кривоярську", "а Тихолісся?"]

NOT_BARE_FOLLOWUPS = [
    #: Довга фраза без питального слова: місто в ній обставина, а не предмет.
    "у житомирі підписали наказ про відпустку минулого тижня",
    #: Коротка, але питає про документ, а не про людей.
    "документ у житомирі?",
    "яка норма у житомирі",
    "що в черзі у житомирі",
]


@pytest.mark.parametrize("q", BARE_FOLLOWUPS)
def test_bare_place_followup_is_recognised(q):
    """«а у житомирі» -- питання про людей, хоч слова «хто» в ньому немає.

    Знайдено Анею на живому діалозі: перша репліка «хто зараз у рівному»
    відповіла правильно (Рівного в даних немає), а «а у житомирі» впало у
    відмову «питання не лягає на жодну дорогу» -- бо мій гейт вимагав
    питального слова. Так люди й питають: попереднє питання лишається в силі,
    міняється один параметр."""
    assert tiers.is_bare_place_followup(q, lookup=_lookup), q


@pytest.mark.parametrize("q", NOT_BARE_FOLLOWUPS)
def test_a_long_or_off_topic_phrase_is_not_a_place_followup(q):
    assert not tiers.is_bare_place_followup(q, lookup=_lookup), q


def test_place_slot_is_written_by_the_catalog_road():
    """Слот, який ніхто не пише, це не пам'ять, а видимість пам'яті.

    `_slots_of_catalog` віддає порожній рядок для шаблонів без наміру, і
    `list_by_place` наміру не має (свого значення в закритій схемі моделі йому
    не заводимо перед демо). Тому пункт пишеться окремою гілкою -- і саме її
    тримає цей тест."""
    from chat_gradio import app as chat_app
    marker = chat_app._slots_of_catalog("list_by_place",
                                        {"place": "м. Житомир"})
    assert "м. Житомир" in marker, marker
    assert "slots:" in marker, marker


def test_carried_place_is_said_out_loud():
    """Успадкований пункт мусить бути НАЗВАНИЙ у відповіді.

    Те саме правило, що для дати й виміру: тиха підстановка з попереднього
    ходу -- це відповідь не на те питання без жодного слова."""
    import io
    src = io.open(os.path.join(APP_DIR, "chat_gradio", "app.py"),
                  encoding="utf-8").read()
    assert "Пункт узято з попереднього питання" in src


# ── Невідомий пункт: чесна відмова замість загальної ─────────────────────────


def test_unknown_place_is_recognised_as_a_place_question():
    """«хто зараз у рівному» -> відмова ПРО ПУНКТ, не «не лягає на жодну дорогу».

    У живому прогоні Ані правильну відмову обрав векторний ярус -- за схожістю
    з прикладом «Хто у Жмеринці?». На «у рівному» схожості не вистачило, і
    питання впало в загальну відмову. Правильність відповіді не мусить залежати
    від того, наскільки формулювання нагадує приклад."""
    assert tiers.unknown_place_candidate("хто зараз у рівному",
                                         lookup=_lookup) == "рівному"
    assert tiers.unknown_place_candidate("є хтось у Жмеринці?",
                                         lookup=_lookup) == "Жмеринці"


@pytest.mark.parametrize("q", [
    #: Стан у питанні -> це підрахунок, а не пункт.
    "хто у відпустці зараз",
    "скільки людей у відрядженні",
    #: Дата -> теж підрахунок.
    "хто повертається у травні",
    #: Підрозділ.
    "хто у 2 роті",
    #: Документ і норма.
    "покажи документ №102",
    "яка норма у Житомирі",
    #: Відомий пункт -- це не «невідомий».
    "хто ще в житомирі",
    #: Немає питального слова -- нема чого забирати.
    "у рівному підписали наказ",
])
def test_unknown_place_does_not_steal_other_questions(q):
    """ЦЕНА ПОМИЛКИ ТУТ -- украдене питання, тому межі суворі."""
    assert tiers.unknown_place_candidate(q, lookup=_lookup) is None, q


# ── «а у рівному»: коротка форма про НЕВІДОМИЙ пункт (Аня 29.08) ─────────────


def test_short_followup_shape_is_recognised():
    """Одне питання двома формами мусить давати ОДНАКОВУ відповідь.

    На живому діалозі було так: «хто у рівному» -> правильна відмова про пункт,
    «а у рівному» -> загальна відмова «не лягає на жодну дорогу». Коротша форма
    давала гіршу відповідь на те саме питання."""
    for q in ("а у рівному", "а у Жмеринці?", "а в Києві"):
        assert tiers.is_place_followup_shape(q), q


@pytest.mark.parametrize("q", [
    #: День тижня -- не пункт. Найлегший промах короткої форми.
    "а у понеділок?",
    #: Занадто коротке слово.
    "а у нас?",
    #: Стан, підрозділ, документ -- усе це інші питання.
    "а у відпустці?",
    "а у 2 роті?",
    "а документ №5?",
    #: Довга фраза: там місто майже завжди обставина.
    "а у рівному скільки людей у відпустці зараз загалом",
])
def test_short_followup_shape_does_not_steal(q):
    """Ціна помилки тут вища за звичайну: гілка відповідає ВІДМОВОЮ про пункт,
    тобто вкрадене питання отримає відмову, а не просто іншу відповідь."""
    assert not tiers.is_place_followup_shape(q), q


def test_unknown_place_followup_needs_the_previous_turn():
    """Гілка спирається на КОНТЕКСТ, а не на саме слово.

    Для невідомого пункту звірити слово з базою неможливо за визначенням, тому
    без попереднього ходу про пункт коротка форма нічого не забирає. Тест
    тримає саму умову в коді."""
    import io
    src = io.open(os.path.join(APP_DIR, "chat_gradio", "app.py"),
                  encoding="utf-8").read()
    block = src[src.index("«А У РІВНОМУ»"):]
    block = block[:block.index("Швидкий шлях ПЕРЕД моделлю")]
    assert '_prev.get("place")' in block, block[:400]
    assert "is_place_followup_shape" in block
    assert "place_unknown" in block
