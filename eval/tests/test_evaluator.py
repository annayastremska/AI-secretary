# -*- coding: utf-8 -*-
"""Тести на сам ВИМІРЮВАЛЬНИЙ ПРИЛАД (eval/evaluate.py).

Окремий файл, а не tests/test_regressions.py: там регресії ПАЙПЛАЙНА, тут --
регресії ОЦІНЮВАЧА. Помилка оцінювача дорожча за помилку пайплайна: вона
робить недійсними всі цифри одразу, і вже двічі так було (0% на датах
відпустки = баг values_by_field; «правильна» галюцинація на LEAVE-011 =
відсутність правила порожнього поля).

Запуск:
    python tests/test_evaluator.py
"""
import io
import os
import sys

# Три рівні вгору, не два: після переносу в структуру KSE файл лежить у
# eval/tests/, а не в tests/ у корені. Два dirname() давали _PROJECT_ROOT =
# .../eval, і всі шляхи до даних мовчки ставали .../eval/data/... -- тести
# падали з FileNotFoundError, хоча ні прилад, ні пайплайн не змінювались.
_EVAL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_EVAL_DIR)
sys.path.insert(0, _PROJECT_ROOT)
# evaluate.py тепер живе в eval/, а не в scripts/ -- саме звідси `import
# evaluate as ev` нижче й бере прилад.
sys.path.insert(0, _EVAL_DIR)

import yaml

import evaluate as ev


# --- Задача 1: порожнє поле на бланку -> очікується null -------------------

def test_printed_state_blank_when_all_keys_empty():
    printed = {"START_D": "", "START_M": "", "START_Y": ""}
    assert ev.printed_state(printed, ["START_D", "START_M", "START_Y"]) == "blank"


def test_printed_state_filled_when_any_key_nonempty():
    printed = {"START_D": "10", "START_M": "", "START_Y": ""}
    assert ev.printed_state(printed, ["START_D", "START_M", "START_Y"]) == "filled"


def test_printed_state_unknown_when_keys_absent_from_template():
    """PERSON_SHORT немає в посвідченні про відрядження. Відсутній ключ НЕ
    порожній -- інакше ПІБ у всіх TRIP очікувався б як null."""
    printed = {"PERSON_FULL": "старший сержант СКИБА Остап Орестович"}
    assert ev.printed_state(printed, ["PERSON_SHORT"]) == "unknown"
    assert ev.printed_state({}, ["PERSON_FULL"]) == "unknown"
    assert ev.printed_state({"PERSON_FULL": "x"}, None) == "unknown"


def test_blank_value_detection():
    assert ev.is_blank_value(None)
    assert ev.is_blank_value("")
    assert ev.is_blank_value("  ")
    assert ev.is_blank_value("—")
    assert ev.is_blank_value({"code": None, "label": None})
    assert not ev.is_blank_value(0)          # число 0 -- це значення
    assert not ev.is_blank_value("11")
    assert not ev.is_blank_value({"code": "soldier", "label": "Солдат"})


def _leave_011_truth():
    path = os.path.join(_PROJECT_ROOT, "data", "eval", "synthetic-2026-05",
                        "per-document", "LEAVE-011.json")
    import json
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def _mapping():
    # field-mapping.yaml -- частина ПРИЛАДУ, тому переїхав у eval/ разом із
    # evaluate.py; у data/eval/ лишились тільки зразки й еталонні відповіді.
    with io.open(os.path.join(_EVAL_DIR, "field-mapping.yaml"),
                 encoding="utf-8") as f:
        return yaml.safe_load(f)


_LEAVE_SCHEMA = {"template": "leave_ticket", "fields": [
    {"name": "document_number"}, {"name": "document_date"},
    {"name": "leave_start_date", "db_target": "fact_date_start"},
    {"name": "leave_end_date_planned", "db_target": "fact_date_end"},
    {"name": "actual_return_date"}, {"name": "duration_days"},
    {"name": "destination_place"},
    {"name": "leave_type_and_destination", "db_target": "fact_value"},
    {"name": "unit_to_report"},
    {"name": "rank", "db_target": "person", "part": "rank"},
    {"name": "surname", "db_target": "person", "part": "surname"},
    {"name": "given_name", "db_target": "person", "part": "given_name"},
    {"name": "patronymic", "db_target": "person", "part": "patronymic"},
]}


