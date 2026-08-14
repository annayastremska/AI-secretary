#!/usr/bin/env python
"""Оцінка пайплайна проти еталона синтетичного набору.

    python -m eval.evaluate --input data/eval/samples/leave/synthetic-2026-05/docx
    python -m eval.evaluate --input ... --no-llm
    python -m eval.evaluate --input ... --report out.json

Навіщо саме так, а не golden-файли: у наборі є по-польові правильні відповіді
(`data/eval/synthetic-2026-05/per-document/<ID>.json`), тож можна міряти
точність, а не лише "чи змінилась поведінка". Звіт пишеться в JSON, тому два
прогони порівнюються діфом -- це закриває і задачу golden-файлів.

Пайплайн викликається через process_file напряму, БЕЗ сховища й БЕЗ
перенесення файлів: прогін на data/eval/samples/ не має ні виносити зразки з
репозиторію, ні засмічувати індекс дедуплікації (інакше другий прогін того
самого набору віддав би 30 duplicate).

Окремо перевіряється, що пайплайн зробив із навмисними дефектами набору
(`empty_fields`, `unknown_person`, `swapped_dates`, `ocr_noise`) і з парами
"документ + той, що його скасовує" -- бо саме там ціна помилки найвища.
"""
import argparse
import collections
import glob
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from pipeline.config import load_config
from pipeline.run import build_resources, process_file

EVAL_DIR = os.path.join("data", "eval", "synthetic-2026-05")
MAPPING_PATH = os.path.join("eval", "field-mapping.yaml")
DOC_ID_RE = re.compile(r"((?:LEAVE|TRIP)-\d+)", re.I)

# Аліаси звань -- щоб порівняння категорії приймало форму, надруковану в
# документі, а не лише label з довідника.
RANK_ALIASES = {}

UKR_MONTHS = {
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5, "червня": 6,
    "липня": 7, "серпня": 8, "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12,
}


def doc_id_from_filename(name: str):
    m = DOC_ID_RE.search(name or "")
    return m.group(1).upper() if m else None


def load_ground_truth(eval_dir: str = None) -> dict:
    truth = {}
    for path in glob.glob(os.path.join(eval_dir or EVAL_DIR, "per-document", "*.json")):
        with io.open(path, encoding="utf-8") as f:
            data = json.load(f)
        truth[data["id"].upper()] = data
    return truth


def _iso_or_raw(y, mo, d, text):
    """Календарно неможливу дату НЕ перетворюємо в ISO.

    Інакше '32.13.2026' стало б '2026-13-32' -- рядком, який виглядає як ISO і
    може зійтися з таким самим сміттям з іншого боку порівняння. Повертаємо
    сирий текст: він не зійдеться з жодною справжньою датою, і помилка лишиться
    видимою в полі `ours` звіту.
    """
    try:
        import datetime
        datetime.date(int(y), int(mo), int(d))
    except (ValueError, TypeError):
        return text
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"


