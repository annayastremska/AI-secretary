# Збирає stand.sqlite з CSV і markdown. База — похідна, її можна видаляти:
# python3 seed.py відтворює її з нуля.

import csv
import glob
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "stand.sqlite")
DATA = os.path.join(HERE, "data")

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
        # Порожнє поле лишається порожнім рядком — нічого не підставляємо.
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

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(f"CREATE TABLE people ({', '.join(c + ' TEXT' for c in PEOPLE_COLS)})")
    conn.execute(f"CREATE TABLE absences ({', '.join(c + ' TEXT' for c in ABSENCE_COLS)})")
    conn.execute("CREATE TABLE reference_docs (doc_title TEXT, section_number TEXT, "
                 "section_title TEXT, text TEXT, source_note TEXT)")
    conn.executemany(
        f"INSERT INTO people VALUES ({','.join('?' * len(PEOPLE_COLS))})",
        [[p[c] for c in PEOPLE_COLS] for p in people])
    conn.executemany(
        f"INSERT INTO absences VALUES ({','.join('?' * len(ABSENCE_COLS))})",
        [[a[c] for c in ABSENCE_COLS] for a in absences])
    conn.executemany(
        "INSERT INTO reference_docs VALUES (?,?,?,?,?)",
        [[r["doc_title"], r["section_number"], r["section_title"],
          r["text"], r["source_note"]] for r in ref_rows])
    conn.commit()
    conn.close()
    print(f"stand.sqlite: people {len(people)}, absences {len(absences)}, "
          f"reference_docs {len(ref_rows)}")


if __name__ == "__main__":
    main()