def _leave_011_meta(**over):
    """Вихід пайплайна на LEAVE-011: усе, чого немає на бланку, -- None."""
    meta = {
        "template": "leave_ticket",
        "status": "needs_review",
        "subject": {"rank": None, "surname": None, "given_name": None,
                    "patronymic": None, "person_alias": None},
        "facts": [{"fact_type": "leave", "value_code": "відпустка у зв’язку з навчанням",
                   "date_start": None, "date_end": None, "confirmed": False,
                   "additional_info": {"destination_place": "м. Хмельницький",
                                       "duration_days": None,
                                       "actual_return_date": None,
                                       "document_number": "143",
                                       "document_date": "2026-05-07",
                                       "unit_to_report": "військова частина А0000"}}],
    }
    meta.update(over)
    return meta


def _checks(meta):
    row = ev.evaluate_record(meta, _leave_011_truth(), _mapping(), _LEAVE_SCHEMA)
    return {c["key"]: c for c in row["checks"]}


def test_honest_null_is_success_on_empty_blank():
    """ГОЛОВНЕ: пайплайн, що чесно віддає null на порожньому бланку, отримує
    успіх. До 13.08 він тут карався."""
    checks = _checks(_leave_011_meta())
    for key in ("початок", "кінець", "повернення", "днів", "ПІБ", "звання"):
        assert checks[key]["ok"], key
        assert checks[key]["expected_blank"], key
        assert checks[key]["expected"] is None, key


def test_hallucinated_scenario_value_is_failure_on_empty_blank():
    """І симетрично: вигадане значення, яке ВИПАДКОВО збігається зі сценарієм
    (днів = 11, START_D = 09.05), більше не зараховується. Саме це робило
    прилад шкідливим -- він винагороджував галюцинацію."""
    meta = _leave_011_meta()
    info = meta["facts"][0]["additional_info"]
    info["duration_days"] = 11
    info["actual_return_date"] = "2026-05-20"
    meta["facts"][0]["date_start"] = "2026-05-09"
    meta["facts"][0]["date_end"] = "2026-05-19"
    meta["subject"] = {"rank": {"code": "soldier", "label": "Солдат"},
                       "surname": "АРТЕМЕНКО", "given_name": "Прохір",
                       "patronymic": "Віталійович",
                       "person_alias": "Артеменко Прохір Віталійович"}
    checks = _checks(meta)
    for key in ("початок", "кінець", "повернення", "днів", "ПІБ", "звання"):
        assert not checks[key]["ok"], f"{key}: галюцинацію зараховано як правильну"


def test_filled_fields_still_compared_against_scenario():
    """Правило порожнього поля не має чіпати заповнені поля того ж документа."""
    checks = _checks(_leave_011_meta())
    for key in ("номер_документа", "дата_видачі", "місце", "підстава"):
        assert checks[key]["ok"], key
        assert not checks[key]["expected_blank"], key


# --- Задача 1b: swapped_dates -- надруковане ПЛЮС позначка -----------------
#
# TRIP-011 має на папері START_D=22, END_D=20. Правильна відповідь -- рівно
# надруковане (22/20) І позначена суперечність, від якої запис не йде в
# підрахунки. До 14.08.2026 міряли лише значення, тому пайплайн, що промовчав,
# мав ту саму оцінку, що пайплайн, який позначив.

_TRIP_SCHEMA = {"template": "deployment_certificate", "fields": [
    {"name": "document_number"}, {"name": "document_date"},
    {"name": "deployment_start_date", "db_target": "fact_date_start"},
    {"name": "deployment_end_date", "db_target": "fact_date_end"},
    {"name": "deployment_days"}, {"name": "destination_points", "db_target": "fact_value"},
    {"name": "destination_org"}, {"name": "purpose"},
    {"name": "rank", "db_target": "person", "part": "rank"},
    {"name": "surname", "db_target": "person", "part": "surname"},
]}


