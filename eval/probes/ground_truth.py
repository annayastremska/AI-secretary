# -*- coding: utf-8 -*-
"""Правильні відповіді на питання аудиту -- моїми запитами, не моделі.

Без цього висновок «відповідь правильна» не перевірити: чат показує число,
а звірити його можна лише з іншим числом, отриманим інакше.
"""
import sys

import psycopg
from psycopg.rows import dict_row

sys.stdout.reconfigure(encoding="utf-8")

env = dict(l.strip().split("=", 1) for l in open(".env")
           if "=" in l and not l.startswith("#"))
DSN = env["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

Q = [
    ("1. середня тривалість відпустки (за датами)",
     "SELECT round(AVG(f.valid_to - f.valid_from + 1), 1) v FROM facts f "
     "JOIN dimensions d ON d.id = f.dimension_id "
     "WHERE d.code = 'leave' AND f.status = 'confirmed'"),
    ("2. найдовша відпустка (за датами)",
     "SELECT MAX(f.valid_to - f.valid_from + 1) v FROM facts f "
     "JOIN dimensions d ON d.id = f.dimension_id "
     "WHERE d.code = 'leave' AND f.status = 'confirmed'"),
    ("2б. найдовша відпустка (leave_days текстом)",
     "SELECT MAX(f.value::int) v FROM facts f "
     "JOIN dimensions d ON d.id = f.dimension_id "
     "WHERE d.code = 'leave_days' AND f.status = 'confirmed'"),
    ("3. середня тривалість відрядження (deployment_location)",
     "SELECT round(AVG(f.valid_to - f.valid_from + 1), 1) v FROM facts f "
     "JOIN dimensions d ON d.id = f.dimension_id "
     "WHERE d.code = 'deployment_location' AND f.status = 'confirmed'"),
    ("3б. чи є ДАТИ у deployment_days (0 = немає)",
     "SELECT count(*) v FROM facts f JOIN dimensions d ON d.id = f.dimension_id "
     "WHERE d.code = 'deployment_days' AND f.valid_from IS NOT NULL"),
    ("4. різних населених пунктів",
     "SELECT count(DISTINCT btrim(f.value)) v FROM facts f "
     "JOIN dimensions d ON d.id = f.dimension_id "
     "WHERE d.code IN ('leave_place', 'deployment_location') "
     "  AND f.status = 'confirmed'"),
    ("5. документів без номера на папері",
     "SELECT count(*) v FROM documents dd WHERE NOT EXISTS ("
     "  SELECT 1 FROM facts f JOIN dimensions d ON d.id = f.dimension_id "
     "  WHERE f.source_doc_id = dd.id AND d.code = 'document_number')"),
    ("6. осіб із більш ніж одним документом",
     "SELECT count(*) v FROM (SELECT object_id FROM facts "
     "  WHERE status = 'confirmed' GROUP BY object_id "
     "  HAVING count(DISTINCT source_doc_id) > 1) t"),
    ("7. людей І з відпусткою, І з відрядженням",
     "SELECT count(*) v FROM (SELECT f.object_id FROM facts f "
     "  JOIN dimensions d ON d.id = f.dimension_id "
     "  WHERE d.code IN ('leave', 'deployment_location') "
     "    AND f.status = 'confirmed' GROUP BY f.object_id "
     "  HAVING count(DISTINCT d.code) = 2) t"),
    ("12. відпусток, що ПОЧАЛИСЬ у липні",
     "SELECT count(*) v FROM facts f JOIN dimensions d ON d.id = f.dimension_id "
     "WHERE d.code = 'leave' AND f.status = 'confirmed' "
     "  AND f.valid_from BETWEEN '2026-07-01' AND '2026-07-31'"),
    ("13. відпусток, що почались у серпні",
     "SELECT count(*) v FROM facts f JOIN dimensions d ON d.id = f.dimension_id "
     "WHERE d.code = 'leave' AND f.status = 'confirmed' "
     "  AND f.valid_from BETWEEN '2026-08-01' AND '2026-08-31'"),
    ("8. відсоток підтверджених фактів",
     "SELECT round(100.0 * count(*) FILTER (WHERE status = 'confirmed') "
     "  / count(*), 1) v FROM facts"),
    ("9. найбільше документів в однієї особи",
     "SELECT max(c) v FROM (SELECT count(DISTINCT source_doc_id) c FROM facts "
     "  WHERE status = 'confirmed' GROUP BY object_id) t"),
    ("11. найдовше відрядження, днів",
     "SELECT MAX(f.valid_to - f.valid_from + 1) v FROM facts f "
     "JOIN dimensions d ON d.id = f.dimension_id "
     "WHERE d.code = 'deployment_location' AND f.status = 'confirmed'"),
]

with psycopg.connect(DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
    for label, sql in Q:
        try:
            cur.execute(sql)
            print("%-52s %s" % (label, cur.fetchone()["v"]))
        except Exception as exc:                     # noqa: BLE001
            print("%-52s ПОМИЛКА: %s" % (label, str(exc)[:60]))
            conn.rollback()