def as_iso_date(value):
    """Еталон дає і '2026-05-09', і '09.05.2026'; наш вихід -- ISO.

    Формати приймаються різні НАВМИСНО (еталон і пайплайн пишуть по-різному),
    але лише як запис ОДНІЄЇ дати: обидва боки проганяються через цю саму
    функцію, тому "неправильна, але схожа" дата зійтися не може -- 2026-05-09 і
    09.05.2026 це один день, а 09.05.2026 і 05.09.2026 дають різні ISO.
    Двоцифровий рік не приймається взагалі: '09.05.26' лишиться сирим текстом,
    бо вгадування століття -- це вже інтерпретація, а не нормалізація.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "—":
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    if m:
        return _iso_or_raw(m.group(1), m.group(2), m.group(3), text)
    m = re.match(r"^(\d{1,2})[.\s/](\d{1,2})[.\s/](\d{4})$", text)
    if m:
        d, mo, y = m.groups()
        return _iso_or_raw(y, mo, d, text)
    m = re.match(r"^(\d{1,2})\s+([а-яіїєґ]+)\s+(\d{4})$", text.lower())
    if m and m.group(2) in UKR_MONTHS:
        return _iso_or_raw(m.group(3), UKR_MONTHS[m.group(2)], m.group(1), text)
    return text


def norm_text(value):
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip().lower()
    text = text.replace("’", "'").replace("`", "'")
    return text or None


def norm_name(value):
    """ПІБ порівнюємо як МНОЖИНУ токенів у нижньому регістрі: еталон дає
    'Лемешко Соломія Романівна', ми -- 'ЛЕМЕШКО Соломія Романівна'. Регістр і
    порядок не є змістом, а різниця в токенах -- є."""
    if value is None:
        return None
    tokens = [t for t in re.split(r"\s+", str(value).strip()) if t]
    return tuple(sorted(t.lower().replace("’", "'") for t in tokens)) or None


BLANK_MARKS = ("", "—", "–", "-", "н/д", "null", "none")


def is_blank_value(value):
    """Чи наше значення -- «нічого». Порожній dict {code:None,label:None} з
    нерозпізнаної категорії теж «нічого»."""
    if value is None:
        return True
    if isinstance(value, dict):
        return not any(str(v).strip() for v in value.values() if v is not None)
    if isinstance(value, (list, tuple, set)):
        return not any(not is_blank_value(v) for v in value)
    return str(value).strip().lower() in BLANK_MARKS


def printed_state(printed: dict, keys) -> str:
    """Що фізично стоїть на бланку в друкованих ключах поля.

    -> 'blank'   усі присутні ключі порожні -> очікуване значення null;
       'filled'  хоч один непорожній -> звіряємось із правильні_відповіді;
       'unknown' у цього шаблону таких ключів немає -> правило не діє.

    Ключ, якого немає в "надруковано", НЕ вважається порожнім: у посвідченні
    про відрядження немає PERSON_SHORT, і це не означає, що ПІБ не надруковано.
    """
    if not keys:
        return "unknown"
    present = [k for k in keys if k in (printed or {})]
    if not present:
        return "unknown"
    if any(str(printed[k]).strip() for k in present):
        return "filled"
    return "blank"


_MONTH_WORDS = {v: k for k, v in UKR_MONTHS.items()}

# Гомогліфи цифр -- ВЛАСНА копія, свідомо НЕ імпорт з pipeline.normalization.
# Прилад мусить читати гліфи незалежно від того, що робить вимірюваний код:
# спільна функція означала б, що обидва боки помиляються однаково й порівняння
# сходиться за побудовою.
_PRINTED_HOMOGLYPHS = str.maketrans({
    "О": "0", "о": "0", "O": "0", "o": "0", "З": "3", "з": "3",
    "б": "6", "І": "1", "і": "1", "l": "1", "I": "1", "S": "5",
})


def _transcribe_printed(value: str) -> str:
    """Знімає ГЛІФОВЕ псування з надрукованого значення, не змінюючи змісту.

    Тут проходить межа між двома різними вадами набору:
    - `ocr_noise` (TRIP-012: `О7.О5.2О2б`) -- це зіпсоване ЗОБРАЖЕННЯ числа.
      Прочитати його як 07.05.2026 -- транскрипція, і саме цього ми хочемо;
    - `swapped_dates` (TRIP-011: 22 і 20) -- це зіпсований ЗМІСТ. Обидві дати
      прочитані правильно, переставлений порядок, і виправляти його пайплайн
      права не має.

    Без цього поділу правило "очікуємо надруковане" вимагало б від пайплайна
    віддавати `О7.О5.2О2б` як дату -- тобто карало б за нормалізацію гомогліфів.
    """
    return str(value).translate(_PRINTED_HOMOGLYPHS)


def printed_expected(printed: dict, keys, kind):
    """Значення, СКЛАДЕНЕ з блоку `надруковано`, або None якщо не складається.

    Рішення Анни 13.08.2026: **очікуватися має надруковане**. Підстава --
    вада `swapped_dates`: TRIP-011 має на папері START_D=22, END_D=20 (кінець
    раніше початку), а `правильні_відповіді` дають ВИПРАВЛЕНИЙ порядок 20/22.
    Пайплайн читає надруковане й ставить `date_range_error` -- тобто працює
    правильно, -- а оцінювач рахував це двома помилками й винагородив би
    пайплайн, що тихо перевертає дати. Документ при цьому НЕ каже, яка з двох
    дат хибна (може, описка в кінці, а не порядок), тому автовиправлення --
    це здогадка, а не нормалізація.

    Свідомо реалізовано ЛИШЕ для дат: саме там живе відомий клас розходження
    "папір проти сценарію", і саме там складання однозначне. Для решти типів
    розходження не змінює оцінку, а показується в звіті (див. `divergence`) --
    щоб не переписувати правила порівняння там, де їх ніхто не перевіряв.
    """
    if kind != "date" or not keys:
        return None
    values = [_transcribe_printed(str((printed or {}).get(k, "")).strip())
              for k in keys]
    if not all(values):
        return None
    if len(values) == 1:
        return as_iso_date(values[0])
    if len(values) != 3:
        return None
    day, month, year = values
    # Рік на бланку двоцифровий ("26"); місяць -- слово або цифра.
    if year.isdigit() and len(year) == 2:
        year = f"20{year}"
    if month.isdigit():
        month_word = _MONTH_WORDS.get(int(month))
        if month_word is None:
            return None
        month = month_word
    return as_iso_date(f"{day} {month} {year}")


def compare(kind, ours, expected):
    """-> (ok, наше_у_порівнюваній_формі, очікуване_у_порівнюваній_формі)"""
    if kind == "date":
        a, b = as_iso_date(ours), as_iso_date(expected)
    elif kind == "number":
        def num(v):
            try:
                return int(str(v).strip())
            except (TypeError, ValueError):
                return None
        a, b = num(ours), num(expected)
    elif kind == "name":
        a, b = norm_name(ours), norm_name(expected)
    elif kind == "category":
        # Наше значення -- {"code","label"}; еталон -- рядок з документа
        # ("рядовий"). Порівнюємо ще й з АЛІАСАМИ довідника: код soldier має
        # label "Солдат", а в документі надруковано "рядовий" -- це той самий
        # ранг, і рахувати це помилкою екстракції неправильно. Перший прогін
        # через це показав 50% на званні, хоч код був правильний.
        b = norm_text(expected)
        if isinstance(ours, dict):
            forms = {norm_text(ours.get("label")), norm_text(ours.get("code"))}
            for alias, (code, label) in (RANK_ALIASES or {}).items():
                if code == ours.get("code"):
                    forms.add(norm_text(alias))
        else:
            forms = {norm_text(ours)}
        return (b in forms and b is not None), sorted(x for x in forms if x), b
    elif kind == "contains":
        a, b = norm_text(ours), norm_text(expected)
        if a is None or b is None:
            return a == b, a, b
        return (b in a), a, b
    else:
        a, b = norm_text(ours), norm_text(expected)
    return a == b, a, b


def values_by_field(meta: dict, schema: dict) -> dict:
    """{ім'я поля схеми: значення у виході} -- за db_target, як його визначає
    сама схема.

    Без цього оцінювач шукав усе в additional_info й у subject, тому кожне
    поле з db_target fact_value / fact_date_start / fact_date_end виглядало як
    невитягнуте. Перший прогін через це показав 0% на датах відпустки, які
    насправді витягуються.
    """
    facts = meta.get("facts") or []
    main = facts[0] if facts else {}
    subject = meta.get("subject") or {}
    additional = main.get("additional_info") or {}

    out = {}
    for field in (schema or {}).get("fields") or []:
        name = field["name"]
        target = field.get("db_target", "additional_info")
        if target == "person":
            part = field.get("part") or name
            out[name] = subject.get(part, subject.get(name))
        elif target == "fact_value":
            out[name] = main.get("value_code")
        elif target == "fact_date_start":
            out[name] = main.get("date_start")
        elif target == "fact_date_end":
            out[name] = main.get("date_end")
        else:
            out[name] = additional.get(name)
    return out


# Ключі, які пайплайн кладе в meta["subject"] поза списком полів схеми.
SUBJECT_EXTRA_KEYS = {"person_alias", "person_complete"}


def check_mapping(mapping: dict, schemas: list, truth: dict) -> list:
    """Помилки САМОГО зіставлення, а не пайплайна.

    Опечатка в `field:` дає тихий 0% -- значення просто не знаходиться, і це
    неможливо відрізнити від невитягнутого поля. Так уже було: 0% на датах
    відпустки виявились багом оцінювача (див. values_by_field). Тому мапінг
    звіряється зі схемами й з розділом "надруковано" ДО прогону.
    """
    problems = []
    by_template = {s["template"]: s for s in schemas or []}
    printed_seen = collections.defaultdict(set)
    tpl_of = {"відпускний квиток": "leave_ticket",
              "посвідчення про відрядження": "deployment_certificate"}
    for doc in (truth or {}).values():
        tpl = tpl_of.get(doc.get("тип"))
        printed_seen[tpl] |= set((doc.get("надруковано") or {}).keys())

    def check_printed(where, key, spec, templates):
        keys = spec.get("printed")
        if not keys:
            problems.append(f"{where}: '{key}' без `printed` -- правило "
                            f"«порожнє на бланку -> null» для нього не діє")
            return
        for tpl in templates:
            known = printed_seen.get(tpl) or set()
            unknown = [k for k in keys if known and k not in known]
            if unknown and len(unknown) == len(keys):
                problems.append(f"{where}: '{key}' printed={keys} -- жодного "
                                f"такого ключа немає в 'надруковано' шаблону "
                                f"{tpl}; правило порожнього поля не діятиме")

    for tpl, keys in (mapping.get("templates") or {}).items():
        schema = by_template.get(tpl)
        if schema is None:
            problems.append(f"templates.{tpl}: такого шаблону немає в schemas/")
            continue
        names = {f["name"] for f in (schema.get("fields") or [])}
        for key, spec in keys.items():
            if spec["field"] not in names:
                problems.append(f"templates.{tpl}.{key}: поля "
                                f"'{spec['field']}' немає в схемі {tpl} -- "
                                f"перевірка дасть тихий 0%")
            check_printed(f"templates.{tpl}", key, spec, [tpl])

    all_names = set(SUBJECT_EXTRA_KEYS)
    for s in schemas or []:
        all_names |= {f["name"] for f in (s.get("fields") or [])}
    for key, spec in (mapping.get("person") or {}).items():
        if spec["field"] not in all_names:
            problems.append(f"person.{key}: поля '{spec['field']}' немає ні в "
                            f"схемах, ні в subject -- тихий 0%")
        check_printed("person", key, spec, list(by_template))
    return problems


def evaluate_record(meta: dict, truth: dict, mapping: dict, schema: dict) -> dict:
    """Порівнює один запис пайплайна з еталоном одного документа."""
    template = meta.get("template")
    per_template = (mapping.get("templates") or {}).get(template or "", {})
    expected = truth.get("правильні_відповіді") or {}
    person = truth.get("людина") or {}
    printed = truth.get("надруковано") or {}

    facts = meta.get("facts") or []
    main = facts[0] if facts else {}
    subject = meta.get("subject") or {}
    by_field = values_by_field(meta, schema)

    checks = []

    def add(key, field, kind, ours, exp, printed_keys=None):
        state = printed_state(printed, printed_keys)
        if state == "blank":
            # ПОРОЖНЄ ПОЛЕ НА БЛАНКУ -> очікуване значення null (рішення Анни,
            # 13.08.2026). Раніше тут звірялось значення зі СЦЕНАРІЮ, якого на
            # папері немає, і прилад винагороджував галюцинацію: LEAVE-011 має
            # DAYS_WORDS: "", але правильні_відповіді.днів = 11, тож вигадане
            # "11" зараховувалось як правильне, а чесний null -- як помилка.
            ok = is_blank_value(ours)
            checks.append({"key": key, "field": field, "compare": "null",
                           "ok": ok, "surplus": False, "expected_blank": True,
                           "printed_keys": list(printed_keys or ()),
                           "ours": compare(kind, ours, None)[1],
                           "expected": None,
                           "scenario_said": exp})
            return

        # НАДРУКОВАНЕ ПЕРЕВАЖАЄ СЦЕНАРІЙ (рішення Анни, 13.08.2026). Якщо на
        # папері стоїть інше, ніж у `правильні_відповіді`, міряємо проти
        # паперу: пайплайн не може знати сценарій, а виправляти документ
        # самостійно він не має права.
        from_printed = False
        divergence = None
        printed_value = printed_expected(printed, printed_keys, kind)
        if printed_value is not None:
            if compare(kind, printed_value, exp)[0] is False:
                divergence = {"printed": printed_value, "scenario": exp}
                exp, from_printed = printed_value, True

        ok, a, b = compare(kind, ours, exp)
        # "surplus" -- перевірка пройшла ЛИШЕ тому, що порівняння м'яке
        # (`contains`), а наше значення містить зайвий текст понад еталон.
        # Без цього прапорця оцінювач сліпий до цілого класу псування:
        # 13.08.2026 варіант екстракції показав "виправив 4, зламав 0", а
        # насправді приклеїв бланковий шум до 21 уже правильного значення
        # ("військова частина А0000" -> "зобов'язаний прибути до місця
        # служби у військова частина А0000"). Рахуємо окремо, СТАТУС `ok`
        # не змінюємо: інакше всі попередні цифри стали б незрівнянними.
        surplus = bool(ok) and kind == "contains" and a != b
        row = {"key": key, "field": field, "compare": kind,
               "ok": bool(ok), "surplus": surplus,
               "expected_blank": False,
               "from_printed": from_printed,
               "ours": a, "expected": b}
        if divergence:
            row["divergence"] = divergence
        if surplus:
            # Сам зайвий текст, а не лише факт його наявності: без цього в звіті
            # видно "надлишок 16", але не видно, чи це стале "військова частина "
            # з бланка, чи приклеєний сусідній блок.
            row["extra"] = a.replace(b, "…", 1) if (a and b) else a
        checks.append(row)

    for key, spec in per_template.items():
        if key not in expected:
            continue
        field = spec["field"]
        ours = by_field.get(field, subject.get(field))
        add(key, field, spec["compare"], ours, expected[key], spec.get("printed"))

    for key, spec in (mapping.get("person") or {}).items():
        if key in person:
            add(key, spec["field"], spec["compare"], subject.get(spec["field"]),
                person[key], spec.get("printed"))

    return {
        "id": truth["id"],
        "категорія": truth.get("категорія"),
        "вада": truth.get("вада"),
        "чинний": truth.get("чинний"),
        "template": template,
        "template_ok": (template == {"відпускний квиток": "leave_ticket",
                                     "посвідчення про відрядження": "deployment_certificate"}
                        .get(truth.get("тип"))),
        "status": meta.get("status"),
        # Причина, чому документ не дійшов до екстракції. Без неї звіт не
        # відрізняв "OCR віддав порожнє" від "текст є, анкори не збіглись", і
        # саме через це тиха деградація OCR посеред пакетного прогону
        # (7 з 16 фото -> unresolved) виглядала як низька точність екстракції.
        "reason": meta.get("reason"),
        "warnings": meta.get("warnings") or [],
        "confirmed": bool(main.get("confirmed")),
        "review_queue": meta.get("review_queue"),
        "date_range_error": meta.get("date_range_error"),
        "unknown_critical_fields": meta.get("unknown_critical_fields") or [],
        "checks": checks,
        "fields_ok": sum(1 for c in checks if c["ok"]),
        "fields_total": len(checks),
    }


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    parser = argparse.ArgumentParser(description="Оцінка проти еталона синтетичного набору")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--input", required=True, help="папка з документами набору")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--report", default=None, help="куди писати JSON-звіт")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--only", default=None,
                        help="лише ці ID через кому (LEAVE-011,TRIP-012,...) -- "
                             "щоб дорогий прогін (OCR/LLM) брав документи, "
                             "обрані за змістом, а не перші за алфавітом")
    parser.add_argument("--ocr", default=None, choices=["none", "surya"],
                        help="перевизначити ocr.engine, не правлячи config.yaml")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if args.ocr:
        cfg["ocr"] = dict(cfg["ocr"], engine=args.ocr)
    res = build_resources(cfg, force_no_llm=args.no_llm)
    # Без сховища: ні перенесення зразків, ні записів у індекс дедуплікації.
    res["store"] = None
    for w in res["warnings"]:
        print(f"[увага] {w}", file=sys.stderr)

    global RANK_ALIASES
    RANK_ALIASES = res["dictionaries"].get("military_rank", {})

    truth = load_ground_truth()
    with io.open(MAPPING_PATH, encoding="utf-8") as f:
        mapping = yaml.safe_load(f)

    map_problems = check_mapping(mapping, res["schemas"], truth)
    for p in map_problems:
        print(f"[МАПІНГ] {p}", file=sys.stderr)

    paths = sorted(glob.glob(os.path.join(args.input, "*")))
    paths = [p for p in paths if os.path.isfile(p) and doc_id_from_filename(os.path.basename(p))]
    if args.only:
        wanted = {s.strip().upper() for s in args.only.split(",") if s.strip()}
        paths = [p for p in paths if doc_id_from_filename(os.path.basename(p)) in wanted]
    if args.limit:
        paths = paths[:args.limit]
    if not paths:
        print(f"У {args.input} немає файлів набору (LEAVE-*/TRIP-*)", file=sys.stderr)
        return 2

    print(f"LLM: {'вимкнено' if not res['llm'] else 'увімкнено'} | документів: {len(paths)}\n")

    results, unmatched = [], []
    for i, path in enumerate(paths, 1):
        doc_id = doc_id_from_filename(os.path.basename(path))
        if doc_id not in truth:
            unmatched.append(os.path.basename(path))
            continue
        meta = process_file(path, res, cfg)
        schema = next((s for s in res["schemas"] if s["template"] == meta.get("template")), None)
        row = evaluate_record(meta, truth[doc_id], mapping, schema)
        row["source_file"] = os.path.basename(path)
        results.append(row)
        mark = "OK " if row["fields_ok"] == row["fields_total"] else "   "
        print(f"{i:>3}/{len(paths)} {mark} {doc_id:10} {str(row['template']):24} "
              f"{row['status']:13} поля {row['fields_ok']}/{row['fields_total']}")

    # --- зведення ---
    per_key = collections.defaultdict(lambda: [0, 0])
    for row in results:
        for c in row["checks"]:
            per_key[c["key"]][1] += 1
            if c["ok"]:
                per_key[c["key"]][0] += 1

    print("\n=== точність по полях (усі документи) ===")
    for key, (ok, total) in sorted(per_key.items(), key=lambda kv: kv[1][0] / max(1, kv[1][1])):
        print(f"  {key:20} {ok:>3}/{total:<3} {100 * ok / max(1, total):5.1f}%")

    # Поля, що не працюють УЗАГАЛІ, окремим блоком. Агрегат їх ховає:
    # 69.3% по корпусу читається як "помірно погано", тоді як за ним стояли
    # звання 0/16 і місце 0/16 -- тобто поле не працює жодного разу.
    dead = [(k, t) for k, (o, t) in sorted(per_key.items()) if o == 0 and t]
    if dead:
        print("\n  !! ПОЛЯ НА НУЛІ (жодного правильного значення):")
        for key, total in dead:
            print(f"     {key:20} 0/{total}")

    # Поле, що працює 1 раз із 14, зламане так само, як те, що не працює
    # жодного разу, але блок вище його не показує, а середнє -- тим більше.
    weak = [(k, o, t) for k, (o, t) in sorted(per_key.items())
            if t and 0 < o and o / t < 0.5]
    if weak:
        print("\n  !  ПОЛЯ НИЖЧЕ 50% (працюють як виняток, не як правило):")
        for key, ok, total in weak:
            print(f"     {key:20} {ok}/{total}")

    # Значення, що пройшли лише завдяки м'якому `contains`.
    surplus = collections.Counter(
        c["key"] for row in results for c in row["checks"] if c.get("surplus")
    )
    if surplus:
        print("\n  ~ ЗАРАХОВАНО ЧЕРЕЗ contains, але зі зайвим текстом "
              "(еталон усередині нашого значення, не дорівнює йому):")
        for key, n in surplus.most_common():
            extras = collections.Counter(
                c.get("extra") for row in results for c in row["checks"]
                if c.get("surplus") and c["key"] == key
            )
            shape = "; ".join(f"{e!r}×{n2}" for e, n2 in extras.most_common(3))
            print(f"     {key:20} {n:>3}  зайве: {shape}")
        print("     ^ це НЕ помилки за поточним правилом порівняння, але саме "
              "тут ховається приклеєний бланковий шум. '…' -- місце еталонного "
              "значення; усе решта в лапках наш екстрактор додав від себе.")

    # Поля, ПОРОЖНІ на бланку: очікуване значення -- null. Показуємо окремо,
    # бо це «безкоштовні» перевірки: пайплайн, який нічого не витягує, отримує
    # їх задарма. Без цього блоку зростання загальної цифри після 13.08 не
    # можна відрізнити від справжнього покращення екстракції.
    blanks = [(row["id"], c) for row in results for c in row["checks"]
              if c.get("expected_blank")]
    if blanks:
        good = sum(1 for _, c in blanks if c["ok"])
        print(f"\n  ø ОЧІКУЄТЬСЯ NULL (поле порожнє на бланку): "
              f"{good}/{len(blanks)} правильно")
        for doc_id, c in blanks:
            verdict = "null, як і слід" if c["ok"] else f"ВИГАДАНО {c['ours']!r}"
            print(f"     {doc_id:10} {c['key']:18} {verdict}"
                  f"   (сценарій казав {c.get('scenario_said')!r})")
        print("     ^ на папері цих значень НЕМА. Раніше тут звірялось значення "
              "зі сценарію, тому вигадане правильне число зараховувалось, а "
              "чесний null -- ні.")

    # Розходження "папір проти сценарію": еталон дає одне, на бланку інше.
    # Показуємо явно, бо саме тут ми свідомо міряємо проти паперу, і читач
    # звіту має бачити, що цифра стосується надрукованого, а не задуманого.
    diverged = [(r["id"], c) for r in results for c in r["checks"] if c.get("divergence")]
    if diverged:
        good = sum(1 for _, c in diverged if c["ok"])
        print(f"\n  ≠ ПАПІР ПРОТИ СЦЕНАРІЮ: {good}/{len(diverged)} правильно "
              "(міряємо проти НАДРУКОВАНОГО -- рішення 13.08.2026)")
        for doc_id, c in diverged:
            d = c["divergence"]
            print(f"     {doc_id:10} {c['key']:18} на папері {d['printed']!r}, "
                  f"сценарій казав {d['scenario']!r}, ми {c['ours']!r}"
                  f"  {'+' if c['ok'] else '-'}")
        print("     ^ пайплайн не може знати сценарій і не має права виправляти "
              "документ сам; суперечність позначається date_range_error.")

    ok_fields = sum(r["fields_ok"] for r in results)
    all_fields = sum(r["fields_total"] for r in results)
    print(f"\nусього полів правильно: {ok_fields}/{all_fields} "
          f"({100 * ok_fields / max(1, all_fields):.1f}%)")
    if blanks:
        pen_ok = ok_fields - sum(1 for _, c in blanks if c["ok"])
        pen_all = all_fields - len(blanks)
        print(f"  з них порожніх на бланку (очікується null): {len(blanks)}; "
              f"без них: {pen_ok}/{pen_all} "
              f"({100 * pen_ok / max(1, pen_all):.1f}%) -- саме цю цифру "
              f"порівнюйте з замірами до 13.08")
    print("шаблон визначено правильно: "
          f"{sum(1 for r in results if r['template_ok'])}/{len(results)}")
    print("статуси:", dict(collections.Counter(r["status"] for r in results)))
    print("підтверджено (основний факт):",
          sum(1 for r in results if r["confirmed"]), f"з {len(results)}")

    print("\n=== навмисні дефекти набору ===")
    for row in results:
        if row["вада"]:
            print(f"  {row['id']:10} {row['вада']:16} status={row['status']:13} "
                  f"confirmed={row['confirmed']} date_range_error={bool(row['date_range_error'])} "
                  f"поля {row['fields_ok']}/{row['fields_total']}")

    print("\n=== пари (документ + той, що скасовує) ===")
    for row in results:
        if row["категорія"] == "пара":
            print(f"  {row['id']:10} чинний={row['чинний']:4} status={row['status']:13} "
                  f"confirmed={row['confirmed']}")
    print("  (пайплайн НЕ знає про скасування -- це очікувано, "
          "зв'язку документ->документ у контракті немає)")

    if unmatched:
        print(f"\nбез еталона ({len(unmatched)}): {', '.join(unmatched[:5])}")

    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with io.open(args.report, "w", encoding="utf-8") as f:
            json.dump({"input": args.input, "llm": bool(res["llm"]),
                       "mapping_problems": map_problems,
                       "per_key": {k: v for k, v in sorted(per_key.items())},
                       "blank_expected": [
                           {"id": i, "key": c["key"], "ok": c["ok"],
                            "ours": c["ours"], "scenario_said": c.get("scenario_said")}
                           for i, c in blanks],
                       "surplus": dict(surplus),
                       "results": results}, f, ensure_ascii=False, indent=1, default=str)
        print(f"\nзвіт: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