def _trip_011_truth():
    import json
    path = os.path.join(_PROJECT_ROOT, "data", "eval", "synthetic-2026-05",
                        "per-document", "TRIP-011.json")
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def _trip_011_meta(start, end, flagged, confirmed):
    return {
        "template": "deployment_certificate",
        "status": "needs_review" if not confirmed else "confirmed",
        "date_range_error": (f"date_start ({start}) > date_end ({end})"
                             if flagged else None),
        "subject": {"rank": {"code": "senior_sergeant", "label": "Старший сержант"},
                    "person_alias": "Скиба Остап Орестович"},
        "facts": [{"value_code": "м. Полтава", "date_start": start, "date_end": end,
                   "confirmed": confirmed,
                   "additional_info": {
                       "document_number": "244", "document_date": "2026-05-19",
                       "deployment_days": 3,
                       "destination_org": "військова частина А4419",
                       "purpose": "отримання засобів індивідуального захисту"}}],
    }


def _trip_checks(meta, truth=None):
    row = ev.evaluate_record(meta, truth or _trip_011_truth(), _mapping(), _TRIP_SCHEMA)
    return {c["key"]: c for c in row["checks"]}


def test_printed_range_conflict_read_from_paper_not_from_defect_label():
    """Умову беремо з 'надруковано'. Поле 'вада' приладу невідоме навмисно --
    інакше на реальному бланку з такою самою опискою перевірки не було б."""
    mapping = _mapping()
    per_tpl = mapping["templates"]["deployment_certificate"]
    spec = mapping["range_checks"]["deployment_certificate"]
    got = ev.printed_range_conflict(_trip_011_truth()["надруковано"], per_tpl, spec)
    assert got and (got["start"], got["end"]) == ("2026-05-22", "2026-05-20")


def test_no_conflict_check_on_a_healthy_document():
    """Здоровий діапазон не додає перевірки -- інакше загальна цифра корпусу
    виросла б на кожному документі, а не на суперечливих."""
    mapping = _mapping()
    per_tpl = mapping["templates"]["deployment_certificate"]
    spec = mapping["range_checks"]["deployment_certificate"]
    healthy = _trip_011_truth()
    healthy["надруковано"] = dict(healthy["надруковано"], START_D="20", END_D="22")
    healthy["правильні_відповіді"]["початок"] = "2026-05-20"
    healthy["правильні_відповіді"]["кінець"] = "2026-05-22"
    assert ev.printed_range_conflict(healthy["надруковано"], per_tpl, spec) is None
    meta = _trip_011_meta("2026-05-20", "2026-05-22", flagged=False, confirmed=True)
    checks = _trip_checks(meta, healthy)
    assert "суперечність_діапазону" not in checks
    assert checks["початок"]["ok"] and checks["кінець"]["ok"]


def test_honest_printed_dates_with_flag_is_success():
    """ГОЛОВНЕ: віддав надруковане, позначив суперечність, не порахував
    фактом -- три перевірки з трьох."""
    checks = _trip_checks(_trip_011_meta("2026-05-22", "2026-05-20",
                                         flagged=True, confirmed=False))
    assert checks["початок"]["ok"] and checks["початок"]["from_printed"]
    assert checks["кінець"]["ok"] and checks["кінець"]["from_printed"]
    assert checks["суперечність_діапазону"]["ok"]


def test_silent_reordering_of_dates_is_a_failure():
    """Пайплайн, що тихо перевернув дати «як має бути», карається: сценарій
    (20/22) більше не є еталоном, а суперечності він не позначив."""
    checks = _trip_checks(_trip_011_meta("2026-05-20", "2026-05-22",
                                         flagged=False, confirmed=True))
    assert not checks["початок"]["ok"], "виправлений порядок зараховано"
    assert not checks["кінець"]["ok"], "виправлений порядок зараховано"


