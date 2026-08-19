# -*- coding: utf-8 -*-
"""Тести на ДОВЕДЕНО ПОРОЖНІЙ СЛОТ (R2-П5-А і П5-Б,
docs/improvement-2026-08-15/r2-llm.md розд. 5.5).

Правка не покращує якість -- вона забирає МАРНУ роботу: поле, чий слот на
бланку доведено порожній, більше не йде в LLM-фолбек. Заміряно: 12B на
LEAVE-011 віддавала null у 8 з 8 таких полів, витрачаючи 122-372 с на
відповідь, відому детерміновано.

Через це тести перевіряють не «стало правильніше», а ТРИ МЕЖІ, кожна з яких
при поломці робить систему гіршою:

  1. скіп працює лише на ДОВЕДЕНІЙ порожнечі (blank_value / printed_form_text /
     друкована підказка локалізованої прогалини), і НЕ на «слот не знайдено»
     (no_label / no_value / чужа редакція бланка) -- інакше правка почала б
     мовчки з'їдати законний фолбек, на якому LLM реально відновлює значення;
  2. порожнє КРИТИЧНЕ поле лишає документ needs_review -- «слот порожній» це
     висновок різака, підтвердити його має людина (чернетка != факт);
  3. скіпнуте поле НІКОЛИ не несе значення -- скіп означає «не питали», а не
     «вирішили»;
  4. (П5-Б) знання про форму живе в СХЕМІ: без оголошеного `empty_pattern:`
     код сам порожній слот дати не скіпає, а оголошений, але недіючий патерн
     мусить бути ПОМИЛКОЮ валідатора, не тихо проігнорованим ключем.

Запуск (без LLM, без OCR):
    python -m pytest eval/tests/test_confirmed_empty_slot.py -q
"""
import glob
import os
import sys

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from pipeline.build_record import build_record, field_criticality
from pipeline.extraction.blank_form import blank_template_text
from pipeline.extraction.extract import (
    CONFIRMED_EMPTY_SLOT_METHOD,
    EMPTY_PATTERN_KEY,
    PROVEN_EMPTY_REASONS,
    extract_document,
    slot_is_provably_empty,
)
from pipeline.identification import (
    load_schemas, schema_title_phrases, validate_schema)
from pipeline.ingestion.ingest import extract_docx_blocks
from pipeline.run import load_dictionaries

SCHEMAS_DIR = os.path.join(_PROJECT_ROOT, "pipeline", "schemas")
DICTS_DIR = os.path.join(_PROJECT_ROOT, "pipeline", "dictionaries")
SAMPLES = os.path.join(_PROJECT_ROOT, "data", "eval", "samples")
LEAVE_DOCX = os.path.join(SAMPLES, "leave", "synthetic-2026-05", "docx")
#: Документ із НАВМИСНО порожніми полями -- єдине джерело LLM-викликів на
#: синтетичному корпусі, і саме на ньому заміряно 219-242 с марної роботи.
EMPTY = os.path.join(LEAVE_DOCX, "LEAVE-011.docx")
#: Заповнений документ: жоден сигнал не має вистрілити (нуль регресії).
FULL = os.path.join(LEAVE_DOCX, "LEAVE-001.docx")
#: Чужа редакція бланка: місця полів НЕ локалізовані, тобто порожнеча НЕ
#: доведена -- фолбек мусить лишитись цілим.
FOREIGN = os.path.join(SAMPLES, "leave", "відпускний_квиток_інша_редакція.docx")

_schemas = load_schemas(SCHEMAS_DIR)
_leave = next(s for s in _schemas if s["template"] == "leave_ticket")
_dicts = load_dictionaries(DICTS_DIR)


