"""Резолвер ідентифікаторів: номер документа -- це КЛЮЧ, а не запит.

Запуск:
    python db/scripts/resolve_identifier.py "НД ТЗІ 2.5-004-99"
    python db/scripts/resolve_identifier.py --self-test

## Чому це не пошук

Запит `НД ТЗІ 2.5-004-99` через FTS безнадійний: Postgres токенізує номер як
`'нд' 'тзі' '2.5' '-004' '-99'`, і AND-запит по цьому дає 85 фрагментів -- нуль
розрізнювальної здатності. Гірше: текст цього номера є у 6 документах, бо
стандарти цитують один одного, а Є цим номером лише ОДИН.

Стрес-тест це підтвердив живцем: на питання «НД ТЗІ 2.5-004-99 що це за
документ» обидві відповіді прийшли з документів, які його цитують (200 і 202), а
цитати були буквально рядками посилання. Жодне ранжування такого не розрізнить
-- потрібен атрибут документа, тобто `documents.identifier_key`.

## Три речі, які резолвер мусить робити правильно

**1. Складати конфузабли.** `НД ТЗI 2.5-004-99` із латинською `I` -- те саме
питання, і людина різниці не бачить. Нормалізація та сама, що будувала
`identifier_key`, інакше збігу не буде за визначенням.

**2. Не залежати від префікса.** У базі ключі лежать як `№2011-хіі`, а людина
пише «2011-XII» або «закон 2011-XII». Тому перед порівнянням з обох боків
знімається `№` і слово-вид («наказ», «закон», «указ»).

**3. Складатися з пошуком, а не конкурувати.** На «що каже НД ТЗІ 2.5-004-99
про паролі» резолвер дає документ, а пошук іде ВСЕРЕДИНІ нього. Якщо запит --
лише номер, шукати нічого: віддаємо картку документа.

## Чого резолвер НЕ робить

Не вгадує. Номер, якого в корпусі немає, -- це «такого документа немає», а не
«знайшлось щось схоже». Це різні відповіді: перша каже людині завантажити
документ, друга веде її не туди.
"""
import argparse
import os
import re
import sys

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "db", "scripts"))

import extract_document_identity as E  # noqa: E402

# Шаблони, за якими в запиті пізнається номер. Свідомо ширші за формати в
# базі: краще знайти кандидата й не підтвердити його, ніж не побачити зовсім.
PATTERNS = [
    # НД ТЗІ -- із префіксом і без («що таке 2.5-004-99»)
    re.compile(r"(?:НД\s*ТЗ[ІIi]\s*)?(\d\.\d\s*[-–]\s*\d{3}\s*[-–]\s*\d{2,4})", re.I),
    # старий законодавчий: 80/94-ВР
    re.compile(r"(\d{1,4}\s*/\s*\d{2,4}\s*[-–]\s*ВР)", re.I),
    # закон/кодекс: 550-XIV, і з латиницею, і з кирилицею
    re.compile(r"№?\s*(\d{2,5}\s*[-–]\s*[IVXLCivxlcІХіх]{1,6})(?![\w-])"),
    # указ: 1153/2008
    re.compile(r"№?\s*(\d{1,5}\s*/\s*\d{4})(?![\d-])"),
    # наказ: «наказ 402», «наказ № 402»
    re.compile(r"наказ\w*\s*№?\s*(\d{1,4})(?!\d)", re.I),
]

def match_key(s):
    """Ключ порівняння -- СТРУКТУРНИЙ, а не рядковий.

    Перша версія знімала префікси («№», «наказ») з рядка й порівнювала рядки.
    Не спрацювало на 7 із 13 випадків: у базі ключ НД ТЗІ лежить як
    `ндтзі2.5-004-99`, а запит дає `2.5-004-99`; ключ наказу --
    `наказ№402від14.08.2008`, а людина пише «наказ 402». Кожен такий випадок
    вимагав би ще одного правила зняття префікса.

    Структурне порівняння знімає весь клас разом: `E.canonical` розбирає обидві
    сторони на (тип, номер) і про префікси більше не питає.
    """
    c = E.canonical(s)
    return c if c else None


def find_candidates(query):
    """Усі рядки в запиті, що виглядають як номер документа."""
    out, seen = [], set()
    for pat in PATTERNS:
        for m in pat.finditer(query):
            raw = " ".join(m.group(1).split())
            # У ключ іде ВЕСЬ збіг, не лише номер: для «наказ 402» без слова
            # «наказ» рядок «402» не є ідентифікатором нічого, і канонізація
            # його не розбирає.
            k = match_key(" ".join(m.group(0).split()))
            if k and k not in seen:
                seen.add(k)
                out.append((raw, k))
    return out


