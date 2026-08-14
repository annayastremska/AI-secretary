# -*- coding: utf-8 -*-
"""Тести на СЛІПУ ПЛЯМУ: інша РЕДАКЦІЯ бланка (known-weak-spots.md розд. 8).

Уся конструкція `pipeline/extraction/blank_form.py` стоїть на ТОЧНОМУ тексті
нашого порожнього бланка. Якщо в частині трапиться форма з тими самими полями,
але перефразованими друкованими рядками:
  * різак меж полів (`resegment_by_blank`) ріже не туди або не ріже взагалі;
  * негативна перевірка `is_printed_form_text` не відхиляє нічого;
  * режими без опори на друкований підпис поля (`regex`,
    `rank_and_name_tokenized`) віддають значення з
    провенансом `matched`, і документ може стати `confirmed`.

Що фіксують тести:
  1. сигнал «наскільки це наш бланк» рахується ОДИН раз, в `identify_template`,
     і в екстракцію приходить готовим (два джерела істини неприпустимі);
  2. межа виведена з даних: усі відомо-добрі документи лежать ВИЩЕ порога, а
     штучно змінена редакція -- у кілька разів нижче;
  3. на нашому наборі правило не спрацьовує за побудовою (нуль регресії);
  4. на чужій редакції значення режимів без опори перестають бути `matched` і
     йдуть у прогалини (тобто в LLM), а не приймаються на віру;
  5. значення при цьому НЕ губиться, якщо модель нічого не віддала.

Запуск (без LLM, без OCR):
    python -m pytest eval/tests/test_foreign_edition.py -q
або без pytest:
    python eval/tests/test_foreign_edition.py
"""
import glob
import os
import sys

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from pipeline.extraction.blank_form import blank_line_coverage
from pipeline.extraction.extract import (
    UNANCHORED_MODES,
    UNVERIFIED_METHOD,
    extract_document,
    find_block_before_label,
    group_blocks_into_lines,
)
from pipeline.identification import (
    DEFAULT_MIN_BLANK_COVERAGE,
    MIN_BLANK_COVERAGE_KEY,
    blank_edition_verdict,
    identify_template,
    load_schemas,
)
from pipeline.ingestion.ingest import extract_docx_blocks

SCHEMAS_DIR = os.path.join(_PROJECT_ROOT, "pipeline", "schemas")
SAMPLES = os.path.join(_PROJECT_ROOT, "data", "eval", "samples")
#: Штучно змінена редакція того самого бланка: друковані рядки перефразовані,
#: структура й ВПИСАНІ ЗНАЧЕННЯ -- ті самі, що в LEAVE-001. Заголовок бланка й
#: посилання на Інструкцію збережені навмисно -- саме такий документ проходить
#: ідентифікацію за анкорами й ламає blank_form.py.
FOREIGN = os.path.join(SAMPLES, "leave", "відпускний_квиток_інша_редакція.docx")
NATIVE = os.path.join(SAMPLES, "leave", "synthetic-2026-05", "docx",
                      "LEAVE-001.docx")

_schemas = load_schemas(SCHEMAS_DIR)
_leave = next(s for s in _schemas if s["template"] == "leave_ticket")


def _text(path):
    text, _blocks = extract_docx_blocks(path)
    return text


def _blocks(path):
    _text_, blocks = extract_docx_blocks(path)
    return blocks


# --- 1. Одне джерело істини ------------------------------------------------

def test_signal_comes_from_identification_not_extraction():
    """Вердикт про редакцію бланка віддає `identify_template`, а екстракція
    його ЧИТАЄ параметром. Якби extract_document рахувала схожість сама, у
    системи було б два твердження про «це наша форма», і при розходженні
    ніхто не знав би, яке з них право."""
    ident = identify_template(_text(FOREIGN), _schemas)
    assert ident["template"] == "leave_ticket", (
        "змінена редакція мусить лишатися ВПІЗНАНОЮ за анкорами -- інакше тест "
        "перевіряє не сліпу пляму, а звичайний невідомий документ")
    assert ident["blank_edition"] is not None
    assert ident["blank_edition"]["recognized"] is False

    import inspect
    signature = inspect.signature(extract_document)
    assert "form_recognized" in signature.parameters
    assert signature.parameters["form_recognized"].default is True, (
        "за замовчуванням -- попередня поведінка: викликач без сигналу "
        "(тест, скрипт) не має отримати нову")
    source = inspect.getsource(extract_document)
    assert "blank_line_coverage" not in source, (
        "екстракція не має рахувати схожість сама -- вона читає готовий "
        "вердикт identify_template")


