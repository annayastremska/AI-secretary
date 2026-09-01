"""Витягує пари «повна форма -- скорочення» з КОРПУСУ, а не зі списку від руки.

Запуск:
    python db/scripts/build_synonyms.py            # показати
    python db/scripts/build_synonyms.py --apply

## Навіщо

Заміряно на живому: запит «вайфай» дає нуль лексичних кандидатів, бо документ
каже «WIFI» і «бездротовий». Те саме буде з «НСД» проти «несанкціонований
доступ» -- і мій же список корпусу називав це однією з причин узяти НД ТЗІ,
бо там є довідник термінів.

## Чому з корпусу, а не списком від руки

Українські нормативні тексти САМІ оголошують свої скорочення: `(далі - ВМС
ЗСУ)`, `Автоматизована система; АС`, `несанкціонований доступ (НСД)`. Це той
самий принцип, що вже тримає решту роботи -- витягуємо задеклароване, не
домислюємо. Список від руки не масштабується: у підрозділі буде 200-400
документів, кожен зі своїми скороченнями.

## Фільтр якості: вкладеність літер

Без фільтра сирі шаблони дають сміття. Заміряно: `назва (АБР)` дала 103 пари,
серед них `країни -> ВВР` (обрізаний хвіст «Відомості Верховної Ради України
(ВВР)») і `ютерна система -> КС` (апостроф у «комп'ютерна» розірвав збіг).
Розширювати запит такими парами гірше, ніж не розширювати.

Правило: **кожна літера скорочення мусить траплятися в повній формі, у тому
самому порядку.** Це не строгі ініціали -- українські скорочення часто беруть
внутрішні літери (`НСД` = **н**е**с**анкціонований **д**оступ), тому перевірка
на перші літери слів відкидала б правильні пари. А вкладеність відкидає рівно
сміття: `ВВР` не вкладається в «країни», `КС` не вкладається в «ютерна
система».

## Чого тут немає

Сленгу. «Вайфай» проти «WIFI» корпус дати не може -- він не оголошує розмовних
форм. Це окрема, коротка й стабільна річ, і її місце -- або маленький ручний
список, або модель у момент запиту. Змішувати її з витягнутим із корпусу не
варто: у них різна надійність, і в таблиці стоїть `source`, щоб це було видно.
"""
import argparse
import os
import re
import sys
from collections import Counter

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "db", "scripts"))

import search_units_test as SU  # noqa: E402

SCHEMA = "andriy_test"

# Скорочення -- великими літерами, можливо кілька слів («ВМС ЗСУ»).
ABBR_OK = re.compile(r"^[А-ЯІЇЄҐA-Z][А-ЯІЇЄҐA-Z0-9\s.\-]{1,14}$")

PATTERNS = [
    # «Військово-Морські Сили ... (далі - ВМС ЗСУ)»
    ("далі", re.compile(
        r"([^.;:\n()]{6,90}?)\(\s*далі\s*[-–—]?\s*([^)]{2,30})\)", re.I)),
    # Формат довідника НД ТЗІ: «4.1.2 Автоматизована система; АС (automated…)»
    ("довідник", re.compile(
        r"^\s*\d+\.\d+(?:\.\d+)?\s+([А-ЯІЇЄҐа-яіїєґ][^;\n]{3,70});\s*"
        r"([А-ЯІЇЄҐA-Z]{2,10})\b", re.M)),
    # «несанкціонований доступ (НСД)»
    ("дужки", re.compile(
        r"([А-ЯІЇЄҐа-яіїєґ][А-ЯІЇЄҐа-яіїєґ'’\s\-]{6,70}?)\s*"
        r"\(\s*([А-ЯІЇЄҐ]{2,10})\s*\)")),
]

DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
CREATE TABLE IF NOT EXISTS {SCHEMA}.synonyms (
    abbr      text NOT NULL,
    full_form text NOT NULL,
    source    text NOT NULL,
    seen      integer NOT NULL DEFAULT 1,
    PRIMARY KEY (abbr, full_form)
);
CREATE INDEX IF NOT EXISTS synonyms_abbr_idx ON {SCHEMA}.synonyms (lower(abbr));
"""

LETTERS = re.compile(r"[^а-яіїєґa-z]")


def is_subsequence(abbr, full):
    """Чи літери скорочення трапляються в повній формі в тому самому порядку.

    Не строгі ініціали: `НСД` = несанкціонований доступ бере внутрішні літери,
    і перевірка на перші літери слів відкинула б правильну пару.
    """
    a = LETTERS.sub("", abbr.lower())
    f = LETTERS.sub("", full.lower())
    if not a or len(a) < 2:
        return False
    i = 0
    for ch in f:
        if i < len(a) and ch == a[i]:
            i += 1
    return i == len(a)


def trim_to_abbr(full, abbr):
    """Обрізає повну форму злІва до вікна, яке відповідає скороченню.

    Дві причини, обидві заміряні:

    * ліва частина захоплюється жадібно, і виходить
      `СВ ЗСУ <- військову форму одягу Сухопутних військ Збройних Сил України`
      -- «військову форму одягу» до скорочення не належить;
    * для двобуквенних скорочень перевірка вкладеності майже безкоштовна:
      `АС` вкладається в «б враховували особливості технолог», бо `а` і `с`
      є в будь-якій довгій українській фразі. Обмеження на кількість слів це
      прибирає -- скорочення з N літер не розшифровується вісьмома словами.

    Беремо найкоротше хвостове вікно, яке ще проходить вкладеність.
    """
    n = len(LETTERS.sub("", abbr.lower()))
    words = full.split()
    best = None
    for k in range(max(1, n - 1), n + 3):
        if k > len(words):
            break
        cand = " ".join(words[-k:])
        if is_subsequence(abbr, cand):
            best = cand
            break
    return best


def clean_full(s):
    s = " ".join(s.split()).strip(" ,;:-–—")
    # Хвіст після коми/тире -- як правило вже інша сутність.
    s = re.split(r"\s+[-–—]\s+", s)[0]
    return s.strip()


def extract(texts):
    found = Counter()
    src = {}
    for text in texts:
        for name, pat in PATTERNS:
            for m in pat.finditer(text):
                full, abbr = clean_full(m.group(1)), " ".join(m.group(2).split())
                if not ABBR_OK.match(abbr):
                    continue
                if len(full) < 6 or len(full) > 90:
                    continue
                full = trim_to_abbr(full, abbr)
                if not full or len(full) < 6:
                    continue
                key = (abbr, full)
                found[key] += 1
                src.setdefault(key, name)
    return found, src


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--min-seen", type=int, default=1)
    args = ap.parse_args(argv)

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""SELECT text_content FROM documents
                        WHERE domain='normative' AND text_content IS NOT NULL""")
        texts = [r[0] for r in cur.fetchall()]
        found, src = extract(texts)

        by_src = Counter(src[k] for k in found)
        print(f"пар після фільтра вкладеності: {len(found)}  {dict(by_src)}")
        print("\nнайчастіші:")
        for (abbr, full), n in found.most_common(18):
            print(f"  x{n:<3} {abbr:<12} <- {full[:64]}  [{src[(abbr, full)]}]")

        # Одне скорочення на кілька повних форм -- це нормально (різні
        # документи, різні контексти), але варто бачити.
        multi = Counter(a for a, _f in found)
        amb = {a: n for a, n in multi.items() if n > 2}
        if amb:
            print(f"\nскорочень із 3+ різними розшифровками: {len(amb)}")
            for a, n in sorted(amb.items(), key=lambda kv: -kv[1])[:6]:
                print(f"  {a} ({n}): " + "; ".join(
                    f[:34] for (x, f) in found if x == a)[:150])

        if args.apply:
            cur.execute(DDL)
            cur.execute(f"TRUNCATE {SCHEMA}.synonyms")
            for (abbr, full), n in found.items():
                if n < args.min_seen:
                    continue
                cur.execute(f"""INSERT INTO {SCHEMA}.synonyms
                                    (abbr, full_form, source, seen)
                                VALUES (%s,%s,%s,%s)
                                ON CONFLICT (abbr, full_form) DO NOTHING""",
                            (abbr, full, src[(abbr, full)], n))
            conn.commit()
            print(f"\nЗАПИСАНО в {SCHEMA}.synonyms")
        else:
            print("\nDRY-RUN: нічого не змінено")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
