"""Приводить наявні дані до правила «немає у штатці -> факт не підтверджений».

Запуск:
    python db/scripts/reconcile_roster_status.py            # показати
    python db/scripts/reconcile_roster_status.py --apply

## Дві дії, і обидві -- наслідок одного правила

Правило продукту: чернетка != факт. Запис із хоч одним непевним полем у
підрахунки не входить, доки людина не підтвердить. Особа, якої немає у штатці,
-- це і є непевність.

**1. Факти осіб без `service_id` -> `unconfirmed`.** Завантажувач тепер ставить
статус за зіставленням зі штаткою, але це подіє лише на майбутні завантаження.
Наявні 20 підтверджених фактів по трьох особах треба перевести тим самим
правилом, інакше зміна нічого не змінює в цифрах демо.

**2. Закрити протерміновані завдання `new_person`.** Із відкритих завдань 161
створено 24.08 -- тоді штатки в базі ще не було, і система відмічала КОЖНЕ нове
прізвище. Після заливки штатки ці особи з нею зійшлися: реально невідомих
лишилось три. Тобто 131 завдання чекає на людину без причини, і черга показує
журнал, а не роботу.

## Чому завдання закриваються не всі, а вибірково

`review_queue` не має посилання на особу -- лише на документ і факт. Тому
«чи зійшлася особа» визначається через документ: беремо всіх осіб, чиї факти
мають цей документ джерелом, і закриваємо завдання ЛИШЕ якщо в усіх них є
`service_id`. Якщо хоч одна не зійшлася -- завдання лишається відкритим, бо
воно й далі про справжню непевність.

Це навмисно обережний бік: закрити зайве завдання гірше, ніж лишити зайве.
Перше приховує роботу від людини, друге лише додає їй шуму.
"""
import argparse
import os

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESOLUTION = "matched_by_roster"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # ── 1. факти осіб без service_id ────────────────────────────────────
        cur.execute("""
            SELECT p.object_id, p.last_name || ' ' || p.first_name AS особа,
                   count(*) FILTER (WHERE f.status = 'confirmed') AS conf,
                   count(*) FILTER (WHERE f.status = 'unconfirmed') AS unconf
              FROM people p
              JOIN facts f ON f.object_id = p.object_id
             WHERE p.service_id IS NULL
             GROUP BY 1, 2
             ORDER BY 3 DESC
        """)
        rows = cur.fetchall()
        total_conf = sum(r[2] for r in rows)
        print(f"Осіб без service_id: {len(rows)}, "
              f"підтверджених фактів у них: {total_conf}")
        for oid, name, conf, unconf in rows:
            print(f"   {name:<28} confirmed {conf:>3} -> unconfirmed, "
                  f"уже unconfirmed {unconf}")

        # ── 2. протерміновані завдання new_person ───────────────────────────
        # Особи документа -- через fact_sources: чиї факти мають цей документ
        # джерелом. Прямого зв'язку review_queue -> особа в схемі немає.
        cur.execute("""
            WITH задача AS (
                SELECT q.id, q.document_id
                  FROM review_queue q
                 WHERE q.resolved_at IS NULL AND q.queue_type = 'new_person'
            ),
            особи AS (
                SELECT з.id AS queue_id, p.object_id, p.service_id
                  FROM задача з
                  JOIN fact_sources fs ON fs.document_id = з.document_id
                  JOIN facts f ON f.id = fs.fact_id
                  JOIN people p ON p.object_id = f.object_id
                 GROUP BY 1, 2, 3
            )
            SELECT queue_id,
                   count(*) AS осіб,
                   count(*) FILTER (WHERE service_id IS NULL) AS без_штатки
              FROM особи GROUP BY 1
        """)
        per_task = cur.fetchall()
        to_close = [q for q, _n, bad in per_task if bad == 0]
        keep = [q for q, _n, bad in per_task if bad > 0]

        cur.execute("""SELECT count(*) FROM review_queue
                        WHERE resolved_at IS NULL AND queue_type = 'new_person'""")
        open_np = cur.fetchone()[0]
        no_person = open_np - len(per_task)

        print(f"\nВідкритих завдань new_person: {open_np}")
        print(f"   зійшлися зі штаткою -> закрити: {len(to_close)}")
        print(f"   є особа без штатки -> лишити:   {len(keep)}  {keep[:6]}")
        print(f"   без жодної особи в фактах -> лишити: {no_person}")

        cur.execute("""SELECT queue_type, count(*) FROM review_queue
                        WHERE resolved_at IS NULL GROUP BY 1 ORDER BY 2 DESC""")
        print("\nЧерга зараз:")
        for t, n in cur.fetchall():
            print(f"   {t:<20} {n}")

        if args.apply:
            cur.execute("""
                UPDATE facts f SET status = 'unconfirmed'
                 WHERE f.status = 'confirmed'
                   AND f.object_id IN (SELECT object_id FROM people
                                        WHERE service_id IS NULL)
            """)
            changed = cur.rowcount
            if to_close:
                cur.execute("""
                    UPDATE review_queue
                       SET resolved_at = now(), resolution = %s
                     WHERE id = ANY(%s) AND resolved_at IS NULL
                """, (RESOLUTION, to_close))
                closed = cur.rowcount
            else:
                closed = 0
            conn.commit()
            print(f"\nЗАСТОСОВАНО: фактів переведено в unconfirmed {changed}, "
                  f"завдань закрито {closed}")

            cur.execute("""SELECT count(*) FROM review_queue
                            WHERE resolved_at IS NULL""")
            print(f"   відкритих завдань лишилось: {cur.fetchone()[0]}")
        else:
            print("\nDRY-RUN: нічого не змінено")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
