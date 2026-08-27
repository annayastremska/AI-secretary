# -*- coding: utf-8 -*-
"""Прилад одиниці обліку: чи не суперечать одне одному дві цифри на одному екрані.

Блок E харнесу. Шість пунктів звіту Дениса 27.08 (11–14, 19, 21) — і всі про
одне: **у відповіді не сказано, ЩО саме порахували.** Кожна цифра окремо
правдива, а разом читаються як брехня:

  «9 з 300 відсутні» -- і поруч 10 рядків, з них Малишко двічі;
  «12 у відпустці»   -- і «15 поза частиною» на ту саму дату;
  «303 у реєстрі»    -- і «300 за штаткою» у наступному рядку.

## Правило приймання -- НЕ «числа однакові»

Це головне про цей прилад. Однакових чисел вимагати неправильно: 12 і 15 на одну
дату **законно**, якщо перше про відпустку, а друге про «поза частиною =
відпустка + відрядження». Незаконно — коли різницю не названо.

Тому прилад міряє не арифметику, а **чи сказала система, що порахувала**:

  1. якщо два числа різні, у відповідях мусить бути видно РІЗНУ метрику або
     різний знаменник — інакше це суперечність;
  2. те саме питання з невидимими відмінностями (подвійні пробіли, неразривний
     пробіл, пробіл перед «?») мусить давати **байт у байт** ту саму відповідь;
  3. де є перелік і число — кількість імен мусить збігатися з числом, і якщо
     ні, це мусить бути сказано вголос.

## Що прилад НЕ робить

Не пише в базу. Моделі не потребує: усе, що він міряє, лежить у правиловій
дорозі й у шаблонах каталогу. Тому прогін детермінований.

Запуск:
    python demos/upload_app/measure_counts.py
    python demos/upload_app/measure_counts.py --json data/eval/counts-report.json
"""
import argparse
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from demos.upload_app.chat_gradio import app as chat_app  # noqa: E402

tiers = chat_app.tier_chat

#: Дати беруться в межах покриття стенду -- інакше всі відповіді будуть «даних
#: немає», і прилад міряв би порожнечу.
DATES = ("2026-09-01", "2026-09-02", "2026-10-10")

#: Пари питань, які МОЖУТЬ дати різні числа. Для кожної сказано, чи різниця
#: законна, і що саме мусить бути назване у відповіді.
PAIRS = [
    ("Скільком осіб у відпустці {d}?",
     "Скільком осіб відсутні {d}?",
     "різні метрики: відпустка проти «поза частиною»",
     ("відпустк", "відрядж")),
    ("Скільком осіб у відпустці {d}?",
     "Хто у відпустці {d}?",
     "та сама метрика -- числа мусять збігатись",
     None),
]

#: Невидимі відмінності вводу (п. 21).
ODD_FORMS = [
    "Скільком осіб у відпустці {d}?",
    "Скільком  осіб  у  відпустці  {d} ?",
    "Скільком осіб у відпустці {d}?",
    "  Скільком осіб у відпустці {d}?  ",
]

_NUM = re.compile(r"(\d+)\s*(?:особ|осіб|людин)")


def _answer(question):
    try:
        return chat_app.answer(question, [])
    except Exception as exc:
        return f"ЗБІЙ: {type(exc).__name__}: {exc}"


def _first_number(text):
    m = _NUM.search(text or "")
    return int(m.group(1)) if m else None


def _visible(text):
    """Текст без блоку «джерело»: людина порівнює те, що бачить."""
    return (text or "").split("<details")[0].strip()


def check_odd_forms(report):
    """п. 21: невидимі пробіли не мусять міняти відповідь."""
    bad = 0
    for d in DATES:
        answers = {}
        for form in ODD_FORMS:
            q = form.format(d=d)
            answers[q] = _visible(_answer(q))
        base = list(answers.values())[0]
        for q, a in answers.items():
            if a != base:
                bad += 1
                print(f"  ПРОВАЛ пробіли: «{q}» дало іншу відповідь")
                report.append({"kind": "whitespace", "question": q,
                               "ok": False})
                break
        else:
            report.append({"kind": "whitespace", "date": d, "ok": True})
    return bad


def check_pairs(report):
    """Дві цифри на одному екрані: різні -- лише якщо різницю названо."""
    bad = 0
    for d in DATES:
        for q1, q2, why, must_name in PAIRS:
            a1, a2 = _answer(q1.format(d=d)), _answer(q2.format(d=d))
            n1, n2 = _first_number(a1), _first_number(a2)
            entry = {"kind": "pair", "date": d, "why": why,
                     "q1": q1.format(d=d), "q2": q2.format(d=d),
                     "n1": n1, "n2": n2}
            if n1 is None or n2 is None:
                entry["ok"] = None
                entry["note"] = "число не знайдено в тексті -- нічого порівнювати"
                report.append(entry)
                continue
            if n1 == n2:
                entry["ok"] = True
                report.append(entry)
                continue
            # Числа різні. Це законно ЛИШЕ якщо різницю названо у відповіді.
            named = True
            if must_name:
                low = (_visible(a2) or "").lower()
                named = all(w in low for w in must_name)
            entry["ok"] = bool(must_name) and named
            if not entry["ok"]:
                bad += 1
                print(f"  ПРОВАЛ метрика: {n1} проти {n2} на {d} "
                      f"({why}) -- різницю не названо")
            report.append(entry)
    return bad


def check_list_matches_number(report):
    """Перелік і число беруть дані різними запитами -- саме там п. 12."""
    bad = 0
    for d in DATES:
        out = _answer(f"Скільком осіб у відпустці {d}?")
        n = _first_number(out)
        vis = _visible(out)
        listed = len([ln for ln in vis.splitlines()
                      if ln.startswith("- ")])
        entry = {"kind": "list", "date": d, "number": n, "listed": listed}
        if n is None:
            entry["ok"] = None
        elif listed == 0:
            # Переліку немає: або нуль, або задовгий -- і про це мусить бути
            # сказано, а не просто пусто.
            entry["ok"] = (n == 0 or "поіменно" in vis.lower())
        else:
            entry["ok"] = (listed == n) or ("число вище" in vis)
        if entry["ok"] is False:
            bad += 1
            print(f"  ПРОВАЛ перелік: число {n}, імен {listed} на {d} -- "
                  f"і розбіжність не названа")
        report.append(entry)
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", default=None, help="куди зберегти звіт")
    args = ap.parse_args()

    report, bad = [], 0
    print("── невидимі відмінності вводу (п. 21) ──")
    bad += check_odd_forms(report)
    print("── дві цифри на одному екрані (п. 12-14, 19) ──")
    bad += check_pairs(report)
    print("── перелік проти числа (п. 12) ──")
    bad += check_list_matches_number(report)

    ok = sum(1 for r in report if r.get("ok") is True)
    skipped = sum(1 for r in report if r.get("ok") is None)
    print()
    print(f"перевірок: {len(report)}   пройшло: {ok}   "
          f"провалів: {bad}   не міряно: {skipped}")
    if skipped:
        print("«не міряно» -- це НЕ успіх: у відповіді не знайшлось числа, "
              "тобто порівнювати було нічого. Дивіться звіт.")

    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with io.open(args.json, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"checks": len(report), "ok": ok, "failed": bad,
                       "not_measured": skipped, "cases": report},
                      fh, ensure_ascii=False, indent=2)
        print(f"звіт: {args.json}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