def load_index(cur):
    """ключ -> [(doc_id, ідентифікатор, назва, чинність)]. 41 рядок, не індекс."""
    cur.execute("""
        SELECT d.id, d.identifier_key, d.doc_identifier, d.doc_title, d.validity,
               coalesce(g.canonical_id, d.id) AS canon
          FROM documents d
     LEFT JOIN document_groups g ON g.document_id = d.id
         WHERE d.identifier_key IS NOT NULL
    """)
    idx = {}
    for doc_id, key, ident, title, validity, canon in cur.fetchall():
        idx.setdefault(match_key(key), []).append(
            dict(id=doc_id, canon=canon, identifier=ident,
                 title=title, validity=validity))
    return idx


def strip_identifiers(query, candidates):
    """Запит без номерів -- щоб побачити, чи лишилось про що питати."""
    rest = query
    for raw, _k in candidates:
        rest = re.sub(re.escape(raw), " ", rest, flags=re.I)
    rest = re.sub(r"(?i)\bнд\s*тз[іi]\b|№|\bнаказ\w*\b|\bзакон\w*\b|\bуказ\w*\b",
                  " ", rest)
    return " ".join(rest.split())


def resolve(cur, query):
    """-> dict(status, documents, rest, candidates).

    status:
      none      -- номера в запиті немає, працює звичайний пошук;
      resolved  -- номер знайдено в корпусі;
      absent    -- номер є, але такого документа в корпусі НЕМА. Це окрема
                   відповідь, не «нічого не знайшлось»: людині треба сказати,
                   що документ не завантажений.
    """
    cands = find_candidates(query)
    if not cands:
        return dict(status="none", documents=[], rest=query, candidates=[])
    idx = load_index(cur)
    found, missing = [], []
    for raw, key in cands:
        hits = idx.get(key)
        if hits:
            # У групі дублікатів лишаємо канонічний, решту прибираємо.
            canon_ids = {h["canon"] for h in hits}
            for h in hits:
                if h["id"] in canon_ids and not any(
                        f["id"] == h["id"] for f in found):
                    found.append(dict(h, asked=raw))
        else:
            missing.append(raw)
    rest = strip_identifiers(query, cands)
    if found:
        return dict(status="resolved", documents=found, rest=rest,
                    candidates=cands, missing=missing)
    return dict(status="absent", documents=[], rest=rest,
                candidates=cands, missing=missing)


SELF_TEST = [
    ("НД ТЗІ 2.5-004-99", "resolved"),
    ("НД ТЗI 2.5-004-99", "resolved"),          # латинська I -- гомоглиф
    ("нд тзі 2.5-004-99 що це за документ", "resolved"),
    ("що каже НД ТЗІ 2.5-004-99 про паролі", "resolved"),
    ("2.5-004-99", "resolved"),                  # без префікса
    ("№ 550-XIV", "resolved"),
    ("закон 2011-XII про відпустки", "resolved"),
    ("наказ 402", "resolved"),
    ("наказ № 402 від 14.08.2008", "resolved"),
    ("НД ТЗІ 9.9-999-99", "absent"),             # номера немає в корпусі
    ("№ 9999-XXV", "absent"),
    ("скільки днів відпустки", "none"),          # номера в запиті немає
    ("хто веде облік особового складу", "none"),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query", nargs="*")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        if args.self_test:
            ok = 0
            print(f"{'запит':<44} {'очікувано':<10} {'вийшло':<10} документи")
            for q, want in SELF_TEST:
                r = resolve(cur, q)
                got = r["status"]
                docs = ", ".join(f"{d['id']}:{d['identifier']}"
                                 for d in r["documents"]) or "—"
                mark = "OK " if got == want else "ХИБ"
                ok += got == want
                print(f"{mark} {q[:42]:<42} {want:<10} {got:<10} {docs[:44]}")
                if r["status"] == "resolved" and r["rest"]:
                    print(f"      лишок запиту для пошуку всередині: {r['rest']!r}")
            print(f"\n{ok} з {len(SELF_TEST)}")
            return 0 if ok == len(SELF_TEST) else 1

        q = " ".join(args.query)
        r = resolve(cur, q)
        print(f"запит: {q!r}\nстатус: {r['status']}")
        for d in r["documents"]:
            print(f"  documents.id={d['id']}  {d['identifier']}  "
                  f"[{d['validity']}]  {d['title']}")
        if r.get("missing"):
            print(f"  у корпусі НЕМА: {r['missing']}")
        if r["rest"] and r["status"] == "resolved":
            print(f"  лишок для пошуку всередині документа: {r['rest']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