def _run(path, schema=None, form_recognized=True):
    """-> (results, [(поля, довжина_контексту), ...]) -- другий елемент це
    РЕАЛЬНІ виклики, які пайплайн зробив би до моделі."""
    schema = schema or _leave
    text, blocks = extract_docx_blocks(path)
    calls = []

    def recorder(field_defs, _context_text, _json_schema):
        calls.append(([f["name"] for f in field_defs], len(_context_text)))
        return {}

    results = extract_document(schema, text, blocks, _dicts,
                               llm_extract_batch=recorder,
                               title_phrases=schema_title_phrases(schema),
                               batch_size=4,
                               form_recognized=form_recognized)
    return results, calls


def _skipped(results):
    return {n for n, (_v, r) in results.items()
            if (r or "").startswith(CONFIRMED_EMPTY_SLOT_METHOD + ":")}


def _asked(calls):
    return {n for fields, _ctx in calls for n in fields}


# --- 1. Межа: доведена порожнеча, а не «не знайшли» -------------------------

def test_only_proven_reasons_skip_the_model():
    """Перелік причин-доказів закритий і НЕ містить підозр. Причини
    oversized_block_suspect / type_mismatch / printed_label_in_value означають
    «кандидат є, але дивний» -- там фолбек законний, і дописування їх сюди
    було б тихою втратою значень, а не економією."""
    assert set(PROVEN_EMPTY_REASONS) == {"blank_value", "printed_form_text"}


def test_empty_person_group_is_not_sent_to_the_model():
    """Заміряний випадок: у LEAVE-011 група «звання + ПІБ» локалізована, але
    її підказка -- друкований рядок бланка, тобто вписаного значення немає.
    Виклик коштував 150.9 с і повертав null у 4 з 4 полів."""
    results, calls = _run(EMPTY)
    person = {"rank", "surname", "given_name", "patronymic"}
    assert person <= _skipped(results), (
        f"порожня група особи не скіпнута: "
        f"{ {n: results[n][1] for n in person} }")
    assert not (person & _asked(calls)), (
        "поле з доведено порожнім слотом усе одно пішло в LLM")
    for name in person:
        assert results[name][1] == f"{CONFIRMED_EMPTY_SLOT_METHOD}:printed_hint"


def test_code_alone_does_not_skip_an_undeclared_empty_slot():
    """Межа між П5-А і П5-Б: група «дні + дати» LEAVE-011 сигналами П5-А НЕ
    ловиться (порожній слот дати -- не placeholder і не літеральний друкований
    рядок), тому без оголошеного в СХЕМІ `empty_pattern` вона мусить іти в
    модель. Саме так на LEAVE-007.png LLM відновлює пропис днів, який regex не
    зматчив -- знання про форму живе в YAML, не в .py."""
    bare = dict(_leave, fields=[{k: v for k, v in f.items()
                                 if k != EMPTY_PATTERN_KEY}
                                for f in _leave["fields"]])
    results, calls = _run(EMPTY, schema=bare)
    assert calls, "усі виклики зникли без жодного оголошеного empty_pattern"
    assert {"duration_days", "leave_start_date"} <= _asked(calls)
    assert results["duration_days"][1] == "no_value"


def test_declared_empty_pattern_closes_the_last_group():
    """П5-Б: з оголошеними скелетами порожнечі LEAVE-011 не викликає модель
    ЖОДНОГО разу -- єдине джерело LLM-викликів на синтетичному корпусі
    зникає, і в пакеті без інших прогалин 12B узагалі не вантажиться."""
    results, calls = _run(EMPTY)
    assert calls == [], f"лишились виклики: {calls}"
    for name in ("duration_days", "leave_start_date", "leave_end_date_planned",
                 "actual_return_date"):
        assert results[name] == (
            None, f"{CONFIRMED_EMPTY_SLOT_METHOD}:empty_pattern"), (
            f"{name}: {results[name]}")


