# Заповнює локальний Postgres (docker-compose.yml поруч) тими самими даними,
# що ../seed.py кладе в stand.sqlite. Джерело — те саме ../data/, схема — schema.sql.
# Ідемпотентно: перед вставкою чистить три таблиці (TRUNCATE), можна ганяти повторно.
#
# Використання:
#   pip install -r requirements.txt
#   docker compose up -d
#   python3 load_data.py

import csv
import glob
import os
import re
import sys

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")

DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://chat_stand:chat_stand_local@localhost:5432/chat_stand",
)

SOURCE_NOTE = "внутрішній документ частини, ред. 2026-04"

PEOPLE_COLS = ["service_id", "full_name", "rank", "position_title",
               "subdivision", "phone"]
ABSENCE_COLS = ["doc_number", "doc_date", "doc_type", "service_id",
                "person_name_raw", "date_from", "date_to", "reason",
                "place", "status", "superseded_by", "source_file"]


def read_csv(path, expected_cols):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != expected_cols:
            sys.exit(f"{os.path.basename(path)}: колонки {reader.fieldnames}, "
                     f"очікував {expected_cols}")
        return [{c: (row[c] or "").strip() for c in expected_cols}
                for row in reader]


def parse_reference(path):
    """Markdown → рядки reference_docs. Один розділ = один рядок."""
    text = open(path, encoding="utf-8").read()
    title_match = re.search(r"^#\s+(.+)$", text, re.M)
    if not title_match:
        sys.exit(f"{os.path.basename(path)}: немає заголовка H1")
    doc_title = title_match.group(1).strip()
    rows = []
    sections = re.split(r"^##\s+", text, flags=re.M)[1:]
    for s in sections:
        head, _, body = s.partition("\n")
        m = re.match(r"(\d+)\.\s*(.+)", head.strip())
        if not m:
            sys.exit(f"{os.path.basename(path)}: розділ без номера: {head!r}")
        rows.append({
            "doc_title": doc_title,
            "section_number": m.group(1),
            "section_title": m.group(2).strip(),
            "text": body.strip(),
            "source_note": SOURCE_NOTE,
        })
    return rows


def main():
    people = read_csv(os.path.join(DATA, "people.csv"), PEOPLE_COLS)
    absences = read_csv(os.path.join(DATA, "absences.csv"), ABSENCE_COLS)
    ref_rows = []
    for path in sorted(glob.glob(os.path.join(DATA, "reference", "*.md"))):
        ref_rows.extend(parse_reference(path))
    if not ref_rows:
        sys.exit("data/reference/ порожня — довідкових документів немає")

    conn = psycopg2.connect(DSN)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("TRUNCATE people, absences, reference_docs")
            cur.executemany(
                f"INSERT INTO people ({','.join(PEOPLE_COLS)}) VALUES "
                f"({','.join(['%s'] * len(PEOPLE_COLS))})",
                [[p[c] for c in PEOPLE_COLS] for p in people])
            cur.executemany(
                f"INSERT INTO absences ({','.join(ABSENCE_COLS)}) VALUES "
                f"({','.join(['%s'] * len(ABSENCE_COLS))})",
                [[a[c] for c in ABSENCE_COLS] for a in absences])
            cur.executemany(
                "INSERT INTO reference_docs "
                "(doc_title, section_number, section_title, text, source_note) "
                "VALUES (%s,%s,%s,%s,%s)",
                [[r["doc_title"], r["section_number"], r["section_title"],
                  r["text"], r["source_note"]] for r in ref_rows])
    finally:
        conn.close()

    print(f"Postgres ({DSN.split('@')[-1]}): people {len(people)}, "
          f"absences {len(absences)}, reference_docs {len(ref_rows)}")


if __name__ == "__main__":
    main()
