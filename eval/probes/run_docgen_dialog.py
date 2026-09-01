# -*- coding: utf-8 -*-
"""Діалог 2 з demo-dialogs: створення документа, обидві частини.

Йде через `docgen.step` -- це та сама функція, яку викликає сторінка (app.py,
`respond` при активному docgen). Модель тут не потрібна: збирання полів
детерміністичне, а особу перевіряє штатка з бази.

Файл кладемо у /tmp, а не в теку сервісу: перевірка не має лишати після себе
документів у робочій теці демо.
"""
import os
import sys
import tempfile

sys.path.insert(0, "demos/upload_app/chat_gradio")
sys.stdout.reconfigure(encoding="utf-8")

import docgen                                       # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "docgen-check")
os.makedirs(OUT, exist_ok=True)
docgen.OUT_DIR = OUT

PART_A = ["відпускний квиток", "Гавриш Адам Станіславович",
          "щорічна основна відпустка за 2026 рік", "м. Кривоярськ",
          "3 листопада 2026", "12 листопада 2026", "пропустити",
          "пропустити", "9201", "1 листопада 2026", "так"]

PART_B = ["відпускний квиток", "Ґоляш Богодар Святославович",
          "щорічна основна відпустка за 2026 рік", "м. Сухобрід",
          "1 жовтня 2026", "5 жовтня 2026"]

PART_B_TAIL = ["скасувати"]


def run(title, steps):
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)
    state = docgen.start()
    state, reply, path = docgen.step(state, "")
    print("\n[0] (початок) ->", (reply or "").strip()[:220])
    for i, text in enumerate(steps, 1):
        state, reply, path = docgen.step(state, text)
        print("\n[%d] «%s» ->" % (i, text))
        print("   ", (reply or "").strip().replace("\n", "\n    ")[:600])
        if path:
            print("    ФАЙЛ:", os.path.basename(path),
                  "|", os.path.getsize(path), "байт")
    return state


run("Частина А — успіх", PART_A)
st = run("Частина Б — відмова на перетині", PART_B)
print("\n--- далі «скасувати»:")
st, reply, _ = docgen.step(st, "скасувати")
print("   ", (reply or "").strip()[:300])
