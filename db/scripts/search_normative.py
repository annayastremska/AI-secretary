"""Український FTS по нормативному корпусу -- перший робочий пошук дороги Б.

Запуск:
    python db/scripts/search_normative.py "за скільки днів подавати рапорт на відпустку"
    python db/scripts/search_normative.py "НСД" --limit 5
    python db/scripts/search_normative.py "облік особового складу" --show-superseded

Два правила, які тут закладені й важливіші за сам пошук:

1. **Deny by default за чинністю.** У вибірку йдуть лише документи з
   `validity = 'current'`. Скасований наказ і документ, про чинність якого ми
   не знаємо (`unknown`), не цитуються. Наш власний дизайн називає впевнену
   цитату зі скасованої інструкції гіршою за відмову відповідати
   (`normative-docs-subsystem.md` §4), а фільтр стоїть **у самому запиті**, не
   пост-фільтром -- інакше кількість результатів і час відповіді самі
   розкажуть, що щось приховано.

2. **Поріг, а не «перший результат».** `ts_rank` нижче порога -- це «нічого не
   знайдено», а не «ось найкраще з поганого». Поточне значення НЕ калібровано
   на розміченому наборі: воно взяте з ока по цьому корпусу і чекає на
   тест-сет. Так і написано в --explain, щоб число не почало виглядати
   науковим саме собою.
"""
import argparse
import os
import sys

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Не калібровано. Підстава -- ручний перегляд видачі на 31 документі:
# нижче цього значення починаються збіги по одному частотному слову.
RANK_THRESHOLD = 0.02


def search(cur, query, limit=5, show_superseded=False, threshold=RANK_THRESHOLD):
    validity_filter = "" if show_superseded else "AND d.validity = 'current'"
    cur.execute(
        f"""
        SELECT d.id,
               d.pipeline_meta ->> 'title'  AS title,
               d.validity,
               ts_rank(to_tsvector('ukrainian', coalesce(d.text_content, '')),
                       websearch_to_tsquery('ukrainian', %(q)s)) AS rank,
               ts_headline('ukrainian', d.text_content,
                           websearch_to_tsquery('ukrainian', %(q)s),
                           'MaxFragments=2, MaxWords=22, MinWords=8,
                            StartSel=«, StopSel=»') AS snippet
          FROM documents d
         WHERE d.domain = 'normative'
           {validity_filter}
           AND to_tsvector('ukrainian', coalesce(d.text_content, ''))
               @@ websearch_to_tsquery('ukrainian', %(q)s)
         ORDER BY rank DESC
         LIMIT %(lim)s
        """,
        {"q": query, "lim": limit},
    )
    rows = cur.fetchall()
    return [r for r in rows if r[3] >= threshold], [r for r in rows if r[3] < threshold]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query", nargs="+")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--show-superseded", action="store_true",
                    help="показати й скасовані -- для перевірки, що фільтр працює")
    ap.add_argument("--explain", action="store_true")
    args = ap.parse_args(argv)
    query = " ".join(args.query)

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        passed, below = search(cur, query, args.limit, args.show_superseded)

    print(f"Запит: {query!r}")
    if not passed:
        # Два різні стани: нічого не знайшлось узагалі vs знайшлось, але слабко.
        # Плутати їх не можна -- користувач має різні дії в кожному випадку.
        if below:
            print(f"\nНижче порога ({RANK_THRESHOLD}) -- вважаємо, що не знайдено.")
            for _id, title, validity, rank, _s in below:
                print(f"  {rank:.4f}  {title}")
        else:
            print("\nЗа запитом нічого не знайдено.")
        return 0

    for _id, title, validity, rank, snippet in passed:
        mark = "" if validity == "current" else f"  [{validity}]"
        print(f"\n  {rank:.4f}  {title}{mark}")
        print(f"          {' '.join((snippet or '').split())[:260]}")

    if below:
        print(f"\n  (ще {len(below)} нижче порога, не показано)")
    if args.explain:
        print(f"\nПоріг {RANK_THRESHOLD} -- НЕ калібрований на розміченому наборі.")
        print("Фільтр чинності стоїть у самому SQL, не пост-фільтром.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
