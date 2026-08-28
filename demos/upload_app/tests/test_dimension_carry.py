# -*- coding: utf-8 -*-
"""Вимір (відпустка/відрядження) переноситься між ходами, і поправка до
попереднього ходу не їде в нормативний пошук.

Живий прогін Ані 28.08, три ходи:

  1. «Хто зараз у відрядженні?»      -> 1 особа, вимір deployment. Правильно.
  2. «а завтра?»                     -> 13 осіб «поза частиною (відпустка або
     відрядження)». Дата успадкувалась, ВИМІР ТИХО РОЗШИРИВСЯ. Людина читає
     це як «за добу стало 13 у відрядженні» -- підміна метрики без жодного
     слова про підміну.
  3. «я питала про відрядження»      -> нормативний пошук, 82.9 с, цитата про
     відрядження до державних органів. Поправка до питання поїхала шукати
     закон, бо слово «відрядження» чинне і в нормативному корпусі.

Причини (обидві в харнесі, не в моделі):

  * у `SLOT_KEYS` не було `state`: дата між ходами переносилась, вимір -- ні;
  * поправка не мала власного наміру й дати, тому жодна дорога підрахунку її
    не брала, і її забирав каталог.

Тести дивляться на ТЕКСТ відповіді (метрику в заголовку), а не на внутрішні
структури: підміна метрики -- це саме те, що бачить людина.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
for p in (APP_DIR, os.path.join(APP_DIR, "chat_gradio")):
    if p not in sys.path:
        sys.path.insert(0, p)

from chat_gradio import app as ca          # noqa: E402
from chat_gradio import tiers as tier_chat  # noqa: E402


# ── Одиниця: вимір є слотом і переноситься ──────────────────────────────────


def test_state_is_a_slot():
    """Без цього переносити нечого: слот мусить існувати і зберігатись."""
    assert "state" in ca.SLOT_KEYS
    assert "state" in ca.INTENT_SLOTS["хто_відсутній"]
    marker = ca._state_marker({"intent": "хто_відсутній", "date": "2026-08-28",
                               "state": "deployment"})
    assert '"state": "deployment"' in marker


def test_rules_params_reads_state_from_question():
    assert ca.rules_params("Хто зараз у відрядженні?")["state"] == "deployment"
    assert ca.rules_params("Хто у відпустці 2026-08-29?")["state"] == "leave"
    # Питання без назви виміру слот не заповнює -- інакше перенесення
    # затиралося б порожнім значенням наступного ходу.
    assert ca.rules_params("а завтра?")["state"] is None


def test_carry_over_keeps_the_dimension():
    """Ядро дефекту 2 із живого прогону, без бази й моделі."""
    prev = {"intent": "хто_відсутній", "date": "2026-08-28",
            "subdivision": None, "name": None, "doc_number": None,
            "state": "deployment"}
    fixed = ca._carry_over(ca.rules_params("а завтра?"), prev)
    assert fixed["intent"] == "хто_відсутній"
    assert fixed["state"] == "deployment", (
        "вимір не успадкувався -- відповідь розширить метрику до обох")


def test_named_dimension_beats_the_carried_one():
    """Поправка мусить бити успадковане, інакше її неможливо застосувати."""
    prev = {"intent": "хто_відсутній", "date": "2026-08-28",
            "state": "deployment", "subdivision": None, "name": None,
            "doc_number": None}
    fixed = ca._carry_over(ca.rules_params("а хто у відпустці?"), prev)
    assert fixed["state"] == "leave"


# ── Одиниця: поправка впізнається, а звичайні питання -- ні ─────────────────


@pytest.mark.parametrize("q", [
    "я питала про відрядження",
    "я питав про відпустку",
    "мене цікавить відрядження",
    "я мала на увазі відпустку",
    "ні, про відрядження",
    "мова про відпустку",
    "йшлося про відрядження",
])
def test_correction_forms(q):
    assert tier_chat.is_correction(q), q


@pytest.mark.parametrize("q", [
    "Хто зараз у відрядженні?",
    "а завтра?",
    # ГОЛОВНИЙ запобіжник: це НЕ поправка, а питання про норму. Якби широке
    # правило «репліка з назвою виміру» лишилось, воно забрало б це питання в
    # підрахунок людей -- рівно дефект блоку E, який уже закритий.
    "скільки максмум термін щорічної відпустки?",
    "Які документи про відрядження є в базі?",
    "покажи документ №207",
])
def test_not_a_correction(q):
    assert not tier_chat.is_correction(q), q


# ── Наскрізь по базі: три ходи живого прогону ────────────────────────────────


def _db_reachable():
    """Пропуск за СПРАВЖНЬОЮ доступністю бази, а не за вгаданими іменами
    змінних. Перша версія перевіряла `DB_DSN`/`PGHOST` -- на сервері, де база
    жива, обидва тести МОВЧКИ пропускались, і я майже прийняла це як «зелено».
    Той самий клас помилки, що з `CHAT_MODEL_PATH`: прилад міряв себе."""
    try:
        with tier_chat._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            return True
    except Exception:
        return False


pytestmark_db = pytest.mark.skipif(not _db_reachable(),
                                   reason="потрібна жива база")


@pytestmark_db
def test_three_turns_keep_the_dimension():
    """Той самий діалог, що в живому прогоні. Модель не потрібна: усі три
    ходи мусять пройти детерміновано."""
    q1 = "Хто зараз у відрядженні?"
    a1 = ca.answer(q1)
    assert "у відрядженні" in a1
    assert "поза частиною" not in a1

    hist = [{"role": "user", "content": q1},
            {"role": "assistant", "content": a1}]
    a2 = ca.answer("а завтра?", hist)
    assert "поза частиною" not in a2, (
        "вимір розширився до обох -- підміна метрики між ходами")
    assert "у відрядженні" in a2
    assert "Вимір узято з попереднього питання" in a2, (
        "успадкований вимір мусить бути видним")


@pytestmark_db
def test_correction_recounts_instead_of_searching_law():
    q1 = "Хто у відпустці 2026-08-29?"
    a1 = ca.answer(q1)
    hist = [{"role": "user", "content": q1},
            {"role": "assistant", "content": a1}]
    a2 = ca.answer("я питала про відрядження", hist)
    assert "дорога: підрахунок" in a2, "поправка поїхала не в підрахунок"
    assert "у відрядженні" in a2
    # Регістр не фіксуємо: `_as_report` дописує «Доповідаю:» і опускає першу
    # літеру речення. Перевіряємо саме твердження, а не його оформлення.
    assert "ерерахувала попереднє питання на 2026-08-29" in a2


# ── Дорога каталогу мусить лишати слоти по собі ──────────────────────────────


def test_catalog_publishes_slots():
    """Відсутня ланка з живого прогону: діалог починався на каталозі, і
    наступний хід не мав ЧОГО переносити взагалі."""
    marker = ca._slots_of_catalog("list_by_state",
                                  {"state": "deployment",
                                   "on_date": "2026-08-28",
                                   "subdivision": None})
    assert '"state": "deployment"' in marker
    assert '"intent": "хто_відсутній"' in marker
    assert '"date": "2026-08-28"' in marker


def test_catalog_slots_are_readable_back():
    """Запис і читання мусять сходитись -- інакше маркер є, а перенесення нема."""
    text = "Доповідаю: 1 особа." + ca._slots_of_catalog(
        "list_by_state", {"state": "deployment", "on_date": "2026-08-28"})
    prev = ca._read_state([{"role": "assistant", "content": text}])
    assert prev and prev["state"] == "deployment"
    fixed = ca._carry_over(ca.rules_params("а завтра?"), prev)
    assert fixed["state"] == "deployment"
    assert fixed["intent"] == "хто_відсутній"


def test_unmapped_template_writes_nothing():
    """Наміру не вигадуємо: шаблон без відповідника лишає порожньо."""
    assert ca._slots_of_catalog("normative_list", {"query": "щось"}) == ""


# ── Регресія, яку я сама внесла разом зі слотами каталогу ────────────────────
#
# Доти зсув «а наступного дня» робила МОДЕЛЬ: слотів після дороги каталогу не
# було, дата лишалась порожньою, хід ішов у гілку уточнення й там його рятував
# маршрутизатор із розмовою. Коли каталог почав лишати слоти, дата перестала
# бути порожньою -- і перенесення підставляло ТОЙ САМИЙ день. Тобто правка
# пам'яті зламала відносні дати. Зловили живі траси (B4), не набір.


@pytest.mark.parametrize("q,prev,want", [
    ("а наступного дня", "2026-10-10", "2026-10-11"),
    ("а на наступний день?", "2026-10-10", "2026-10-11"),
    ("через 2 дні", "2026-10-10", "2026-10-12"),
    ("через 10 днів", "2026-09-25", "2026-10-05"),
    ("а попереднього дня", "2026-10-10", "2026-10-09"),
    ("днем раніше", "2026-10-01", "2026-09-30"),
    # Місяць мусить перевалювати правильно, а не впиратись у 31-е.
    ("а наступного дня", "2026-08-31", "2026-09-01"),
])
def test_shift_from_prev(q, prev, want):
    assert ca._shift_from_prev(q, prev) == want


@pytest.mark.parametrize("q", [
    "а завтра?",            # це від СЬОГОДНІ, і його вміє extract_date
    "а по 2 роті?",
    "а 22?",
    "Хто у відпустці 2026-10-11?",
])
def test_no_shift_where_there_is_none(q):
    assert ca._shift_from_prev(q, "2026-10-10") is None


def test_unresolved_relative_form_does_not_carry_the_date():
    """Страховка: форму не порахували -- дату НЕ переносимо буквально.

    Інакше «а наступного тижня» тихо відповідає за той самий день. Порожня
    дата віддає хід ярусу, що читає розмову, -- і це правильна ціна."""
    assert ca._RELATIVE_DATE_HINT.search("а наступного тижня")
    assert ca._shift_from_prev("а наступного тижня", "2026-10-10") is None
    # А там, де форма порахована, страховка не потрібна й не спрацьовує.
    assert ca._shift_from_prev("а наступного дня", "2026-10-10") == "2026-10-11"


@pytestmark_db
def test_next_day_still_shifts_after_catalog_answer():
    """Той самий хід, що зловили живі траси B4."""
    q1 = "Скільком у відпустці 2026-10-10?"
    a1 = ca.answer(q1)
    hist = [{"role": "user", "content": q1},
            {"role": "assistant", "content": a1}]
    a2 = ca.answer("а наступного дня", hist)
    # Дивимось на ЗРІЗ, а не на присутність рядка «2026-10-10» у тексті: межа
    # покриття бази (2026-05-10 — 2026-10-10) законно згадується в примітці,
    # і перша версія цього тесту падала саме на ній -- тобто перевіряла не те.
    assert "зріз: 2026-10-11" in a2 or "на 2026-10-11" in a2, (
        "зсув на день не відбувся -- дата перенеслась буквально")
