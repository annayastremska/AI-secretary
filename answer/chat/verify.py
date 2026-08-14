# Перевірка тестового стенду. Написана ДО коду — фіксує, що код МАВ робити.
# Запуск: python3 verify.py
# Проходить повністю → стенд готовий. Ні → не готовий, як би добре не виглядав.

import glob
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "stand.sqlite")

failures = []
checks = 0


def check(name, condition, detail=""):
    global checks
    checks += 1
    if condition:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}" + (f" — {detail}" if detail else ""))
        failures.append(name)


def is_iso_or_empty(value):
    if value == "":
        return True
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)))


# ── 1. seed.py відпрацьовує з нуля ──────────────────────────────────────────

print("\n[1] seed.py з нуля")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
seed = subprocess.run(
    [sys.executable, os.path.join(HERE, "seed.py")],
    capture_output=True, text=True, cwd=HERE,
)
check("seed.py завершився без помилки", seed.returncode == 0, seed.stderr[-500:])
check("stand.sqlite створено", os.path.exists(DB_PATH))
if failures:
    print("\nseed.py не відпрацював — далі перевіряти нема чого.")
    sys.exit(1)

import sqlite3  # noqa: E402

conn = sqlite3.connect(DB_PATH)
tables = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")}
for t in ("people", "absences", "reference_docs"):
    check(f"таблиця {t} існує", t in tables)

n_people = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
n_abs = conn.execute("SELECT COUNT(*) FROM absences").fetchone()[0]
n_ref = conn.execute("SELECT COUNT(*) FROM reference_docs").fetchone()[0]
# Кількість не фіксуємо (дані поповнюються) — база має збігатися з CSV.
import csv as _csv


def _csv_rows(name):
    with open(os.path.join(HERE, "data", name), newline="", encoding="utf-8") as f:
        return sum(1 for _ in _csv.DictReader(f))


check("people: у базі стільки ж, скільки в people.csv",
      n_people == _csv_rows("people.csv") and n_people > 0,
      f"база {n_people}, csv {_csv_rows('people.csv')}")
check("absences: у базі стільки ж, скільки в absences.csv",
      n_abs == _csv_rows("absences.csv") and n_abs > 0,
      f"база {n_abs}, csv {_csv_rows('absences.csv')}")
n_ref_files = len(glob.glob(os.path.join(HERE, "data", "reference", "*.md")))
n_ref_docs = conn.execute(
    "SELECT COUNT(DISTINCT doc_title) FROM reference_docs").fetchone()[0]
check("reference_docs: документів у базі стільки ж, скільки файлів у data/reference/",
      n_ref_docs == n_ref_files and n_ref_docs > 0,
      f"база {n_ref_docs}, файлів {n_ref_files}")
check("reference_docs: у кожного документа є розділи",
      n_ref >= n_ref_docs, f"розділів {n_ref}")
conn.close()

# ── 2. Сім функцій db.py: викликаються, повертають список ───────────────────

print("\n[2] сім функцій зі docs/contracts/2026-08-14_chat-db-interface.md")
sys.path.insert(0, HERE)
import db  # noqa: E402

PEOPLE_KEYS = {"service_id", "full_name", "rank", "position_title",
               "subdivision", "phone"}
ABSENCE_KEYS = {"doc_number", "doc_date", "doc_type", "service_id",
                "person_name_raw", "date_from", "date_to", "reason",
                "place", "status", "superseded_by", "source_file"}
COUNT_KEYS = {"subdivision", "absent", "total"}
REF_KEYS = {"doc_title", "section_number", "section_title", "text",
            "source_note", "score"}

calls = [
    ("find_people", lambda: db.find_people(subdivision=None, name=None), PEOPLE_KEYS),
    ("absences_on_date", lambda: db.absences_on_date("2026-05-15"), ABSENCE_KEYS),
    ("returning_on_date", lambda: db.returning_on_date("2026-05-24"), ABSENCE_KEYS),
    ("absences_for_person", lambda: db.absences_for_person("UNIT-0001", only_active=True), ABSENCE_KEYS),
    ("document_by_number", lambda: db.document_by_number("№101"), ABSENCE_KEYS),
    ("count_absent_by_subdivision", lambda: db.count_absent_by_subdivision("2026-05-15"), COUNT_KEYS),
    ("search_reference", lambda: db.search_reference("відпустка", limit=3), REF_KEYS),
]

results = {}
for name, fn, keys in calls:
    try:
        out = fn()
    except Exception as e:  # noqa: BLE001
        check(f"{name} викликається", False, repr(e))
        continue
    results[name] = out
    check(f"{name} повертає список", isinstance(out, list), type(out).__name__)
    if isinstance(out, list) and out:
        got = set(out[0].keys())
        check(f"{name}: ключі рівно як у docs/contracts/2026-08-14_chat-db-interface.md", got == keys,
              f"зайві {got - keys or '—'}, бракує {keys - got or '—'}")

