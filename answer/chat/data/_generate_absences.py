#!/usr/bin/env python3
"""Генератор data/absences.csv для тестового стенду чата (капстоун №21).

Джерела значень:
  - реєстр:      experiments/2026-08-07_synthetic-documents-generator/data/unit_roster.csv
  - словники:    experiments/2026-08-07_synthetic-documents-generator/налаштування.yaml
Нічого не вигадуємо — ПІБ, service_id, підрозділи беремо з реєстру,
reason/place — зі списків yaml.

Запуск: python3 _generate_absences.py
"""

import csv
import re
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
GEN = HERE.parents[1] / "2026-08-07_synthetic-documents-generator"
ROSTER = GEN / "data" / "unit_roster.csv"
SETTINGS = GEN / "налаштування.yaml"
OUT = HERE / "absences.csv"

COLUMNS = [
    "doc_number", "doc_date", "doc_type", "service_id", "person_name_raw",
    "date_from", "date_to", "reason", "place", "status", "superseded_by",
    "source_file",
]


def read_roster():
    with ROSTER.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_list(key):
    """Тягне простий список рядків із yaml без зовнішніх бібліотек."""
    lines = SETTINGS.read_text(encoding="utf-8").splitlines()
    out, inside = [], False
    for line in lines:
        if re.match(rf"^{re.escape(key)}:\s*$", line):
            inside = True
            continue
        if inside:
            m = re.match(r"^\s+-\s+(.*\S)\s*$", line)
            if m:
                out.append(m.group(1))
            elif line.strip():
                break
    return out


def d(s):
    return date.fromisoformat(s)


def iso(x):
    return x.isoformat()


def doc_date_for(date_from, offset_days):
    """doc_date за 3–7 днів до date_from, але не раніше 2026-05-01."""
    value = d(date_from) - timedelta(days=offset_days)
    return iso(max(value, date(2026, 5, 1)))


