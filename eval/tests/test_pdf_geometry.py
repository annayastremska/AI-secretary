# -*- coding: utf-8 -*-
"""Тести на МЕЖУ БЛОКУ в текстовому шарі PDF (known-weak-spots.md розд. 5.7).

Блок, який віддає PyMuPDF (`page.get_text("blocks")`), -- це група
послідовних рядків, склеєна за близькістю, а НЕ абзац. Наслідки в обидва боки:

  * один блок містить кілька полів бланка підряд -- це вже закрито
    (`_value_lines_after_label_note`, `value_starts_after` у схемі);
  * ОДНЕ значення, розірване переносом рядка, розкладається на ДВА блоки --
    і тоді "попередній блок" перед лейблом виявляється самим ХВОСТОМ значення.
    Заміряно 14.08.2026: `position_and_workplace` на 11 з 14 документів
    deployment/pdf виходив як `частина А0000` / `військова частина А0000` з
    провенансом `matched`, тобто тихо обрізане значення з найвищою довірою.

Що фіксують тести:
  1. на pdf значення `position_and_workplace` -- те саме, що на docx, БАЙТ У
     БАЙТ, на всіх 14 документах набору (це і є сам дефект);
  2. виправлення обмежене текстовим шаром PDF за побудовою: docx і фото
     (`resegment_by_blank` занулює `source`) цієї гілки не бачать;
  3. добір назад ЗУПИНЯЄТЬСЯ на межі попереднього поля -- інакше замість
     обрізаного значення ми отримали б значення з приклеєним чужим полем,
     що гірше;
  4. позначка походження блоку ставиться в інжесті й має одне джерело.

Запуск (без LLM, без OCR):
    python -m pytest eval/tests/test_pdf_geometry.py -q
або без pytest:
    python eval/tests/test_pdf_geometry.py
"""
import glob
import os
import sys

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from pipeline.extraction.blank_form import (
    printed_lines, printed_order, resegment_by_blank)
from pipeline.extraction.extract import (
    MAX_PDF_WRAP_LOOKBACK_LINES,
    _extend_across_pdf_wrap,
    compile_value_boundaries,
    find_block_before_label,
    group_blocks_into_lines,
    schema_label_heads,
    strip_literal_prefix,
    validate_block_value,
)
from pipeline.identification import load_schemas
from pipeline.ingestion.ingest import PDF_TEXT_SOURCE, load_document_blocks

SCHEMAS_DIR = os.path.join(_PROJECT_ROOT, "pipeline", "schemas")
TRIP_DIR = os.path.join(_PROJECT_ROOT, "data", "eval", "samples", "deployment",
                        "synthetic-2026-05")

_schemas = load_schemas(SCHEMAS_DIR)
_trip = next(s for s in _schemas if s["template"] == "deployment_certificate")


def _block_before_label_values(path, schema):
    """{ім'я поля: (значення, причина)} для всіх полів `block_before_label`.

    Свідомо НЕ через `extract_document`: перевіряється саме детермінований
    шлях, без нормалізації й без LLM, щоб тест не залежав ні від моделі, ні
    від довідників."""
    _text, blocks = load_document_blocks(path)
    blocks, _changed = resegment_by_blank(blocks, schema)
    grouped = group_blocks_into_lines(blocks)
    printed, order = printed_lines(schema), printed_order(schema)
    heads = schema_label_heads(schema)
    out = {}
    for field in schema["fields"]:
        if field.get("extraction") != "block_before_label":
            continue
        raw, reason = find_block_before_label(
            grouped, field["label_before"], set(),
            anchor=field.get("strip_prefix"),
            boundaries=compile_value_boundaries(field),
            printed=printed, order=order)
        if raw is not None and field.get("strip_prefix"):
            raw = strip_literal_prefix(raw, field["strip_prefix"])
        if raw is None:
            out[field["name"]] = (None, reason)
        else:
            out[field["name"]] = validate_block_value(field, raw, heads, printed)
    return out


# --- 1. Сам дефект: pdf мусить давати те саме, що docx ---------------------

def test_position_on_pdf_carries_the_same_value_as_docx():
    """`position_and_workplace` на pdf несе те саме ЗНАЧЕННЯ, що на docx, на
    всіх документах набору.

    Порівняння з точністю до пробілів, а не байт у байт, і межа тут навмисна:
    перенос рядка ВСЕРЕДИНІ одного блоку PyMuPDF ця правка не переглядає (він
    лишається і в `purpose`, і в TRIP-001/003/011 -- окрема, старша розбіжність,
    розд. 5.7). Байт у байт перевіряється окремо, там, де шов справді зшивала
    ця правка -- `test_seam_stitched_value_is_byte_identical_to_docx`."""
    import re

    def norm(s):
        return re.sub(r"\s+", " ", s).strip() if isinstance(s, str) else s

    docx_paths = sorted(glob.glob(os.path.join(TRIP_DIR, "docx", "*.docx")))
    assert len(docx_paths) >= 14, "набір deployment/docx не знайдено"
    for docx_path in docx_paths:
        doc_id = os.path.splitext(os.path.basename(docx_path))[0]
        pdf_path = os.path.join(TRIP_DIR, "pdf", doc_id + ".pdf")
        assert os.path.exists(pdf_path), pdf_path
        pdf_value, pdf_reason = _block_before_label_values(
            pdf_path, _trip)["position_and_workplace"]
        docx_value, docx_reason = _block_before_label_values(
            docx_path, _trip)["position_and_workplace"]
        assert (norm(pdf_value), pdf_reason) == (norm(docx_value), docx_reason), (
            f"{doc_id}: pdf віддає {pdf_value!r}, docx -- {docx_value!r}")


