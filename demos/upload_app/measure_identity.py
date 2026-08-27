# -*- coding: utf-8 -*-
"""Прилад ідентифікації особи: чи ТА сама людина і чи ЇЇ документи в картці.

Блок C харнесу — найнебезпечніша група звіту Дениса 27.08. Його ж слова:
«неправильну цифру помітиш, а картка з чужими документами виглядає як
нормальна картка».

## Чому приладу не було й чому без нього не можна

Приладу на ідентифікацію в проєкті не існувало — і саме тому цей дефект дожив
до третього прогону. Але важливіше інше: **прилад, який міряє «людина
знайшлась», показав би 100% на всіх п'яти провалах Дениса.** Тому тут три
числа, і вони не рівноцінні:

  1. **та сама особа** — порівнюється `object_id`, а не ПІБ. Порівняння ПІБ і є
     тим механізмом, що ламається: спільний шматок імені склеює трьох людей;
  2. **чистота картки** — усі документи в ній належать ЦІЙ особі. Нуль чужих.
     Це критерій-стоп: одна чужа картка гірша за десять відмов;
  3. **тихе виправлення вводу** — якщо система шукала не те, що написала
     людина («Голяш» → «Гоголь-Яновський»), вона мусить це сказати. Правило вже
     діє для номера документа («номер не виправляємо і схожих не
     підставляємо») — прізвище має працювати так само.

«Не знайшла» тут — **успіх**, а не провал.

## Що прилад НЕ робить

Не пише в базу й не потребує моделі: ідентифікація — це регулярки й SQL. Тому
прогін детермінований і швидкий, і тому ж результат можна ставити в тест.

Запуск (на сервері або будь-де з доступом до бази на читання):
    python demos/upload_app/measure_identity.py
    python demos/upload_app/measure_identity.py --json data/eval/identity-report.json
    python demos/upload_app/measure_identity.py --set eval/chat/identity.tsv
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from demos.upload_app.chat_gradio import app as chat_app  # noqa: E402

db = chat_app.db

DEFAULT_SET = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "eval", "chat", "identity.tsv")


def load_set(path):
    """-> [{question, expect, strict_docs, note}] із TSV; коментарі пропускає."""
    rows = []
    with io.open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            rows.append({
                "question": parts[0].strip(),
                "expect": parts[1].strip(),
                "strict_docs": (parts[2].strip() == "1") if len(parts) > 2 else True,
                "note": parts[3].strip() if len(parts) > 3 else "",
            })
    return rows


def probe(question):
    """Пройти шлях ідентифікації так, як його проходить чат.

    Свідомо НЕ через `answer()`: там зверху ще маршрутизація, модель і рендер,
    і провал розмився б по чотирьох ярусах. Тут міряється рівно ланка
    «питання -> особа + її документи», у якій і живуть п. 6-9 звіту.
    """
    res = chat_app.resolve_person(question)
    people = res["people"]
    used = " ".join(res["words"]) or None
    if people:
        docs = db.absences_for_object(people[0]["object_id"], only_active=False)
    elif used:
        docs = db.absences_for_person(used.split()[0], only_active=False)
    else:
        docs = []
    return {"stem": used, "people": people, "docs": docs}


def judge(case, got):
    """-> (ok, вердикти по трьох числах, деталі)."""
    expect = case["expect"]
    people, docs, stem = got["people"], got["docs"], got["stem"]
    chosen = people[0] if people else None
    chosen_id = chosen.get("object_id") if chosen else None

    v = {}

    # ── 1. та сама особа ────────────────────────────────────────────────────
    if expect == "refusal":
        # Правильно -- НІКОГО не знайти. Знайдена людина тут гірша за порожньо:
        # це впевнена відповідь про іншу особу.
        v["person"] = "ok" if not people and not docs else "wrong-person"
    elif expect == "ambiguous":
        # Правильно -- побачити кілька збігів. Один збіг теж провал: система
        # обрала за людину.
        v["person"] = "ok" if len(people) > 1 else "not-ambiguous"
    else:
        v["person"] = "ok" if chosen_id == int(expect) else "wrong-person"

    # ── 2. чистота картки ──────────────────────────────────────────────────
    doc_owners = {d.get("object_id") for d in docs if d.get("object_id")}
    foreign = set()
    if case["strict_docs"]:
        if expect in ("refusal",):
            foreign = doc_owners
        elif expect == "ambiguous":
            # При кількох збігах картку взагалі не показуємо, тому чужих
            # документів бути не може за побудовою -- перевіряємо це.
            foreign = doc_owners - {p.get("object_id") for p in people}
        elif chosen_id is not None:
            foreign = doc_owners - {chosen_id}
        else:
            foreign = doc_owners
    v["docs"] = "ok" if not foreign else "foreign-docs"

    # ── 3. тихе виправлення вводу ──────────────────────────────────────────
    # Ознака виправлення: те, що шукали (stem), НЕ є початком того, що
    # написала людина, або знайдене ПІБ не містить stem. Обидва випадки
    # означають, що система пішла не за буквальним вводом.
    # ВІДМІНОК -- НЕ ВИПРАВЛЕННЯ. Перша версія цієї перевірки вимагала, щоб
    # написане слово було дослівним підрядком знайденого ПІБ, і чесно
    # відмічала «Ґоляша» -> «Ґоляш» як тихе виправлення. Це неправда:
    # українська змінює слово за відмінком, і казати про це людині означало б
    # шумом попереджати її про нормальну мову. Прилад міряв би тоді сам себе.
    #
    # Виправленням вважається інше слово: коли жодне зі СЛІВ знайденого ПІБ не
    # починається з основи того, що написала людина. Основа -- та сама, якою
    # шукає сам пошук (`db.name_word_regex`), тому критерій і пошук не можуть
    # розійтись. Саме так відрізняється «Голяш» -> «Гоголь-Яновський».
    silent = False
    if stem and chosen:
        found_words = (chosen.get("full_name") or "").lower().split()
        for written in str(stem).split():
            base = written.lower()[:max(5, len(written) - 3)]
            if not any(w.startswith(base) for w in found_words):
                silent = True
                break
    # Перевірку «шукали обрізок» знято: обрізання по літері прибрано, допуск на
    # відмінок робить сам пошук (`db.name_word_regex`), і слова беруться з
    # питання як є. Лишилась головна ознака тихого виправлення -- знайдене ПІБ
    # не містить того, що людина написала.
    v["said"] = "ok" if not silent else "silent-fix"

    return (all(x == "ok" for x in v.values()), v,
            {"stem": stem,
             "chosen": (chosen or {}).get("full_name"),
             "chosen_id": chosen_id,
             "matches": len(people),
             "doc_owners": sorted(o for o in doc_owners if o),
             "foreign": sorted(foreign)})


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", default=DEFAULT_SET, help="файл набору (TSV)")
    ap.add_argument("--json", default=None, help="куди зберегти звіт")
    ap.add_argument("--quiet", action="store_true", help="лише підсумок")
    args = ap.parse_args()

    cases = load_set(args.set)
    if not cases:
        print(f"набір порожній: {args.set}")
        return 1

    totals = {"person": 0, "docs": 0, "said": 0}
    results, bad = [], 0
    for case in cases:
        try:
            got = probe(case["question"])
        except Exception as exc:            # база недоступна -- це не «0%»
            print(f"ЗБІЙ на «{case['question']}»: {type(exc).__name__}: {exc}")
            return 2
        ok, v, detail = judge(case, got)
        for k in totals:
            totals[k] += (v[k] == "ok")
        if not ok:
            bad += 1
        results.append({**case, **v, **detail})
        if not args.quiet:
            mark = "OK  " if ok else "ПРОВАЛ"
            print(f"{mark} {case['question'][:52]:54} "
                  f"особа={v['person']:14} документи={v['docs']:12} "
                  f"ввід={v['said']}")
            if not ok:
                print(f"       очікували {case['expect']}, "
                      f"шукали «{detail['stem']}», знайшли "
                      f"{detail['chosen_id']} ({detail['chosen']}), "
                      f"збігів {detail['matches']}, "
                      f"власники документів {detail['doc_owners']}"
                      + (f", ЧУЖІ {detail['foreign']}" if detail["foreign"] else ""))
                if case["note"]:
                    print(f"       {case['note']}")

    n = len(cases)
    print()
    print(f"питань: {n}")
    print(f"  та сама особа:      {totals['person']}/{n}")
    print(f"  чистота картки:     {totals['docs']}/{n}   "
          f"(критерій-стоп: мусить бути {n}/{n})")
    print(f"  ввід не виправлено мовчки: {totals['said']}/{n}")
    print(f"повністю правильних: {n - bad}/{n}")

    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with io.open(args.json, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"questions": n,
                       "person_ok": totals["person"],
                       "docs_clean": totals["docs"],
                       "input_not_silently_fixed": totals["said"],
                       "fully_ok": n - bad,
                       "cases": results}, fh, ensure_ascii=False, indent=2)
        print(f"звіт: {args.json}")

    # Код виходу: 1, якщо зламаний критерій-стоп (чужі документи), інакше 0
    # навіть при інших провалах -- щоб прилад можна було ганяти по ходу правок.
    return 1 if totals["docs"] < n else 0


if __name__ == "__main__":
    sys.exit(main())
