"""Завантажує нормативний корпус: качає документи, витягує текст, пише в базу.

Перелік і обґрунтування вибору -- docs/tasks/2026-08-21_normative-corpus.md.

Запуск:
    python db/scripts/load_normative_corpus.py            # завантажити все
    python db/scripts/load_normative_corpus.py --download-only
    python db/scripts/load_normative_corpus.py --only 548-14 z1407-22

Граблі, закладені в код (усі перевірені на практиці, див. той самий файл):

* zakon.rada.gov.ua і mil.gov.ua віддають 403 без User-Agent і без пауз між
  запитами. Половина першого пакета так і "не існувала".
* Відповідь rada стиснута -- без Accept-Encoding/gzip виходить двійкове
  сміття, яке легко прийняти за поганий OCR.
* Сторінка /laws/show/<id> -- це лише навігаційна оболонка (~4 тис. символів
  меню). Повний текст лише на /laws/show/<id>/print (для 548-14 -- 520 тис.
  символів проти 4 тис.).
* HTTP 200 не означає, що це потрібний документ. Тому в кожного запису є
  expect_title, і розбіжність -- це помилка, а не попередження.
"""
import argparse
import hashlib
import html as html_mod
import os
import re
import sys
import time
import urllib.error
import urllib.request

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "eval", "samples", "normative", "downloaded")
UA = "Mozilla/5.0 (compatible; milidoc-corpus-loader/1.0)"
RADA = "https://zakon.rada.gov.ua/laws/show/"

# validity: current / superseded / cancelled / probably_superseded / unknown.
# unknown НЕ ставимо вручну -- це дефолт колонки для того, чого ми не знаємо.
# validity_source: declared -- у самому документі є відмітка про втрату
# чинності; manual -- поставили ми, звіривши з карткою на rada.
DOCS = [
    # -- служба, відпустки, відсутність --
    ("548-14",  "rada", "Статут внутрішньої служби ЗСУ",              "Про Статут внутрішньої служб", "current", "manual"),
    ("551-14",  "rada", "Дисциплінарний статут ЗСУ",                  "Дисциплінарний статут",        "current", "manual"),
    ("2232-12", "rada", "Про військовий обов'язок і військову службу", "військовий обов",              "current", "manual"),
    ("2011-12", "rada", "Про соціальний і правовий захист військовослужбовців", "соціальний і правовий захист", "current", "manual"),
    ("3543-12", "rada", "Про мобілізаційну підготовку та мобілізацію", "мобілізаційну підготовку",     "current", "manual"),
    ("389-19",  "rada", "Про правовий режим воєнного стану",          "воєнного стану",               "current", "manual"),
    ("1932-12", "rada", "Про оборону України",                        "оборону України",              "current", "manual"),
    ("1934-12", "rada", "Про Збройні Сили України",                   "Збройні Сили України",         "current", "manual"),
    ("3099-14", "rada", "Про Військову службу правопорядку у ЗСУ",    "службу правопорядку",          "current", "manual"),
    ("2341-14", "rada", "Кримінальний кодекс України",                "Кримінальний кодекс",          "current", "manual"),
    ("2262-12", "rada", "Про пенсійне забезпечення осіб, звільнених з військової служби", "пенсійне забезпечення", "current", "manual"),
    ("3551-12", "rada", "Про статус ветеранів війни",                 "ветеранів війни",              "current", "manual"),

    # -- накази МОУ: наша предметна область --
    # Пара 333 -> 280 і є головна цінність корпусу: справжній скасований
    # документ і його чинна заміна, обидва про облік особового складу.
    ("z1407-22", "rada", "Наказ МОУ 280 (2022) — облік особового складу в системі МОУ", "обліку особового складу", "current", "manual"),
    ("z0611-14", "rada", "Наказ МОУ 333 (2014) — облік особового складу ЗСУ [СКАСОВАНО]", "обліку особового складу", "superseded", "declared"),
    ("z1126-25", "rada", "Зміни до наказу МОУ 280 (2025)",             "",                             "current", "manual"),
    ("z0638-08", "rada", "Інструкція про порядок виплати грошового забезпечення", "грошового забезпечення", "current", "manual"),

    # -- інформація, гриф, персональні дані --
    ("3855-12", "rada", "Про державну таємницю",                      "державну таємницю",            "current", "manual"),
    ("2297-17", "rada", "Про захист персональних даних",               "персональних даних",           "current", "manual"),
    ("2657-12", "rada", "Про інформацію",                             "інформацію",                   "current", "manual"),
    ("2939-17", "rada", "Про доступ до публічної інформації",          "публічної інформації",         "current", "manual"),
    ("80/94-вр", "rada", "Про захист інформації в інформаційно-комунікаційних системах", "захист інформації", "current", "manual"),
    ("2469-19", "rada", "Про національну безпеку України",             "національну безпеку",          "current", "manual"),
    ("55-2018-п", "rada", "Типова інструкція з документування управлінської інформації (діловодство)", "документування", "current", "manual"),

    # -- технічна нормативка (PDF) --
    ("nd-1.1-003-99",  "pdf", "НД ТЗІ 1.1-003-99 — Термінологія в галузі захисту інформації",       "https://tzi.com.ua/downloads/1.1-003-99.pdf",  "current", "manual"),
    ("nd-2.5-004-99",  "pdf", "НД ТЗІ 2.5-004-99 — Критерії оцінки захищеності інформації",         "https://tzi.com.ua/downloads/2.5-004-99.pdf",  "current", "manual"),
    ("nd-2.5-010-03",  "pdf", "НД ТЗІ 2.5-010-03 — Вимоги до захисту інформації WEB-сторінки",      "https://tzi.com.ua/downloads/2.5-010-03.pdf",  "current", "manual"),
    ("nd-1.4-001-2000", "pdf", "НД ТЗІ 1.4-001-2000 — Типове положення про службу захисту інформації", "https://tzi.com.ua/downloads/1.4-001-2000.pdf", "current", "manual"),
    ("nd-3.6-001-2000", "pdf", "НД ТЗІ 3.6-001-2000 — Порядок створення засобів ТЗІ",               "https://tzi.com.ua/downloads/3.6-001-2000.pdf", "current", "manual"),
    ("nd-2.6-001-11",  "pdf", "НД ТЗІ 2.6-001-11 — Порядок державної експертизи КСЗІ",              "https://tzi.com.ua/downloads/2.6-001-11.pdf",  "current", "manual"),
    ("nd-2.7-013-2016", "pdf", "НД ТЗІ 2.7-013-2016 — Зіставлення з вимогами ISO/IEC 15408",        "https://usts.kiev.ua/wp-content/uploads/2020/07/nd-tzi-2.7-013-2016.pdf", "current", "manual"),
    ("szch",           "pdf", "Самовільне залишення військової частини — роз'яснення",              "https://hups.mil.gov.ua/assets/doc/public-information/samovilne-zalishennya-viyskovoyi-chastini.pdf", "current", "manual"),
]


