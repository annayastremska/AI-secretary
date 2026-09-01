"""Проставляє чинність нормативним документам.

Навіщо окремо: пайплайн Ані чинності не витягує (їй це й не потрібно -- вона
віддає текст і домен), а колонка `validity` має дефолт `unknown`, і пошук по
нормативці бере лише `current`. Тобто без цього кроку дорога Б віддає
порожньо -- і це правильна робота deny-by-default, а не помилка.

Запуск:
    python db/scripts/set_normative_validity.py            # лише показати
    python db/scripts/set_normative_validity.py --apply

## Звідки береться стан -- і чому це не «система вирішила сама»

Наш дизайн вимагає: «система ніколи не позначає документ чинним самостійно»
(`normative-docs-subsystem.md` §4). Тому два різні джерела:

* **declared** -- документ САМ пише, що втратив чинність. На rada це стоїть
  першим рядком у фігурних дужках: «{Наказ втратив чинність на підставі...}».
  Це не висновок системи, це цитата з документа, тож найсильніше джерело.
* **manual** -- людина звірила. Для решти корпусу це я: кожен документ
  збирався поштучно за списком (`docs/tasks/2026-08-21_normative-corpus.md`),
  посилання й назва перевірялись, а rada віддає чинну редакцію. Позначка
  `manual` фіксує саме те, що це людське твердження, а не автоматичний
  висновок -- щоб через місяць було видно різницю.

`inferred` (той самий номер + новіша дата -> «ймовірно замінено») тут НЕ
використовується: для цього потрібні розібрані реквізити, а не текст.
"""
import argparse
import os
import re
import sys

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Шукаємо лише в шапці: далі в тексті «втратив чинність» зустрічається як
# згадка про ІНШІ документи (напр. перелік скасованих актів у додатку), і
# позначати документ скасованим через таку згадку було б грубою помилкою.
HEADER_CHARS = 600
REPEALED = re.compile(r"втратив\s+чинн|втратила\s+чинн|визнан\w*\s+таким,?\s+що\s+втратив",
                      re.IGNORECASE)
# «на підставі Наказу ... № 280 від 15.09.2022» -- чим саме замінено
REPLACED_BY = re.compile(r"на\s+підставі\s+([^}]{0,120}?)(?:\}|$)", re.IGNORECASE)


def classify(text: str):
    head = (text or "")[:HEADER_CHARS]
    if REPEALED.search(head):
        m = REPLACED_BY.search(head)
        note = " ".join(m.group(1).split()) if m else None
        return "superseded", "declared", note
    return "current", "manual", None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, text_content, validity
                  FROM documents
                 WHERE domain = 'normative' AND text_content IS NOT NULL
                 ORDER BY id
            """)
            rows = cur.fetchall()

            stats = {}
            declared = []
            for doc_id, text, current_validity in rows:
                validity, source, note = classify(text)
                stats[validity] = stats.get(validity, 0) + 1
                if source == "declared":
                    declared.append((doc_id, note, " ".join((text or "")[:90].split())))
                if args.apply and validity != current_validity:
                    cur.execute(
                        "UPDATE documents SET validity = %s, validity_source = %s WHERE id = %s",
                        (validity, source, doc_id),
                    )

            print(f"Нормативних документів: {len(rows)}")
            for k, v in sorted(stats.items()):
                print(f"  {k}: {v}")

            print(f"\nСамі оголосили втрату чинності ({len(declared)}) -- джерело declared:")
            for doc_id, note, head in declared:
                print(f"  id={doc_id}: {head[:78]}")
                if note:
                    print(f"      замінено: {note[:78]}")

        if args.apply:
            conn.commit()
            print("\nЗАСТОСОВАНО")
        else:
            print("\nDRY-RUN: нічого не змінено, для застосування додай --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
