"""Знаходить документи-дублікати й обирає в групі канонічний.

Запуск:
    python db/scripts/group_duplicate_docs.py            # показати
    python db/scripts/group_duplicate_docs.py --apply

## Навіщо -- і це вже кусає, не теоретично

У корпусі є пари, де той самий акт лежить двічі: 201/224 (той самий закон із
тією самою ВВР-статтею), 237/238 (той самий указ), 205/222 (Положення про ВЛК
і наказ, яким воно затверджене).

На питанні «за скільки днів подавати рапорт» ОБА місця топ-2 пішли на ту саму
цитату з пари 237/238. Тобто дублікат не просто дає повтор -- він витісняє з
видачі інші документи, які могли б відповісти. Ворота при цьому двічі платять
за той самий текст.

## Як визначається дублікат

Два сигнали, обидва механічні:

1. **Однаковий ідентифікатор.** Якщо в обох витягнуто той самий номер --
   це той самий акт. Ловить 201/224 і 237/238.
2. **Схожість голів.** Ловить пари, де ідентифікатор витягнуто лише з одного
   (205 не має свого номера в тексті, 222 має наказ № 402).

Аня просила НЕ видаляти жодного документа з пари: провенанс тримається на
обох. Тому тут не видалення, а групування -- обидва лишаються в базі, у видачі
група рахується як один результат.

## Хто канонічний

Той, у кого є ідентифікатор; при рівності -- довший текст (повніше
вивантаження). Це щоб у підвалі відповіді стояло «наказ № 402», а не «—».
"""
import argparse
import difflib
import os
import re
import sys

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "db", "scripts"))

import extract_document_identity as E  # noqa: E402

SCHEMA = "andriy_test"
SIM_THRESHOLD = 0.60

DDL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.doc_groups (
    document_id  bigint PRIMARY KEY,
    canonical_id bigint NOT NULL,
    reason       text NOT NULL
);
"""


def dsn():
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    return os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("""SELECT id, text_content FROM documents
                        WHERE domain='normative' AND text_content IS NOT NULL
                        ORDER BY id""")
        docs = cur.fetchall()

        info = {}
        for doc_id, text in docs:
            r = E.extract(text)
            info[doc_id] = {
                "ident": r.get("identifier"),
                "len": len(text),
                "head": re.sub(r"\s+", " ", E.strip_braces(text[:3000])).strip().lower(),
            }

        # 1. однаковий ідентифікатор
        pairs = []
        by_ident = {}
        for doc_id, d in info.items():
            if d["ident"]:
                by_ident.setdefault(E.normalize_key(d["ident"]), []).append(doc_id)
        for key, ids in by_ident.items():
            if len(ids) > 1:
                for a in ids[1:]:
                    pairs.append((ids[0], a, f"той самий ідентифікатор {key}"))

        # 2. схожість голів -- для пар, де номер витягнуто лише з одного
        ids = sorted(info)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if any(a in (x, y) and b in (x, y) for x, y, _ in pairs):
                    continue
                sm = difflib.SequenceMatcher(None, info[a]["head"], info[b]["head"])
                if sm.quick_ratio() < SIM_THRESHOLD:
                    continue
                r = sm.ratio()
                if r >= SIM_THRESHOLD:
                    pairs.append((a, b, f"схожість голів {r:.2f}"))

        # з'єднуємо в групи
        parent = {}

        def find(x):
            while parent.get(x, x) != x:
                x = parent[x]
            return x

        for a, b, _r in pairs:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra
        groups = {}
        for doc_id in info:
            groups.setdefault(find(doc_id), []).append(doc_id)
        groups = {k: sorted(v) for k, v in groups.items() if len(v) > 1}

        print(f"груп дублікатів: {len(groups)}")
        rows = []
        for members in groups.values():
            # канонічний: із ідентифікатором, далі -- довший текст
            canon = sorted(members,
                           key=lambda d: (info[d]["ident"] is None, -info[d]["len"]))[0]
            why = next((r for a, b, r in pairs
                        if a in members and b in members), "група")
            print(f"  {members} -> канонічний {canon} "
                  f"({info[canon]['ident'] or '—'})   {why}")
            for m in members:
                print(f"      {m}: {info[m]['ident'] or '—':<26} "
                      f"{info[m]['len']:>7} симв.")
                rows.append((m, canon, why))

        if args.apply:
            cur.execute(DDL)
            cur.execute(f"TRUNCATE {SCHEMA}.doc_groups")
            for m, canon, why in rows:
                cur.execute(f"""INSERT INTO {SCHEMA}.doc_groups
                                    (document_id, canonical_id, reason)
                                VALUES (%s,%s,%s)""", (m, canon, why))
            conn.commit()
            print(f"\nЗАПИСАНО: {len(rows)} рядків у {SCHEMA}.doc_groups")
        else:
            print("\nDRY-RUN: нічого не змінено")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
