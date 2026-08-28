# -*- coding: utf-8 -*-
"""Теми нормативного корпусу: перелік тем фіксований, зіставлення автоматичне.

Критерії приймання -- `docs/tasks/2026-08-27_acceptance-criteria.md`, розділ 9.
Тести написані ДО коду (вимога Ані).

Головне рішення, яке ці тести охороняють: **тема не дописується документу**.
Руками пишеться лише перелік тем; кожен документ зіставляється з ним за
словами з номера, назви й початку тексту. Тому корпус може рости без правок,
а «інше» показується вголос і служить сигналом «пора додати тему».

Заперечення Ані, через яке дизайн змінився: «нормативні доки ж будуть
додаватися, тому ти не можеш до них завжди дописувати назви».
"""
import io
import os
import re
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
for p in (APP_DIR, os.path.join(APP_DIR, "chat_gradio")):
    if p not in sys.path:
        sys.path.insert(0, p)

from chat_gradio import normative_topics as nt   # noqa: E402
from chat_gradio import tiers as tier_chat       # noqa: E402

TOPICS_YAML = os.path.join(APP_DIR, "normative_topics.yaml")


# ── Файл тем: форма й повнота ────────────────────────────────────────────────


def test_topics_file_exists_and_parses():
    assert os.path.exists(TOPICS_YAML), "перелік тем мусить бути у файлі"
    data = yaml.safe_load(io.open(TOPICS_YAML, encoding="utf-8").read())
    assert data.get("topics"), "у файлі немає тем"


def test_every_topic_has_title_match_and_example():
    for t in nt.load():
        assert t["title"].strip(), t
        assert t["match"].strip(), t
        # Приклад питання -- частина теми, а не окрема таблиця: тема без
        # приклада не відповідає на «а що можна питати».
        assert t["ask"], f"тема без приклада питання: {t['title']}"
        for q in t["ask"]:
            assert len(q) > 10, q


def test_match_patterns_compile():
    for t in nt.load():
        re.compile(t["match"], re.IGNORECASE)


# ── Зіставлення: автоматичне, багатотемне, без ручних міток ──────────────────


@pytest.mark.parametrize("ident,title,expect", [
    ("№ 550-XIV", "Про Статут гарнізонної та вартової служб Збройних Сил "
                  "України", "статути"),
    ("№ 2297-VI", "Про захист персональних даних", "інформац"),
    ("№ 2262-XII", "Про пенсійне забезпечення осіб, звільнених з військової "
                   "служби", "соціальн"),
    ("наказ № 402", "Про затвердження Положення про військово-лікарську "
                    "експертизу в Збройних Силах України", "медичн"),
    ("наказ № 333", "Про затвердження Інструкції з організації обліку "
                    "особового складу", "облік"),
    ("№ 2657-XII", "Про інформацію", "інформац"),
])
def test_topic_is_derived_from_the_document(ident, title, expect):
    """Тема виводиться з номера й назви -- нічого не підписано руками."""
    got = nt.topics_of(ident, title, "")
    assert got, f"{title} -- жодної теми"
    assert any(expect in g.lower() for g in got), (title, got)


def test_a_document_may_belong_to_several_topics():
    """Для питання «про що можна питати» кілька тем краще за одну."""
    got = nt.topics_of(
        "наказ № 280",
        "Про затвердження Інструкції з організації обліку особового складу "
        "та порядку надання відпусток", "")
    assert len(got) >= 2, got


def test_unknown_document_falls_into_other_not_into_a_wrong_topic():
    """Чесне «інше» краще за вгадану тему -- те саме правило, що «відмова
    краща за вигадку»."""
    assert nt.topics_of("№ 1", "Про бджільництво", "") == []


# ── Рендер відповіді ─────────────────────────────────────────────────────────


ROWS = [
    {"doc_identifier": "№ 550-XIV",
     "doc_title": "Про Статут гарнізонної та вартової служб Збройних Сил "
                  "України", "validity": "current", "head": ""},
    {"doc_identifier": "№ 550-XIV",
     "doc_title": "Про Статут гарнізонної та вартової служб Збройних Сил "
                  "України", "validity": "current", "head": ""},
    {"doc_identifier": "№ 2297-VI", "doc_title": "Про захист персональних "
     "даних", "validity": "current", "head": ""},
    {"doc_identifier": None, "doc_title": None, "validity": "current",
     "head": "МІНІСТЕРСТВО ОБОРОНИ УКРАЇНИ\nНАКАЗ"},
    {"doc_identifier": "№ 9", "doc_title": "Про бджільництво",
     "validity": "current", "head": ""},
]


