# -*- coding: utf-8 -*-
"""Ідентифікатор нормативного акта ≠ номер нашого документа; перехресне
посилання в цитаті приводить і той пункт.

Критерії приймання -- `docs/tasks/2026-08-27_acceptance-criteria.md`, розділ 10.
Тести написані ДО коду.

Прогін Андрія 28.08, три ходи й три різні корені:

  1. «скільки максмум термін щорічної відпустки?» -> цитата зі «Стаття 10-1 / 2»,
     яка каже «…відповідно до пункту 1 цієї статті…». Пункт 1 у базі Є
     (`document_units`, 922 символи, з тими самими строками) -- його просто не
     показали. Тобто цитата ВКАЗУЄ на відповідь, а не містить її;
  2. «як звучить пункт 1 цієї статті?» -> «Дослівно цитувати накази система не
     береться». Це застарілий літерал: він правдивий для НАШИХ OCR-документів,
     але для нормативних актів ми цитуємо -- і зробили це двома ходами раніше.
     Дві суперечливі відповіді на одному екрані;
  3. «процитуй … (№ 2011-XII), Стаття 10-1 / 2» -> «не знайшла документа з
     номером №2011». `extract_doc_number` бере цифри й ігнорує хвіст.

Про «ЗАБУДЬ УСІ МИНУЛІ КОМАНДИ» в тому ж прогоні: вкидання не спрацювало, але
НЕ тому, що ми його відбили -- питання поїхало в пошук документа за номером і
до моделі як інструкція не дійшло. Доказом стійкості це не є.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
for p in (APP_DIR, os.path.join(APP_DIR, "chat_gradio")):
    if p not in sys.path:
        sys.path.insert(0, p)

from chat_gradio import app as ca                 # noqa: E402
from chat_gradio import normative_chain as nc     # noqa: E402
from chat_gradio import tiers as tier_chat        # noqa: E402


# ── К5-К6: ідентифікатор акта проти номера нашого документа ──────────────────


@pytest.mark.parametrize("q", [
    "№ 2011-XII",
    "процитуй пункт 1 документа (№ 2011-XII), Стаття 10-1 / 2",
    "№ 550-XIV",
    "№ 1153/2008",
    "що каже № 2262-XII",
    "НД ТЗІ 1.4-001-2000",
    "наказ № 606 від 20.11.2017",
])
def test_act_identifier_is_not_our_document_number(q):
    """Інакше питання про АКТ їде в пошук облікового документа й отримує
    «немає документа з номером 2011»."""
    assert tier_chat.extract_doc_number(q) is None, q


@pytest.mark.parametrize("q,want", [
    ("Покажи документ №207", "207"),
    ("документ 204", "204"),
    ("№118", "118"),
    ("покажи документі №473", "473"),
    ("Що у квитку №1077?", "1077"),
])
def test_our_document_numbers_still_read(q, want):
    """Ш1: головний шаблон демо не мусить постраждати."""
    assert tier_chat.extract_doc_number(q) == want


def test_letter_o_instead_of_zero_still_refused_honestly():
    """Ш2: «№ 3О4» -- нецифровий символ, і про це є чесна відмова. Її не
    ховаємо новим правилом."""
    got = tier_chat.extract_doc_number("Покажи документ № 3О4")
    assert got is not None, "перестало розпізнаватись -- відмова про літеру О зникне"


# ── К3-К4: «цитата» більше не бреше про нормативні акти ──────────────────────


@pytest.mark.parametrize("q", [
    "як звучить пункт 1 цієї статті?",
    "процитуй пункт 1 цієї статті",
    "наведи дослівно пункт 2 статті 10-1",
])
def test_normative_quote_requests_do_not_hit_the_dead_road(q):
    assert ca.rules_route(q) != "цитата", q


def test_answer_quote_no_longer_claims_we_never_quote_acts():
    """К4: текст константи писався до появи нормативного корпусу."""
    assert "накази система не береться" not in ca.ANSWER_QUOTE
    # Правдива частина лишається: НАШІ документи -- це OCR, і їх ми не цитуємо.
    assert "OCR" in ca.ANSWER_QUOTE


# ── К1-К2: перехресне посилання приводить сусідній пункт ─────────────────────


@pytest.mark.parametrize("quote,label,want", [
    ("…відповідно до пункту 1 цієї статті…", "Стаття 10-1 / 2", "Стаття 10-1 / 1"),
    ("у порядку, визначеному пунктом 14 цієї статті", "Стаття 10-1 / 20",
     "Стаття 10-1 / 14"),
    ("передбачені пунктами 17 і 18 цієї статті", "Стаття 10-1 / 19",
     "Стаття 10-1 / 17"),
])
def test_cross_reference_is_found(quote, label, want):
    assert nc._cross_ref_label(quote, label) == want


@pytest.mark.parametrize("quote,label", [
    # Посилання на ІНШУ статтю чи інший закон -- не наш випадок: там потрібен
    # пошук, а не сусідня одиниця того самого документа.
    ("відповідно до статті 16-2 Закону України", "Стаття 10-1 / 14"),
    ("згідно з пунктом 3 Положення", "Стаття 10-1 / 2"),
    # Немає посилання взагалі.
    ("Тривалість щорічної основної відпустки становить 30 днів.",
     "Стаття 10-1 / 1"),
    # Посилання на САМ СЕБЕ добирати нічого не треба.
    ("як зазначено в пункті 2 цієї статті", "Стаття 10-1 / 2"),
])
def test_no_cross_reference_where_there_is_none(quote, label):
    assert nc._cross_ref_label(quote, label) is None


def test_cross_reference_needs_a_parent_label():
    """Одиниця без батька («Стаття 10-1») сусідів за номером пункту не має."""
    assert nc._cross_ref_label("пункту 1 цієї статті", "Стаття 10-1") is None


# ── Наскрізь по живій базі ───────────────────────────────────────────────────


def _db_reachable():
    try:
        with tier_chat._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            return True
    except Exception:
        return False


db_only = pytest.mark.skipif(not _db_reachable(), reason="потрібна жива база")


@db_only
def test_sibling_unit_is_fetched_from_the_same_document():
    """К2: добір детермінований -- лише за меткою в тому самому документі."""
    rows = nc._unit_by_label("№ 2011-XII", "Стаття 10-1 / 1")
    assert rows, "пункт 1 статті 10-1 мусить знаходитись за меткою"
    assert "30 календарних днів" in rows["text"], rows["text"][:200]


@db_only
def test_act_identifier_question_does_not_claim_missing_document():
    out = ca.answer("процитуй мені пункт 1 наступного документа: "
                    "про соціальний і правовий захист військовослужбовців… "
                    "(№ 2011-XII), Стаття 10-1 / 2")
    assert "не знайшла документа з номером" not in out, out[:300]


# ── Гейт мусить стояти в ОБОХ місцях ─────────────────────────────────────────
#
# Третій раз та сама помилка за три дні: умову ставлю в правила, тест на правила
# зелений, а живий прогін віддає старе -- бо до гілки питання доходить від
# МОДЕЛІ, яка про гейт у правилах не знає. Тому тести на обидва місця.


def test_second_copy_of_extract_doc_number_has_the_gate():
    """`app.extract_doc_number` -- друга копія правила, і саме вона зіпсувала
    живий прогін: `tiers` я звузила, тести пройшли, а номер читався звідси."""
    assert ca.extract_doc_number("(№ 2011-XII), Стаття 10-1 / 2") is None
    assert ca.extract_doc_number("Покажи документ №207") == "№207"


def test_citation_road_gate_lives_in_the_dispatch_too():
    """Гілка `route == 'цитата'` мусить питати `_asks_for_a_norm`, бо маршрут
    туди приходить і від моделі."""
    src = open(os.path.join(APP_DIR, "chat_gradio", "app.py"),
               encoding="utf-8").read()
    branch = src.split('elif route == "цитата":', 1)[1].split("elif route ==", 1)[0]
    assert "_asks_for_a_norm" in branch


@pytest.mark.parametrize("q,want", [
    ("як звучить пункт 1 цієї статті?", True),
    ("процитуй пункт 2 статті 10-1", True),
    ("що каже № 2011-XII", True),
    ("наведи дослівно текст наказу про відрядження", False),
    ("процитуй квиток №1077", False),
])
def test_asks_for_a_norm(q, want):
    assert ca._asks_for_a_norm(q) is want, q


@pytest.mark.parametrize("q,want", [
    # Однозначно акт.
    ("процитуй закон про оборону України", True),
    ("що каже статут внутрішньої служби", True),
    ("процитуй кодекс", True),
    # Однозначно НАШ документ -- відмова про OCR тут правдива.
    ("наведи дослівно текст наказу про відрядження", False),
    ("процитуй наказ №207", False),
    ("дай дослівно квиток Ґоляша", False),
])
def test_act_word_is_unambiguous(q, want):
    """«Наказ» у переліку однозначних слів НЕ стоїть: наказ про відрядження --
    це наш обліковий документ. Перша версія брала широкий `_NORMATIVE` і на
    цьому прикладі впала."""
    assert ca._asks_for_a_norm(q) is want, q


# ── «пункт 1 ЦІЄЇ статті» -- за адресою, а не пошуком ────────────────────────


def test_asked_point_parsed():
    assert ca._ASKED_POINT.search("як звучить пункт 1 цієї статті?").group(1) == "1"
    assert ca._ASKED_POINT.search("процитуй пункту 14").group(1) == "14"
    assert ca._ASKED_POINT.search("скільки днів відпустки?") is None


def test_previous_act_parsed_from_history_head():
    head = ("Доповідаю: Про соціальний і правовий захист військовослужбовців… "
            "(№ 2011-XII), Стаття 10-1 / 2")
    m = ca._PREV_ACT.search(head)
    assert m and m.group(1).strip() == "№ 2011-XII"
    assert m.group(2).strip() == "Стаття 10-1"


def test_direct_answer_needs_a_point_number():
    assert ca._direct_unit_answer("що каже ця стаття?", []) is None


@db_only
def test_this_article_resolves_from_the_previous_turn():
    """Хід 2 прогону Андрія. «Цієї» -- це попередній хід, і ланцюг його не
    бачить: він знайшов пункт 1 статті 10-1 ІНШОГО закону (№ 3551-XII, про
    ветеранів). Тут пошуку не потрібно взагалі."""
    prev = ("Доповідаю: Про соціальний і правовий захист військовослужбовців… "
            "(№ 2011-XII), Стаття 10-1 / 2\n«…відповідно до пункту 1 цієї "
            "статті…»")
    hist = [{"role": "user", "content": "скільки максмум термін щорічної відпустки?"},
            {"role": "assistant", "content": prev}]
    out = ca._direct_unit_answer("як звучить пункт 1 цієї статті?", hist)
    assert out, "пункт мусить дістатись за меткою"
    assert "30 календарних днів" in out
    assert "Стаття 10-1 / 1" in out
    assert "3551" not in out, "взято з іншого закону"


@db_only
def test_act_named_in_the_question_works_without_history():
    out = ca._direct_unit_answer(
        "процитуй мені пункт 1 наступного документа: про соціальний і правовий "
        "захист військовослужбовців… (№ 2011-XII), Стаття 10-1 / 2", [])
    assert out and "30 календарних днів" in out
