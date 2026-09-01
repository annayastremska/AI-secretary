# -*- coding: utf-8 -*-
"""Аудит-2: вільний SQL як УТОЧНЮЮЧЕ питання, а не перше.

Запит Ані 30.08. Гіпотеза, яку перевіряємо: уточнююча репліка («а найдовше з
них?») не має ні предмета, ні виміру -- вони лишились у попередньому ході. Якщо
до яруса вільного SQL доїжджає лише текст репліки, модель складає запит
наосліп, і відповідь буде впевненою й не про тих людей.

Історія передається так, як її тримає сторінка (role/content), тобто приховані
слоти між ходами переносяться справжнім шляхом.
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
OUT = "/tmp/free-sql-dialogs.jsonl"

DIALOGS = [
    ("Д1 · відпустки -> середня тривалість", [
        "Скільки у відпустці 30 серпня?",
        "а яка в них середня тривалість?",
    ]),
    ("Д2 · відрядження -> найдовше", [
        "Хто зараз у відрядженні?",
        "а найдовше з них?",
    ]),
    ("Д3 · документ -> скільки таких", [
        "Покажи документ №207",
        "а скільки таких документів у базі?",
    ]),
    ("Д4 · відпустки -> кому лишилось мало", [
        "Скільки у відпустці зараз?",
        "а скільком із них лишилось менше трьох днів?",
    ]),
    ("Д5 · пункт -> скільки пунктів усього", [
        "Хто у Кривоярську?",
        "а скільки різних пунктів усього в документах?",
    ]),
    ("Д6 · черга -> відсоток", [
        "Що в черзі перевірки?",
        "а який це відсоток від усіх документів?",
    ]),
    ("Д7 · відпустки -> період поза корпусом", [
        "Скільки у відпустці 30 серпня?",
        "а порівняй із серпнем минулого року",
    ]),
    ("Д8 · вільний SQL -> уточнення до вільного SQL", [
        "Яка середня тривалість відпустки за нашими документами?",
        "а у відрядженні?",
    ]),
]


def ask(question, history):
    body = json.dumps({"data": [question, history]}).encode()
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
    return reply, msgs


def sql_of(reply):
    m = re.search(r"SQL:?<br>(.*?)(?:<br>дорога|</details>)", reply, re.S)
    if not m:
        m = re.search(r"SQL:\s*(SELECT.*?)(?:<br>|\n|</details>)", reply, re.S)
    return re.sub(r"<[^>]+>", " ", m.group(1)).strip() if m else ""


with io.open(OUT, "w", encoding="utf-8") as out:
    for title, questions in DIALOGS:
        history = []
        for turn, q in enumerate(questions, 1):
            t0 = time.time()
            try:
                reply, history = ask(q, history)
                err = ""
            except Exception as exc:                 # noqa: BLE001
                reply, err = "", f"{type(exc).__name__}: {exc}"
            rec = {"dialog": title, "turn": turn, "q": q,
                   "sec": round(time.time() - t0, 1), "reply": reply,
                   "sql": sql_of(reply),
                   "free": "НЕШАБЛОННИЙ" in reply, "error": err}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            print("%-42s [%d] %5.1f с %s %s"
                  % (title, turn, rec["sec"],
                     "вільний" if rec["free"] else "шаблон ", q[:44]))
print("готово ->", OUT)
