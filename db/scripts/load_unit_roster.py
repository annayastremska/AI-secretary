"""Завантажує штатку (реєстр особового складу) в базу.

Запуск:
    python db/scripts/load_unit_roster.py            # лише показати
    python db/scripts/load_unit_roster.py --apply

## Навіщо

Без штатки в базі є лише люди, створені з документів -- і **ні в кого немає
`service_id`** (перевірено на сервері: 134 особи, 0 із service_id). Наслідки
видно одразу:

* чат до кожного прізвища дописує «не підтверджено реєстром», бо саме
  відсутність `service_id` це й означає;
* зведення по підрозділах порахувати нічим -- підрозділ береться зі штатки,
  а не з відпускного квитка;
* «непідтверджених записів: N» у підвалі відповіді -- це N = усі.

## Ідентичність: доповнюємо, а не дублюємо

Головний ризик -- завантажити 300 людей поверх 134 наявних і отримати кожного
двічі. Тому спершу шукаємо наявну особу за ПІБ і **доповнюємо** її
`service_id` та рештою полів; нову створюємо лише якщо збігу немає.

Збіг за ПІБ ненадійний у принципі (однофамільці), тому неоднозначні випадки
скрипт **не вирішує сам**, а показує списком. Тихо злити двох людей в одну
гірше, ніж лишити роботу людині.

## Звання, посада, підрозділ -- фактами, не колонками

У `people` цих колонок немає, і це навмисно: усе три змінюються в часі
(переведення, підвищення), а `people` тримає незмінне -- ПІБ, РНОКПП, дату
народження. Тому вони йдуть у `facts` з `validity_model='current_state'`:
«чинно, доки не з'явиться новіший факт того самого виміру».

Виміру `subdivision` у базі ще немає -- створюється тут, із `current_state`.
Саме він і був причиною, чому зведення по ротах порахувати нічим.

**Штатка не перезаписує документи.** Якщо в особи вимір уже заповнений з
документа, штатка його не торкається. Це не обережність, а порядок шарів:
наказ про підвищення новіший і авторитетніший за реєстр. Перевірено на даних --
без цього правила виходило гірше за просто «зайвий факт»:

* звання в `facts` лежить КОДОМ (`soldier`), у штатці -- назвою («рядовий»);
  посада в базі -- `'сапер, військова частина Ж3085'`, у штатці -- `'сапер'`.
  Тобто кожна особа виглядала як «зміна звання й посади», хоча не змінилось
  ніщо;
* `insert_fact` вважав це суперсесією і закривав старий факт датою штатки
  (зарахування, 2024-07-07), яка СТАРІША за `valid_from` старого факту
  (2026-08-24) -- і падав на `facts_check`.

Друге з цього -- справжній дефект `insert_fact`: він припускає, що новий факт
хронологічно новіший. Історичні дані його ламають. Тут це обійдено, але не
виправлено -- виправлення в завантажувачі, окремою роботою.

## Провенанс

Штатка сама по собі документ («Штатна книжка»), тому для неї створюється рядок
у `documents` із `domain='staffing'`, і факти посилаються на нього. Інакше
з'явились би факти без джерела, а це те, чого ми не дозволяємо ніде інде.
"""
import argparse
import csv
import hashlib
import os
import sys
from collections import defaultdict

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "airflow", "plugins"))
import ai_secretary_loader as L  # noqa: E402

ROSTER = os.path.join(PROJECT_ROOT, "db", "seeds", "unit_roster.csv")

# Колонки CSV, які прямо відповідають колонкам people.
PEOPLE_COLS = [
    "service_id", "last_name", "first_name", "patronymic", "rnokpp",
    "id_document_type", "id_document_series", "id_document_number",
    "birth_date", "birth_place", "gender", "education", "service_type",
    "contract_start_date", "contract_end_date", "conscription_period",
    "enrollment_date", "enrollment_order_date", "enrollment_order_number",
    "arrived_from", "service_entry_date", "service_entry_authority",
    "relatives_info", "additional_info",
]
DATE_COLS = {"birth_date", "contract_start_date", "contract_end_date",
             "enrollment_date", "enrollment_order_date", "service_entry_date"}

