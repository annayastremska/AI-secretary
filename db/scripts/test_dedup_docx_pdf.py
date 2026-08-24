"""Перевірка дедуплікації: той самий документ у .docx і .pdf дає ОДИН факт.

Це не unit-тест, а перевірка на живій базі -- нам потрібне саме поведінка
лоадера з реальним SQL, а не мок. Записи створюються синтетично й у кінці
прибираються.

Запуск: python db/scripts/test_dedup_docx_pdf.py

Перевіряє чотири речі:
  1. два файли того самого документа -> один факт, два джерела;
  2. РІЗНІ документи (інший номер) -> два факти, не зливаються;
  3. документи з порожніми реквізитами -> НЕ зливаються (інакше дефектні
     документи склеювались би між собою -- це втрата даних);
  4. список не показує дубля, підрахунок і список дають те саме число.
"""
import os
import sys
import uuid

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "airflow", "plugins"))
import ai_secretary_loader as L  # noqa: E402

MARK = f"dedup-test-{uuid.uuid4().hex[:8]}"


def make_doc(cur, checksum, source_file):
    cur.execute(
        """
        INSERT INTO documents (source_kind, status, domain, checksum, raw_uri,
                               validity, pipeline_meta)
        VALUES ('electronic', 'extracted', 'leave', %s, %s, 'unknown',
                jsonb_build_object('title', %s::text, 'test_mark', %s::text))
        RETURNING id
        """,
        (checksum, f"file:///{source_file}", source_file, MARK),
    )
    return cur.fetchone()[0]


def make_person(cur, surname):
    object_id, _ = L.get_or_create_person(cur, surname, "Тест", "Тестович", None)
    return object_id


def insert_requisites(cur, doc_id, obj_id, number, date):
    """Реквізити документа -- фактами, як їх віддає пайплайн."""
    for code, value in (("document_number", number), ("document_date", date)):
        if not value:
            continue
        dim = L.get_or_create_dimension(cur, code, "permanent_event")
        cur.execute(
            """INSERT INTO facts (object_id, dimension_id, value, source_doc_id, status)
               VALUES (%s, %s, %s, %s, 'confirmed') RETURNING id""",
            (obj_id, dim, value, doc_id),
        )
        L.add_fact_source(cur, cur.fetchone()[0], doc_id, is_primary=True)


def load_leave(cur, doc_id, obj_id, number, date, value="щорічна основна",
               start="2026-05-10", end="2026-05-24"):
    dim = L.get_or_create_dimension(cur, "leave", "ranged")
    return L.insert_fact(cur, obj_id, dim, value, start, end,
                         doc_id, True, doc_number=number, doc_date=date)


def main():
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    ok = True

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            print("── 1. той самий документ у docx і pdf")
            person = make_person(cur, f"Дедуплікатенко{MARK[-4:]}")
            d1 = make_doc(cur, f"{MARK}-docx", "LEAVE-777.docx")
            d2 = make_doc(cur, f"{MARK}-pdf", "LEAVE-777.pdf")
            insert_requisites(cur, d1, person, "№777", "2026-05-01")
            insert_requisites(cur, d2, person, "№777", "2026-05-01")
            f1 = load_leave(cur, d1, person, "№777", "2026-05-01")
            f2 = load_leave(cur, d2, person, "№777", "2026-05-01")
            same = f1 == f2
            cur.execute("SELECT count(*) FROM fact_sources WHERE fact_id = %s", (f1,))
            n_src = cur.fetchone()[0]
            print(f"   факт один: {same} (id {f1} / {f2}), джерел: {n_src}")
            ok &= same and n_src == 2

            print("── 2. РІЗНІ документи (інший номер) -- не зливати")
            d3 = make_doc(cur, f"{MARK}-other", "LEAVE-888.docx")
            insert_requisites(cur, d3, person, "№888", "2026-05-02")
            # Інший період: дві відпустки того самого виду -- це справді два
            # факти, і зливати їх не можна.
            f3 = load_leave(cur, d3, person, "№888", "2026-05-02",
                            start="2026-06-01", end="2026-06-14")
            distinct = f3 not in (f1, f2)
            print(f"   окремий факт: {distinct} (id {f3})")
            ok &= distinct

            print("── 3. порожні реквізити -- НЕ зливати")
            d4 = make_doc(cur, f"{MARK}-empty1", "DEFECT-1.docx")
            d5 = make_doc(cur, f"{MARK}-empty2", "DEFECT-2.docx")
            # Період інший, ніж у пари з п.1 -- інакше ці два факти теж
            # попадуть у зріз п.4 і зіпсують його.
            f4 = load_leave(cur, d4, person, None, None, "без реквізитів",
                            start="2026-07-01", end="2026-07-10")
            f5 = load_leave(cur, d5, person, None, None, "без реквізитів",
                            start="2026-07-01", end="2026-07-10")
            not_merged = f4 != f5
            print(f"   не злилися: {not_merged} (id {f4} / {f5})")
            ok &= not_merged

            print("── 4. на дату пари: підрахунок і список дають те саме")
            # Зріз на 2026-05-15 -- у цей день чинна лише відпустка з пари
            # docx/pdf. Саме тут раніше й розходились числа: підрахунок
            # (COUNT DISTINCT object_id) давав 1, а список -- 2 рядки.
            slice_sql = """
                  FROM facts f
                  JOIN dimensions d ON d.id = f.dimension_id
                 WHERE d.code = 'leave' AND f.status <> 'rejected'
                   AND f.object_id = %s
                   AND f.valid_from <= '2026-05-15' AND f.valid_to >= '2026-05-15'
            """
            cur.execute("SELECT count(DISTINCT f.object_id) " + slice_sql, (person,))
            counted = cur.fetchone()[0]
            cur.execute("SELECT count(*) " + slice_sql, (person,))
            listed = cur.fetchone()[0]
            print(f"   підрахунок {counted}, рядків у списку {listed}")
            ok &= counted == listed == 1

            # Прибираємо за собою: тест не має лишати сміття в базі.
            cur.execute("""
                DELETE FROM facts WHERE source_doc_id IN (
                    SELECT id FROM documents WHERE pipeline_meta ->> 'test_mark' = %s)
            """, (MARK,))
            cur.execute("DELETE FROM documents WHERE pipeline_meta ->> 'test_mark' = %s", (MARK,))
            cur.execute("DELETE FROM object_aliases WHERE object_id = %s", (person,))
            cur.execute("DELETE FROM people WHERE object_id = %s", (person,))
            cur.execute("DELETE FROM objects WHERE id = %s", (person,))
        conn.commit()

    print("\n" + ("ВСЕ ПРОЙШЛО" if ok else "Є ПОМИЛКИ"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