def fetch(url: str, retries: int = 3) -> bytes:
    """UA обов'язковий, gzip обов'язковий, пауза між спробами обов'язкова."""
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "text/html,application/pdf,*/*",
        })
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    data = gzip.decompress(data)
                return data
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            last = exc
            time.sleep(3 * (attempt + 1))   # 403 тут майже завжди -- це ліміт
    raise RuntimeError(f"{url}: {type(last).__name__}: {last}")


def html_to_text(raw: bytes) -> str:
    h = raw.decode("utf-8", errors="replace")
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<[^>]+>", "\n", h)
    text = html_mod.unescape(h)
    lines = (" ".join(ln.split()) for ln in text.split("\n"))
    return "\n".join(ln for ln in lines if ln)


def pdf_to_text(path: str) -> str:
    from pypdf import PdfReader
    pages = []
    for page in PdfReader(path).pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")           # окрема сторінка може не читатись
    return "\n".join(pages)


def download(doc, out_dir):
    doc_id, kind, title, expect, validity, vsource = doc
    safe = re.sub(r"[^\w.-]", "_", doc_id)
    if kind == "rada":
        # /print, не /laws/show/<id> -- інакше отримаємо меню замість тексту.
        # safe='/' обов'язково: номер «80/94-вр» містить слеш як частину ШЛЯХУ,
        # і закодований у %2F він дає 404 (перевірено -- саме на цьому падало).
        url = f"{RADA}{urllib.request.quote(doc_id, safe='/')}/print"
        path = os.path.join(out_dir, f"{safe}.html")
        raw = fetch(url)
        with open(path, "wb") as f:
            f.write(raw)
        text = html_to_text(raw)
    else:
        url = expect                    # для pdf у полі expect лежить URL
        path = os.path.join(out_dir, f"{safe}.pdf")
        raw = fetch(url)
        with open(path, "wb") as f:
            f.write(raw)
        text = pdf_to_text(path)
    return url, path, text


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--only", nargs="*", help="лише ці id")
    args = ap.parse_args(argv)

    os.makedirs(OUT_DIR, exist_ok=True)
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

    todo = [d for d in DOCS if not args.only or d[0] in args.only]
    print(f"Документів до обробки: {len(todo)}\n")

    ok, failed, short = [], [], []
    for i, doc in enumerate(todo, 1):
        doc_id, kind, title, expect, validity, vsource = doc
        try:
            url, path, text = download(doc, OUT_DIR)
        except Exception as exc:
            print(f"[{i:2}/{len(todo)}] ✗ {doc_id}: {exc}")
            failed.append((doc_id, str(exc)))
            continue

        # Перевірка, що це той документ. Для rada -- за очікуваним фрагментом
        # назви; порожній expect означає "не перевіряємо" (зміни до наказу
        # не мають власної впізнаваної назви).
        if kind == "rada" and expect and expect.lower() not in text[:4000].lower():
            print(f"[{i:2}/{len(todo)}] ✗ {doc_id}: у тексті немає «{expect}» — це не той документ")
            failed.append((doc_id, "назва не збігається"))
            continue

        # Оболонка замість тексту -- окремий симптом, не плутати з коротким
        # документом: у меню rada близько 4 тис. символів.
        if kind == "rada" and len(text) < 6000:
            short.append((doc_id, len(text)))

        print(f"[{i:2}/{len(todo)}] ✓ {doc_id:12} {len(text):>8} симв.  {title[:52]}")
        ok.append((doc_id, kind, title, url, path, text, validity, vsource))
        time.sleep(2.5)                 # без паузи rada починає віддавати 403

    if args.download_only:
        print(f"\nЗавантажено {len(ok)}, помилок {len(failed)}. У базу не писали.")
        return 0 if not failed else 1

    print()
    inserted = 0
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM sources WHERE code = 'unit_export'")
            row = cur.fetchone()
            source_id = row[0] if row else None

            for doc_id, kind, title, url, path, text, validity, vsource in ok:
                checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
                rel = os.path.relpath(path, PROJECT_ROOT).replace(os.sep, "/")
                cur.execute(
                    """
                    INSERT INTO documents
                        (type_id, source_id, source_kind, status, raw_uri,
                         text_content, domain, checksum, validity,
                         validity_source, pipeline_meta)
                    VALUES (NULL, %s, 'electronic', 'extracted', %s, %s,
                            'normative', %s, %s, %s, %s)
                    ON CONFLICT (checksum) DO UPDATE
                        SET text_content = EXCLUDED.text_content,
                            validity = EXCLUDED.validity,
                            validity_source = EXCLUDED.validity_source,
                            pipeline_meta = EXCLUDED.pipeline_meta
                    RETURNING id
                    """,
                    (source_id, f"file:///{rel}", text, checksum, validity,
                     vsource, Jsonb({"corpus_id": doc_id, "title": title,
                                     "source_url": url, "chars": len(text)})),
                )
                inserted += 1
        conn.commit()

    # Зв'язок «скасований -> чинний»: заповнюємо ПІСЛЯ вставки, бо обидва
    # документи мусять уже існувати.
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE documents old
                   SET superseded_by_doc_id = new.id
                  FROM documents new
                 WHERE old.pipeline_meta ->> 'corpus_id' = 'z0611-14'
                   AND new.pipeline_meta ->> 'corpus_id' = 'z1407-22'
            """)
            linked = cur.rowcount
        conn.commit()

    print(f"Записано в documents: {inserted}")
    print(f"Зв'язок «скасований → чинний» проставлено: {linked}")
    if short:
        print("\n[увага] підозріло коротко (можлива оболонка замість тексту):")
        for doc_id, n in short:
            print(f"  {doc_id}: {n} символів")
    if failed:
        print(f"\n[помилки] {len(failed)}:")
        for doc_id, why in failed:
            print(f"  {doc_id}: {why}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
