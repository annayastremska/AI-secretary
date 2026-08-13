#!/usr/bin/env python
"""Оцінка пайплайна проти еталона синтетичного набору.

    python scripts/evaluate.py --input data/samples/leave/synthetic-2026-05/docx
    python scripts/evaluate.py --input ... --no-llm
    python scripts/evaluate.py --input ... --report out.json

Навіщо саме так, а не golden-файли: у наборі є по-польові правильні відповіді
(`data/eval/synthetic-2026-05/per-document/<ID>.json`), тож можна міряти
точність, а не лише "чи змінилась поведінка". Звіт пишеться в JSON, тому два
прогони порівнюються діфом -- це закриває і задачу golden-файлів.

Пайплайн викликається через process_file напряму, БЕЗ сховища й БЕЗ
перенесення файлів: прогін на data/samples/ не має ні виносити зразки з
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
MAPPING_PATH = os.path.join("data", "eval", "field-mapping.yaml")
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


def load_ground_truth() -> dict:
    truth = {}
    for path in glob.glob(os.path.join(EVAL_DIR, "per-document", "*.json")):
        with io.open(path, encoding="utf-8") as f:
            data = json.load(f)
        truth[data["id"].upper()] = data
    return truth


def as_iso_date(value):
    """Еталон дає і '2026-05-09', і '09.05.2026'; наш вихід -- ISO."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "—":
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    if m:
        return text
    m = re.match(r"^(\d{1,2})[.\s/](\d{1,2})[.\s/](\d{4})$", text)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    m = re.match(r"^(\d{1,2})\s+([а-яіїєґ]+)\s+(\d{4})$", text.lower())
    if m and m.group(2) in UKR_MONTHS:
        return f"{m.group(3)}-{UKR_MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"
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


def evaluate_record(meta: dict, truth: dict, mapping: dict, schema: dict) -> dict:
    """Порівнює один запис пайплайна з еталоном одного документа."""
    template = meta.get("template")
    per_template = (mapping.get("templates") or {}).get(template or "", {})
    expected = truth.get("правильні_відповіді") or {}
    person = truth.get("людина") or {}

    facts = meta.get("facts") or []
    main = facts[0] if facts else {}
    subject = meta.get("subject") or {}
    by_field = values_by_field(meta, schema)

    checks = []

    def add(key, field, kind, ours, exp):
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
        checks.append({"key": key, "field": field, "compare": kind,
                       "ok": bool(ok), "surplus": surplus,
                       "ours": a, "expected": b})

    for key, spec in per_template.items():
        if key not in expected:
            continue
        field = spec["field"]
        ours = by_field.get(field, subject.get(field))
        add(key, field, spec["compare"], ours, expected[key])

    for key, spec in (mapping.get("person") or {}).items():
        if key in person:
            add(key, spec["field"], spec["compare"], subject.get(spec["field"]), person[key])

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

    # Значення, що пройшли лише завдяки м'якому `contains`.
    surplus = collections.Counter(
        c["key"] for row in results for c in row["checks"] if c.get("surplus")
    )
    if surplus:
        print("\n  ~ ЗАРАХОВАНО ЧЕРЕЗ contains, але зі зайвим текстом "
              "(еталон усередині нашого значення, не дорівнює йому):")
        for key, n in surplus.most_common():
            print(f"     {key:20} {n}")
        print("     ^ це НЕ помилки за поточним правилом порівняння, але саме "
              "тут ховається приклеєний бланковий шум -- дивіться, якщо "
              "число зростає після зміни екстракції")

    ok_fields = sum(r["fields_ok"] for r in results)
    all_fields = sum(r["fields_total"] for r in results)
    print(f"\nусього полів правильно: {ok_fields}/{all_fields} "
          f"({100 * ok_fields / max(1, all_fields):.1f}%)")
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
                       "per_key": {k: v for k, v in sorted(per_key.items())},
                       "results": results}, f, ensure_ascii=False, indent=1, default=str)
        print(f"\nзвіт: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
