# -*- coding: utf-8 -*-
"""Дві прогалини можливості, знайдені зворотним проходом приладу по еталону
(known-weak-spots.md розд. 10.1 і 10.2), і два механізми, які їх закрили.

Обидві прогалини були невидимі не тому, що витяг помилявся, а тому що витягу
НЕ БУЛО: правильна відповідь в еталоні лежала, а прилад її не питав.

  10.1 -- супутники на відпускному квитку («Разом з ... прямують ...»);
  10.2 -- ФАКТИЧНА дата повернення з відрядження (таблиця відміток).

Головний тест тут -- `test_return_date_comes_from_the_marks_not_the_plan`:
на нашому корпусі відмітка про прибуття ЗБІГАЄТЬСЯ з плановим кінцем на всіх
14 документах, тобто реалізація, яка просто скопіювала б планову дату,
отримала б ті самі 14/14. Корпус такої підміни не розрізняє -- тому джерело
значення доводиться мутацією тексту в памʼяті, а не цифрою прогону.

Запуск (без LLM, без OCR):
    python -m pytest eval/tests/test_new_fields_2026_08_23.py -q
"""
import glob
import os
import re
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from pipeline.config import load_config
from pipeline.extraction.extract import extract_document, extract_field_regex
from pipeline.ingestion.ingest import load_document_blocks
from pipeline.run import build_resources

_LEAVE = os.path.join(_PROJECT_ROOT, "data", "eval", "samples", "leave",
                      "synthetic-2026-05", "docx")
_TRIP = os.path.join(_PROJECT_ROOT, "data", "eval", "samples", "deployment",
                     "synthetic-2026-05", "docx")


@pytest.fixture(scope="module")
def resources():
    cfg = load_config("config.yaml", project_root=_PROJECT_ROOT)
    res = build_resources(cfg, force_no_llm=True)
    return cfg, res


def _schema(res, template):
    schema = next((s for s in res["schemas"] if s["template"] == template), None)
    assert schema is not None, (
        f"схема {template} не завантажилась -- дивіться res['warnings']: "
        f"{[w for w in res['warnings'] if template in w][:3]}")
    return schema


def _extract(res, template, path):
    text, blocks = load_document_blocks(path)
    return text, extract_document(_schema(res, template), text, blocks,
                                  res["dictionaries"])


# --- 10.1 супутники ----------------------------------------------------------

def test_companions_are_extracted_where_the_form_has_them(resources):
    """8 з 16 документів мають супутників, 8 -- прочерк. Обидва випадки
    мусять бути ВИДИМІ й РІЗНІ: значення проти доведеної порожнечі."""
    _cfg, res = resources
    values, empties = [], []
    for path in sorted(glob.glob(os.path.join(_LEAVE, "*.docx"))):
        _text, out = _extract(res, "leave_ticket", path)
        value, reason = out["co_travelers"]
        if value is not None:
            values.append((os.path.basename(path), value, reason))
        else:
            empties.append((os.path.basename(path), reason))
    assert len(values) == 8, values
    assert len(empties) == 8, empties
    assert all(r == "matched" for _n, _v, r in values), values
    # Прочерк -- це ДОВЕДЕНА порожнеча («нікого»), а не «не знайшли».
    assert all(r.startswith("confirmed_empty_slot") for _n, r in empties), empties


def test_companions_do_not_steal_the_main_person(resources):
    """Примітка «(військове звання, прізвище та ініціали» стоїть на бланку
    ДВІЧІ. Уточнення `label_preceded_by` не має переплутати поля місцями."""
    _cfg, res = resources
    path = sorted(glob.glob(os.path.join(_LEAVE, "*.docx")))[0]
    _text, out = _extract(res, "leave_ticket", path)
    companions = out["co_travelers"][0]
    assert companions and "Лемешко А." in companions, companions
    # Прізвище відпускника -- ЛЕМЕШКО (те саме прізвище, інша особа), тому
    # перевіряємо не прізвище, а що в супутниках немає ПІБ відпускника.
    assert "Соломія" not in companions, companions


def test_ambiguous_label_without_the_hint_is_still_refused(resources):
    """Механізм не послаблює правило: без `label_preceded_by` той самий
    лейбл лишається неоднозначним, а не «беремо перше входження»."""
    _cfg, res = resources
    path = sorted(glob.glob(os.path.join(_LEAVE, "*.docx")))[0]
    text, blocks = load_document_blocks(path)
    from pipeline.extraction.extract import (find_block_before_label,
                                             group_blocks_into_lines)
    grouped = group_blocks_into_lines(blocks)
    raw, reason = find_block_before_label(grouped, "прізвище та ініціали")
    assert raw is None and reason == "ambiguous_label", (raw, reason)
    raw, reason = find_block_before_label(grouped, "прізвище та ініціали",
                                          preceded_by="Разом з")
    assert reason == "matched", (raw, reason)