def test_render_says_both_numbers():
    """К4: 44 записи -- не 44 різних акти. Дублі не подаються як різні."""
    text = "\n".join(nt.render(ROWS))
    assert "5" in text and "4" in text, text
    assert "різних" in text or "унікальн" in text


def test_render_has_no_raw_table_header():
    """К3: інвентаризація -- не відповідь."""
    text = "\n".join(nt.render(ROWS))
    assert "uploaded_on" not in text
    assert "validity |" not in text


def test_render_never_shows_the_first_line_of_text_as_a_title():
    """К1: саме це побачила Аня -- «МІНІСТЕРСТВО ОБОРОНИ УКРАЇНИ» як назва."""
    text = "\n".join(nt.render(ROWS))
    assert "МІНІСТЕРСТВО ОБОРОНИ УКРАЇНИ" not in text
    assert "НОРМАТИВНИЙ ДОКУМЕНТ" not in text


def test_render_names_other_with_a_number():
    """К5: документ без теми не ховається."""
    text = "\n".join(nt.render(ROWS))
    assert re.search(r"інше\D{0,40}1|1\D{0,20}інше", text), text


def test_render_has_no_examples_of_questions():
    """Рішення Ані 28.08: приклади в самій відповіді зайві -- вони переїхали в
    готові кнопки на екрані вітання, де під рукою завжди, а не лише тоді, коли
    людина вже спитала про склад корпусу."""
    text = "\n".join(nt.render(ROWS))
    assert "приклад" not in text.lower()


def test_render_stays_short():
    """К9: 3977 -> 641 -> і не росте назад."""
    text = "\n".join(nt.render(ROWS))
    assert len(text) <= 1200, len(text)


def test_render_does_not_print_document_text():
    """Ш5: початок тексту читається ЛИШЕ для зіставлення."""
    text = "\n".join(nt.render(ROWS))
    assert "НАКАЗ" not in text


# ── Ш2-Ш4: гейт корпусу не забирає чужі питання ──────────────────────────────


@pytest.mark.parametrize("q", [
    "а на які теми вони",
    "на які теми ці документи?",
    "про що ці нормативні документи",
])
def test_topic_question_is_a_corpus_question(q):
    assert tier_chat.is_corpus_question(q), q


@pytest.mark.parametrize("q", [
    # ЗМІСТОВІ нормативні питання -- не про склад корпусу. Якщо гейт їх
    # забере, людина отримає перелік тем на питання про норму, тобто дефект
    # блоку 7 повернеться з іншого боку.
    "Яка процедура оформлення відпустки?",
    "Який максимальний термін щорічної відпустки?",
    "Хто видає доступ до службової мережі?",
    "Скільком триває базова військова служба?",
    # Питання про людей -- тим паче.
    "Хто у відпустці 2026-08-29?",
    "Хто зараз у відрядженні?",
    "Де Ґоляш Богодар Святославович?",
])
def test_content_questions_are_not_corpus_questions(q):
    assert not tier_chat.is_corpus_question(q), q


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
def test_share_of_other_is_below_the_threshold():
    """К7: зіставлення автоматичне, і його якість -- число, а не смак.

    Поріг живе у файлі тем разом із переліком: якщо корпус виріс і нові
    документи не лягли ні в одну тему, падає ЦЕЙ тест, а не тихо росте
    «інше» у відповіді."""
    rows = nt.fetch_rows()
    assert rows, "у базі немає нормативних документів"
    other = [r for r in rows
             if not nt.topics_of(r["doc_identifier"], r["doc_title"],
                                 r.get("head") or "")]
    share = len(other) / len(rows)
    limit = nt.max_other_share()
    assert share <= limit, (
        f"без теми {len(other)} із {len(rows)} = {share:.0%} > {limit:.0%}; "
        f"додайте тему в normative_topics.yaml: "
        f"{[(r['doc_identifier'], r['doc_title']) for r in other][:5]}")


