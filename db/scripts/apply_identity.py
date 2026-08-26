"""Заповнює реквізити документів із ДВОХ джерел: YAML-шапки й тексту.

Запуск:
    python db/scripts/apply_identity.py            # показати
    python db/scripts/apply_identity.py --apply

## Чому два джерела, а не одне

Заміряно на 41 документі: джерела ДОПОВНЮЮТЬ одне одного, і жодне не покриває
все.

* **YAML-шапка вивантаження** (`source_file` -- оригінальне ім'я файла) знає
  номер у 7 документів, де в тексті його немає взагалі: додаток до наказу,
  «Про затвердження Змін», Правила носіння форми без шапки наказу. Серед них
  пара 227/231 -- скасований наказ 333 і чинний 280, тобто саме той випадок,
  на якому перевіряється «не процитовано скасований документ».
* **Текст** знає у 2 документів, де файл названо руками
  (`інструкція_діловодство.docx` -- жодного номера в імені).

На решті 29 обидва джерела ЗБІГАЮТЬСЯ, тобто шапка ще й незалежно підтверджує
роботу текстового екстрактора.

Порядок: шапка -> текст -> відмова. `identity_source` фіксує, звідки взято, бо
без цього неможливо відрізнити витягнуте від здогадки.

## Чому шапка перша, хоч я півсесії писав екстрактор

Бо ім'я файла -- це те, що написала ЛЮДИНА, яка завантажувала документ, а не
результат розбору. Витягування з тексту лишається як доповнення й перехресна
перевірка. Помилка була в тому, що я спершу не подивився в шапку взагалі:
`pipeline_meta` у базі поля `source_file` не має, і я вирішив, що пайплайн його
відкинув. Він його зберіг -- загубила копія в базі.
"""
import argparse
import glob
import os
import re
import sys

import psycopg
import yaml
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "db", "scripts"))

import extract_document_identity as E  # noqa: E402

# Тека вивантаження пайплайна, звідки читаються шапки. Лише читання.
OUTPUT_DIRS = [
    os.path.expanduser("~/anya/ai-secretary/data/output-demo/documents"),
    os.path.join(PROJECT_ROOT, "data", "output-demo", "documents"),
]

MONTHS = {"01": 1}
# Хвіст імені файла з zakon.rada: «- d472415-20240307.pdf»
TAIL = re.compile(r"\s*-\s*[a-z]?\d{5,}-\d{8}\.\w+$", re.I)
# «- Наказ № 606 від 20.11.2017», «- Закон № 2011-XII від 20.12.1991»
KIND_NUM = re.compile(
    r"\s*-\s*(Наказ|Закон|Указ|Постанова|Кодекс|Розпорядження)\s*(?:України\s*)?"
    r"№\s*([\w/\-]+)\s*(?:від\s*(\d{2}\.\d{2}\.\d{4}))?", re.I)
TZI = re.compile(r"^(НД\s*ТЗІ\s*\d\.\d-\d{3}-\d{2,4})\s*-\s*(.+)$", re.I)


def from_filename(src):
    """-> (ідентифікатор, назва, дата ISO) з оригінального імені файла."""
    if not src:
        return None, None, None
    name = TAIL.sub("", src).strip()
    name = re.sub(r"\.(pdf|docx?|htm l?|html|md)$", "", name, flags=re.I).strip()

    m = TZI.match(name)
    if m:
        return re.sub(r"\s+", " ", m.group(1)), m.group(2).strip(), None

    m = KIND_NUM.search(name)
    if m:
        title = name[:m.start()].strip(" -–—")
        num, date = m.group(2), m.group(3)
        iso = None
        if date:
            d, mo, y = date.split(".")
            iso = f"{y}-{mo}-{d}"
        # Підкреслення в імені файла -- це заборонений у файлових системах
        # слеш: «1153_2008» це «1153/2008», «80_94-ВР» це «80/94-ВР».
        num = num.replace("_", "/")
        kind = m.group(1).lower()
        ident = (f"наказ № {num}" if kind == "наказ" else f"№ {num}")
        if date and kind == "наказ":
            ident += f" від {date}"
        return ident, title or None, iso
    # Жоден шаблон не збігся. Віддавати саме ім'я файла як назву можна лише
    # коли воно ним і є: у вивантаженнях zakon.rada ім'я -- це назва акта зі
    # пробілами. А `leave-request-procedure.md` -- слаг, і як назва він гірший
    # за те, що вже лежить у pipeline_meta.
    looks_like_title = " " in name and not re.search(r"[_]{1,}", name)
    return None, (name if looks_like_title else None), None


def load_headers():
    """ai_secretary_id -> шапка. Читає .md вивантаження пайплайна."""
    heads = {}
    for base in OUTPUT_DIRS:
        for path in glob.glob(os.path.join(base, "*", "*.md")):
            try:
                raw = open(path, encoding="utf-8").read(8000)
            except OSError:
                continue
            m = re.match(r"---\n(.*?)\n---\n", raw, re.S)
            if not m:
                continue
            try:
                meta = yaml.safe_load(m.group(1))
            except yaml.YAMLError:
                continue
            if isinstance(meta, dict) and meta.get("id"):
                heads[meta["id"]] = meta
        if heads:
            break
    return heads


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    heads = load_headers()
    print(f"шапок прочитано: {len(heads)}")

    stats = {"header": 0, "text": 0, "none": 0}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""SELECT id, pipeline_meta ->> 'ai_secretary_id',
                              pipeline_meta ->> 'title',
                              pipeline_meta ->> 'source_file', text_content
                         FROM documents
                        WHERE domain = 'normative' AND text_content IS NOT NULL
                        ORDER BY id""")
        rows = cur.fetchall()

        for doc_id, aid, meta_title, meta_src, text in rows:
            src = meta_src or (heads.get(aid, {}) or {}).get("source_file")
            ident, title, iso = from_filename(src)
            source, conf = ("header", 0.95) if ident else (None, None)

            if not ident:
                r = E.extract(text)
                if r.get("identifier"):
                    ident = r["identifier"]
                    source, conf = "text", r.get("confidence") or 0.5
                title = title or r.get("title")
                iso = iso or r.get("issue_date")

            # meta_title останнім: для внутрішніх інструкцій, у яких ані
            # номера, ані структурованого імені файла немає, назва лежить
            # саме там (перший markdown-заголовок, поставлений при завантаженні).
            title = title or meta_title
            stats[source or "none"] += 1
            key = E.normalize_key(ident) if ident else None
            print(f"  {doc_id:>4} [{source or 'відмова':<7} {conf or 0:.2f}] "
                  f"{str(ident or '—'):<30} {(title or '—')[:44]}")

            if args.apply:
                cur.execute("""
                    UPDATE documents
                       SET doc_identifier = %s, doc_title = %s,
                           issue_date = %s, identifier_key = %s,
                           identity_source = %s, identity_confidence = %s
                     WHERE id = %s
                """, (ident, title, iso, key, source, conf, doc_id))
        if args.apply:
            conn.commit()

    print(f"\nз шапки {stats['header']}, з тексту {stats['text']}, "
          f"відмов {stats['none']}  (усього {len(rows)})")
    print("ЗАСТОСОВАНО" if args.apply else "DRY-RUN: нічого не змінено")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
