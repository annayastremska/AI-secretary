# -*- coding: utf-8 -*-
"""Аудит яруса вільного SQL: 17 питань різної форми через ЖИВИЙ сервіс.

Чому через HTTP, а не імпортом: модель одна й лежить у процесі сервісу; у
своєму процесі ярус вільного SQL не працює зовсім, і аудит міряв би мій
процес, а не продукт.

Кожен хід зберігаємо ЦІЛИМ -- разом із блоком «джерело», бо саме там лежить
SQL, який склала модель. Без SQL висновок «відповідь правильна» перевірити
неможливо.
"""
import base64
import io
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
OUT = "/tmp/free-sql-audit.jsonl"

QUESTIONS = [
    # ── очікуємо ЧИСЛО ──────────────────────────────────────────────────────
    "Яка середня тривалість відпустки за нашими документами?",
    "Яка найдовша відпустка в документах?",
    "Яка середня тривалість відрядження?",
    "Скільки різних населених пунктів згадано в документах?",
    "Скільки документів не мають номера на папері?",
    "Скільки осіб мають більше одного документа?",
    "Скільки людей мають і відпустку, і відрядження?",
    "Скільки відсотків фактів підтверджено?",
    # ── очікуємо ПЕРЕЛІК або згрупований підсумок ───────────────────────────
    "У кого найбільше документів?",
    "Яка найчастіша причина відпустки?",
    "Хто був у відрядженні найдовше?",
    "Скільки відпусток почалось у липні?",
    "Порівняй кількість відпусток у липні й у серпні",
    "Скільки днів у середньому між видачею документа й початком відпустки?",
    # ── очікуємо ВІДМОВУ ────────────────────────────────────────────────────
    "Скільки набоїв на складі?",
    "Хто найстарший за званням?",
    "Яка середня зарплата військовослужбовця?",
]


def ask(question):
    body = json.dumps({"data": [question, []]}).encode()
    req = urllib.request.Request(BASE, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": AUTH})
    eid = json.load(urllib.request.urlopen(req, timeout=30))["event_id"]
    req2 = urllib.request.Request(BASE + "/" + eid,
                                  headers={"Authorization": AUTH})
    last = None
    with urllib.request.urlopen(req2, timeout=400) as fh:
        for raw in fh:
            line = raw.decode("utf-8", "replace")
            if line.startswith("data: "):
                last = line[6:]
    msgs = json.loads(last)[1]["value"]
    reply = ""
    for m in msgs:
        if m.get("role") == "assistant":
            c = m.get("content")
            reply = c[0]["text"] if isinstance(c, list) else str(c)
    return reply


def sql_of(reply):
    """SQL із блоку «джерело», якщо хід ішов вільним ярусом."""
    m = re.search(r"SQL:?<br>(.*?)(?:<br>дорога|</details>)", reply, re.S)
    if not m:
        m = re.search(r"SQL:\s*(SELECT.*?)(?:<br>|\n|</details>)", reply, re.S)
    return re.sub(r"<[^>]+>", " ", m.group(1)).strip() if m else ""


with io.open(OUT, "w", encoding="utf-8") as out:
    for i, q in enumerate(QUESTIONS, 1):
        t0 = time.time()
        try:
            reply = ask(q)
            err = ""
        except Exception as exc:                     # noqa: BLE001
            reply, err = "", f"{type(exc).__name__}: {exc}"
        rec = {"n": i, "q": q, "sec": round(time.time() - t0, 1),
               "reply": reply, "sql": sql_of(reply), "error": err}
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()
        print("[%2d/%d] %5.1f с  %s" % (i, len(QUESTIONS), rec["sec"], q))
print("готово ->", OUT)