def test_empty_pattern_matches_the_declared_blank_and_no_filled_document():
    """Патерн порожнечі мусить збігатися з ПОРОЖНІМ бланком (інакше він
    написаний під щось інше й не спрацює ніколи -- це ловить валідатор) і НЕ
    збігатися з жодним заповненим документом (інакше він забирав би дані)."""
    blank = blank_template_text(_leave)
    assert blank, "бланк не читається -- тест нічого не міряє"
    declared = [f for f in _leave["fields"] if f.get(EMPTY_PATTERN_KEY)]
    assert declared, "жодного оголошеного empty_pattern -- тест нічого не міряє"
    for field in declared:
        assert slot_is_provably_empty(field, blank), (
            f"{field['name']}: скелет порожнечі не знайдено в порожньому бланку")
    for path in sorted(glob.glob(os.path.join(LEAVE_DOCX, "*.docx"))):
        if os.path.basename(path) == "LEAVE-011.docx":
            continue          # навмисно порожній -- єдиний, де збіг очікується
        text, _blocks = extract_docx_blocks(path)
        for field in declared:
            assert not slot_is_provably_empty(field, text), (
                f"{os.path.basename(path)}/{field['name']}: скелет порожнечі "
                "збігся в ЗАПОВНЕНОМУ документі")


def test_found_value_beats_the_emptiness_proof():
    """Значення важливіше за доказ порожнечі: якщо детермінований шлях щось
    знайшов, скіп не має права стерти результат. Інакше двосторінковий бланк
    (друга, незаповнена копія тих самих полів) забирав би знайдені дати."""
    text, blocks = extract_docx_blocks(FULL)
    # Той самий текст, до якого ДОПИСАНО порожній скелет: значення в документі
    # є, доказ порожнечі теж збігається.
    tail = "\nз “____” ________________ 20___ р.  по “____” ________________ 20___ р.\n"
    results = extract_document(_leave, text + tail, blocks, _dicts,
                               llm_extract_batch=lambda *a, **k: {},
                               title_phrases=schema_title_phrases(_leave),
                               batch_size=4)
    for name in ("leave_start_date", "leave_end_date_planned"):
        assert results[name][0] is not None, (
            f"{name}: знайдене значення стерто доказом порожнечі")
        assert results[name][1] == "matched"


def test_foreign_edition_loses_nothing_to_the_cutter():
    """Чужа редакція бланка: різаки не збігаються, місця полів не
    локалізовані. Жоден сигнал не має вистрілити -- інакше правка почала б
    з'їдати фолбек саме там, де він єдиний працює (реальне фото)."""
    results, calls = _run(FOREIGN, form_recognized=False)
    assert not _skipped(results), (
        f"на чужій редакції спрацював скіп: "
        f"{ {n: results[n][1] for n in _skipped(results)} }")
    assert len(_asked(calls)) >= 14, (
        f"на чужій редакції в LLM пішло лише {len(_asked(calls))} полів")


def test_filled_document_is_untouched():
    """Заповнений документ: слотів-порожнеч немає, викликів немає, скіпів
    немає. Нуль регресії за побудовою."""
    results, calls = _run(FULL)
    assert not _skipped(results)
    assert not calls


# --- 2. Межа: порожнє критичне поле не стає фактом -------------------------

def test_confirmed_empty_critical_field_keeps_document_in_review():
    """Чернетка != факт: скіп міняє МАРШРУТ, не статус. Якби порожнеча
    вважалась підтвердженою відповіддю, документ із порожніми полями особи
    поїхав би в базу як confirmed."""
    results, _calls = _run(EMPTY)
    record = build_record(_leave, results, _dicts)
    assert record["facts"][0]["confirmed"] is False
    critical_empty = {n for n in _skipped(results)
                      if field_criticality(
                          next(f for f in _leave["fields"] if f["name"] == n))
                      == "critical"}
    assert critical_empty, "тест нічого не міряє: серед скіпнутих немає критичних"
    assert critical_empty <= set(record["unknown_critical_fields"]), (
        "порожнє критичне поле зникло з unknown_critical_fields -- рев'юер "
        "більше не побачить, що саме треба підтвердити")