def test_printed_dates_without_flag_is_a_failure():
    """САМЕ ЦЕ й було невидиме: значення надруковані, а суперечність
    промовчана -- до 14.08 такий пайплайн отримував 10/10, як і чесний."""
    checks = _trip_checks(_trip_011_meta("2026-05-22", "2026-05-20",
                                         flagged=False, confirmed=False))
    assert checks["початок"]["ok"] and checks["кінець"]["ok"]
    assert not checks["суперечність_діапазону"]["ok"], \
        "промовчану суперечність зараховано як правильну поведінку"


def test_flagged_but_still_counted_as_fact_is_a_failure():
    """Позначка без наслідку -- не позначка: «чернетка ≠ факт» означає, що
    запис із суперечливим діапазоном не входить у підрахунки."""
    checks = _trip_checks(_trip_011_meta("2026-05-22", "2026-05-20",
                                         flagged=True, confirmed=True))
    assert not checks["суперечність_діапазону"]["ok"]


def test_range_check_typo_is_reported_not_silently_disabled():
    schemas = []
    for name in ("leave_ticket", "deployment_certificate"):
        with io.open(os.path.join(_PROJECT_ROOT, "pipeline", "schemas", name + ".yaml"),
                     encoding="utf-8") as f:
            schemas.append(yaml.safe_load(f))
    truth = ev.load_ground_truth(os.path.join(
        _PROJECT_ROOT, "data", "eval", "synthetic-2026-05"))
    broken = _mapping()
    broken["range_checks"]["deployment_certificate"]["end"] = "кiнець"  # лат. i
    problems = ev.check_mapping(broken, schemas, truth)
    assert any("range_checks" in p for p in problems), problems


# --- Задача 2: надлишок у значеннях ---------------------------------------

def test_contains_flags_surplus_and_shows_the_extra_text():
    ours = "м. Полтава, військова частина А4419"
    ok, a, b = ev.compare("contains", ours, "м. Полтава")
    assert ok and a != b, "надлишок мусить проходити contains, але бути видимим"
    assert a.replace(b, "…", 1) == "…, військова частина а4419"


def test_exact_rejects_surplus():
    assert not ev.compare("exact", "м. Полтава, військова частина А4419",
                          "м. Полтава")[0]


def test_only_justified_fields_keep_contains():
    """М'якого порівняння в мапінгу НЕМА ЖОДНОГО, і новий не має з'явитись тихо.

    ОНОВЛЕНО 14.08.2026: раніше очікувався рівно один виправданий випадок --
    `unit_to_report`, бо на бланку "військова частина А0000" надруковано одним
    рядком, а еталон записує лише код. Обґрунтування знято: викидати друковані
    слова форми -- це робота екстрактора, і схема тепер знімає цей префікс
    (`strip_prefix`), тож значення -- чистий "А0000".

    Наслідок, вартий тесту: порівняння стало ПОВНІСТЮ строгим, тобто
    приклеєний сусідній блок більше не може зійтися ніде. Саме `contains`
    приховував два справжні дефекти (рід у strip_prefix; DEST_ORG, приклеєний
    до місця), тому поява нового м'якого порівняння мусить бути помітною."""
    mapping = _mapping()
    soft = {(tpl, key)
            for tpl, keys in mapping["templates"].items()
            for key, spec in keys.items() if spec["compare"] == "contains"}
    soft |= {("person", key) for key, spec in mapping["person"].items()
             if spec["compare"] == "contains"}
    assert soft == set(), sorted(soft)


# --- Задача 3: решта сліпоти ----------------------------------------------

def test_every_mapped_field_exists_in_its_schema():
    """Опечатка в `field:` дає тихий 0%, а не помилку. Так уже було з датами
    відпустки. Тест ловить це без прогону документів."""
    schemas = []
    for name in ("leave_ticket", "deployment_certificate"):
        with io.open(os.path.join(_PROJECT_ROOT, "pipeline", "schemas", name + ".yaml"),
                     encoding="utf-8") as f:
            schemas.append(yaml.safe_load(f))
    truth = ev.load_ground_truth(os.path.join(
        _PROJECT_ROOT, "data", "eval", "synthetic-2026-05"))
    problems = ev.check_mapping(_mapping(), schemas, truth)
    assert problems == [], problems


