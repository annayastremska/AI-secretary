"""Додає внутрішні інструкції підрозділу в корпус (тестово, зворотно).

Запуск:
    python db/scripts/load_informal_test.py --files a.md b.md
    python db/scripts/load_informal_test.py --remove

## Навіщо

У корпусі 41 документ, і всі вони з двох джерел -- zakon.rada й tzi.com.ua.
Жанру «внутрішня інструкція підрозділу» (як називати вайфай, порядок
оформлення відпустки, техпаспорт ДГУ) там немає ЖОДНОГО. А саме він і буде
переважати в реальній частині, і саме на нього припадають питання, на які
система потрібна щодня.

Практичний наслідок цього перекосу вже виміряний: питання «за скільки днів
подавати рапорт» не мало в корпусі точної відповіді, і система відповідала
дотичною нормою про строк НАКАЗУ. Норма про строк самого рапорту є -- у
внутрішній інструкції, якої в базі не було.

## Зворотність

Рядки в `documents` позначені `pipeline_meta.loaded_by = 'andriy_test_informal'`,
тому `--remove` прибирає їх одним DELETE разом з одиницями. Наявних документів
скрипт не торкається.
"""
import argparse
import glob
import hashlib
import os
import sys

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "db", "scripts"))

import segment_documents as S  # noqa: E402

SCHEMA = "andriy_test"
MARK = "andriy_test_informal"


def dsn():
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    return os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")


def title_of(text, fallback):
    """Назва -- перший markdown-заголовок першого рівня, інакше ім'я файла."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--remove", action="store_true")
    args = ap.parse_args(argv)

    if args.remove:
        with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
            cur.execute(f"""DELETE FROM {SCHEMA}.document_units
                             WHERE document_id IN (
                                 SELECT id FROM documents
                                  WHERE pipeline_meta ->> 'loaded_by' = %s)""", (MARK,))
            units = cur.rowcount
            cur.execute("DELETE FROM documents WHERE pipeline_meta ->> 'loaded_by' = %s",
                        (MARK,))
            print(f"прибрано документів {cur.rowcount}, одиниць {units}")
            conn.commit()
        return 0

    paths = []
    for pattern in (args.files or []):
        paths.extend(sorted(glob.glob(pattern)) or [pattern])
    if not paths:
        print("нічого не передано в --files")
        return 1

    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        for path in paths:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            checksum = hashlib.sha256(text.encode()).hexdigest()
            cur.execute("SELECT id FROM documents WHERE checksum = %s", (checksum,))
            row = cur.fetchone()
            if row:
                print(f"  уже є: {os.path.basename(path)} -> documents.id={row[0]}")
                doc_id = row[0]
            else:
                cur.execute("""
                    INSERT INTO documents (source_kind, status, domain, checksum,
                                           raw_uri, validity, validity_source,
                                           text_content, pipeline_meta)
                    VALUES ('electronic', 'extracted', 'normative', %s, %s,
                            'current', 'manual', %s, %s)
                    RETURNING id
                """, (checksum, f"file:///{os.path.basename(path)}", text,
                      Jsonb({"loaded_by": MARK,
                             "title": title_of(text, os.path.basename(path)),
                             "source_file": os.path.basename(path),
                             "genre": "внутрішня інструкція підрозділу"})))
                doc_id = cur.fetchone()[0]
                print(f"  додано: {os.path.basename(path)} -> documents.id={doc_id}")

            cur.execute(f"DELETE FROM {SCHEMA}.document_units WHERE document_id = %s",
                        (doc_id,))
            family, units = S.segment(text, "nest")
            for ord_, u in enumerate(units):
                cur.execute(f"""
                    INSERT INTO {SCHEMA}.document_units
                        (document_id, ord, label, base_label, parent_label,
                         char_start, char_end, from_length_split, text, tsv)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,to_tsvector('ukrainian', %s))
                """, (doc_id, ord_, u["label"], u["base_label"], u["parent"],
                      u["char_start"], u["char_end"],
                      S.SPLIT_MARK in u["label"], u["text"], u["text"]))
            print(f"      маркери: {family}, одиниць {len(units)}")
        conn.commit()
    print("\nвектори -- окремо: build_units_test.py --embed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
