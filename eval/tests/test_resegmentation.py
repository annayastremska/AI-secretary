# -*- coding: utf-8 -*-
"""Тести на відновлення меж полів за порожнім бланком (A3, known-weak-spots 2.11a).

ЩО ЗА ДЕФЕКТ. Surya групує не абзаци, а області зображення, тому весь корпус
відпускного квитка приходить ОДНИМ блоком, де значення й друковані лейбли
склеєні без роздільника:

    "звільнена(військове звання...)щорічна основна відпустка за 2026
     рік(вид відпустки та найменування населеного пункту,м. Житомирдо якого
     звільнено військовослужбовця)терміном натринадцять(кількість днів..."

Наслідок заміряний: `місце` 0/6 (лейбл склеєний у слово "Житомирдо", тому
пошук із межею слова його не бачить), `днів` 2/6 (regex вимагає пробіл у
"терміном на"), `військова_частина`/`підстава`/`ПІБ`/`звання` -- по 2-3 з 6.

ЩО ЛІКУЄ. `blank_form.resegment_by_blank` ріже такий блок по друкованих
рядках самого бланка (`blank_template:` у схемі) і повертає ту саму
послідовність "лейбл / значення", яку docx-шлях має з абзаців безкоштовно.

Тести читають СПРАВЖНІЙ порожній шаблон, а не свій список фраз -- з тієї
самої причини, що й test_blank_form.py: перелік друкованих фраз ніхто не веде
руками, і тест зі своїм переліком перевіряв би не той механізм.

Моделі не потребують: різання детерміноване, вхід -- заміряні рядки OCR.

    python -m pytest eval/tests/test_resegmentation.py -q
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from pipeline.extraction.blank_form import (
    printed_lines,
    printed_order,
    printed_after_label,
    resegment_by_blank,
    resegment_text,
    printed_cutters,
)
from pipeline.extraction.extract import (
    extract_document,
    find_block_before_label,
    group_blocks_into_lines,
    validate_block_value,
)
from pipeline.identification import load_schemas

SCHEMAS_DIR = os.path.join(_PROJECT_ROOT, "pipeline", "schemas")

# Дослівний блок Surya з LEAVE-001.png (дамп 14.08.2026). Один блок висотою
# 908 px на десять полів бланка; "\n" усередині -- це <br> від Surya, а не
# межа поля, і саме тому вони стоять не там, де межі.
SURYA_MEGA_BLOCK = (
    "звільнена(військове звання, прізвище, ім'я та по батькові)"
    "щорічна основна відпустка за 2026 рік"
    "(вид відпустки та найменування населеного пункту,\n"
    "м. Житомирдо якого звільнено військовослужбовця)терміном натринадцять"
    "(кількість днів прописом)з \"10\" травня 2026 р. по \"22\" травня 2026 р."
    "Після закінчення строку відпусткирядовий ЛЕМЕШКО С.Р."
    "(військове звання, прізвище та\n"
    "ініціализобов'язана прибути до місця служби увійськова частина А0000"
    "(найменування військової частини або населеного пункту)"
    "\"23\" травня 2026 р.\n"
    "(дата повернення)Для проїзду видано військові перевізні документи за №8144/26"
)


def _schema(template="leave_ticket"):
    return next(s for s in load_schemas(SCHEMAS_DIR) if s["template"] == template)


def _blocks(schema, *texts):
    """Блоки у форматі, який віддає ingest для фото (bbox обов'язковий)."""
    return [{"text": t, "bbox": (0, 100 * n, 1000, 100 * n + 40), "page": 0}
            for n, t in enumerate(texts)]


def test_glued_block_is_cut_into_form_paragraphs():
    """Головний тест: злитий блок Surya стає тією самою послідовністю
    "лейбл / значення", що й абзаци docx."""
    pieces = resegment_text(SURYA_MEGA_BLOCK, printed_cutters(_schema()))
    assert "м. Житомир" in pieces
    assert "до якого звільнено військовослужбовця)" in pieces
    assert "щорічна основна відпустка за 2026 рік" in pieces
    assert "тринадцять" in pieces
    assert "військова частина А0000" in pieces


def test_label_becomes_findable_again():
    """`місце` = 0/6 на фото тому, що лейбл склеєний з попереднім значенням у
    слово "Житомирдо", і пошук із межею слова його не знаходить узагалі."""
    schema = _schema()
    label = "до якого звільнено військовослужбовця"

    before = group_blocks_into_lines(_blocks(schema, SURYA_MEGA_BLOCK))
    assert find_block_before_label(before, label) == (None, "no_label")

    blocks, changed = resegment_by_blank(_blocks(schema, SURYA_MEGA_BLOCK), schema)
    assert changed == 1
    value, reason = find_block_before_label(
        group_blocks_into_lines(blocks), label,
        printed=printed_lines(schema), order=printed_order(schema))
    assert (value, reason) == ("м. Житомир", "matched")


def test_regex_field_needs_the_rebuilt_text():
    """`днів` ламався не на блоках, а на тексті: схемний патерн вимагає
    "терміном\\s+на\\s+", а OCR віддає "терміном натринадцять". Тому
    extract_document перебудовує текст із розрізаних блоків."""
    schema = _schema()
    result = extract_document(schema, SURYA_MEGA_BLOCK,
                              _blocks(schema, SURYA_MEGA_BLOCK), {})
    assert result["duration_days"][0] == "тринадцять"
    assert result["destination_place"] == ("м. Житомир", "matched")
    assert result["unit_to_report"] == ("А0000", "matched")


def test_gender_form_does_not_defeat_the_cutter():
    """Бланк друкує чоловічий рід, документ несе рід військовослужбовця.
    Без послаблення закінчень "зобов'язана прибути до місця служби у"
    лишалась би приклеєною до номера частини."""
    pieces = resegment_text(
        "ініціализобов'язана прибути до місця служби увійськова частина А0000"
        "(найменування військової частини або населеного пункту)",
        printed_cutters(_schema()))
    assert "військова частина А0000" in pieces


def test_aligned_block_is_returned_untouched():
    """Інваріант docx: блок, у якому друкований рядок і так стоїть окремим
    рядком, НЕ ріжеться -- і повертається тим самим об'єктом. Саме ця умова
    лишає 176/176 і 140/140 недоторканими."""
    schema = _schema()
    original = ["звільнений", "(вид відпустки та найменування населеного пункту,",
                "м. Львів", "до якого звільнено військовослужбовця)"]
    blocks, changed = resegment_by_blank(list(original), schema)
    assert changed == 0
    assert blocks == original
    assert resegment_text("м. Львів\n(дата повернення)", printed_cutters(schema)) == []


def test_orphan_paren_is_trimmed_only_when_unbalanced():
    """Лейбл `місце` -- дужка НАВКОЛО значення, тому після відрізання обох
    половин у значенні лишається осиротіла ")" (заміряно на LEAVE-005/009/010).
    Значення з ВЛАСНИМИ парними дужками чіпати не можна."""
    cutters = printed_cutters(_schema())
    pieces = resegment_text(
        "(вид відпустки та найменування населеного пункту,м. Полтава)"
        "до якого звільнено військовослужбовця)", cutters)
    assert "м. Полтава" in pieces
    pieces = resegment_text(
        "(вид відпустки та найменування населеного пункту,"
        "щорічна додаткова відпустка (за особливий характер служби)"
        "до якого звільнено військовослужбовця)", cutters)
    assert "щорічна додаткова відпустка (за особливий характер служби)" in pieces


def test_reordered_printed_block_is_skipped_but_only_forward():
    """OCR читає бланк по областях, тому рядок "терміном на" (надрукований
    НИЖЧЕ лейбла) опиняється між значенням і лейблом -- заміряно на
    LEAVE-003/004/007. Такий блок пропускається.

    Друкований рядок, що на бланку стоїть ПЕРЕД лейблом, НЕ пропускається:
    саме він законно стоїть впритул, коли поле порожнє, і пропустивши його,
    ми віддали б значення ПОПЕРЕДНЬОГО поля як своє."""
    order = printed_order(_schema())
    label = "до якого звільнено військовослужбовця"
    assert printed_after_label("терміном на",
                               _label_index(label, order), order)
    assert not printed_after_label("(вид відпустки та найменування населеного пункту,",
                                   _label_index(label, order), order)


def _label_index(label, order):
    from pipeline.extraction.blank_form import label_order_index
    return label_order_index(label, order)


def test_adjacent_printed_lines_are_put_back_into_blank_order():
    """OCR читає бланк по областях, тому "терміном на" (на бланку НИЖЧЕ
    лейбла `місце`) приходить ВИЩЕ нього -- заміряно на 8 з 16 фото. Обмін
    двох СУСІДНІХ друкованих рядків повертає розкладку бланка, і схемний
    патерн `терміном\\s+на\\s+(пропис)` знову збігається."""
    pieces = resegment_text(
        "(вид відпустки та найменування населеного пункту,м. Рівнетерміном на"
        "до якого звільнено військовослужбовця)десять(кількість днів прописом)",
        printed_cutters(_schema()))
    assert pieces.index("до якого звільнено військовослужбовця)") \
        < pieces.index("терміном на") < pieces.index("десять")
    assert pieces.index("м. Рівне") < pieces.index("до якого звільнено військовослужбовця)")


def test_reordering_never_moves_a_value():
    """Обмін дозволений ЛИШЕ для двох сусідніх ДРУКОВАНИХ шматків: між ними
    нічого немає, тому жодне значення зсунутись не може. Сортувати весь
    список за номерами бланка не можна -- у значень номера немає."""
    from pipeline.extraction.blank_form import _reorder_printed
    before = [("значення", None), ("лейбл Б", 9), ("лейбл А", 4), ("інше значення", None)]
    after = _reorder_printed(before)
    assert [p for p, _n in after] == ["значення", "лейбл А", "лейбл Б", "інше значення"]
    # Значення між двома переставленими лейблами -> обміну немає взагалі.
    keep = [("лейбл Б", 9), ("значення", None), ("лейбл А", 4)]
    assert _reorder_printed(keep) == keep


def test_touching_bboxes_do_not_hide_the_value_above_the_label():
    """Рамки сусідніх рядків OCR постійно торкаються на 1-2 px. Строге
    "кандидат цілком вище лейбла" викидало правильного кандидата з розгляду
    ВЗАГАЛІ (LEAVE-006.png: ПІБ на два пікселі заходив на свій лейбл), і
    "найближчим зверху" ставав номер документа рядком вище."""
    blocks = group_blocks_into_lines([
        {"text": "№ 124 від 13.05.2026", "bbox": (726, 601, 1116, 643), "page": 0},
        {"text": "старший сержант ВЛОХ Святослав Олесьович",
         "bbox": (584, 643, 1260, 685), "page": 0},
        {"text": "(військове звання, прізвище, ім'я та по батькові)",
         "bbox": (571, 683, 1274, 727), "page": 0},
    ])
    value, reason = find_block_before_label(blocks, "військове звання, прізвище, ім")
    assert (value, reason) == ("старший сержант ВЛОХ Святослав Олесьович", "matched")


def test_empty_field_stays_empty_when_value_is_absent():
    """Найдорожчий ризик пропуску переставлених блоків -- взяти значення
    СУСІДНЬОГО поля, коли своє порожнє. Бланк без значення `місце`:
    попереду лейбла стоїть лише його ж перша половина, і вона не
    пропускається, тому поле лишається порожнім, а не краде вид відпустки."""
    schema = _schema()
    blocks = group_blocks_into_lines(_blocks(
        schema,
        "щорічна основна відпустка за 2026 рік",
        "(вид відпустки та найменування населеного пункту,",
        "до якого звільнено військовослужбовця)"))
    printed = printed_lines(schema)
    raw, _reason = find_block_before_label(
        blocks, "до якого звільнено військовослужбовця",
        printed=printed, order=printed_order(schema))
    # Кандидатом стає ПЕРША ПОЛОВИНА того самого лейбла -- не вид відпустки з
    # сусіднього поля, -- і її відхиляє перевірка друкованого тексту.
    field = next(f for f in schema["fields"] if f["name"] == "destination_place")
    assert validate_block_value(field, raw, (), printed) == (None, "printed_form_text")


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"OK   {name}")
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {name}: {exc}")
    print("провалено:", failed)
    sys.exit(1 if failed else 0)
