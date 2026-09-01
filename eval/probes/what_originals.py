# -*- coding: utf-8 -*-
"""Що це за оригінали, яких немає в git: питаємо базу за їхнім хешем.

Файли в `data/originals/` названі власним sha256 -- тим самим, що лежить у
`documents.checksum`. Тобто по імені файла можна дізнатись, який це документ і
коли він з'явився: чи це наш демо-набір, чи чиєсь тестове завантаження.
"""
import sys

import psycopg
from psycopg.rows import dict_row

sys.stdout.reconfigure(encoding="utf-8")

HASHES = [
    "5f2462f4a8ede7eda468b6936e93e510d3041efe79189f83ed9ef8855edc01d7",
    "778aae7c4d0435ab0a818065defe15c98566604d6566fcf312c5c4ae877785e8",
    "7d50e40409c737a13709c98b49f30b868c3995ef4900b6d02e0cfabbb55a7958",
    "8575318363a24cb7d2ebd7a2a053a8762be19091680aa80f3ee080166923f21b",
    "a2c915aa5c6499eea701adc70d8dbe6cb006c76d9c5dcc0858b335adff52a019",
    "ce0696e4f4cf9a71c1307a7cbf5a88e04f729766229151b7394b8dc868936b53",
]

env = dict(l.strip().split("=", 1) for l in open(".env")
           if "=" in l and not l.startswith("#"))
DSN = env["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

with psycopg.connect(DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
    cur.execute("""
        SELECT checksum, id, domain, status, source_kind,
               to_char(uploaded_at, 'MM-DD HH24:MI') AS uploaded
          FROM documents
         WHERE checksum = ANY(%(h)s)
         ORDER BY uploaded_at
    """, {"h": HASHES})
    rows = cur.fetchall()

seen = set()
for r in rows:
    seen.add(r["checksum"])
    print("%s  запис №%-4s %-11s %-13s %s %s"
          % (r["checksum"][:8], r["id"], r["domain"], r["status"],
             r["source_kind"], r["uploaded"]))
for h in HASHES:
    if h not in seen:
        print("%s  У БАЗІ НЕМА -- завантажений, але не підтверджений" % h[:8])