def test_hint_that_does_not_occur_refuses_instead_of_guessing(resources):
    """Якщо якоря в документі немає (інша редакція, поганий OCR) --
    неоднозначність лишається неоднозначністю."""
    _cfg, res = resources
    path = sorted(glob.glob(os.path.join(_LEAVE, "*.docx")))[0]
    _text, blocks = load_document_blocks(path)
    from pipeline.extraction.extract import (find_block_before_label,
                                             group_blocks_into_lines)
    grouped = group_blocks_into_lines(blocks)
    raw, reason = find_block_before_label(grouped, "прізвище та ініціали",
                                          preceded_by="ТАКОГО РЯДКА НЕМА")
    assert raw is None and reason == "ambiguous_label", (raw, reason)


# --- 10.2 фактичне повернення з відрядження ---------------------------------

def test_return_date_is_extracted_on_the_whole_corpus(resources):
    """14 з 14 документів дають дату відмітки про прибуття."""
    _cfg, res = resources
    got = []
    for path in sorted(glob.glob(os.path.join(_TRIP, "*.docx"))):
        _text, out = _extract(res, "deployment_certificate", path)
        value, reason = out["actual_return_date"]
        got.append((os.path.basename(path), value, reason))
    assert len(got) == 14, got
    assert all(r == "matched" and v for _n, v, r in got), got


def test_return_date_comes_from_the_marks_not_the_plan(resources):
    """ГОЛОВНИЙ ТЕСТ. На корпусі відмітка збігається з плановим кінцем на всіх
    14 документах -- отже реалізація, що копіює планову дату, дала б ті самі
    14/14. Тому джерело доводиться мутацією: змінюємо ОСТАННЮ відмітку про
    прибуття на дату, якої в документі більше немає, і вимагаємо саме її.

    Це і є той випадок, для якого поле й додавалось: дострокове повернення."""
    _cfg, res = resources
    path = sorted(glob.glob(os.path.join(_TRIP, "*.docx")))[0]
    text, _blocks = load_document_blocks(path)
    field = next(f for f in _schema(res, "deployment_certificate")["fields"]
                 if f["name"] == "actual_return_date")

    before, _reason = extract_field_regex(field, text)
    assert before, before

    # Остання заповнена відмітка «Прибу... до ...» + дата рядком нижче.
    marks = list(re.finditer(
        r"(Прибу\w+\s+до\s+(?!_)[^\n]+\n\s*[«\"“„]?)(\d{1,2})", text))
    assert marks, "у документі мусить бути хоч одна заповнена відмітка"
    last = marks[-1]
    mutated = text[:last.start(2)] + "07" + text[last.end(2):]

    after, reason = extract_field_regex(field, mutated)
    assert reason == "matched", reason
    assert after["day"] == "07", (after, before)
    assert after["day"] != before["day"], (
        "мутація не змінила результат -- значення береться НЕ з відмітки")


def test_empty_marks_rows_are_not_mistaken_for_a_return(resources):
    """Порожні рядки таблиці (їх на бланку 4) не є відмітками: у них замість
    місця підкреслення. Інакше «останній збіг» означав би «четвертий рядок
    бланка», а не «остання реальна зупинка»."""
    _cfg, res = resources
    field = next(f for f in _schema(res, "deployment_certificate")["fields"]
                 if f["name"] == "actual_return_date")
    blank = os.path.join(_PROJECT_ROOT, "data", "eval", "samples", "deployment",
                         "посвідчення_відрядження.docx")
    text, _blocks = load_document_blocks(blank)
    value, reason = extract_field_regex(field, text)
    assert value is None, (value, reason)


def test_strict_ambiguity_stays_the_default(resources):
    """`multiple_matches: last` -- виняток, оголошений полем. Поле БЕЗ цього
    ключа на двох різних збігах мусить і далі віддавати неоднозначність
    (це правило C-03, і послабити його для всіх було б регресією)."""
    field = {"name": "x", "type": "text",
             "regex_variants": [{"pattern": r"№\s*(?P<value>\d+)"}]}
    text = "наказ № 777 ... квиток № 102"
    value, reason = extract_field_regex(field, text)
    assert value is None and reason.startswith("ambiguous"), (value, reason)
    value, reason = extract_field_regex(dict(field, multiple_matches="last"), text)
    assert value == "102" and reason == "matched", (value, reason)


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
