"""Виявлення перекриттів: одна особа у двох чинних діапазонних станах одночасно.

Запуск (тільки читання):
    python db/scripts/detect_overlap_conflicts.py
    python db/scripts/detect_overlap_conflicts.py --status any --out ~/andriy/run/overlaps.json

## Що це і чого НЕ робить

Аня спитала: чи є заборона на конфлікт документів між собою -- може особа бути
«двічі у відпустці»? Може: жорсткого обмеження в базі немає (перевірено --
лише status-enum і valid_to>=valid_from; розширення btree_gist для
exclusion-констрейнта не встановлено).

І жорсткого констрейнта тут бути НЕ повинно: він відкинув би факт із другого
документа при вставці, тобто мовчки викинув би дані. Наш принцип -- витягти й
підняти людині, а не заблокувати. Тому це ДЕТЕКТОР, а не констрейнт: він
знаходить перекриття й доповідає. Черги (`review_queue`) він НЕ чіпає -- це
продуктова таблиця Ані; підняття завдань там -- окремий крок за її згодою і не
з readonly-ролі.

## Чому лише `ranged`

Перекриття має сенс лише для вимірів із діапазоном чинності (`validity_model =
ranged`): відпустка, відрядження тощо. `current_state` (звання, посада,
підрозділ) -- це «останнє чинне», і два таких факти означають не подвійний
стан, а незакрите витіснення: інша хвороба, не ця. `permanent_event` (номер
наказу, дата) -- точки, не діапазони.

## Що вважається перекриттям

Дві РІЗНІ записи (`a.id < b.id`) тієї самої особи й того самого виміру, чиї
періоди [valid_from, valid_to] дотикаються або накладаються. Порожній
`valid_to` = «досі триває» (infinity). Значення можуть збігатися (дубль того
самого періоду з двох документів) або різнитися (справжній конфлікт, як
анульований і виданий замість нього квиток) -- обидва випадки доповідаються,
бо обидва треба показати людині.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg  # noqa: E402

from build_units_test import dsn  # noqa: E402

SUMMARY = """
SELECT d.code,
       count(*)                                                       AS pairs,
       count(DISTINCT a.object_id)                                    AS people,
       count(*) FILTER (WHERE translate(a.value, $q$'$q$, $q$’$q$)
                            = translate(b.value, $q$'$q$, $q$’$q$))    AS same_value,
       count(*) FILTER (WHERE translate(a.value, $q$'$q$, $q$’$q$)
                           <> translate(b.value, $q$'$q$, $q$’$q$))    AS diff_value
FROM facts a
JOIN facts b ON a.object_id = b.object_id
            AND a.dimension_id = b.dimension_id
            AND a.id < b.id
JOIN dimensions d ON d.id = a.dimension_id AND d.validity_model = 'ranged'
WHERE {status}
  AND a.valid_from IS NOT NULL AND b.valid_from IS NOT NULL
  AND a.valid_from <= coalesce(b.valid_to, 'infinity'::date)
  AND b.valid_from <= coalesce(a.valid_to, 'infinity'::date)
GROUP BY d.code
ORDER BY pairs DESC, d.code
"""

DETAIL = """
SELECT d.code, a.object_id,
       a.id AS a_id, a.valid_from AS a_from, a.valid_to AS a_to,
       a.source_doc_id AS a_doc, a.value AS a_val,
       b.id AS b_id, b.valid_from AS b_from, b.valid_to AS b_to,
       b.source_doc_id AS b_doc, b.value AS b_val
FROM facts a
JOIN facts b ON a.object_id = b.object_id
            AND a.dimension_id = b.dimension_id
            AND a.id < b.id
JOIN dimensions d ON d.id = a.dimension_id AND d.validity_model = 'ranged'
WHERE {status}
  AND a.valid_from IS NOT NULL AND b.valid_from IS NOT NULL
  AND a.valid_from <= coalesce(b.valid_to, 'infinity'::date)
  AND b.valid_from <= coalesce(a.valid_to, 'infinity'::date)
ORDER BY d.code, a.object_id, a.id
"""

STATUS_SQL = {
    "confirmed": "a.status = 'confirmed' AND b.status = 'confirmed'",
    "any": "a.status <> 'rejected' AND b.status <> 'rejected'",
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--status", choices=list(STATUS_SQL), default="confirmed",
                    help="confirmed: обидва підтверджені (за замовч.); "
                         "any: враховувати й чернетки")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    cond = STATUS_SQL[args.status]

    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(SUMMARY.format(status=cond))
        summary = cur.fetchall()
        cur.execute(DETAIL.format(status=cond))
        detail = cur.fetchall()

    total = sum(r[1] for r in summary)
    print(f"=== перекриття діапазонних станів (status={args.status})")
    if not summary:
        print("  нема")
    else:
        print(f"  {'вимір':22s} {'пар':>4s} {'осіб':>5s} "
              f"{'дублі':>6s} {'різні':>6s}")
        for code, pairs, people, same, diff in summary:
            print(f"  {code:22s} {pairs:>4d} {people:>5d} {same:>6d} {diff:>6d}")
        print(f"  {'РАЗОМ':22s} {total:>4d}")

    if detail:
        print("\n=== деталі:")
        for r in detail:
            (code, obj, aid, af, at, adoc, aval,
             bid, bf, bt, bdoc, bval) = r
            kind = "дубль" if aval == bval else "КОНФЛІКТ"
            print(f"  [{code}] особа {obj}  {kind}")
            print(f"      факт {aid}: {af}..{at}  док {adoc}  «{(aval or '')[:54]}»")
            print(f"      факт {bid}: {bf}..{bt}  док {bdoc}  «{(bval or '')[:54]}»")

    if args.out:
        payload = {
            "status": args.status,
            "total_pairs": total,
            "by_dimension": [
                {"code": c, "pairs": p, "people": ppl,
                 "same_value": s, "diff_value": d}
                for c, p, ppl, s, d in summary],
            "pairs": [
                {"dimension": r[0], "object_id": r[1],
                 "a": {"id": r[2], "from": str(r[3]), "to": str(r[4]),
                       "doc": r[5], "value": r[6]},
                 "b": {"id": r[7], "from": str(r[8]), "to": str(r[9]),
                       "doc": r[10], "value": r[11]},
                 "kind": "duplicate" if r[6] == r[11] else "conflict"}
                for r in detail],
        }
        with open(os.path.expanduser(args.out), "w",
                  encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"\nзаписано {args.out}")

    # Черги НЕ чіпаємо: підняти overlap_conflict у review_queue -- окремий крок
    # за згодою Ані, з пишучої ролі. Тут лише доповідь.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
