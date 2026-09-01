"""Прибирає дублі фактів, які вже лежать у базі.

Потрібен окремо від фікса в лоадері: `find_equivalent_fact` працює НА ВСТАВЦІ
і не чіпає те, що завантажене раніше. На сервері 198 записів завантажені до
фікса, тож дублі там уже є (виміряно: 67 груп).

Повторне завантаження тут не допомагає -- лоадер ідемпотентний за checksum і
на ті самі файли скаже `unchanged`.

Запуск:
    python db/scripts/dedupe_existing_facts.py            # лише показати
    python db/scripts/dedupe_existing_facts.py --apply    # застосувати

За замовчуванням НІЧОГО не змінює. `--apply` треба вказати явно.

## Що вважається дублем

Ті самі об'єкт, вимір, значення й період, і документи-джерела мають ОДНАКОВИЙ
номер і дату. Умова про номер+дату та сама, що в лоадері, і з тієї ж причини:
два різні документи можуть законно описувати той самий факт (наказ і виписка
з нього), і зливати їх нельзя. Обидва реквізити мусять бути непорожні, інакше
дефектні документи склеювались би між собою.

Апостроф нормалізується ЛИШЕ для порівняння: `’` (U+2019) і `'` (U+0027)
вважаються однаковими. Знайдено на реальних даних -- «відпустка у зв'язку з
навчанням» проти «відпустка у зв’язку з навчанням» -- тобто різниця
типографічна, не змістова, і через неї факт не зливався. Збережені значення
не змінюються: правити текст у базі мусить той, хто його витягує.

## Що робиться з дублем

Лишається НАЙДОВІРЕНІША копія, а не найраніша: спершу `confirmed`, далі
`electronic` перед `photo` (OCR ненадійніший за цифровий витяг), далі вища
`confidence`, і лише потім менший id як розв'язувач нічиїх. Це важливо:
сортування за id лишало б OCR-версію й видаляло витягнуту з docx.

Решта копій ВИДАЛЯЮТЬСЯ, а їхні документи долучаються як додаткові джерела в
`fact_sources`. Документи в `documents` не чіпаються -- на них тримається
провенанс, і Аня прямо просила їх не видаляти.

Видалення, а не `status='rejected'`: rejected означає «факт визнано хибним»,
а тут факт правильний, просто записаний двічі. Плутати ці два стани не варто,
інакше в базі з'явиться шум, який виглядає як відхилені дані.
"""
import argparse
import os
import sys

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIND_DUPES = """
WITH req AS (
    SELECT f.source_doc_id AS doc_id,
           max(CASE WHEN d.code = 'document_number' THEN f.value END) AS num,
           max(CASE WHEN d.code = 'document_date'   THEN f.value END) AS dt
      FROM facts f
      JOIN dimensions d ON d.id = f.dimension_id
     WHERE d.code IN ('document_number', 'document_date')
     GROUP BY f.source_doc_id
)
SELECT f.object_id, f.dimension_id, min(f.value) AS value, f.valid_from, f.valid_to,
       r.num, r.dt,
       -- Порядок НЕ за id: лишаємо найдовіренішу копію, а не найранішу.
       -- Спершу confirmed, далі electronic перед photo (OCR ненадійніший за
       -- цифровий витяг), далі вища впевненість, і лише потім id як
       -- розв'язувач нічиїх. На сервері значення обох шляхів збіглися
       -- один-в-один, але спиратись на це як на правило не можна.
       array_agg(f.id ORDER BY
                 (f.status = 'confirmed') DESC,
                 (d.source_kind = 'electronic') DESC,
                 f.confidence DESC NULLS LAST,
                 f.id)                            AS fact_ids,
       array_agg(f.source_doc_id ORDER BY
                 (f.status = 'confirmed') DESC,
                 (d.source_kind = 'electronic') DESC,
                 f.confidence DESC NULLS LAST,
                 f.id)                            AS doc_ids
  FROM facts f
  JOIN documents d ON d.id = f.source_doc_id
  JOIN req r ON r.doc_id = f.source_doc_id
 WHERE f.status <> 'rejected'
   AND r.num IS NOT NULL AND r.num <> ''
   AND r.dt  IS NOT NULL AND r.dt  <> ''
 GROUP BY f.object_id, f.dimension_id, replace(f.value, '’', ''''), f.valid_from, f.valid_to, r.num, r.dt
HAVING count(*) > 1
 ORDER BY r.num
"""