def test_verdict_absent_when_schema_declares_no_blank():
    """Схема без `blank_template:` -> сигналу немає, і форма вважається
    впізнаною. Та сама межа, що вже оголошена в blank_form.py: новий бланк без
    оголошеного шаблону не має почати мовчки не довіряти власним полям."""
    verdict = blank_edition_verdict("будь-який текст", {"template": "x",
                                                        "fields": []})
    assert verdict == {"found": 0, "total": 0, "coverage": None,
                       "threshold": DEFAULT_MIN_BLANK_COVERAGE,
                       "recognized": True}


def test_threshold_is_declarable_per_schema():
    """Поріг можна підняти під конкретний бланк, не правлячи код -- той самий
    підхід, що `min_score` і `llm_floor`."""
    strict = dict(_leave)
    strict["identification"] = dict(_leave["identification"],
                                    **{MIN_BLANK_COVERAGE_KEY: 0.99})
    assert blank_edition_verdict(_text(NATIVE), strict)["recognized"] is False
    assert blank_edition_verdict(_text(NATIVE), _leave)["recognized"] is True


# --- 2 і 3. Межа з даних, нуль регресії ------------------------------------

def test_every_known_good_document_is_above_threshold():
    """Правило не спрацьовує на нашому наборі ЗА ПОБУДОВОЮ. Якби спрацювало --
    це регресія, а не захист, і саме цей тест мусить її показати."""
    groups = {
        "leave_ticket": [
            "leave/synthetic-2026-05/docx/*.docx",
            "leave/synthetic-2026-05/pdf/*.pdf",
            "leave/відпускний_шаблон.docx",
        ],
        "deployment_certificate": [
            "deployment/synthetic-2026-05/docx/*.docx",
            "deployment/synthetic-2026-05/pdf/*.pdf",
            "deployment/посвідчення_відрядження.docx",
            "deployment/посвідчення_відрядження_заповнений.docx",
        ],
    }
    worst = {}
    for template, patterns in groups.items():
        schema = next(s for s in _schemas if s["template"] == template)
        paths = []
        for pattern in patterns:
            paths.extend(sorted(glob.glob(os.path.join(SAMPLES, pattern))))
        assert paths, f"зразків для {template} не знайдено -- тест нічого не міряє"
        for path in paths:
            if path.lower().endswith(".pdf"):
                from pipeline.ingestion.ingest import extract_pdf_blocks
                text, _b = extract_pdf_blocks(path)
            else:
                text = _text(path)
            verdict = blank_edition_verdict(text, schema)
            assert verdict["recognized"], (
                f"{os.path.basename(path)}: покриття "
                f"{verdict['found']}/{verdict['total']} нижче порога "
                f"{verdict['threshold']} -- правило спрацювало на ВІДОМО "
                "ДОБРОМУ документі")
            if verdict["coverage"] < worst.get(template, (1.0, ""))[0]:
                worst[template] = (verdict["coverage"], os.path.basename(path))
    # Поріг мусить стояти НИЖЧЕ найгіршого відомо-доброго із запасом, а не
    # впритул: інакше перше ж розходження OCR зробить із захисту регресію.
    for template, (coverage, name) in worst.items():
        assert coverage >= DEFAULT_MIN_BLANK_COVERAGE + 0.2, (
            f"{template}: найгірший відомо-добрий ({name}) дає {coverage:.3f}, "
            f"а поріг {DEFAULT_MIN_BLANK_COVERAGE} -- запасу немає")


def test_foreign_edition_is_far_below_threshold():
    """Розділення між класами -- у кілька разів, не на межі."""
    native = blank_edition_verdict(_text(NATIVE), _leave)
    foreign = blank_edition_verdict(_text(FOREIGN), _leave)
    assert foreign["recognized"] is False
    assert native["recognized"] is True
    assert foreign["coverage"] * 2 < DEFAULT_MIN_BLANK_COVERAGE, (
        f"чужа редакція дає {foreign['coverage']:.3f} при порозі "
        f"{DEFAULT_MIN_BLANK_COVERAGE} -- замало розділення")