def test_seam_stitched_value_is_byte_identical_to_docx():
    """Там, де значення справді було розкладене на два блоки, зшите значення
    збігається з docx БАЙТ У БАЙТ.

    Байт у байт тут не педантизм: поле має `dimension: position`, тобто йде в
    `facts.value_code` БД-споживача. Два написання того самого факту залежно
    від формату файлу -- це два різні факти для того, хто рахує. Саме тому шов
    склеюється пробілом, а не `\\n`."""
    doc_id = "TRIP-004"   # заміряний приклад із докстрінга _extend_across_pdf_wrap
    from_pdf = _block_before_label_values(
        os.path.join(TRIP_DIR, "pdf", doc_id + ".pdf"), _trip)
    from_docx = _block_before_label_values(
        os.path.join(TRIP_DIR, "docx", doc_id + ".docx"), _trip)
    assert from_pdf["position_and_workplace"] == from_docx["position_and_workplace"]
    assert "\n" not in from_pdf["position_and_workplace"][0]


def test_position_on_pdf_is_not_a_truncated_tail():
    """Явна перевірка САМОГО заміряного симптому, окремо від порівняння з docx.

    Потрібна тому, що порівняння з docx стало б зеленим і в разі, якби
    ЗЛАМАВСЯ docx-шлях -- обидва віддавали б однаковий хвіст."""
    for pdf_path in sorted(glob.glob(os.path.join(TRIP_DIR, "pdf", "*.pdf"))):
        value, reason = _block_before_label_values(
            pdf_path, _trip)["position_and_workplace"]
        assert reason == "matched", (pdf_path, reason)
        assert not value.lower().startswith(("частина", "військова частина")), (
            f"{os.path.basename(pdf_path)}: значення обрізане до хвоста "
            f"{value!r} -- саме цей дефект закривав розд. 5.7")
        assert "," in value, (
            f"{os.path.basename(pdf_path)}: {value!r} не схоже на трійку "
            "посада/підрозділ/частина -- значення, найпевніше, обрізане")


# --- 2. Правка обмежена текстовим шаром PDF за побудовою -------------------

def test_wrap_extension_is_inert_without_pdf_source():
    """Блок без позначки `source: pdf_text` не розширюється НІКОЛИ.

    Це головна гарантія нуля регресії: на docx межа блоку -- абзац або
    клітинка, тобто справжня межа поля, а на фото межі відновлює
    `resegment_by_blank`, який `source` навмисно занулює. Обидва шляхи цю
    гілку не бачать, тому їхні цифри не можуть змінитися."""
    blocks = [
        {"lines": ["голова значення, яку відрізало"], "bbox": None,
         "page": 0, "source": None},
        {"lines": ["хвіст значення"], "bbox": None, "page": 0, "source": None},
    ]
    value_lines = blocks[1]["lines"]
    assert _extend_across_pdf_wrap(blocks, 1, 0, value_lines) is value_lines


def test_wrap_extension_joins_the_seam_with_a_space():
    """Той самий набір блоків, але з позначкою pdf -- хвіст добирається, і шов
    склеюється ПРОБІЛОМ, а не `\\n`: це місце переносу одного значення, не межа
    двох різних (те саме рішення, що в `_extend_across_block_boundary`)."""
    blocks = [
        {"lines": ["навідник, 3-тя механізована рота, військова"], "bbox": None,
         "page": 0, "source": PDF_TEXT_SOURCE},
        {"lines": ["частина А0000"], "bbox": None, "page": 0,
         "source": PDF_TEXT_SOURCE},
    ]
    assert _extend_across_pdf_wrap(blocks, 1, 0, blocks[1]["lines"]) == [
        "навідник, 3-тя механізована рота, військова частина А0000"]


def test_pdf_text_blocks_are_marked_at_ingestion():
    """Позначку ставить інжест, і вона одна на весь пайплайн. Літерал у двох
    місцях розійшовся б, і добір хвоста тихо перестав би працювати."""
    _text, blocks = load_document_blocks(
        os.path.join(TRIP_DIR, "pdf", "TRIP-004.pdf"))
    assert blocks and all(b.get("source") == PDF_TEXT_SOURCE for b in blocks), (
        "усі блоки текстового шару PDF мусять несуть позначку походження")
    _text2, docx_blocks = load_document_blocks(
        os.path.join(TRIP_DIR, "docx", "TRIP-004.docx"))
    assert all(not isinstance(b, dict) or b.get("source") is None
               for b in docx_blocks), "docx-блоки позначки не мають"


