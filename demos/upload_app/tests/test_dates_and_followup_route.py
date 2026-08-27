# -*- coding: utf-8 -*-
"""Блоки A і B харнесу: дата/період і пам'ять наступного ходу.

Що закривають ці тести — п. 1–5 звіту Дениса 27.08. Розбір причин і критерії
приймання: `docs/tasks/2026-08-27_harness-research-plan.md` і
`docs/tasks/2026-08-27_acceptance-criteria.md`.

Основа — патч Андрія (`docs/contracts/patches/2026-08-27_chat-followups-and-dates.diff`),
зміряний ним окремо по кожній зміні: дві дати = період 4/8 → 8/8; історія до
маршрутизатора 8/21 → 18/21; каталог і формулювання промпту +2…3 і додаються.

**Чого тут НЕМА і це свідомо.** Число 18/21 перевіряється не тестом, а живим
прогоном на моделі: `llama.cpp` при `temperature=0` не відтворюваний (заміряно
Андрієм), тому один прогін не є числом, а тест на моделі був би або повільним,
або брехливим. Тут перевіряється рівно те, що детерміноване: розбір дат, склад
блока розмови, схема маршрутизатора й те, що службовий текст не витікає людині.

Запуск:
    python -m pytest demos/upload_app/tests/test_dates_and_followup_route.py -q
"""
import datetime

import pytest

from demos.upload_app.chat_gradio import app as chat_app

tiers = chat_app.tier_chat

ROSTER = ["1-ша механізована рота", "2-га механізована рота",
          "3-тя механізована рота", "Взвод забезпечення"]


@pytest.fixture(autouse=True)
def roster(monkeypatch):
    monkeypatch.setattr(tiers, "subdivisions", lambda: ROSTER)


# ── Блок A. Дві дати -- це період, а не перша з них ─────────────────────────

def test_two_dates_are_a_period_not_the_first_one():
    """п. 4 звіту: «з 2026-05-10 по 2026-10-10» ставало одним днем -- сім
    разів із семи. Причина була не в моделі: `re.search` брав перший збіг."""
    on_date, date_from, date_to = tiers.extract_dates(
        "Хто був у відрядженні з 2026-05-10 по 2026-10-10?")
    assert on_date is None
    assert (date_from, date_to) == (datetime.date(2026, 5, 10),
                                    datetime.date(2026, 10, 10))


def test_period_survives_the_route_not_just_the_parser():
    """Головне в цьому блоці: період мусить дійти до ПАРАМЕТРІВ шаблону.

    Розбір дат можна полагодити й не помітити, що склейка нижче згортає період
    назад в один день -- рівно це й було: `on_date` від моделі підставлявся в
    обидві межі."""
    tid, params = tiers.rules_route(
        "Хто був у відрядженні з 2026-05-10 по 2026-10-10?")
    assert tid == "list_by_state"
    assert params["date_from"] == datetime.date(2026, 5, 10)
    assert params["date_to"] == datetime.date(2026, 10, 10)


def test_next_day_after_a_date_is_the_next_day():
    """п. 2: «наступного дня після 2026-10-10» давало 10-те."""
    on_date, _, _ = tiers.extract_dates(
        "Скільком осіб у відпустці наступного дня після 2026-10-10?")
    assert on_date == datetime.date(2026, 10, 11)


def test_next_day_without_a_date_is_not_guessed():
    """«Наступного дня» без дати -- нема звідки взяти, і «сьогодні+1» був би
    вгадуванням. Порожньо краще за вгадану дату."""
    on_date, date_from, date_to = tiers.extract_dates(
        "Скільком осіб у відпустці наступного дня?")
    assert on_date is None and date_from is None and date_to is None


def test_not_later_than_is_an_upper_bound_not_a_point():
    """п. 3: «закінчується не пізніше 2026-10-20» читалось як «саме 20-го»."""
    on_date, date_from, date_to = tiers.extract_dates(
        "А чия відпустка закінчується не пізніше 2026-10-20?")
    assert on_date is None, "верхня межа -- не точка зрізу"
    assert date_to == datetime.date(2026, 10, 20)
    assert date_from is None