# CSV -> вимір. Усі три -- current_state: змінюються в часі.
FACT_COLS = {"rank": "Звання", "position_title": "Посада",
             "subdivision": "Підрозділ"}
FACT_DIM = {"rank": "rank", "position_title": "position",
            "subdivision": "subdivision"}

# Звання у facts зберігається КОДОМ (`soldier`, `sergeant`), а в штатці --
# українською назвою («рядовий», «сержант»). Без зіставлення кожна особа
# отримувала фальшиву «зміну звання» назва->код, insert_fact вважав це
# суперсесією і падав на CHECK, бо дата штатки (зарахування) СТАРІША за дату
# документа. Довідник -- той самий, що в пайплайні, не наша копія.
RANK_DICT = ("pipeline", "dictionaries", "military_rank.yaml")


def load_rank_codes(search_roots):
    """label/alias -> code із military_rank.yaml пайплайна."""
    import yaml
    for root in search_roots:
        path = os.path.join(root, *RANK_DICT)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        mapping = {}
        for item in data.get("values", []):
            code = item["code"]
            for form in [item.get("label", "")] + list(item.get("aliases") or []):
                if form:
                    mapping[form.strip().lower()] = code
        return mapping, path
    return None, None


def clean(value, col):
    v = (value or "").strip()
    if not v:
        return None
    if col in DATE_COLS and len(v) != 10:
        return None          # у CSV бувають порожні або часткові дати
    return v


