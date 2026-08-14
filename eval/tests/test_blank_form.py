# -*- coding: utf-8 -*-
"""Тести на перевірку «значення не є друкованим текстом бланка».

Закриває known-weak-spots.md 5.9 (порожній шаблон віддавав два друковані рядки
як значення) і 5.8 (незаповнений слот ДАТИ не вважався placeholder-ом).

Механізм: схема оголошує `blank_template:` -- порожній бланк своєї форми, і
кандидат, який ЦІЛКОМ складається з рядків цього файлу, значенням не
приймається (pipeline/extraction/blank_form.py). Тести навмисно читають
СПРАВЖНІЙ порожній шаблон із data/eval/samples/, а не свій список фраз: сенс
переходу на шаблон саме в тому, що перелік друкованих фраз ніхто не веде
руками, і тест, який вів би свій, перевіряв би не той механізм.

Запуск (без LLM, без OCR, без обробки документів):
    python -m pytest eval/tests/test_blank_form.py -q
або без pytest:
    python eval/tests/test_blank_form.py
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from pipeline.extraction.blank_form import (
    BLANK_TEMPLATE_KEY,
    blank_template_path,
    is_printed_form_text,
    printed_lines,
)
from pipeline.extraction.extract import (
    ground_llm_value,
    validate_block_value,
    validate_regex_value,
)
from pipeline.identification import load_schemas
from pipeline.normalization.normalize import is_placeholder

SCHEMAS_DIR = os.path.join(_PROJECT_ROOT, "pipeline", "schemas")

# Рядки, які на порожньому бланку відпускного квитка приймались за значення
# (5.9). Обидва -- друкований текст форми.
LEAVE_UNIT_PRINTED = "зобов’язаний прибути до місця служби у"
LEAVE_TRAVEL_PRINTED = "Разом з"
# Незаповнений слот дати того самого бланка (5.8).
LEAVE_DATE_SLOT = "“_____” ______________20___ р."


def _schemas():
    return {s["template"]: s for s in load_schemas(SCHEMAS_DIR)}


def _field(schema, name):
    return next(f for f in schema["fields"] if f["name"] == name)


def test_both_schemas_declare_an_existing_blank_template():
    """Оголошення без файлу = перевірка, якої немає. Саме цей клас (коментар
    обіцяє перевірку, код її не робить) і є вадою 5.8, тому декларація
    перевіряється разом із наявністю файлу на диску."""
    for template, schema in _schemas().items():
        assert schema.get(BLANK_TEMPLATE_KEY), \
            f"{template}: немає {BLANK_TEMPLATE_KEY}"
        path = blank_template_path(schema)
        assert os.path.exists(path), f"{template}: не знайдено {path}"
        assert printed_lines(schema), f"{template}: з шаблону не прочитано рядків"


def test_printed_lines_contain_the_two_phrases_from_5_9():
    printed = printed_lines(_schemas()["leave_ticket"])
    assert is_printed_form_text(LEAVE_UNIT_PRINTED, printed)
    assert is_printed_form_text(LEAVE_TRAVEL_PRINTED, printed)


def test_unit_to_report_rejects_printed_line_of_the_form():
    """5.9, головний випадок: фраза не оголошена в схемі ні як `label_before`,
    ні як анкор, тому перевірка «голів лейблів» її не бачила, а поле має
    `dimension:` і дійшло б до БД окремим фактом."""
    schema = _schemas()["leave_ticket"]
    field = _field(schema, "unit_to_report")
    value, reason = validate_block_value(
        field, LEAVE_UNIT_PRINTED, printed=printed_lines(schema))
    assert (value, reason) == (None, "printed_form_text")


def test_travel_document_number_rejects_printed_line_of_the_form():
    """5.9, другий випадок, і він на regex-шляху: патерн
    'документи за №\\s*(?P<value>.+?)\\n' МУСИТЬ переходити через перенос рядка
    (у заповнених документах номер стоїть наступним рядком), тому лікується
    результат, а не патерн."""
    schema = _schemas()["leave_ticket"]
    field = _field(schema, "travel_document_number")
    value, reason = validate_regex_value(
        field, LEAVE_TRAVEL_PRINTED, printed_lines(schema))
    assert (value, reason) == (None, "printed_form_text")


def test_unfilled_date_slot_is_rejected_although_not_a_placeholder():
    """5.8: `_BLANK_FILL_RE` не вважає слот дати placeholder-ом -- у рядку
    лишаються надруковані "20" і "р.". Перша перевірка тут фіксує цю
    розбіжність як факт (щоб вона не читалась як працююча перевірка), друга --
    що наслідку в неї більше немає: слот відхиляється як друкований рядок
    бланка."""
    schema = _schemas()["leave_ticket"]
    assert is_placeholder(LEAVE_DATE_SLOT) is False
    value, reason = validate_block_value(
        _field(schema, "actual_return_date"), LEAVE_DATE_SLOT,
        printed=printed_lines(schema))
    assert (value, reason) == (None, "printed_form_text")


def test_llm_value_that_is_printed_form_text_is_rejected():
    """Заземлення (`ground_llm_value`) тут безсиле за побудовою: друкований
    текст У ДОКУМЕНТІ Є, тобто підрядковий пошук його підтверджує. На порожньому
    бланку посвідчення модель саме так і віддала `document_number` =
    "Додаток 28" -- заміряно 14.08.2026."""
    schema = _schemas()["deployment_certificate"]
    printed = printed_lines(schema)
    field = _field(schema, "document_number")
    value, reason = ground_llm_value(field, "Додаток 28", "Додаток 28\n...", printed)
    assert (value, reason) == (None, "printed_form_text")


def test_regex_placeholder_slot_is_not_a_value():
    """Порожній бланк посвідчення: "Підстава відрядження: рішення, наказ від
    ________ №___" віддавав `basis_order_number` = "___" з провенансом
    `matched`. Значення далі гасила нормалізація, тобто в БД воно не йшло, --
    але провенанс брехав, що поле прочитане."""
    schema = _schemas()["deployment_certificate"]
    value, reason = validate_regex_value(
        _field(schema, "basis_order_number"), "___", printed_lines(schema))
    assert (value, reason) == (None, "blank_value")


def test_not_issued_sentinel_survives_the_new_checks():
    """Сентинел "не видавались" фізично лежить у PLACEHOLDER_TOKENS, тому нова
    перевірка на порожнє мусить пускати його далі -- інакше різниця
    «документ прямо каже, що ВПД не видавались» (підтверджено-порожнє) і
    «не вдалося прочитати» (прогалина) зникає ще до нормалізації."""
    schema = _schemas()["leave_ticket"]
    field = _field(schema, "travel_document_number")
    assert field.get("not_issued_sentinel") == "не видавались"
    assert validate_regex_value(field, "не видавались", printed_lines(schema)) \
        == ("не видавались", "matched")


def test_real_values_of_filled_documents_are_not_rejected():
    """Регресія: перевірка не має чіпати значення заповнених документів. Усі
    рядки нижче взяті з корпусу docx, а не вигадані."""
    leave, trip = _schemas()["leave_ticket"], _schemas()["deployment_certificate"]
    leave_printed, trip_printed = printed_lines(leave), printed_lines(trip)
    for value in ("8144/26", "м. Житомир", "щорічна основна відпустка за 2026 рік",
                  "тринадцять", "військова частина А0000", "А0000", "157",
                  "перервана, відкликаний з відпустки"):
        assert not is_printed_form_text(value, leave_printed), value
    for value in ("м. Вінниця", "Центральна база зберігання майна (в/ч Т3011)",
                  "заступник командира взводу, 2-га механізована рота, "
                  "військова частина А0000",
                  "отримання засобів індивідуального захисту", "345", "244"):
        assert not is_printed_form_text(value, trip_printed), value


def test_page_number_line_is_not_a_printed_phrase():
    """У бланку відпускного окремим блоком стоїть номер сторінки "2", а "2" --
    законне значення `deployment_days` (TRIP-001: 'Термін відрядження "2"
    днів'). Тому до переліку беруться лише рядки з ЛІТЕРАМИ."""
    printed = printed_lines(_schemas()["leave_ticket"])
    assert "2" not in printed
    assert not is_printed_form_text("2", printed)
    assert not is_printed_form_text("3", printed)


def test_apostrophe_and_quote_glyphs_do_not_defeat_the_check():
    """Той самий друкований рядок приходить із docx із фігурним ’, а з OCR --
    із прямим '. Якби порівняння залежало від гліфа, перевірка працювала б на
    docx і мовчки не працювала на фото -- тобто рівно там, де помилок більше."""
    printed = printed_lines(_schemas()["leave_ticket"])
    assert is_printed_form_text("зобов'язаний прибути до місця служби у", printed)
    assert is_printed_form_text("  Разом   з ", printed)


def test_mixed_candidate_keeps_its_value():
    """Кандидат, у якому друкований рядок ПРИКЛЕЇВСЯ до справжнього значення,
    не викидається: там є текст, вписаний людиною, і втратити його гірше, ніж
    віддати з приклеєним лейблом (цей клас ловлять
    `_value_lines_after_label_note` і `printed_label_in_value`)."""
    printed = printed_lines(_schemas()["leave_ticket"])
    assert not is_printed_form_text("Разом з\nчоловік Лемешко А., діти — 1", printed)


def test_check_is_inert_without_declaration():
    """Схема без `blank_template:` -> перевірка не діє, поведінка як була.
    Потрібно, щоб новий бланк без оголошеного шаблону не почав мовчки
    відхиляти значення."""
    assert printed_lines({"template": "x", "fields": []}) == frozenset()
    assert not is_printed_form_text("будь-що", frozenset())
    assert validate_regex_value({"name": "f", "type": "text"}, "Разом з") \
        == ("Разом з", "matched")


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
