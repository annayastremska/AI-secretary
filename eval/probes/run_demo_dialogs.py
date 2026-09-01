# -*- coding: utf-8 -*-
"""Прогін усіх питань із docs/tasks/2026-08-29_demo-dialogs.md наскрізь.

Не по одному питанню: репліки йдуть ПОСЛІДОВНО, з історією, бо саме
перенесення слотів між репліками і є те, що ламається. Історія збирається у
тому самому вигляді, що й у чаті (role/content), тому приховані слоти
`<!--slots:...-->` читаються так само, як у живій сторінці.
"""
import io
import os
import re
import sys
import time

sys.path.insert(0, "demos/upload_app/chat_gradio")
sys.stdout.reconfigure(encoding="utf-8")

import app as chat                                  # noqa: E402

DIALOGS = [
    ("Діалог 1 — чат", [
        "Скільки у відпустці 30 серпня?",
        "а наступного дня?",
        "а хто з них повертається 1 вересня?",
        "а хто у Кривоярську?",
        "а у рівному?",
    ]),
    ("Діалог 3 — норма", [
        "За скільки днів подавати рапорт на відпустку?",
        "Яка тривалість щорічної основної відпустки?",
        "Скільком набоїв на складі?",
    ]),
]


def clean(text):
    """Прибрати приховані слоти й службову розмітку -- лишити те, що бачить
    людина."""
    text = re.sub(r"<!--slots:.*?-->", "", text or "", flags=re.S)
    return text.strip()


for title, questions in DIALOGS:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    history = []
    for q in questions:
        t0 = time.time()
        try:
            out = chat.answer(q, history)
        except Exception as exc:                     # noqa: BLE001
            out = f"!!! ВИНЯТОК: {type(exc).__name__}: {exc}"
        dt = time.time() - t0
        print("\n--- ПИТАННЯ: %s   [%.1f с]" % (q, dt))
        print(clean(out)[:1400])
        history.append({"role": "user", "content": q})
        history.append({"role": "assistant", "content": out})