def test_not_earlier_than_is_a_lower_bound():
    on_date, date_from, date_to = tiers.extract_dates(
        "Чия відпустка починається не раніше 2026-10-01?")
    assert on_date is None and date_to is None
    assert date_from == datetime.date(2026, 10, 1)


# ── Блок A. Перевернутий період: не розвертаємо, але НАЗИВАЄМО ─────────────

def test_inverted_period_is_kept_as_written():
    """п. 5: кінець раніше за початок. Межі НЕ міняємо -- поміняти мовчки
    означало б відповісти на інше питання й не сказати про це."""
    _, date_from, date_to = tiers.extract_dates(
        "Скільком осіб у відпустці з 2026-10-01 по 2026-09-01?")
    assert date_from == datetime.date(2026, 10, 1)
    assert date_to == datetime.date(2026, 9, 1)
    assert date_from > date_to, "розворот стався мовчки -- саме цього не можна"


def test_inverted_period_is_named_out_loud():
    """І друга половина п. 5, без якої перша безглузда: факт мусить бути
    сказаний. Інакше нуль читається як «нікого не було»."""
    lines = tiers._inverted_period_lines(
        {"date_from": datetime.date(2026, 10, 1),
         "date_to": datetime.date(2026, 9, 1)})
    assert lines, "перевернутий період не названий"
    text = " ".join(lines)
    assert "⚠️" in text
    assert "нуль" in text.lower()


def test_normal_period_gets_no_warning():
    """Побічна шкода: попередження не має з'являтись на звичайному періоді."""
    assert tiers._inverted_period_lines(
        {"date_from": datetime.date(2026, 9, 1),
         "date_to": datetime.date(2026, 10, 1)}) == []
    assert tiers._inverted_period_lines({"date_from": None,
                                         "date_to": None}) == []


# ── Блок B. Розмова доходить до маршрутизатора ─────────────────────────────

HISTORY = [
    {"role": "user", "content": "Скільком осіб у відпустці 2026-10-10?"},
    {"role": "assistant", "content": "1 особа, зріз на 2026-10-10"},
    {"role": "user", "content": "А у відрядженні?"},
]


def test_history_block_has_only_human_turns():
    """Формат навмисно бідний: відповіді чата додають токенів і шуму, а
    заміряно (8/21 -> 18/21) було саме це."""
    block = tiers._history_block(HISTORY)
    assert "Скільком осіб у відпустці 2026-10-10?" in block
    assert "А у відрядженні?" in block
    assert "1 особа" not in block, "у блок потрапила відповідь чата"
    assert block.startswith("Розмова:")


def test_history_block_is_empty_without_history():
    """Перший хід не має отримувати ні порожнього заголовка, ні «Розмова:»."""
    assert tiers._history_block(None) == ""
    assert tiers._history_block([]) == ""
    assert tiers._history_block([{"role": "assistant", "content": "х"}]) == ""


def test_history_block_reads_gradio_multimodal_content():
    """Gradio 6 віддає content списком навіть для звичайного тексту. Без
    розпаковки історія мовчки зникає -- уже раз ловили."""
    block = tiers._history_block([
        {"role": "user",
         "content": [{"type": "text", "text": "Скільком у відпустці?"}]}])
    assert "Скільком у відпустці?" in block


def test_model_route_accepts_history_and_stays_compatible():
    """Підпис має лишитись сумісним: історія -- необов'язкова."""
    import inspect
    sig = inspect.signature(tiers.model_route)
    assert list(sig.parameters) == ["question", "history"]
    assert sig.parameters["history"].default is None
    assert inspect.signature(
        chat_app._model_catalog_tier).parameters["history"].default is None


def test_route_schema_declares_subdivision_and_carried_over():
    """`subdivision` у схемі не було ВЗАГАЛІ -- тому «а в першій роті?» не мало
    чим виразитись (друга половина п. 1)."""
    props = tiers.ROUTE_SCHEMA["properties"]
    assert "subdivision" in props
    assert "carried_over" in props
    assert "subdivision" in tiers.ROUTE_SCHEMA["required"]
    # Форма carried_over ОБМЕЖЕНА: у Андрія поле пливло чотирма формами
    # (назва+значення, дві назви, «назва=значення»), і користуватись ним було
    # неможливо. Тут -- рівно перелік назв параметрів.
    enum = props["carried_over"]["items"]["enum"]
    assert "on_date" in enum and "state" in enum
    assert all("=" not in v for v in enum)