@db_only
def test_every_example_question_has_an_answer_in_the_corpus():
    """К6: приклад питання без відповіді -- гірше за відсутність приклада.

    Перевіряється не ланцюгом (він потребує моделі), а тим, що слова питання
    справді трапляються в корпусі: FTS по text_content. Це слабша перевірка,
    ніж «ланцюг знайшов місце», і вона названа слабшою навмисно -- сильну
    робить живий прогін."""
    with tier_chat._connect() as conn, conn.cursor() as cur:
        for t in nt.load():
            for q in t["ask"]:
                cur.execute(
                    "SELECT count(*) AS n FROM documents "
                    " WHERE domain = 'normative' "
                    "   AND to_tsvector('simple', COALESCE(text_content, '')) "
                    "       @@ websearch_to_tsquery('simple', %s)", (q,))
                assert cur.fetchone()["n"] > 0, (
                    f"приклад питання не має відповіді в корпусі: {q!r} "
                    f"(тема «{t['title']}»)")


# ── К8: маршрут питання про теми ─────────────────────────────────────────────
#
# Перша версія вела «на які теми» тим самим гейтом, що «що є в базі», і
# продовження розмови отримало СКЛАД БАЗИ по домейнах. Краще за цитату закону,
# але так само не відповідь -- тому гейти розділені.


@pytest.mark.parametrize("q", [
    "а на які теми вони",
    "на які теми ці документи?",
    "про що ці нормативні документи",
])
def test_topic_question_routes_to_topics(q):
    assert tier_chat.is_topic_question(q), q
    routed = tier_chat.rules_route(q)
    assert routed and routed[0] == "normative_list", (q, routed)


@pytest.mark.parametrize("q,tid", [
    ("Який період покривають документи в базі?", "documents_count"),
    ("Скільком документів у базі?", "documents_count"),
])
def test_corpus_composition_still_goes_to_composition(q, tid):
    """Ш4: розділення гейтів не мусило відібрати питання про склад бази."""
    routed = tier_chat.rules_route(q)
    assert routed and routed[0] == tid, (q, routed)


def test_named_document_is_not_a_topic_question():
    """«На яку тему наказ №118?» -- питання про документ, не про корпус."""
    assert not tier_chat.is_topic_question("На яку тему наказ №118?")


# ── Прилад не мусить міряти власні тести ─────────────────────────────────────


def test_tests_do_not_write_into_the_production_trace():
    """Знахідка 28.08: після прогону набору на сервері `trace_lookup --check`
    сказав «ЗЛАМАНО: 2 без джерела, 4 збої» -- і це були самі тести.

    Тест тримає розділення: слід тестів мусить лежати НЕ там, де бойовий.
    Інакше прилад, яким приймається робота, міряє власний набір."""
    from chat_gradio import trace
    assert "logs" not in trace.TRACE_PATH.replace("\\", "/").split("/")[:-1], (
        trace.TRACE_PATH)
    assert "tests" in os.path.basename(trace.TRACE_PATH), trace.TRACE_PATH


# ── Готові кнопки на екрані вітання ──────────────────────────────────────────


def test_chips_exist():
    #: Одна, не три: Аня 28.08 скоротила готові кнопки з дев'яти до шести, і
    #: нормативна дорога представлена однією -- решта п'ять показують різні
    #: види підрахунку.
    assert len(nt.chips()) == 1, nt.chips()
    for q in nt.chips():
        assert q.endswith("?"), q


def test_every_chip_is_a_known_example():
    """Кнопка мусить бути серед `ask` якоїсь теми -- інакше вона розійдеться з
    перевіркою «приклад має відповідь у корпусі» і поведе в порожнечу.

    Рівно на цьому ми вже горіли: попередні зашиті кнопки питали про травень,
    якого в базі немає."""
    known = {q.strip().rstrip("?") for t in nt.load() for q in t["ask"]}
    for q in nt.chips():
        assert q.rstrip("?") in known, (q, sorted(known))


def test_chips_are_in_the_chat_buttons():
    from chat_gradio import app as chat_app
    examples = chat_app.example_questions()
    for q in nt.chips():
        assert q in examples, (q, examples)


def test_buttons_split_into_rows_of_three():
    """Шість кнопок -- два повні рядки по три.

    Тримає не саме число, а ділимість: сітка в CSS має три колонки, і кнопка,
    що не добирає рядок, лишає на екрані дірку. Тобто додавати їх можна лише
    по три."""
    from chat_gradio import app as chat_app
    n = len(chat_app.example_questions())
    assert n == 6, f"кнопок {n}, а Аня просила шість"
    assert n % 3 == 0, f"кнопок {n} -- рядок по три не складеться"