# --- 3. Добір зупиняється на межі попереднього поля -----------------------

def test_wrap_extension_stops_at_printed_label_note():
    """Дужкова примітка бланка ЗАКРИВАЄ попереднє поле, тож добір мусить
    зупинитись на ній. Без цієї зупинки значення набирало б чуже поле --
    помилка гірша за обрізане значення, бо виглядає повною."""
    blocks = [
        {"lines": ["рядовий КАБАЛЮК Тимофій Леонідович",
                   "(військове звання, прізвище ім’я по батькові)",
                   "навідник, 3-тя механізована рота, військова"],
         "bbox": None, "page": 0, "source": PDF_TEXT_SOURCE},
        {"lines": ["частина А0000"], "bbox": None, "page": 0,
         "source": PDF_TEXT_SOURCE},
    ]
    assert _extend_across_pdf_wrap(blocks, 1, 0, blocks[1]["lines"]) == [
        "навідник, 3-тя механізована рота, військова частина А0000"]


def test_wrap_extension_stops_at_printed_blank_line():
    """Дослівний друкований рядок ПОРОЖНЬОГО бланка -- найсильніша з меж і
    єдина, що не є евристикою."""
    printed = printed_lines(_trip)
    assert printed, "у схемі deployment оголошено blank_template"
    stopper = "Дійсно в разі пред’явлення документа, який засвідчує особу."
    blocks = [
        {"lines": [stopper], "bbox": None, "page": 0, "source": PDF_TEXT_SOURCE},
        {"lines": ["значення"], "bbox": None, "page": 0, "source": PDF_TEXT_SOURCE},
    ]
    value_lines = blocks[1]["lines"]
    assert _extend_across_pdf_wrap(blocks, 1, 0, value_lines,
                                   printed=printed) is value_lines


def test_wrap_extension_respects_lookback_limit():
    """Межі не знайшлось у межах ліміту -> кандидат лишається БЕЗ ЗМІН.
    Безпечний відкат, а не найкращий здогад: та сама дисципліна, що в
    `_extend_to_anchor` і `_extend_across_block_boundary`."""
    filler = [{"lines": [f"рядок {k} без жодної межі поля"], "bbox": None,
               "page": 0, "source": PDF_TEXT_SOURCE}
              for k in range(MAX_PDF_WRAP_LOOKBACK_LINES + 3)]
    blocks = filler + [{"lines": ["хвіст"], "bbox": None, "page": 0,
                        "source": PDF_TEXT_SOURCE}]
    extended = _extend_across_pdf_wrap(blocks, len(blocks) - 1, 0,
                                       blocks[-1]["lines"])
    assert len(extended) == 1, (
        "усі дібрані рядки склеюються в один -- шов це перенос, не межа")
    assert extended[0].count("рядок") == MAX_PDF_WRAP_LOOKBACK_LINES, (
        f"дібрано {extended[0]!r} -- ліміт добору не витриманий")


def test_wrap_extension_does_not_cross_page_boundary():
    """Інша сторінка -- це низ попередньої сторінки (підписи, печатки), а не
    продовження значення. Той самий інваріант, що в `_geometric_candidate` і
    `_lines_backwards`."""
    blocks = [
        {"lines": ["текст із попередньої сторінки"], "bbox": None, "page": 0,
         "source": PDF_TEXT_SOURCE},
        {"lines": ["значення"], "bbox": None, "page": 1,
         "source": PDF_TEXT_SOURCE},
    ]
    value_lines = blocks[1]["lines"]
    assert _extend_across_pdf_wrap(blocks, 1, 0, value_lines) is value_lines


# --- 4. Решта полів набору не зачеплена -----------------------------------

def test_other_block_before_label_fields_unchanged_on_pdf():
    """Правка мусить рухати РІВНО одне поле. Решта полів `block_before_label`
    на pdf лишається тим самим, що на docx (з точністю до переносу рядка
    ВСЕРЕДИНІ блоку -- це окрема, старша розбіжність, розд. 5.7)."""
    import re
    for docx_path in sorted(glob.glob(os.path.join(TRIP_DIR, "docx", "*.docx"))):
        doc_id = os.path.splitext(os.path.basename(docx_path))[0]
        from_docx = _block_before_label_values(docx_path, _trip)
        from_pdf = _block_before_label_values(
            os.path.join(TRIP_DIR, "pdf", doc_id + ".pdf"), _trip)
        for name, (value, reason) in from_docx.items():
            pdf_value, pdf_reason = from_pdf[name]
            assert pdf_reason == reason, (doc_id, name, reason, pdf_reason)
            norm = (lambda s: re.sub(r"\s+", " ", s).strip()
                    if isinstance(s, str) else s)
            assert norm(pdf_value) == norm(value), (doc_id, name,
                                                    value, pdf_value)


if __name__ == "__main__":
    failed = 0
    for _name, _fn in sorted(list(globals().items())):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"OK   {_name}")
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {_name}: {exc}")
    print("провалено:", failed)
    sys.exit(1 if failed else 0)