# Той самий документ, той самий вимір -- але РІЗНІ значення. Це не дубль, а
# розбіжність між шляхами витягу (напр. OCR прочитав інакше, ніж docx), і
# зливати її не можна: вона мусить бути видима людині. Показуємо окремо.
FIND_DISAGREEMENTS = """
WITH req AS (
    SELECT f.source_doc_id AS doc_id,
           max(CASE WHEN d.code = 'document_number' THEN f.value END) AS num,
           max(CASE WHEN d.code = 'document_date'   THEN f.value END) AS dt
      FROM facts f
      JOIN dimensions d ON d.id = f.dimension_id
     WHERE d.code IN ('document_number', 'document_date')
     GROUP BY f.source_doc_id
)
SELECT r.num, r.dt, dm.code,
       array_agg(DISTINCT f.value) AS values,
       array_agg(DISTINCT d.source_kind) AS kinds
  FROM facts f
  JOIN documents d ON d.id = f.source_doc_id
  JOIN dimensions dm ON dm.id = f.dimension_id
  JOIN req r ON r.doc_id = f.source_doc_id
 WHERE f.status <> 'rejected'
   AND r.num IS NOT NULL AND r.num <> ''
   AND r.dt  IS NOT NULL AND r.dt  <> ''
 GROUP BY f.object_id, r.num, r.dt, dm.code
HAVING count(DISTINCT f.source_doc_id) > 1
   AND count(DISTINCT coalesce(replace(f.value, '’', ''''), '')) > 1
 ORDER BY r.num
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="справді змінити базу")
    args = ap.parse_args(argv)

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(FIND_DISAGREEMENTS)
            disagreements = cur.fetchall()
            if disagreements:
                print(f"⚠ РОЗБІЖНОСТІ між шляхами витягу: {len(disagreements)}")
                print("  Той самий документ, той самий вимір, РІЗНІ значення.")
                print("  Це НЕ дублі -- не зливаємо, лишаємо людині.\n")
                for num, dt, code, values, kinds in disagreements[:15]:
                    print(f"    №{num} від {dt} · {code} · {kinds}")
                    for v in values:
                        print(f"        {str(v)[:60]!r}")
                print()

            cur.execute(FIND_DUPES)
            groups = cur.fetchall()

            if not groups:
                print("Дублів не знайдено.")
                return 0

            total_removed = 0
            print(f"Груп дублів: {len(groups)}\n")
            for (obj, dim, value, vfrom, vto, num, dt, fact_ids, doc_ids) in groups:
                keep, drop = fact_ids[0], fact_ids[1:]
                total_removed += len(drop)
                cur.execute("SELECT code FROM dimensions WHERE id = %s", (dim,))
                dim_code = cur.fetchone()[0]
                print(f"  №{num} від {dt} · {dim_code} · {str(value)[:28]!r}")
                print(f"      лишаємо факт {keep}, видаляємо {drop}, "
                      f"джерела -> {sorted(set(doc_ids))}")

                if args.apply:
                    for doc_id in doc_ids:
                        cur.execute(
                            """INSERT INTO fact_sources (fact_id, document_id, is_primary)
                               VALUES (%s, %s, false) ON CONFLICT DO NOTHING""",
                            (keep, doc_id),
                        )
                    # review_log спершу: інакше FK не дасть видалити факт, на
                    # який він посилається, і ми втратимо слід про причину.
                    cur.execute(
                        """INSERT INTO review_log (fact_id, changed_by, old_value,
                                                    new_value, action)
                           SELECT %s, 'dedupe_existing_facts', %s, %s,
                                  'merged_duplicate_from_same_document'
                        """,
                        (keep, f"дубль фактів {drop}", f"джерел: {len(set(doc_ids))}"),
                    )
                    cur.execute("DELETE FROM fact_sources WHERE fact_id = ANY(%s)", (drop,))
                    cur.execute("DELETE FROM review_queue WHERE fact_id = ANY(%s)", (drop,))
                    cur.execute("DELETE FROM review_log WHERE fact_id = ANY(%s)", (drop,))
                    cur.execute("DELETE FROM facts WHERE id = ANY(%s)", (drop,))

        if args.apply:
            conn.commit()
            print(f"\nЗАСТОСОВАНО: видалено {total_removed} дублів")
        else:
            print(f"\nDRY-RUN: видалилось би {total_removed} фактів. "
                  f"Нічого не змінено -- для застосування додай --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