def roster_document(cur, apply_):
    """Рядок у documents для самої штатки -- джерело фактів про звання/посаду."""
    with open(ROSTER, "rb") as f:
        checksum = hashlib.sha256(f.read()).hexdigest()
    cur.execute("SELECT id FROM documents WHERE checksum = %s", (checksum,))
    row = cur.fetchone()
    if row:
        return row[0]
    if not apply_:
        return None
    cur.execute("SELECT id FROM document_types WHERE code = 'staff_roster'")
    t = cur.fetchone()
    cur.execute(
        """INSERT INTO documents (type_id, source_kind, status, domain, checksum,
                                   raw_uri, validity, pipeline_meta)
           VALUES (%s, 'electronic', 'extracted', 'staffing', %s, %s, 'current', %s)
           RETURNING id""",
        (t[0] if t else None, checksum,
         "file:///db/seeds/unit_roster.csv",
         Jsonb({"title": "Штатна книжка підрозділу (синтетична)",
                "source": "db/seeds/unit_roster.csv"})),
    )
    return cur.fetchone()[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

    with open(ROSTER, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    print(f"У штатці: {len(rows)} осіб")

    rank_codes, dict_path = load_rank_codes([
        PROJECT_ROOT,
        os.path.expanduser("~/anya/ai-secretary"),
        os.path.join(os.path.dirname(PROJECT_ROOT), "AI-secretary-anya"),
    ])
    if rank_codes:
        print(f"Довідник звань: {dict_path} ({len(rank_codes)} форм)")
    else:
        print("⚠ military_rank.yaml не знайдено -- звання НЕ завантажуємо.\n"
              "  Писати українську назву там, де в базі коди, означає створити\n"
              "  фальшиву зміну звання кожній особі.")
    print()

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # Наявні особи -- за ПІБ. Одразу бачимо неоднозначні збіги.
            cur.execute("""
                SELECT p.object_id, p.last_name, p.first_name, p.patronymic, p.service_id
                  FROM people p
            """)
            by_name = defaultdict(list)
            for oid, ln, fn, pt, sid in cur.fetchall():
                by_name[(ln or "", fn or "", pt or "")].append((oid, sid))

            ambiguous = {k: v for k, v in by_name.items() if len(v) > 1}
            doc_id = roster_document(cur, args.apply)

            matched = created = skipped = 0
            facts_added = kept = 0
            unmapped = set()
            for r in rows:
                key = (r["last_name"].strip(), r["first_name"].strip(),
                       r["patronymic"].strip())
                hits = by_name.get(key, [])
                if len(hits) > 1:
                    skipped += 1
                    continue                    # неоднозначно -- лишаємо людині

                if hits:
                    object_id = hits[0][0]
                    matched += 1
                    if args.apply:
                        sets, vals = [], []
                        for col in PEOPLE_COLS:
                            v = clean(r.get(col), col)
                            if v is not None:
                                sets.append(f"{col} = %s")
                                vals.append(v)
                        if sets:
                            cur.execute(
                                f"UPDATE people SET {', '.join(sets)} WHERE object_id = %s",
                                vals + [object_id])
                else:
                    created += 1
                    if args.apply:
                        cur.execute(
                            "SELECT resolve_or_create_object(%s, 'person', %s)",
                            (r["full_name"].strip(), doc_id))
                        object_id = cur.fetchone()[0]
                        cols = [c for c in PEOPLE_COLS if clean(r.get(c), c) is not None]
                        vals = [clean(r[c], c) for c in cols]
                        cur.execute(
                            f"""INSERT INTO people (object_id, {', '.join(cols)})
                                VALUES (%s{', %s' * len(cols)})
                                ON CONFLICT (object_id) DO NOTHING""",
                            [object_id] + vals)
                    else:
                        object_id = None

                if args.apply and object_id:
                    cur.execute("""
                        SELECT dm.code FROM facts f JOIN dimensions dm ON dm.id = f.dimension_id
                         WHERE f.object_id = %s AND dm.code = ANY(%s)
                    """, (object_id, list(FACT_DIM.values())))
                    have = {r[0] for r in cur.fetchall()}

                    for csv_col, _label in FACT_COLS.items():
                        # Штатка -- БАЗОВИЙ шар, документ -- новіший. Якщо вимір
                        # уже заповнений з документа, штатка його не торкається.
                        if FACT_DIM[csv_col] in have:
                            kept += 1
                            continue
                        value = clean(r.get(csv_col), csv_col)
                        if not value:
                            continue
                        if csv_col == "rank":
                            if not rank_codes:
                                continue          # без довідника не пишемо
                            code = rank_codes.get(value.lower())
                            if not code:
                                unmapped.add(value)
                                continue
                            value = code
                        dim = L.get_or_create_dimension(cur, FACT_DIM[csv_col],
                                                        "current_state")
                        L.insert_fact(cur, object_id, dim, value,
                                      clean(r.get("enrollment_date"), "enrollment_date"),
                                      None, doc_id, True)
                        facts_added += 1

        if args.apply:
            conn.commit()

    print(f"  зіставлено з наявними: {matched}")
    print(f"  створено нових:        {created}")
    print(f"  пропущено (неоднозначні за ПІБ): {skipped}")
    if args.apply:
        print(f"  фактів (звання/посада/підрозділ): {facts_added}")
        print(f"  не торкались (вже є з документа):  {kept}")
    if unmapped:
        print(f"\n⚠ Звання без коду в довіднику ({len(unmapped)}) -- пропущені:")
        for v in sorted(unmapped):
            print(f"    {v!r}")
    if ambiguous:
        print(f"\n⚠ Однакові ПІБ у базі -- НЕ зіставляю, розбирати людині ({len(ambiguous)}):")
        for (ln, fn, pt), hits in list(ambiguous.items())[:10]:
            print(f"    {ln} {fn} {pt} -> об'єкти {[h[0] for h in hits]}")
    print("\n" + ("ЗАСТОСОВАНО" if args.apply else
                  "DRY-RUN: нічого не змінено, для застосування --apply"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