def test_coverage_signal_is_not_a_second_recognizer():
    """Сигнал стоїть на ТИХ САМИХ різаках, якими ріжуться межі полів: на
    порожньому бланку покриття рівно повне. Якби міра була своя, окрема, вона
    могла б не побачити те, що бачить різак (або навпаки)."""
    found, total = blank_line_coverage(
        _text(os.path.join(SAMPLES, "leave", "відпускний_шаблон.docx")), _leave)
    assert total > 0
    assert found == total, (
        f"на ВЛАСНОМУ порожньому бланку знайдено лише {found} з {total} "
        "різаків -- міра й різак розійшлися")


# --- 4. Поведінка на чужій редакції ---------------------------------------

def test_labelled_modes_already_have_insurance():
    """Перевірка твердження, на яке спирається UNANCHORED_MODES: поле з
    ЛЕЙБЛОМ і без нього поводяться по-різному. Перефразований лейбл дає
    `no_label`, тобто прогалину й шлях у LLM -- окремого захисту йому не
    потрібно."""
    blocks = group_blocks_into_lines(_blocks(FOREIGN))
    label = next(f["label_before"] for f in _leave["fields"]
                 if f.get("extraction") == "block_before_label")
    value, reason = find_block_before_label(blocks, label)
    assert value is None and reason == "no_label", (
        f"очікувався no_label на перефразованому лейблі, отримано "
        f"({value!r}, {reason})")


def test_unanchored_values_stop_being_matched_on_foreign_edition():
    """Головне твердження мандату: значення без опори на друкований підпис
    поля більше не приймаються на віру."""
    text, blocks = extract_docx_blocks(FOREIGN)
    trusted = extract_document(_leave, text, blocks, {}, form_recognized=True)
    checked = extract_document(_leave, text, blocks, {}, form_recognized=False)

    unanchored = {f["name"] for f in _leave["fields"]
                  if f.get("extraction") in UNANCHORED_MODES}
    was_matched = {n for n in unanchored
                   if trusted[n][0] is not None and trusted[n][1] == "matched"}
    assert was_matched, (
        "тест нічого не перевіряє: на цій редакції жоден режим без опори не "
        "віддав значення з провенансом matched")
    for name in was_matched:
        value, reason = checked[name]
        assert reason == UNVERIFIED_METHOD, (
            f"{name}: очікувався {UNVERIFIED_METHOD}, отримано {reason}")
        # 5. Значення НЕ губиться: перевірка не є знищенням даних.
        assert value == trusted[name][0], (
            f"{name}: значення втрачено при переході на перевірку")


def test_recognized_form_is_bit_for_bit_unchanged():
    """Форма впізнана -> не змінюється НІЧОГО. Це головна вимога до правки:
    детермінований шлях мусить лишитися тим самим, інакше це обмін, а не
    поліпшення."""
    text, blocks = extract_docx_blocks(NATIVE)
    assert (extract_document(_leave, text, blocks, {}, form_recognized=True)
            == extract_document(_leave, text, blocks, {}))


def test_deterministic_value_survives_silent_llm():
    """Модель не віддала нічого -> повертається детермінований результат із
    провенансом UNVERIFIED_METHOD, а не порожнє поле. Без цього перевірка
    ПЕРЕТВОРЮВАЛАСЬ БИ на знищення даних: чуже формулювання бланка коштувало б
    усіх полів цих режимів."""
    text, blocks = extract_docx_blocks(FOREIGN)
    silent = extract_document(_leave, text, blocks, {},
                              llm_extract_batch=lambda *a, **k: {},
                              form_recognized=False)
    trusted = extract_document(_leave, text, blocks, {}, form_recognized=True)
    kept = [n for n, (v, r) in silent.items() if r == UNVERIFIED_METHOD]
    assert kept, "жодного значення не збережено як непідтверджене"
    for name in kept:
        assert silent[name][0] == trusted[name][0]


def test_deterministic_value_survives_llm_error():
    """Те саме для ЗБОЮ моделі: непідтверджене значення чесніше за порожнє
    поле, доки провенанс каже, що воно непідтверджене."""
    def boom(*_a, **_k):
        raise RuntimeError("сервер моделі впав")

    text, blocks = extract_docx_blocks(FOREIGN)
    broken = extract_document(_leave, text, blocks, {},
                              llm_extract_batch=boom, form_recognized=False)
    assert any(r == UNVERIFIED_METHOD for _v, r in broken.values())


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
