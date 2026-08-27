"""Перевіряє хронологію витіснення в insert_fact -- усі три випадки.

Запуск (на сервері, де є база):
    python db/scripts/test_insert_fact_chronology.py

## Чому тест іде проти СПРАВЖНЬОЇ бази, але нічого не змінює

Уся робота йде в одній транзакції, яка в кінці ВІДКОЧУЄТЬСЯ. Це навмисно:
половина того, що тут перевіряється, -- не логіка Python, а обмеження бази
(`facts_check: valid_to >= valid_from`) і справжня SQL-функція
`resolve_or_create_object`. На підробці (мок-курсор) саме ці дві речі й не
спрацювали б, а падав старий код рівно на них.

Об'єкти створюються свої, з унікальним псевдонімом на кожен випадок, щоб
наявні факти чужих осіб не впливали на перевірку.

## Що саме перевіряється

Інваріант, для якого функція існує: **в об'єкта не може бути двох чинних
(valid_to IS NULL) підтверджених фактів того самого виміру.** Плюс -- що в
кожному з трьох випадків витіснення ухвалено правильне рішення, а не просто
не впало.
"""
import os
import sys
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "airflow", "plugins"))

import psycopg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

import ai_secretary_loader as L  # noqa: E402

DIM = "position"          # вимір із validity_model='current_state'
ok = 0
fail = 0


def check(name, condition, detail=""):
    global ok, fail
    if condition:
        ok += 1
        print(f"  OK   {name}")
    else:
        fail += 1
        print(f"  ПРОВАЛ {name}   {detail}")


def fresh_person(cur, doc_id, tag):
    alias = f"ТестХронології-{tag}-{uuid.uuid4().hex[:8]}"
    object_id, _ = L.get_or_create_person(cur, alias, "Тест", "Тестович", doc_id,
                                          person_alias=alias)
    return object_id


def state(cur, object_id, dim_id):
    cur.execute(
        """
        SELECT id, value, valid_from, valid_to, status FROM facts
         WHERE object_id = %s AND dimension_id = %s
         ORDER BY id
        """,
        (object_id, dim_id),
    )
    return cur.fetchall()


def open_confirmed(rows):
    return [r for r in rows if r[3] is None and r[4] == "confirmed"]


def actions(cur, fact_ids):
    if not fact_ids:
        return []
    cur.execute("SELECT action FROM review_log WHERE fact_id = ANY(%s)", (list(fact_ids),))
    return [r[0] for r in cur.fetchall()]