def test_values_by_field_covers_every_db_target_in_use():
    """values_by_field шукає значення за db_target. db_target, якого вона не
    знає, тихо падає в additional_info -> None -> 0% на живому полі."""
    meta = {"facts": [{"value_code": "V", "date_start": "S", "date_end": "E",
                       "additional_info": {"plain": "P"}}],
            "subject": {"rank": {"code": "soldier"}, "surname": "X"}}
    schema = {"fields": [
        {"name": "v", "db_target": "fact_value"},
        {"name": "s", "db_target": "fact_date_start"},
        {"name": "e", "db_target": "fact_date_end"},
        {"name": "plain", "db_target": "additional_info"},
        {"name": "rank", "db_target": "person", "part": "rank"},
        {"name": "surname", "db_target": "person", "part": "surname"},
    ]}
    got = ev.values_by_field(meta, schema)
    assert got == {"v": "V", "s": "S", "e": "E", "plain": "P",
                   "rank": {"code": "soldier"}, "surname": "X"}


def test_impossible_date_is_not_silently_turned_into_iso():
    """'2026-13-32' не має виглядати як ISO-дата: інакше два однакові сміттєві
    рядки з різних боків порівняння зійшлись би як «правильна» дата."""
    assert ev.as_iso_date("32.13.2026") == "32.13.2026"
    assert ev.as_iso_date("2026-02-30") == "2026-02-30"
    assert ev.as_iso_date("09.05.2026") == "2026-05-09"
    assert ev.as_iso_date("9 травня 2026") == "2026-05-09"


def test_two_digit_year_is_not_guessed():
    """На бланку рік друкується як '26'. Вгадувати століття -- інтерпретація,
    а не нормалізація; такий рядок мусить лишитись сирим і не зійтися."""
    assert ev.as_iso_date("09.05.26") == "09.05.26"
    assert not ev.compare("date", "09.05.26", "2026-05-09")[0]


def test_similar_but_wrong_date_does_not_converge():
    assert not ev.compare("date", "2026-05-10", "09.05.2026")[0]
    assert not ev.compare("date", "05.09.2026", "09.05.2026")[0]
    assert ev.compare("date", "09.05.2026", "2026-05-09")[0]


def test_category_accepts_aliases_of_the_same_rank_only():
    ev.RANK_ALIASES = {"рядовий": ("soldier", "Солдат"),
                       "солдат": ("soldier", "Солдат"),
                       "ст. сержант": ("senior_sergeant", "Старший сержант"),
                       "старший сержант": ("senior_sergeant", "Старший сержант")}
    ours = {"code": "soldier", "label": "Солдат"}
    assert ev.compare("category", ours, "рядовий")[0]
    assert ev.compare("category", ours, "солдат")[0]
    # ЧУЖЕ звання -- ні, навіть якщо рядок схожий на аліас іншого коду
    assert not ev.compare("category", ours, "старший сержант")[0]
    # і не підрядок: 'сержант' не має проходити за 'старший сержант'
    ours2 = {"code": "senior_sergeant", "label": "Старший сержант"}
    assert not ev.compare("category", ours2, "сержант")[0]
    assert not ev.compare("category", None, "рядовий")[0]


def test_rank_dictionary_has_no_alias_shared_by_two_codes():
    """Аліаси приймаються ЛИШЕ тому, що кожен належить одному кодові. Якщо
    довідник колись дасть один аліас двом званням, порівняння стане щедрим
    непомітно."""
    path = os.path.join(_PROJECT_ROOT, "pipeline", "dictionaries", "military_rank.yaml")
    if not os.path.exists(path):
        return
    with io.open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    entries = data.get("values") or data.get("entries") or []
    assert entries, "структура довідника змінилась -- тест став порожнім"
    seen = {}
    for entry in entries:
        code = entry.get("code")
        for alias in [entry.get("label")] + list(entry.get("aliases") or []):
            if not alias:
                continue
            k = str(alias).strip().lower()
            assert seen.get(k, code) == code, f"аліас {k!r}: {seen[k]} і {code}"
            seen[k] = code


