"""Класифікація типу питання: нормативне чи фактологічне.

Запуск (модель піднята; набір поза репозиторієм):
    git show origin/anya-pipeline:demos/upload_app/query_catalog.yaml > /tmp/qc.yaml
    python db/scripts/measure_question_type.py --set ~/andriy/golden/golden_all.tsv \\
        --catalog /tmp/qc.yaml --today 2026-08-28

## Чому саме тут precision і recall доречні

Це БІНАРНА задача, тому обидві величини визначені однозначно -- на відміну від
вибору шаблона, де 28 класів на 15 питаннях дали б шум.

Розмітку зробив Денис: у золотому наборі кожне питання позначене `norm` або
`fact`. Тобто істина тут не моя.

## Дві помилки коштують по-різному, тому рахуються окремо

* **фактологічне пішло в нормативний шлях** -- людина отримує цитату замість
  цифри. Видно одразу;
* **нормативне пішло в SQL** -- отримує число або відмову замість норми. Це
  дорожче: виглядає авторитетно, і збоку помилку не видно.

Тому recall класу «нормативне» (скільком нормативним дали нормативний шлях) --
головне число, а precision -- поруч.

## Що НЕ рахується як помилка класифікації

`вільний_sql` і `відмова` -- це не «інший тип», а «не лягає на шаблон». Такі
відповіді виносяться окремим рядком, а не приписуються ні до нормативних, ні до
фактологічних: інакше метрика мовчки перетворює відмову на помилку типу.

## Межа

Маршрутизатор тут -- мій прилад (той самий промпт-конструктор і схема, що в
`measure_followup_route`), а не функція застосунку. Він рівносильний за
задумом, але не тотожний: у застосунку промпт живе у `prompts/route.md` і
викликається через llama-cpp у процесі, а тут -- через llama-server. Тому число
описує ЗДАТНІСТЬ моделі розрізнити тип, а не поведінку застосунку.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml  # noqa: E402

import measure_followup_route as M  # noqa: E402

NORM_TEMPLATES = {"normative_search"}
NEITHER = {"вільний_sql", "відмова"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", required=True)
    ap.add_argument("--catalog", default="/tmp/qc.yaml")
    ap.add_argument("--today", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    with open(args.catalog, encoding="utf-8") as fh:
        catalog = {t["id"]: t for t in yaml.safe_load(fh)["templates"]}
    schema = M.schema_for(catalog)

    rows = []
    with open(os.path.expanduser(args.set), encoding="utf-8") as fh:
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 4:
                rows.append({"id": p[0], "family": p[1], "sub": p[2], "q": p[3]})
    print(f"питань: {len(rows)}  "
          f"(norm {sum(1 for r in rows if r['family'] == 'norm')}, "
          f"fact {sum(1 for r in rows if r['family'] == 'fact')})\n")

    tp = fp = fn = tn = neither_norm = neither_fact = 0
    unparsed = 0
    for r in rows:
        data, _dt, _raw = M.ask(catalog, schema, [], r["q"], args.today)
        tid = (data or {}).get("template")
        if not tid:
            unparsed += 1
            pred = "?"
        elif tid in NORM_TEMPLATES:
            pred = "norm"
        elif tid in NEITHER:
            pred = "-"
        else:
            pred = "fact"
        want = r["family"]
        if pred == "-" or pred == "?":
            if want == "norm":
                neither_norm += 1
            else:
                neither_fact += 1
        elif want == "norm" and pred == "norm":
            tp += 1
        elif want == "fact" and pred == "norm":
            fp += 1
        elif want == "norm" and pred == "fact":
            fn += 1
        else:
            tn += 1
        mark = "OK  " if pred == want else ("вбік" if pred in ("-", "?") else "ХИБА")
        print(f"  {mark} [{r['id']} {want}/{r['sub']}] -> {tid or 'не розібрано'}"
              f"    {r['q'][:52]}")

    norm_total = sum(1 for r in rows if r["family"] == "norm")
    fact_total = len(rows) - norm_total
    correct = tp + tn
    scored = tp + tn + fp + fn
    print(f"\n{'=' * 70}")
    print(f"типів визначено правильно: {correct} із {scored} оцінюваних "
          f"({len(rows)} питань, {neither_norm + neither_fact} пішли у "
          f"вільний_sql/відмову і в підсумок не входять)")
    print(f"  нормативних розпізнано (recall):  {tp} із {tp + fn}")
    print(f"  точність класу «нормативне» (precision): {tp} із {tp + fp}"
          if tp + fp else "  точність класу «нормативне»: не визначена (нуль передбачень)")
    print(f"  фактологічних розпізнано: {tn} із {tn + fp}")
    print(f"  «не лягає на шаблон»: нормативних {neither_norm}, "
          f"фактологічних {neither_fact}")
    if unparsed:
        print(f"  вивід не розібрано: {unparsed}")

    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"tp": tp, "fp": fp, "fn": fn, "tn": tn,
                       "neither_norm": neither_norm, "neither_fact": neither_fact,
                       "norm_total": norm_total, "fact_total": fact_total,
                       "as_of": args.today}, fh, ensure_ascii=False, indent=2)
        print(f"\nзаписано {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