def main():
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

    conn = psycopg.connect(dsn)
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM documents ORDER BY id LIMIT 1")
        doc_id = cur.fetchone()[0]
        dim_id = L.get_or_create_dimension(cur, DIM, "current_state")
        cur.execute("SELECT validity_model FROM dimensions WHERE id = %s", (dim_id,))
        vm = cur.fetchone()[0]
        print(f"вимір '{DIM}' id={dim_id} validity_model={vm}, документ-джерело {doc_id}\n")
        if vm != "current_state":
            print(f"вимір не current_state -- тест не про це, вихід")
            return 1

        # ── 1. новий факт ПІЗНІШИЙ: звичайне витіснення ──────────────────
        print("1. новий пізніший -> закривається наявний")
        o = fresh_person(cur, doc_id, "later")
        a = L.insert_fact(cur, o, dim_id, "старший стрілець", "2026-01-01", None, doc_id, True)
        b = L.insert_fact(cur, o, dim_id, "командир відділення", "2026-06-01", None, doc_id, True)
        rows = state(cur, o, dim_id)
        old = [r for r in rows if r[0] == a][0]
        new = [r for r in rows if r[0] == b][0]
        check("наявний закритий датою нового", str(old[3]) == "2026-06-01", f"valid_to={old[3]}")
        check("новий чинний", new[3] is None and new[4] == "confirmed", f"{new[3]} {new[4]}")
        check("один чинний підтверджений", len(open_confirmed(rows)) == 1,
              f"{len(open_confirmed(rows))}")

        # ── 2. новий факт РАНІШИЙ: це історія, не заміна ─────────────────
        print("\n2. новий раніший -> історія, наявний лишається чинним")
        o = fresh_person(cur, doc_id, "hist")
        a = L.insert_fact(cur, o, dim_id, "командир відділення", "2026-06-01", None, doc_id, True)
        b = L.insert_fact(cur, o, dim_id, "старший стрілець", "2026-01-01", None, doc_id, True)
        rows = state(cur, o, dim_id)
        old = [r for r in rows if r[0] == a][0]
        new = [r for r in rows if r[0] == b][0]
        check("наявний НЕ закритий", old[3] is None, f"valid_to={old[3]}")
        check("новий закритий початком наявного", str(new[3]) == "2026-06-01", f"valid_to={new[3]}")
        check("один чинний підтверджений", len(open_confirmed(rows)) == 1,
              f"{len(open_confirmed(rows))}")
        check("у журналі inserted_as_historical", "inserted_as_historical" in actions(cur, [b]),
              str(actions(cur, [b])))

        # ── 3а. дати РІВНІ: впорядкувати неможливо ───────────────────────
        print("\n3а. дати рівні -> не витісняємо, новий лишається чернеткою")
        o = fresh_person(cur, doc_id, "eq")
        a = L.insert_fact(cur, o, dim_id, "командир відділення", "2026-03-01", None, doc_id, True)
        b = L.insert_fact(cur, o, dim_id, "старший стрілець", "2026-03-01", None, doc_id, True)
        rows = state(cur, o, dim_id)
        old = [r for r in rows if r[0] == a][0]
        new = [r for r in rows if r[0] == b][0]
        check("наявний НЕ закритий", old[3] is None, f"valid_to={old[3]}")
        check("новий -- чернетка", new[4] == "unconfirmed", f"status={new[4]}")
        check("один чинний підтверджений", len(open_confirmed(rows)) == 1,
              f"{len(open_confirmed(rows))}")
        check("у журналі ambiguous_order_left_unconfirmed",
              "ambiguous_order_left_unconfirmed" in actions(cur, [b]), str(actions(cur, [b])))

        # ── 3б. дата ВІДСУТНЯ: той самий тихий випадок ───────────────────
        print("\n3б. дата нового відсутня -> те саме (тут і був тихий дефект)")
        o = fresh_person(cur, doc_id, "null")
        a = L.insert_fact(cur, o, dim_id, "командир відділення", "2026-03-01", None, doc_id, True)
        b = L.insert_fact(cur, o, dim_id, "старший стрілець", None, None, doc_id, True)
        rows = state(cur, o, dim_id)
        new = [r for r in rows if r[0] == b][0]
        check("новий -- чернетка", new[4] == "unconfirmed", f"status={new[4]}")
        check("НЕ два чинних підтверджених", len(open_confirmed(rows)) == 1,
              f"чинних підтверджених {len(open_confirmed(rows))} -- саме це давав старий код")

        # ── 4а. те саме значення БЕЗ реквізитів -- НЕ зливається ─────────
        # Це не недогляд, а засторога у find_equivalent_fact: без номера й
        # дати зливалися б між собою дефектні документи, у яких реквізити не
        # розпізнались, а це вже втрата даних, не дедуплікація. Тест стоїть
        # тут саме тому, що я спершу очікував протилежного.
        print("\n4а. те саме значення без реквізитів -> дедуплікації НЕ буде")
        o = fresh_person(cur, doc_id, "same")
        a = L.insert_fact(cur, o, dim_id, "командир відділення", "2026-03-01", None, doc_id, True)
        b = L.insert_fact(cur, o, dim_id, "командир відділення", "2026-03-01", None, doc_id, True)
        check("два окремих факти", a != b, f"{a} == {b}")

        # ── 4б. те саме значення З реквізитами -- один факт, два джерела ──
        print("\n4б. те саме значення з тими самими реквізитами -> один факт")
        cur.execute(
            """
            SELECT fn.source_doc_id, fn.value, fd.value
              FROM facts fn
              JOIN dimensions dn ON dn.id = fn.dimension_id AND dn.code = 'document_number'
              JOIN facts fd ON fd.source_doc_id = fn.source_doc_id
              JOIN dimensions dd ON dd.id = fd.dimension_id AND dd.code = 'document_date'
             WHERE fn.value <> '' AND fd.value <> ''
             LIMIT 1
            """
        )
        req = cur.fetchone()
        if req is None:
            print("  ПРОПУСК: у базі немає документа з фактами номера й дати")
        else:
            src, num, dat = req
            o = fresh_person(cur, src, "dedup")
            a = L.insert_fact(cur, o, dim_id, "командир відділення", "2026-03-01", None, src,
                              True, doc_number=num, doc_date=dat)
            b = L.insert_fact(cur, o, dim_id, "командир відділення", "2026-03-01", None, src,
                              True, doc_number=num, doc_date=dat)
            check("той самий факт, не другий", a == b, f"{a} != {b} (№{num} від {dat})")

    finally:
        conn.rollback()
        conn.close()
        print("\nтранзакцію відкочено -- у базі нічого не змінилось")

    print(f"\nразом: OK {ok}, провалів {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
