# -*- coding: utf-8 -*-
"""Питає ЖИВИЙ сервіс через його ж HTTP-API, а не імпортом у свій процес.

Причина: модель одна й лежить у процесі сервісу. Прогін імпортом дає
«локальна модель недоступна» -- і тоді нормативні ворота відмовляють не через
продукт, а через відсутність моделі. Тобто перевірка міряла б мій процес.

Історія передається так само, як її тримає сторінка (role/content), тому
приховані слоти між репліками переносяться справжнім шляхом.
"""
import base64
import json
import os
import re
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1/chat/gradio_api/call/respond"
# Пароль НЕ в коді: у git він поїхав би як є (base64 не шифрує), а репозиторій
# бачить університет. Береться з оточення -- `APP_BASIC` у вигляді
# `логін:пароль`, той самий, що в `.env` сервера.
AUTH = "Basic " + base64.b64encode(
    os.environ.get("APP_BASIC", "demo:ПАРОЛЬ-З-ENV").encode()).decode()


def ask(question, history):
    body = json.dumps({"data": [question, history]}).encode()
    req = urllib.request.Request(BASE, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": AUTH})
    eid = json.load(urllib.request.urlopen(req, timeout=30))["event_id"]
    req2 = urllib.request.Request(BASE + "/" + eid,
                                  headers={"Authorization": AUTH})
    last = None
    with urllib.request.urlopen(req2, timeout=300) as fh:
        for raw in fh:
            line = raw.decode("utf-8", "replace")
            if line.startswith("data: "):
                last = line[6:]
    payload = json.loads(last)
    msgs = payload[1]["value"]
    reply = ""
    for m in msgs:
        if m.get("role") == "assistant":
            c = m.get("content")
            reply = c[0]["text"] if isinstance(c, list) else str(c)
    return reply, msgs


def clean(t):
    t = re.sub(r"<!--slots:.*?-->", "", t or "", flags=re.S)
    t = re.sub(r"<details.*?</details>", "", t, flags=re.S)
    return t.strip()


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
    ("Додатково — те, що ловилось живцем", [
        "Хто зараз у відрядженні?",
        "а завтра",
        "хтось із них в житомирі?",
    ]),
]

for title, questions in DIALOGS:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)
    history = []
    for q in questions:
        t0 = time.time()
        try:
            reply, msgs = ask(q, history)
        except Exception as exc:                     # noqa: BLE001
            print("\n### %s -> ВИНЯТОК %s: %s" % (q, type(exc).__name__, exc))
            continue
        print("\n### %s   [%.1f с]" % (q, time.time() - t0))
        body = clean(reply)
        print(body[:700] + ("…" if len(body) > 700 else ""))
        history = msgs
