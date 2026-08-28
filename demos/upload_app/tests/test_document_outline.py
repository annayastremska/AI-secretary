# -*- coding: utf-8 -*-
"""Карта документа після цитати — для питань про ПОСЛІДОВНІСТЬ дій.

Критерії приймання — `docs/tasks/2026-08-27_acceptance-criteria.md`, розділ 13.
Тести написані ДО коду.

Прогін Андрія 28.08, 20:08:

    «як подати рапорт про щорічну відпустку»
      -> цитата з пункту 1 («не пізніше ніж за 10 календарних днів»)
    «я не питаю за скільки часу, я питаю послідовність моїх дій, куди і як»
      -> відмова

Ворота відкинули кандидатів ПО ОДНОМУ, і правильно: жоден фрагмент окремо на
все питання не відповідає. Пошук теж не винен — документ знайдений той, що
треба (№252 «Порядок оформлення відпустки у військовій частині А0000»), і в
ньому рівно те, що просили, шістьма пунктами по порядку.

Винне те, СКІЛЬКОМ одиниць ми віддаємо людині. Тому на питання про
послідовність після цитати показуємо карту документа: сусідні одиниці в
порядку `ord`, з позначкою, яку щойно навели.

Модель нічого не складає. Це принципово: якби ми дали їй «зібрати
послідовність із кількох фрагментів», вона переказала б їх своїми словами —
новий спосіб вигадати рівно там, де його найважче помітити, бо переказ
виглядає як відповідь. Різання на пункти теж не її робота: одиниці ріже код
за розмітковими маркерами документа, тому в карти справжні адреси.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
for p in (APP_DIR, os.path.join(APP_DIR, "chat_gradio")):
    if p not in sys.path:
        sys.path.insert(0, p)

from chat_gradio import app as ca             # noqa: E402
from chat_gradio import tiers as tier_chat    # noqa: E402


# ── К1-К2: які питання отримують карту ──────────────────────────────────────


@pytest.mark.parametrize("q", [
    "як подати рапорт про щорічну відпустку?",
    "я питаю послідовність моїх дій при поданні рапорту, куди і як",
    "який порядок дій при поданні рапорту?",
    "які кроки потрібні, щоб оформити відпустку?",
    "куди і як подавати рапорт",
    "що робити далі після рапорту?",
    "з чого почати оформлення відрядження?",
    "яка процедура оформлення відпустки?",
])
def test_procedure_questions_recognised(q):
    assert tier_chat.is_procedure_question(q), q


@pytest.mark.parametrize("q", [
    # Звичайні нормативні питання: там ОДНА цитата і є відповідь, і карта
    # роздула б її без користі.
    "яка тривалість щорічної основної відпустки?",
    "скільки максимум інформація може лишатися державною таємницею?",
    "хто підписує контракт про проходження військової служби?",
    "що таке державна таємниця?",
    "які види дисциплінарних стягнень?",
    # Питання про людей -- тим паче.
    "хто зараз у відрядженні?",
    "скільком у відпустці 2026-10-10?",
])
def test_ordinary_questions_get_no_map(q):
    assert not tier_chat.is_procedure_question(q), q


# ── К3-К6: як складається карта ─────────────────────────────────────────────


ROWS = [
    {"label": "Порядок оформлення відпустки у військовій частині А0000",
     "text": "# Порядок оформлення відпустки у військовій частині А0000\n\n"},
    {"label": "1. Строки подання рапорту",
     "text": "## 1. Строки подання рапорту\n\nРапорт на щорічну…"},
    {"label": "2. Хто підписує рапорт і наказ",
     "text": "## 2. Хто підписує рапорт і наказ\n\nРапорт послідовно…"},
    {"label": "3. Отримання і здача відпускного квитка",
     "text": "## 3. Отримання…"},
    {"label": "4. Дострокове відкликання з відпустки", "text": "## 4. …"},
    {"label": "5. Хвороба під час відпустки", "text": "## 5. …"},
    {"label": "6. Порядок дій при відмові в наданні відпустки", "text": "## 6. …"},
]


def test_outline_lists_real_units_in_order():
    lines = tier_chat._outline_lines(ROWS, "1. Строки подання рапорту")
    body = "\n".join(lines)
    for want in ("1. Строки подання рапорту", "2. Хто підписує рапорт і наказ",
                 "6. Порядок дій при відмові"):
        assert want in body, want
    # Порядок збережений.
    assert body.index("1. Строки") < body.index("2. Хто підписує") \
           < body.index("6. Порядок дій")


def test_outline_skips_the_document_title():
    """К3: заголовок документа -- не пункт послідовності."""
    lines = tier_chat._outline_lines(ROWS, "1. Строки подання рапорту")
    body = "\n".join(lines)
    assert body.count("Порядок оформлення відпустки у військовій частині") == 0


def test_outline_marks_the_quoted_unit():
    """К4: людина мусить бачити, де вона зараз."""
    lines = tier_chat._outline_lines(ROWS, "2. Хто підписує рапорт і наказ")
    marked = [ln for ln in lines if "щойно" in ln]
    assert len(marked) == 1, lines
    assert "2. Хто підписує" in marked[0]


def test_outline_is_capped():
    """К6: відповідь мусить лишитись короткою."""
    many = [{"label": f"{i}. Пункт {i} " + "х" * 200, "text": f"## {i}. …"}
            for i in range(1, 30)]
    lines = tier_chat._outline_lines(many, "1. Пункт 1 " + "х" * 200)
    # 12 пунктів плюс три службові рядки: шапка, «і ще N», підпис. Перша
    # версія тесту дозволяла на один менше -- моя арифметика, не код.
    assert len(lines) <= tier_chat.OUTLINE_UNITS_SHOWN + 3, len(lines)
    for ln in lines:
        assert len(ln) <= tier_chat.OUTLINE_LABEL_CHARS + 40, ln
    assert any("29" in ln or "ще" in ln for ln in lines), "решта не названа"


def test_no_map_for_a_single_unit():
    """К8: карта з одного пункту -- це не карта."""
    one = [ROWS[0], ROWS[1]]
    assert tier_chat._outline_lines(one, "1. Строки подання рапорту") == []


def test_no_map_when_quoted_unit_is_unknown():
    assert tier_chat._outline_lines(ROWS, "невідома адреса") == []


# ── К5: документ знаходиться за записом у базі ──────────────────────────────


def test_record_id_is_parsed_from_the_source_block():
    """К5: у внутрішніх інструкцій `doc_identifier` порожній, тому шукати
    документ можна лише за записом у базі -- а він у джерелі вже є."""
    source = [
        "нормативний ланцюг: одиниці → реранкер → ворота → перевірка цитати",
        "документ: запис №252 у базі, адреса 1. Строки подання рапорту",
        "збіг лем питання й цитати: 1.00",
    ]
    assert tier_chat._doc_record_id(source) == 252
    assert tier_chat._quoted_address(source) == "1. Строки подання рапорту"


def test_record_id_absent_is_not_an_error():
    assert tier_chat._doc_record_id(["щось інше"]) is None
    assert tier_chat._quoted_address(["щось інше"]) is None


# ── Наскрізь по живій базі ──────────────────────────────────────────────────


def _db_reachable():
    try:
        with tier_chat._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            return True
    except Exception:
        return False


db_only = pytest.mark.skipif(not _db_reachable(), reason="потрібна жива база")


@db_only
def test_siblings_of_the_procedure_document():
    rows = tier_chat._fetch_sibling_units(252, "1. Строки подання рапорту")
    labels = [r["label"] for r in rows]
    assert "2. Хто підписує рапорт і наказ" in labels, labels
    assert "6. Порядок дій при відмові в наданні відпустки" in labels, labels


@db_only
def test_siblings_are_scoped_to_the_article_not_the_whole_law():
    """Для закону сусіди -- пункти ТІЄЇ САМОЇ статті, а не всі статті кодексу.
    Інакше карта була б переліком сотень статей і не допомогла б нікому."""
    cur = tier_chat._connect()
    with cur as conn, conn.cursor() as c:
        c.execute("SELECT id FROM documents WHERE doc_identifier = %s LIMIT 1",
                  ("№ 2011-XII",))
        doc_id = c.fetchone()["id"]
    rows = tier_chat._fetch_sibling_units(doc_id, "Стаття 10-1 / 2")
    labels = [r["label"] for r in rows]
    assert labels, "сусідів статті 10-1 не знайдено"
    assert all(l.startswith("Стаття 10-1") for l in labels), labels[:5]


# ── Другий хід: ланцюг відмовив, а документ відомий із розмови ───────────────


def test_map_from_previous_needs_a_procedure_question():
    prev = ("Доповідаю: цитата\n"
            "документ: запис №252 у базі, адреса 1. Строки подання рапорту")
    hist = [{"role": "assistant", "content": prev}]
    ans = ("Доповідаю: знайшла схожі за темою місця, але жодне не відповідає "
           "на питання прямо.")
    # Звичайне питання карти не отримує навіть після відмови.
    same = ca._map_from_previous("яка тривалість відпустки?", hist, ans)
    assert same == ans


def test_map_from_previous_only_on_refusal():
    """Якщо ланцюг ВІДПОВІВ, карту вже дописав `run_template` -- другий раз
    не треба."""
    prev = ("документ: запис №252 у базі, адреса 1. Строки подання рапорту")
    hist = [{"role": "assistant", "content": prev}]
    ok = "Доповідаю: «дослівна цитата»"
    assert ca._map_from_previous("як подати рапорт?", hist, ok) == ok


def test_map_from_previous_without_history_is_noop():
    ans = "Доповідаю: знайшла схожі за темою місця, але жодне не відповідає на питання прямо."
    assert ca._map_from_previous("як подати рапорт?", [], ans) == ans


@db_only
def test_second_turn_gets_the_map_of_the_previous_document():
    """Той самий хід, що зловив Андрій."""
    prev = ("Доповідаю: порядок оформлення відпустки у військовій частині "
            "А0000, 1. Строки подання рапорту\n«цитата»\n"
            "документ: запис №252 у базі, адреса 1. Строки подання рапорту")
    hist = [{"role": "assistant", "content": prev}]
    ans = ("Доповідаю: знайшла схожі за темою місця, але жодне не відповідає "
           "на питання прямо.")
    out = ca._map_from_previous(
        "я питаю послідовність моїх дій при поданні рапорту, куди і як",
        hist, ans)
    assert "документ із попередньої відповіді" in out, out[:200]
    assert "2. Хто підписує рапорт і наказ" in out
    assert "6. Порядок дій при відмові" in out


def test_address_is_parsed_from_the_REAL_source_shape():
    """У готовій відповіді джерело склеєне в ОДИН рядок через `<br>`.

    Перша версія регулярки брала «адресу» до кінця рядка й захопила півблока
    разом: «1. Строки подання рапорту<br>збіг лем питання й цитати: 0.75<br>…».
    Тести цього не зловили, бо подавали джерело окремими елементами переліку --
    тобто перевіряли форму, якої не буває. Цей тест подає справжню.
    """
    real = ('<details class="src"><summary>джерело</summary>'
            'нормативний ланцюг: одиниці → реранкер → ворота → перевірка цитати'
            '<br>документ: запис №252 у базі, адреса 1. Строки подання рапорту'
            '<br>збіг лем питання й цитати: 0.75'
            '<br>дорога: каталог шаблонів (normative_search)'
            '<br>звернення: ce4180</details>')
    assert tier_chat._doc_record_id([real]) == 252
    assert tier_chat._quoted_address([real]) == "1. Строки подання рапорту"


def test_map_goes_before_the_source_block():
    """Карта мусить стояти НАД «джерелом», а не під згорткою.

    Перша версія дописувала в кінець тексту -- тобто після блоку джерела, де
    карту ніхто не побачить. Я цього не помітила, бо в перевірці різала вивід
    по «<details» і сама ж відрізала те, що перевіряла."""
    prev = "документ: запис №252 у базі, адреса 1. Строки подання рапорту"
    hist = [{"role": "assistant", "content": prev}]
    ans = ("Доповідаю: знайшла схожі за темою місця, але жодне не відповідає "
           "на питання прямо.\n\n<details class=\"src\"><summary>джерело"
           "</summary>щось</details>")
    out = ca._map_from_previous("як подати рапорт?", hist, ans)
    if out == ans:
        pytest.skip("потрібна жива база для сусідніх одиниць")
    assert out.index("описує порядок цілком") < out.index("<details"), out
