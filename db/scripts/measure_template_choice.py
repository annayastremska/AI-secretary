"""Якість вибору SQL-шаблона модельним ярусом на тест-сеті Ані.

Запуск (модель піднята):
    git show origin/anya-pipeline:demos/upload_app/query_catalog.yaml > /tmp/qc.yaml
    git show origin/anya-pipeline:demos/upload_app/router_testset.yaml > /tmp/rt.yaml
    python db/scripts/measure_template_choice.py --today 2026-08-28

## Чому цей замір не дублює `demos/upload_app/measure_router.py`

Прилад Ані міряє ВЕКТОРНИЙ ярус: енкодер проти `examples` каталогу, з
порогом і «впевнено-неправильними». Цей -- МОДЕЛЬНИЙ ярус на тому самому
наборі. Це різні речі й різні числа; вони не конкурують.

Перевірено, що патч 27.08 векторного ярусу не зачіпає: `catalog_routes`
індексує лише `examples`, а патч змінив три рядки `title`. Тому число Ані
лишається її, повторювати його немає підстав.

## Розмітка не моя

`router_testset.yaml` склала Аня ДО вибору енкодера, з групами (звідки
питання) і правилом «рівно один захисний expected». Сірі зони
(count_by_state_on_date проти absent_breakdown_on_date) у не-example групи
навмисно не брали. Я не міняю ні питань, ні очікувань.

## Головне застереження: група `example` для моделі забруднена

У промпт модельного ярусу `catalog_lines` підставляє ПО ДВА приклади з
каталогу. Тому 71 питання групи `example` модель здебільшого вже бачить
дослівно, і їхня точність нічого не доводить. Головне число тут -- на
групах, яких у промпті немає: paraphrase, typo, colloquial, trap,
smalltalk. `example` друкується окремо як sanity, а не в підсумок.

## Що рахується окремо, а не як помилка

Падіння у `вільний_sql`/`відмова` там, де очікувався шаблон, -- це не
«обрано не той шаблон», а «шаблон не знайдено». Коштує воно інакше
(людина бачить відмову, а не хибне число), тому рахується окремим рядком.
"""
import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml  # noqa: E402

import measure_followup_route as M  # noqa: E402

FELLTHROUGH = {"вільний_sql", "відмова"}
SEEN_IN_PROMPT = "example"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", default="/tmp/qc.yaml")
    ap.add_argument("--testset", default="/tmp/rt.yaml")
    ap.add_argument("--today", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    with open(args.catalog, encoding="utf-8") as fh:
        catalog = {t["id"]: t for t in yaml.safe_load(fh)["templates"]}
    schema = M.schema_for(catalog)
    with open(args.testset, encoding="utf-8") as fh:
        rows = yaml.safe_load(fh)["questions"]

    bad = [q["expected"] for q in rows if q["expected"] not in catalog]
    if bad:
        sys.exit(f"expected поза каталогом: {sorted(set(bad))}")

    per = defaultdict(lambda: {"n": 0, "ok": 0, "fell": 0, "wrong": []})
    for q in rows:
        grp = q.get("group") or "?"
        data, _dt, _raw = M.ask(catalog, schema, [], q["q"], args.today)
        got = (data or {}).get("template") or "не розібрано"
        rec = per[grp]
        rec["n"] += 1
        if got == q["expected"]:
            rec["ok"] += 1
            mark = "OK  "
        elif got in FELLTHROUGH:
            rec["fell"] += 1
            mark = "вбік"
            rec["wrong"].append((q["q"], q["expected"], got))
        else:
            mark = "ХИБА"
            rec["wrong"].append((q["q"], q["expected"], got))
        print(f"  {mark} [{grp}] хотіли {q['expected']} -> {got}"
              f"    {q['q'][:46]}")

    unseen = {g: r for g, r in per.items() if g != SEEN_IN_PROMPT}
    n = sum(r["n"] for r in unseen.values())
    ok = sum(r["ok"] for r in unseen.values())
    fell = sum(r["fell"] for r in unseen.values())
    print("\n" + "=" * 70)
    print(f"МЕТРИКА правильних шаблонів обрано: {ok} із {n} питань, "
          f"яких у промпті немає")
    print(f"  з решти: шаблона не знайдено (вільний_sql/відмова): {fell}")
    print(f"  обрано інший шаблон: {n - ok - fell}")
    for g in sorted(unseen):
        r = unseen[g]
        print(f"    {g:11s} {r['ok']:2d} із {r['n']:2d}"
              f"   (вбік {r['fell']})")
    e = per.get(SEEN_IN_PROMPT)
    if e:
        print(f"  sanity, група example (модель бачить ці приклади в промпті): "
              f"{e['ok']} із {e['n']}")

    print("\nпомилки:")
    for g in sorted(per):
        for qq, want, got in per[g]["wrong"]:
            print(f"  [{g}] {want} -> {got}   {qq[:60]}")

    if args.out:
        payload = {"as_of": args.today, "unseen_ok": ok, "unseen_of": n,
                   "unseen_fellthrough": fell,
                   "example_ok": (e or {}).get("ok"),
                   "example_of": (e or {}).get("n"),
                   "per_group": {g: {k: v for k, v in r.items() if k != "wrong"}
                                 for g, r in per.items()}}
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"\nзаписано {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
