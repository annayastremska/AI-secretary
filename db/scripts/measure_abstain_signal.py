"""Чи є в нас число, на якому харнес може будувати ВІДМОВУ.

Запуск:
    python db/scripts/measure_abstain_signal.py --set eval/retrieval/abstain_set.tsv

## Питання, на яке це відповідає

Рішення про відмову -- не наше, а харнеса. Але ЧИСЛО, на якому вони вирішують,
дає наш бік, і зараз ми не маємо годящого:

* **RRF непорівнюваний між запитами.** Заміряно: питання «яка максимальна
  швидкість танка Leopard 2» отримало НАЙВИЩИЙ rrf з п'яти -- 0.1329, вище за
  будь-яку правильну відповідь. Причина структурна: RRF складає обернені
  МІСЦЯ, а місця є завжди, хоч би що знайшлось. Порогу на ньому не буває.
* **Косинус градієнт має, але зазор тонкий.** Є відповідь 0.887-0.909, немає
  0.855-0.861, не по темі 0.798-0.827 -- тобто поріг мусить стояти близько
  0.87, а між «немає» і «не по темі» лише 0.03.
* **Скор реранкера -- єдиний кандидат, який МОЖЕ бути порівнюваним**, бо
  CrossEncoder оцінює пару «запит + текст» разом і видає логіт «відповідає /
  не відповідає», а не місце в списку. Це припущення, і воно не перевірене.

Тут воно перевіряється: три класи питань, три сигнали, і питання одне -- чи
існує поріг, який розділяє класи.

## Чому клас `no_answer` доводиться, а не заявляється

Я двічі оголосив «відповіді в корпусі немає» і двічі помилявся (номери законів
лежали в підписному блоці; строк подання рапорту -- у внутрішній інструкції).
Тому для цього класу в наборі стоять терміни, відсутність яких скрипт
перевіряє сам і скаржиться, якщо клас поставлено неправильно.

## Чого цей замір НЕ доводить

n = 18 питань. Це напрямок, не число з довірчим інтервалом. Якщо класи
розділяться -- це підстава ставити поріг і перевіряти його на більшому наборі,
а не оголошувати точність.
"""
import argparse
import os
import statistics
import sys

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "db", "scripts"))

import search_units_test as SU  # noqa: E402

POOL = 50
RERANK_CHARS = 1800


def load_set(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            cls, q = parts[0].strip(), parts[1].strip()
            terms = parts[2].strip() if len(parts) > 2 else ""
            rows.append((cls, q, terms))
    return rows


def verify_absent(cur, terms):
    """Чи справді жодна одиниця не містить цих термінів."""
    bad = []
    for t in [x.strip() for x in terms.split("|") if x.strip()]:
        cur.execute(f"SELECT count(*) FROM {SU.UNITS} WHERE text ILIKE %s",
                    ("%" + t + "%",))
        n = cur.fetchone()[0]
        if n:
            bad.append((t, n))
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", required=True)
    ap.add_argument("--pool", type=int, default=POOL)
    args = ap.parse_args(argv)

    rows = load_set(args.set)
    from build_units_test import load_encoder, dsn
    from measure_rerank_lift import load_reranker
    encode = load_encoder()
    score = load_reranker()

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    out = []
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        # 1. Перевіряємо самі мітки класів, перш ніж на них спиратися.
        print("── перевірка класу no_answer " + "─" * 48)
        wrong = 0
        for cls, q, terms in rows:
            if cls != "no_answer" or not terms:
                continue
            bad = verify_absent(cur, terms)
            if bad:
                wrong += 1
                print(f"  ⚠ КЛАС ПОСТАВЛЕНО ХИБНО: {q[:52]}")
                print(f"      терміни знайдено в корпусі: {bad}")
        print(f"  хибних міток: {wrong}\n")

        # 2. Три сигнали на кожне питання.
        print("── сигнали " + "─" * 66)
        print(f"{'клас':<11} {'RRF top1':>9} {'косинус':>9} {'реранкер':>10}  питання")
        for cls, q, _t in rows:
            vec = str(encode(["query: " + q])[0])
            lex = SU.lexical(cur, q)
            sem = SU.semantic(cur, vec)
            cos = sem[0][3] if sem else 0.0
            fused = SU.dedupe_by_text(cur, SU.rrf_merge(lex, sem), SU.canon_map(cur))
            rrf = fused[0][1]["rrf"] if fused else 0.0

            pool = fused[:args.pool]
            texts = [SU.quote_of(cur, d, b)[0][:RERANK_CHARS] for (d, b), _m in pool]
            rr = max(score(q, texts)) if texts else float("-inf")

            out.append((cls, q, rrf, cos, rr))
            print(f"{cls:<11} {rrf:>9.4f} {cos:>9.4f} {rr:>10.3f}  {q[:44]}")

    # 3. Чи існує поріг.
    print("\n── чи розділяє сигнал класи " + "─" * 49)
    print(f"{'сигнал':<12} {'answerable':>22} {'no_answer':>22} {'off_topic':>22}")
    for name, idx in (("RRF", 2), ("косинус", 3), ("реранкер", 4)):
        cells = []
        by = {}
        for cls in ("answerable", "no_answer", "off_topic"):
            vals = sorted(r[idx] for r in out if r[0] == cls)
            by[cls] = vals
            cells.append(f"{min(vals):.3f}..{max(vals):.3f} (med {statistics.median(vals):.3f})")
        print(f"{name:<12} " + " ".join(f"{c:>22}" for c in cells))
        # Поріг існує, якщо найгірше answerable вище за найкраще НЕ-answerable.
        worst_ok = min(by["answerable"])
        best_bad = max(by["no_answer"] + by["off_topic"])
        gap = worst_ok - best_bad
        verdict = ("ПОРІГ ІСНУЄ" if gap > 0 else "поріг НЕ розділяє")
        print(f"{'':<12} найгірше answerable {worst_ok:.3f} проти найкращого "
              f"іншого {best_bad:.3f} -> зазор {gap:+.3f}  {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