def test_buttons_cover_different_kinds_of_answer():
    """Шість обрані за ВИДОМ підрахунку, а не за темою (вимога Ані: «візьми
    різні за типом обрахунку, типами доків, найбільш різноманітні»).

    Перевіряється на тому наборі, який реально видно, і одне місце -- за кодом.
    Причина: четверта кнопка -- це СЛОТ, у який підставляється справжній номер
    документа з бази (`examples[3] = ...`). Без бази там лишається питання
    «скільки непідтверджених фактів», і в цьому немає біди: відкатний набір
    показується лише тоді, коли база лежить, а тоді не працює жодна кнопка --
    вони всі підрахункові. Тобто «безпечний» зашитий номер там нічого не
    рятує, а зіпсуватись може (рівно на цьому ми горіли з «5 травня»).
    """
    from chat_gradio import app as chat_app
    qs = [q.lower() for q in chat_app.example_questions()]
    joined = " | ".join(qs)
    #: Дороги, які не залежать від бази й мусять бути представлені завжди.
    assert any("відпустц" in q and "скільки" in q for q in qs), joined
    assert any(q.startswith("хто") for q in qs), joined
    assert any("підрозділ" in q for q in qs), joined
    assert any("черз" in q for q in qs), joined
    assert any("тривалість" in q for q in qs), joined

    #: Слот документа: або він уже заповнений із бази, або в коді стоїть рядок,
    #: який його заповнить. Одне з двох мусить бути правдою.
    src = io.open(os.path.join(APP_DIR, "chat_gradio", "app.py"),
                  encoding="utf-8").read()
    filled = any("документ" in q for q in qs)
    assert filled or 'examples[3] = f"Покажи документ' in src, joined

    #: І жодна дорога не з'їдає більше половини набору. З живою базою «скільки»
    #: лишається двоє; без бази -- троє, і це межа.
    assert sum(q.startswith("скільки") for q in qs) <= 3, joined
    if filled:
        assert sum(q.startswith("скільки") for q in qs) <= 2, joined


@pytest.mark.parametrize("n,want", [
    (1, "1 акт"), (2, "2 акти"), (4, "4 акти"), (5, "5 актів"),
    (11, "11 актів"), (12, "12 актів"), (14, "14 актів"),
    (21, "21 акт"), (22, "22 акти"), (25, "25 актів"),
])
def test_acts_plural(n, want):
    """У живій відповіді з'явилось «4 акт(и)». Дужки в тексті для людини --
    видима недороблена робота, і відповідь чата читає не програміст."""
    assert nt._acts(n) == want


def test_render_has_no_parenthesised_endings():
    text = "\n".join(nt.render(ROWS))
    assert "(и)" not in text and "(ів)" not in text, text


def test_render_does_not_claim_documents_were_loaded_twice():
    """Формулювання перевіряється окремо, бо перше було НЕПРАВДОЮ.

    Я написала «залиті двічі». Перевірка показала інше: файли різні за хешем
    (тобто дедуплікація за файлом відпрацювала правильно), а в парі наказу
    № 606 копії відрізняються переліком змін -- одна згадує наказ № 398 від
    04.11.2020, друга ні. Це різні РЕДАКЦІЇ того самого акта, і тримати обидві
    законно. Незаконно інше -- не знати, яка чинна, і саме це ми й кажемо.
    """
    text = "\n".join(nt.render(ROWS))
    assert "залиті двічі" not in text, "недоведене твердження про дублікати"
    assert "двох версіях" in text
    assert "не встановлено" in text


def test_hero_cards_are_one_grid_not_three_rows():
    """Зауваження Ані 28.08 про рівність карток.

    Три окремі `gr.Row` вирівняти між собою неможливо: висоту ряду задає
    найдовша кнопка в ньому, а ряди нічого одне про одного не знають. Тест
    тримає саме конструкцію -- один контейнер плюс сітка в CSS."""
    src = io.open(os.path.join(APP_DIR, "chat_gradio", "app.py"),
                  encoding="utf-8").read()
    block = src.split('elem_id="hero-cards"', 1)[1][:400]
    assert "hero_btns = [gr.Button(q) for q in EXAMPLES]" in block, block[:200]
    assert src.count('elem_id="hero-cards"') == 1, "рядів мусить бути один"

    css = io.open(os.path.join(APP_DIR, "chat_gradio", "theme-v3.css"),
                  encoding="utf-8").read()
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in css
    assert "align-items: stretch" in css