def _run():
    fails = 0
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        try:
            fn()
            print("  OK   " + name)
        except AssertionError as e:
            fails += 1
            print("  FAIL " + name + ": " + str(e))
        except Exception as e:            # noqa: BLE001
            fails += 1
            print("  ERR  " + name + ": " + type(e).__name__ + ": " + str(e))
    print(("\nвсі тести пройшли" if not fails else "\nневдач: %d" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_run())


def test_expected_printed_measures_field_absent_from_scenario_answers():
    """Поле, для якого в `правильні_відповіді` ключа НЕМАЄ, все одно міряється --
    очікуване береться з розділу «надруковано».

    Це не дрібниця приладу, а закриття заміряної сліпоти: position_and_workplace
    є в схемі, є на бланку (POSITION) і через `dimension: position` доходить до
    facts окремим фактом, але сценарної відповіді для нього не існує, тому
    порівняння тихо пропускалось (`if key not in expected: continue`). Місяць
    значення на pdf обрізалось до "частина А0000" з провенансом `matched`, і
    жодна цифра приладу не змінилась ані від поломки, ані від виправлення
    (known-weak-spots 5.7).
    """
    mapping = _mapping()
    spec = mapping["templates"]["deployment_certificate"]["посада"]
    assert spec["expected_printed"] == "POSITION"
    assert spec["field"] == "position_and_workplace"
    # exact, а не contains: саме обрізаний хвіст був дефектом, і м'яке
    # порівняння зарахувало б його.
    assert spec["compare"] == "exact"


def test_mapping_key_that_is_never_answered_is_reported():
    """Ключ мапінгу, якого немає ні в еталонних відповідях, ні в
    `expected_printed`, мусить бути ПОМИЛКОЮ мапінгу, а не тишею.

    Тиша тут -- це і є механізм, яким поломка поля стає невидимою: перевірка
    пропускається, а звіт показує 100% на тому, що не міряли.
    """
    truth = {"TRIP-004": {"тип": "посвідчення про відрядження",
                          "правильні_відповіді": {"номер_документа": "209"},
                          "надруковано": {"DOC_NUMBER": "209"}}}
    schema = {"template": "deployment_certificate",
              "fields": [{"name": "document_number"}, {"name": "ghost_field"}]}
    # `doc_types` тут не косметика: з 23.08.2026 перевірка «ключ не міряється
    # взагалі» ставиться лише до типів, ЯКІ Є в завантаженому еталоні (інакше
    # прогін на holdout, де немає ні квитків, ні посвідчень, видавав 24 хибні
    # помилки й код виходу 1). Без цього рядка тип документа не резолвиться в
    # шаблон, і фікстура міряла б поведінку, якої в реальному мапінгу немає.
    mapping = {"doc_types": {"посвідчення про відрядження": "deployment_certificate"},
               "templates": {"deployment_certificate": {
        "номер_документа": {"field": "document_number", "compare": "exact",
                            "printed": ["DOC_NUMBER"]},
        "привид": {"field": "ghost_field", "compare": "exact",
                   "printed": ["DOC_NUMBER"]},
    }}}
    problems = ev.check_mapping(mapping, [schema], truth)
    assert any("привид" in p and "НЕ міряється" in p for p in problems), problems
    assert not any("номер_документа" in p for p in problems), problems


def test_position_is_measured_on_both_formats_of_the_real_mapping():
    """Регресія на самому мапінгу: `посада` мусить лишатися виміряною.

    Прибрати рядок із field-mapping.yaml -- однорядкова правка, яка знову
    зробить поле невидимим і при цьому НЕ зламає жодного іншого тесту.
    """
    mapping = _mapping()
    keys = mapping["templates"]["deployment_certificate"]
    measured = [k for k, s in keys.items()
                if s.get("expected_printed") or s.get("printed")]
    assert "посада" in measured