def main():
    roster = read_roster()
    leave_types = read_list("leave_types")
    cities = read_list("cities")
    trip_purposes = read_list("trip_purposes")

    # Люди беруться з реєстру з рівним кроком — щоб потрапили різні підрозділи.
    # 25 людей: 10 відпусток + 10 відряджень + 2 зіпсовані + 3 пари.
    picks = [roster[i] for i in range(3, 3 + 25 * 12, 12)]
    leave_people = picks[0:10]
    trip_people = picks[10:20]
    defect_people = picks[20:22]   # для №303 і №3О4
    pair_people = picks[22:25]

    places = cities + ["м. Кропивницький"]
    rows = []

    def row(**kw):
        r = {c: "" for c in COLUMNS}
        r.update(kw)
        rows.append(r)

    # --- 10 коректних відпусток, №101…№110 -------------------------------
    # (date_from, date_to) — травень 2026, тривалість 7–20 днів.
    leave_dates = [
        ("2026-05-10", "2026-05-24"),  # фіксовано, №101
        ("2026-05-04", "2026-05-18"),  # фіксовано, №102
        ("2026-05-06", "2026-05-13"),
        ("2026-05-08", "2026-05-21"),
        ("2026-05-11", "2026-05-30"),
        ("2026-05-05", "2026-05-16"),
        ("2026-05-12", "2026-05-19"),
        ("2026-05-07", "2026-05-26"),
        ("2026-05-09", "2026-05-23"),
        ("2026-05-13", "2026-05-27"),
    ]
    for i, (person, (df, dt)) in enumerate(zip(leave_people, leave_dates)):
        row(
            doc_number=f"№{101 + i}",
            doc_date=doc_date_for(df, 3 + i % 5),
            doc_type="відпустка",
            service_id=person["service_id"],
            person_name_raw=person["full_name"],
            date_from=df,
            date_to=dt,
            reason=leave_types[i % len(leave_types)],
            place=places[i % len(places)],
            status="чинний",
            source_file=f"leave_{i + 1:03d}.pdf",
        )

    # --- 10 коректних відряджень, №201…№210 ------------------------------
    trip_dates = [
        ("2026-05-12", "2026-05-15"),  # фіксовано, №201
        ("2026-05-04", "2026-05-08"),
        ("2026-05-06", "2026-05-07"),
        ("2026-05-11", "2026-05-20"),
        ("2026-05-13", "2026-05-18"),
        ("2026-05-05", "2026-05-11"),
        ("2026-05-18", "2026-05-21"),
        ("2026-05-07", "2026-05-14"),
        ("2026-05-20", "2026-05-26"),
        ("2026-05-25", "2026-05-29"),
    ]
    for i, (person, (df, dt)) in enumerate(zip(trip_people, trip_dates)):
        row(
            doc_number=f"№{201 + i}",
            doc_date=doc_date_for(df, 3 + (i + 2) % 5),
            doc_type="відрядження",
            service_id=person["service_id"],
            person_name_raw=person["full_name"],
            date_from=df,
            date_to=dt,
            reason=trip_purposes[i % len(trip_purposes)],
            place=cities[(i + 3) % len(cities)],
            status="чинний",
            source_file=f"trip_{i + 1:03d}.pdf",
        )

    # --- 4 зіпсовані документи -------------------------------------------
    # №301 — порожні service_id, person_name_raw, date_from, date_to
    row(
        doc_number="№301",
        doc_date="2026-05-06",
        doc_type="відпустка",
        reason=leave_types[1],
        place=places[2],
        status="чинний",
        source_file="defect_001.pdf",
    )
    # №302 — людини немає в реєстрі: service_id порожній, ПІБ вигаданий
    row(
        doc_number="№302",
        doc_date="2026-05-09",
        doc_type="відпустка",
        person_name_raw="Заболотний Ярослав Тимофійович",
        date_from="2026-05-14",
        date_to="2026-05-24",
        reason=leave_types[0],
        place=places[5],
        status="чинний",
        source_file="defect_002.pdf",
    )
    # №303 — date_to раніше date_from
    p = defect_people[0]
    row(
        doc_number="№303",
        doc_date="2026-05-15",
        doc_type="відрядження",
        service_id=p["service_id"],
        person_name_raw=p["full_name"],
        date_from="2026-05-20",
        date_to="2026-05-11",
        reason=trip_purposes[4],
        place=cities[7],
        status="чинний",
        source_file="defect_003.pdf",
    )
    # №3О4 — шум OCR: кирилична «О» замість нуля в самому номері
    p = defect_people[1]
    row(
        doc_number="№3О4",
        doc_date="2026-05-04",
        doc_type="відпустка",
        service_id=p["service_id"],
        person_name_raw=p["full_name"],
        date_from="2026-05-08",
        date_to="2026-05-22",
        reason=leave_types[2],
        place=places[9],
        status="чинний",
        source_file="defect_004.pdf",
    )

    # --- 3 пари «документ скасовує документ» ------------------------------
    # 1. Перервана відпустка: №401 → №402
    p = pair_people[0]
    row(
        doc_number="№401", doc_date="2026-05-01", doc_type="відпустка",
        service_id=p["service_id"], person_name_raw=p["full_name"],
        date_from="2026-05-05", date_to="2026-05-25",
        reason=leave_types[0], place=places[1],
        status="скасований", superseded_by="№402",
        source_file="pair_001a.pdf",
    )
    row(
        doc_number="№402", doc_date="2026-05-13", doc_type="відпустка",
        service_id=p["service_id"], person_name_raw=p["full_name"],
        date_from="2026-05-05", date_to="2026-05-14",
        reason=leave_types[0], place=places[1],
        status="чинний",
        source_file="pair_001b.pdf",
    )
    # 2. Переоформлене відрядження: №403 → №404
    p = pair_people[1]
    row(
        doc_number="№403", doc_date="2026-05-13", doc_type="відрядження",
        service_id=p["service_id"], person_name_raw=p["full_name"],
        date_from="2026-05-18", date_to="2026-05-22",
        reason=trip_purposes[1], place=cities[4],
        status="скасований", superseded_by="№404",
        source_file="pair_002a.pdf",
    )
    row(
        doc_number="№404", doc_date="2026-05-20", doc_type="відрядження",
        service_id=p["service_id"], person_name_raw=p["full_name"],
        date_from="2026-05-25", date_to="2026-05-29",
        reason=trip_purposes[1], place=cities[4],
        status="чинний",
        source_file="pair_002b.pdf",
    )
    # 3. Два документи з одним номером №145
    p = pair_people[2]
    row(
        doc_number="№145", doc_date="2026-05-03", doc_type="відпустка",
        service_id=p["service_id"], person_name_raw=p["full_name"],
        date_from="2026-05-06", date_to="2026-05-20",
        reason=leave_types[3], place=places[6],
        status="скасований", superseded_by="№145",
        source_file="pair_003a.pdf",
    )
    row(
        doc_number="№145", doc_date="2026-05-12", doc_type="відпустка",
        service_id=p["service_id"], person_name_raw=p["full_name"],
        date_from="2026-05-13", date_to="2026-05-27",
        reason=leave_types[3], place=places[6],
        status="чинний",
        source_file="pair_003b.pdf",
    )

    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    check(rows, roster, leave_types, cities, trip_purposes, places)


