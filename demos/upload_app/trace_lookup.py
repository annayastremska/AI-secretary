# -*- coding: utf-8 -*-
"""Розбір ходу чата за номером звернення — і зведення по всіх ходах.

Запит Дениса 27.08: «щоб на скріні був цей код і можна було автоматично
прогнати аналіз, як система там давала інфо». Номер на скріншоті — ключ; цей
скрипт по ключу віддає структуру ходу, а не рядок тексту.

Запуск:
    python demos/upload_app/trace_lookup.py cd3433          один хід, читно
    python demos/upload_app/trace_lookup.py cd3433 --json   той самий, машині
    python demos/upload_app/trace_lookup.py --stats          зведення по всіх
    python demos/upload_app/trace_lookup.py --check          перевірка правил

`--check` — окрема річ і найкорисніша: він не описує, а СУДИТЬ. Дві умови, які
на демо мусять триматись, і кожну видно лише на сукупності ходів:

  * **кожна відповідь має джерело.** Це правило продукту, і порушення тут
    гірше за будь-яку повільність: цифра без джерела не перевірна;
  * **жодного технічного збою.** Відмова «не знайшла» — не збій, це штатна
    поведінка; збій — це недоступна база або виняток.

Персональних даних у сліді немає за побудовою: тексту відповіді там не
зберігається взагалі, від рядків із бази — лише кількість. Розбір цього файла
— `chat_gradio/trace.py`.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from demos.upload_app.chat_gradio import trace  # noqa: E402


def show(row):
    """Один хід, читно для людини."""
    print(f"звернення {row.get('id')}   {row.get('at')}")
    print(f"питання:  {row.get('question')}")
    print(f"дорога:   {row.get('road') or '—'}")
    secs = row.get("seconds")
    print(f"тривало:  {secs if secs is not None else '—'} с")
    print(f"відповідь: {row.get('answer_chars')} символів"
          + ("  (відмова)" if row.get("refusal") else "")
          + ("" if row.get("has_source") else "  БЕЗ ДЖЕРЕЛА"))
    if row.get("error"):
        print(f"збій:     {row['error']}")
    steps = row.get("steps") or []
    if not steps:
        print("кроків не записано (дорога без шаблону: правила, довідка, "
              "уточнення)")
        return
    print("\nкроки:")
    for st in steps:
        kind = st.get("kind")
        if kind == "template":
            print(f"  шаблон {st.get('template')} — {st.get('title')}")
            print(f"    параметри: {json.dumps(st.get('params'), ensure_ascii=False)}")
            print(f"    рядків із бази: {st.get('rows')}")
            sql = (st.get("sql") or "").strip()
            if sql:
                print("    SQL шаблону:")
                for line in sql.splitlines():
                    print("      " + line)
        elif kind == "blocked":
            print(f"  відмова шаблону {st.get('template')} — "
                  f"{st.get('title')} (SQL немає за побудовою)")
        else:
            print(f"  {kind}: {json.dumps(st, ensure_ascii=False)}")


def stats():
    data = trace.summary()
    if not data.get("turns"):
        print("слідів немає: файл порожній або чат ще не питали")
        return 0
    print(f"ходів: {data['turns']}   "
          f"({data.get('first_at')} — {data.get('last_at')})")
    print(f"медіана: {data.get('median_seconds')} с   "
          f"найдовший: {data.get('slowest_seconds')} с")
    print(f"відмов: {data.get('refusals')}   "
          f"технічних збоїв: {data.get('errors')}   "
          f"відповідей БЕЗ джерела: {data.get('answers_without_source')}")
    print("\nдороги:")
    for road, n in sorted(data.get("roads", {}).items(), key=lambda kv: -kv[1]):
        print(f"  {n:5}  {road}")
    tpl = data.get("templates") or {}
    if tpl:
        print("\nшаблони:")
        for tid, n in sorted(tpl.items(), key=lambda kv: -kv[1]):
            print(f"  {n:5}  {tid}")
    return 0


def check():
    """Судити, а не описувати. Код виходу 1, якщо правило зламане."""
    data = trace.summary()
    if not data.get("turns"):
        print("НЕМА ЩО ПЕРЕВІРЯТИ: слідів нуль")
        return 0
    bad = 0
    no_source = data.get("answers_without_source") or 0
    if no_source:
        print(f"ЗЛАМАНО: відповідей без джерела {no_source} із "
              f"{data['turns']}. Це правило продукту, а не якість: цифра без "
              f"джерела не перевірна.")
        bad += 1
    else:
        print(f"OK: усі {data['turns']} відповідей мають джерело")
    errors = data.get("errors") or 0
    if errors:
        print(f"ЗЛАМАНО: технічних збоїв {errors}. Відмова «не знайшла» тут "
              f"не рахується -- це штатна поведінка; рахуються недоступна "
              f"база й винятки.")
        bad += 1
    else:
        print(f"OK: технічних збоїв нема на {data['turns']} ходах")
    print(f"довідково: відмов {data.get('refusals')} (це НЕ збій), "
          f"медіана {data.get('median_seconds')} с")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("request_id", nargs="?", help="номер звернення, 6 символів")
    ap.add_argument("--json", action="store_true",
                    help="віддати слід як JSON (для скриптів)")
    ap.add_argument("--stats", action="store_true", help="зведення по всіх ходах")
    ap.add_argument("--check", action="store_true",
                    help="перевірити правила; код виходу 1, якщо зламано")
    ap.add_argument("--path", default=None, help="інший файл сліду")
    args = ap.parse_args()

    if args.path:
        trace.TRACE_PATH = args.path

    if args.check:
        return check()
    if args.stats:
        return stats()
    if not args.request_id:
        ap.error("вкажи номер звернення або --stats / --check")

    row = trace.find(args.request_id)
    if row is None:
        print(f"ходу з номером {args.request_id} у сліді немає.")
        print(f"файл: {trace.TRACE_PATH}")
        print("Якщо хід був до 27.08 -- сліду тоді ще не збирали; у звичайному "
              "журналі logs/app.log він є.")
        return 1
    if args.json:
        print(json.dumps(row, ensure_ascii=False, indent=2))
    else:
        show(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