# ── 3. Правила стику ────────────────────────────────────────────────────────

print("\n[3] правила стику")

# Нічого не знайшли → [], не None, не виняток
for name, fn in [
    ("find_people(вигаданий підрозділ)", lambda: db.find_people(subdivision="99-та неіснуюча")),
    ("absences_on_date(2030-01-01)", lambda: db.absences_on_date("2030-01-01")),
    ("returning_on_date(2030-01-01)", lambda: db.returning_on_date("2030-01-01")),
    ("absences_for_person(немає такого)", lambda: db.absences_for_person("Нікогонемаєвич")),
    ("document_by_number(№999)", lambda: db.document_by_number("№999")),
    ("search_reference(トマト)", lambda: db.search_reference("トマト")),
]:
    try:
        out = fn()
        check(f"порожньо → []: {name}", out == [], f"повернуло {out!r:.80}")
    except Exception as e:  # noqa: BLE001
        check(f"порожньо → []: {name}", False, f"виняток {e!r}")

# Дати на виході — рядки YYYY-MM-DD (або порожній рядок)
sample = db.absences_on_date("2026-05-15") + db.document_by_number("№101")
dates_ok = all(
    is_iso_or_empty(r[k]) for r in sample for k in ("doc_date", "date_from", "date_to")
)
check("дати на виході — рядки YYYY-MM-DD або порожні", bool(sample) and dates_ok)

# Дублікат номера → ДВА записи, обидва віддаються (функція не вибирає чинний)
dup = db.document_by_number("№145")
check("document_by_number('№145') → 2 записи", len(dup) == 2, f"є {len(dup)}")
if len(dup) == 2:
    statuses = sorted(d["status"] for d in dup)
    check("№145: віддано і чинний, і скасований",
          statuses == ["скасований", "чинний"], str(statuses))

# Зіпсований документ: порожні поля лишаються порожніми
broken = db.document_by_number("№301")
check("№301 (порожні поля) знаходиться", len(broken) == 1, f"є {len(broken)}")
if broken:
    b = broken[0]
    check("№301: person_name_raw порожній, не підставлений", b["person_name_raw"] == "")
    check("№301: date_from/date_to порожні, не підставлені",
          b["date_from"] == "" and b["date_to"] == "")

# Переплутані дати лишаються як є
swapped = db.document_by_number("№303")
check("№303 (переплутані дати) знаходиться", len(swapped) == 1)
if swapped:
    check("№303: date_to < date_from збережено як є",
          swapped[0]["date_to"] < swapped[0]["date_from"])

# Шум у номері не «виправлено»
noisy = db.document_by_number("№3О4")  # кирилична О
check("№3О4 (шум OCR) знаходиться за точним номером", len(noisy) == 1)
check("№304 (латинський нуль) НЕ знаходиться — номер не виправлено мовчки",
      db.document_by_number("№304") == [])

# absences_on_date не показує скасовані
# Пара 1: №401 (05-05..05-25, скасований) і №402 (05-05..05-14, чинний), та сама людина
on20 = db.absences_on_date("2026-05-20")
check("absences_on_date не показує status=скасований",
      all(r["status"] == "чинний" for r in on20))
check("№401 (скасований, 05-05..05-25) не видно 2026-05-20",
      not any(r["doc_number"] == "№401" for r in on20))
on10 = db.absences_on_date("2026-05-10")
pair1_docs = [r for r in on10 if r["doc_number"] in ("№401", "№402")]
check("людина з пари 1 на 2026-05-10 видна рівно раз — за №402",
      len(pair1_docs) == 1 and pair1_docs[0]["doc_number"] == "№402",
      str([r["doc_number"] for r in pair1_docs]))

# returning_on_date: №101 повертається 2026-05-24
ret = db.returning_on_date("2026-05-24")
check("returning_on_date('2026-05-24') містить №101",
      any(r["doc_number"] == "№101" for r in ret))

# absences_for_person: only_active перемикає видимість скасованих
p401 = db.document_by_number("№401")
if p401:
    person = p401[0]["service_id"]
    active = db.absences_for_person(person, only_active=True)
    everything = db.absences_for_person(person, only_active=False)
    check("absences_for_person(only_active=True) — лише чинний",
          all(r["status"] == "чинний" for r in active) and len(active) == 1,
          str([(r["doc_number"], r["status"]) for r in active]))
    check("absences_for_person(only_active=False) — обидва документи пари",
          len(everything) == 2, f"є {len(everything)}")
else:
    check("№401 існує (потрібен для перевірки only_active)", False)

# find_people: частковий збіг імені
found = db.find_people(name="Гавриш")
check("find_people(name='Гавриш') знаходить UNIT-0001",
      any(p["service_id"] == "UNIT-0001" for p in found))