def check(rows, roster, leave_types, cities, trip_purposes, places):
    by_id = {r["service_id"]: r["full_name"] for r in roster}
    names = {r["full_name"] for r in roster}
    problems = []

    if len(rows) != 30:
        problems.append(f"рядків даних {len(rows)}, треба 30")

    for r in rows:
        n = r["doc_number"]
        if n in ("№301", "№302"):
            if r["service_id"]:
                problems.append(f"{n}: service_id має бути порожнім")
            continue
        if r["service_id"] not in by_id:
            problems.append(f"{n}: service_id {r['service_id']!r} немає в реєстрі")
        elif by_id[r["service_id"]] != r["person_name_raw"]:
            problems.append(f"{n}: ПІБ не збігається з реєстром")

    if rows[21]["person_name_raw"] in names:
        problems.append("№302: ПІБ випадково є в реєстрі")

    for r in rows:
        if r["reason"] and r["reason"] not in leave_types + trip_purposes:
            problems.append(f"{r['doc_number']}: reason не зі словника")
        if r["place"] and r["place"] not in places:
            problems.append(f"{r['doc_number']}: place не зі словника")

    superseded = [r for r in rows if r["superseded_by"]]
    if len(superseded) != 3:
        problems.append(f"superseded_by заповнений у {len(superseded)} рядках, треба 3")
    if any(r["status"] != "скасований" for r in superseded):
        problems.append("superseded_by стоїть у нескасованому документі")

    # кожна людина реєстру — максимум в одному документі, крім пар
    seen = {}
    for r in rows:
        if not r["service_id"]:
            continue
        seen.setdefault(r["service_id"], []).append(r["doc_number"])
    for sid, docs in seen.items():
        limit = 2 if any(x in ("№401", "№402", "№403", "№404", "№145") for x in docs) else 1
        if len(docs) > limit:
            problems.append(f"{sid}: {len(docs)} документів — {docs}")

    print(f"Записано: {OUT}")
    print(f"Рядків даних: {len(rows)} (+ заголовок)")
    subs = {r["subdivision"] for r in roster
            if r["service_id"] in seen}
    print(f"Підрозділів задіяно: {len(subs)}")
    if problems:
        print("ПРОБЛЕМИ:")
        for p in problems:
            print("  -", p)
    else:
        print("Перевірки пройдено: без зауважень")


if __name__ == "__main__":
    main()