# ── Блок B. Успадковане мусить бути ВИДНИМ ────────────────────────────────

def test_carried_parameters_are_shown_to_the_person():
    """Правило продукту, не зручність: тихо взяти дату з попереднього питання
    й відповісти впевнено -- це відповідь не на те питання без жодного слова.

    Перевіряється рендер: технічні імена (`on_date`) людині не показуються --
    це був би п. 25 звіту, внутрішня кухня назовні."""
    assert "on_date" in chat_app.CARRIED_LABEL
    label = chat_app.CARRIED_LABEL["on_date"]
    assert label == "дата", label
    assert all("_" not in v for v in chat_app.CARRIED_LABEL.values())


def test_carried_key_never_reaches_sql():
    """Службовий ключ `_carried` не має доходити до параметрів запиту."""
    sql_params, _ = tiers._sql_params(
        "count_by_state_on_date",
        {"dims": ["leave"], "on_date": datetime.date(2026, 10, 10),
         "_carried": ["on_date"]})
    assert "_carried" not in sql_params
    assert set(sql_params) == {"dims", "on_date"}


# ── Каталог: підказка моделі не витікає людині ─────────────────────────────

def test_route_hints_go_to_the_prompt_but_not_to_the_person():
    """Андрій зміряв +2 точності шаблонів, вписавши уточнення в `title`. Але
    `title` друкується людині в блоці «джерело» -- тобто приріст купувався б
    за ціну службового тексту на екрані. Тому окреме поле `route_hint`."""
    prompt = tiers._catalog_lines()
    assert "date_from і date_to однакові" in prompt
    assert "інакше list_by_state" in prompt
    assert "route_hint" not in prompt, "назва поля потрапила в промпт"
    # А заголовки, які бачить людина, лишились чистими
    for tid in ("list_by_state", "doc_by_number",
                "list_by_state_in_subdivision"):
        title = tiers._CATALOG[tid]["title"]
        assert ";" not in title, (tid, title)
        assert "date_from" not in title, (tid, title)


def test_route_md_tells_the_model_about_the_conversation():
    """Формулювання зміряне: «ЗАПОВНИ поле» проти «перелічи в carried_over».
    Друге модель робила дослівно -- клала значення в carried_over, а поле
    лишала порожнім."""
    system = tiers._route_system()
    assert system is not None
    assert "РОЗМОВУ" in system
    assert "ЗАПОВНИ" in system
    assert "НАЗВИ" in system


def test_history_reaches_model_route_through_the_real_call_path(monkeypatch):
    """ПЕРЕВІРКА НА ПОБІЧНУ ШКОДУ, і вона тут не теоретична.

    Тести вище перевіряють ПІДПИСИ, і цього виявилось мало: пробрасуючи
    історію, я передала її в `_model_catalog_tier` із `_extra_tiers`, у якого
    параметра `history` не було зовсім. Виходив `NameError` на будь-якому
    питанні, що доходило до модельного яруса, -- тобто чат падав. Зловив це
    лише повний прогін набору (`test_unknown_domain_gate`), і саме тому
    загальна перевірка після кожного блоку не скорочується.

    Тест іде РЕАЛЬНИМ шляхом виклику й дивиться, що дійшло до маршрутизатора.
    """
    seen = {}

    def fake_route(question, history=None):
        seen["question"] = question
        seen["history"] = history
        return None            # далі яруси не потрібні

    monkeypatch.setattr(tiers, "model_route", fake_route)
    monkeypatch.setattr(chat_app, "model_available", lambda: True)
    monkeypatch.setattr(tiers, "_get_model", lambda: object())

    history = [{"role": "user", "content": "Скільком у відпустці 2026-10-10?"},
               {"role": "assistant", "content": "1 особа"}]
    chat_app._extra_tiers("а хто?", history)

    assert seen.get("history") == history, (
        "історія не дійшла до маршрутизатора реальним шляхом виклику")