# count_absent_by_subdivision: всі підрозділи, сума total = 301
counts = db.count_absent_by_subdivision("2026-05-15")
check("count_absent: сума total по підрозділах = всім людям у реєстрі",
      sum(r["total"] for r in counts) == n_people,
      f"сума {sum(r['total'] for r in counts) if counts else 0}")
check("count_absent: absent ніде не більший за total",
      all(0 <= r["absent"] <= r["total"] for r in counts))
abs15 = db.absences_on_date("2026-05-15")
known15 = {r["service_id"] for r in abs15 if r["service_id"]}
check("count_absent: сума absent = людям з підтвердженим service_id на цю дату",
      sum(r["absent"] for r in counts) == len(known15),
      f"absent {sum(r['absent'] for r in counts) if counts else 0} vs {len(known15)}")

# Фільтри subdivision/doc_type — звужують, а не ігноруються (діра M3 аудиту)
all15 = db.absences_on_date("2026-05-15")
sub15 = db.absences_on_date("2026-05-15", subdivision="2-га механізована рота")
check("absences_on_date(subdivision=…) звужує вибірку",
      0 < len(sub15) < len(all15), f"{len(sub15)} з {len(all15)}")
sub_people = {p["service_id"] for p in db.find_people(subdivision="2-га механізована рота")}
check("absences_on_date(subdivision=…): всі з service_id — люди цього підрозділу",
      all(r["service_id"] in sub_people for r in sub15 if r["service_id"]))
trips15 = db.absences_on_date("2026-05-15", doc_type="відрядження")
check("absences_on_date(doc_type='відрядження') — лише відрядження",
      0 < len(trips15) < len(all15) and all(r["doc_type"] == "відрядження" for r in trips15))

n101 = db.document_by_number("№101")[0]
p101 = db.find_people(name=n101["person_name_raw"])
if p101:
    own_sub = p101[0]["subdivision"]
    other_sub = next(s for s in {p["subdivision"] for p in db.find_people()} if s != own_sub)
    check("returning_on_date(subdivision=свій) містить №101",
          any(r["doc_number"] == "№101" for r in db.returning_on_date("2026-05-24", subdivision=own_sub)))
    check("returning_on_date(subdivision=чужий) НЕ містить №101",
          not any(r["doc_number"] == "№101" for r in db.returning_on_date("2026-05-24", subdivision=other_sub)))
else:
    check("людина з №101 знаходиться в реєстрі", False)

# absences_for_person за прізвищем, не тільки за service_id (діра M5 аудиту)
check("absences_for_person('Гаврилів') знаходить №101",
      any(r["doc_number"] == "№101" for r in db.absences_for_person("Гаврилів")))

# count_absent рахує ЛЮДЕЙ, не документи (діра M4): у людини з №201 і №501
# на 2026-05-13 два чинні документи, що перекриваються — вона мусить
# порахуватись один раз. Ловиться перевіркою «сума absent = distinct людям».
docs13 = db.absences_on_date("2026-05-13")
overlap_ids = [r["service_id"] for r in docs13]
check("дані містять людину з двома чинними документами на 2026-05-13",
      len(overlap_ids) != len(set(overlap_ids)))
counts13 = db.count_absent_by_subdivision("2026-05-13")
check("count_absent на 2026-05-13 рахує людей, не документи (DISTINCT)",
      sum(r["absent"] for r in counts13) == len({i for i in overlap_ids if i}),
      f"absent {sum(r['absent'] for r in counts13)} vs людей {len({i for i in overlap_ids if i})}")

# search_reference шукає і по назвах документа, не лише по тексту (діра M6)
by_title = db.search_reference("паспорт", limit=5)
check("search_reference('паспорт') знаходить розділи техпаспорта за назвою",
      any("паспорт" in r["doc_title"].lower() for r in by_title))
sr2 = db.search_reference("рапорт відпустка", limit=5)
check("search_reference: score рахує збіги (двослівний запит → score 2 зверху)",
      bool(sr2) and sr2[0]["score"] >= 2, str([r["score"] for r in sr2]))
check("search_reference: відсортовано за score, спадання",
      all(sr2[i]["score"] >= sr2[i + 1]["score"] for i in range(len(sr2) - 1)))

# search_reference: score є, limit працює
sr = db.search_reference("рапорт відпустка", limit=2)
check("search_reference: limit=2 → не більше 2", len(sr) <= 2)
check("search_reference('рапорт відпустка') щось знаходить", len(sr) >= 1)
if sr:
    check("search_reference: кожен рядок несе doc_title+section_number+source_note",
          all(r["doc_title"] and r["section_number"] and r["source_note"] for r in sr))

# ── Підсумок ────────────────────────────────────────────────────────────────

print(f"\nПеревірок: {checks}, провалено: {len(failures)}")
if failures:
    print("Провалені:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("УСЕ ПРОЙШЛО")