def test_reviewer_sees_the_signal_not_silence():
    """Провенанс несе САМ сигнал після двокрапки -- рев'юер має бачити, ЩО це
    висновок різака (і який саме), а не голий null без причини."""
    results, _calls = _run(EMPTY)
    for name in _skipped(results):
        signal = results[name][1].split(":", 1)[1]
        assert signal in set(PROVEN_EMPTY_REASONS) | {"printed_hint",
                                                       "empty_pattern"}, signal


# --- 3. Межа: скіп це «не питали», а не «вирішили» -------------------------

def test_skipped_field_never_carries_a_value_on_any_corpus():
    """По всіх docx обох корпусів: поле з провенансом порожнього слота не має
    значення НІКОЛИ. Інакше «порожньо» означало б водночас «ось значення»."""
    checked = 0
    for template, pattern in (
            ("leave_ticket", "leave/synthetic-2026-05/docx/*.docx"),
            ("deployment_certificate",
             "deployment/synthetic-2026-05/docx/*.docx")):
        schema = next(s for s in _schemas if s["template"] == template)
        paths = sorted(glob.glob(os.path.join(SAMPLES, pattern)))
        assert paths, f"зразків для {template} немає -- тест нічого не міряє"
        for path in paths:
            results, _calls = _run(path, schema=schema)
            for name in _skipped(results):
                checked += 1
                assert results[name][0] is None, (
                    f"{os.path.basename(path)}/{name}: слот оголошено порожнім, "
                    f"але поле несе значення {results[name][0]!r}")
    assert checked, "жодного скіпу на корпусі -- тест нічого не перевірив"


# --- 4. Валідатор схем: недіючий патерн мусить бути ПОМИЛКОЮ --------------

def test_validator_rejects_pattern_that_the_blank_does_not_contain():
    """Скелет порожнечі існує в порожньому бланку за визначенням. Патерн, який
    у ньому не збігається, написаний під щось інше й не спрацює НІКОЛИ -- поле
    й далі витрачало б виклик моделі на відомо порожній слот, і ніде не було б
    жодного сигналу про це."""
    broken = dict(_leave, fields=[
        dict(f, **{EMPTY_PATTERN_KEY: "цього рядка в бланку немає"})
        if f["name"] == "leave_start_date" else f
        for f in _leave["fields"]])
    problems = validate_schema(broken)
    assert any(sev == "error" and EMPTY_PATTERN_KEY in msg
               and "blank_template" in msg for sev, msg in problems), problems


def test_validator_rejects_pattern_on_a_field_that_never_calls_the_model():
    """`empty_pattern` на derived_from-полі не діє ніколи: таке поле в LLM не
    їде взагалі. Мовчки проігнороване налаштування -- рівно те, від чого
    захищає валідатор схем."""
    derived = next(f for f in _leave["fields"]
                   if f.get("extraction") == "derived_from")
    broken = dict(_leave, fields=[
        dict(f, **{EMPTY_PATTERN_KEY: "терміном"}) if f is derived else f
        for f in _leave["fields"]])
    problems = validate_schema(broken)
    assert any(sev == "error" and EMPTY_PATTERN_KEY in msg
               for sev, msg in problems), problems


def test_validator_rejects_invalid_regex():
    broken = dict(_leave, fields=[
        dict(f, **{EMPTY_PATTERN_KEY: "(незакрита дужка"})
        if f["name"] == "leave_start_date" else f
        for f in _leave["fields"]])
    problems = validate_schema(broken)
    assert any(sev == "error" and "невалідний" in msg for sev, msg in problems)


def test_real_schemas_have_no_empty_pattern_complaints():
    """Обидві робочі схеми проходять валідатор без НОВИХ помилок -- інакше
    build_resources виключив би схему з набору, і всі документи шаблону пішли
    б в unresolved."""
    for schema in _schemas:
        errors = [msg for sev, msg in validate_schema(schema)
                  if sev == "error"]
        assert not errors, errors


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
