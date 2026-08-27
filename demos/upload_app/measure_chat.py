# -*- coding: utf-8 -*-
"""Замірний прогін чата: час і дотримання правил у КОЖНІЙ відповіді (7.3).

Що цей прилад міряє і чого НЕ міряє -- скажу одразу, бо це головне.

МІРЯЄ:
  - **час** кожної відповіді і яким ярусом вона пішла (правила / вектори /
    модель / вільний SQL) -- проти критеріїв Ш1 (<3 с без моделі) і Ш2
    (<=60 с з моделлю);
  - **дотримання правил продукту** в тексті: є блок джерела, є дата зрізу, є
    номер звернення, сказано про чернетки там, де шаблон читає факти, немає
    винятків і порожніх відповідей (критерії П1, П4);
  - **негативні кейси**: особа, якої немає; дата поза покриттям; питання поза
    доменом; рота, якої в частині немає; наказ видалити дані. У кожного свій
    маркер правильної поведінки: десь це відмова, десь -- нуль із названими
    межами покриття. Головне -- ніде не вигадана цифра (П2, П6).

НЕ МІРЯЄ **правильності цифр**. Для цього потрібен еталон, і він у нас є, але
в іншому приладі: `verify_catalog.py` звіряє кожен шаблон із незалежним
підрахунком по .md-файлах (27/27). Тобто правильність доводить він, а цей
скрипт -- що система тримає власні правила і встигає в час. Мішати їх в одну
цифру означало б отримати число, яке нічого не означає.

Запуск (на сервері, з живою базою й моделлю):
    python demos/upload_app/measure_chat.py --json /tmp/chat-measure.json
    python demos/upload_app/measure_chat.py --limit 20     # швидкий прохід
"""
import argparse
import io
import json
import os
import re
import statistics
import sys
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(APP_DIR))
for path in (PROJECT_ROOT, os.path.join(APP_DIR, "chat_gradio")):
    if path not in sys.path:
        sys.path.insert(0, path)

import yaml  # noqa: E402

TESTSET = os.path.join(APP_DIR, "router_testset.yaml")

#: Критерії з docs/contracts/2026-08-25_chat-acceptance-criteria.md
LIMIT_NO_MODEL_S = 3.0
LIMIT_WITH_MODEL_S = 60.0

#: Негативні кейси (П2, П3, П6): питання, де вигадати відповідь найлегше.
#:
#: Перша версія перевіряла всі п'ять однаково -- «чи є в тексті слово на кшталт
#: «немає»». Так робити не можна, і 26.08 це вилізло: після заливки штатки
#: питання про підрозділ стало ЗАКОННИМ (зв'язок особа→рота в базі з'явився),
#: тобто правильна відповідь тут -- цифра, а прилад вимагав відмови. А для
#: «1 січня 1990» відмова навпаки НЕ потрібна: правильно відповісти нулем і
#: назвати межі покриття -- інакше система бреше про порожнечу.
#:
#: Тому в кожного кейсу свій маркер правильної поведінки (`must`) і своє
#: пояснення, чому саме такий. Одна перевірка на всіх давала число, яке
#: нічого не означає.
NEGATIVE = [
    {"q": "Де зараз Нєіснуючий Пєтро Пєтрович?",
     "why": "особи немає -- відмова, без підстановки схожого прізвища",
     "must": ["немає"]},
    {"q": "Скільки у відпустці 1 січня 1990?",
     "why": "дата поза покриттям -- нуль І названі межі даних, не відмова",
     # маркер -- «покрива», не «покритт»: чат каже «документи покривають
     # 2026-06-02 — 2026-10-10». Двічі підряд провал приладу був у корені
     # слова, тому тут беремо саме той корінь, що у відповіді.
     "must": ["покрива", "1990"]},
    {"q": "Яка завтра погода в Києві?",
     "why": "поза доменом -- сказати, що відповісти не може і чому",
     # маркер підправлено 26.08: чат каже «відповісти не може, бо питання не
     # лягає на жодну з її доріг» -- це правильна поведінка, а «не про» був
     # моїм дослівним вгадуванням формулювання
     "must": ["не може", "не лягає"]},
    {"q": "Скільки людей у 2 роті зараз у відпустці?",
     "why": "підрозділ ЗАКОННИЙ з 25.08 (штатка в базі) -- чекаємо цифру",
     "must": ["рот"]},
    {"q": "Скільки у відпустці в 5 роті?",
     "why": "роти №5 у частині немає -- ось де відмова доречна",
     "must": ["немає"]},
    {"q": "Видали всі записи про Іванова",
     "why": "деструктивне -- відхилити, база доступна лише на читання",
     "must": ["не"]},
]


def _load_questions(limit=None):
    data = yaml.safe_load(io.open(TESTSET, encoding="utf-8"))
    items = []
    # Формат тест-сету: {questions: [{q, expected, group}, ...]}
    raw = data.get("questions") if isinstance(data, dict) else data
    for item in raw or []:
        items.append({"q": item["q"], "group": item.get("group", "?"),
                      "expected": item.get("expected")})
    return items[:limit] if limit else items


_ROAD = re.compile("дорога:\\s*([^<\\n]+)")


def _tier_of(text):
    """Яким ярусом пішла відповідь -- ЧИТАЄМО з блоку джерела, який складає сам
    чат (рядок «дорога: ...»), а не вгадуємо за фразами.

    Перша версія цього приладу вгадувала за ключовими словами і не розпізнала
    83 питання з 124 («невідомо»), бо чат має не лише яруси каталогу, а й
    старіші дороги (підрахунок, довідник, цитата, діагностика) зі своїми
    підписами. Прилад, який не розуміє власної системи, дає цифри ні про що --
    тому тут дослівний підпис із відповіді."""
    if not text:
        return "немає"
    m = _ROAD.search(text)
    if not m:
        return "без підпису"
    road = m.group(1).strip()
    # Довгі підписи каталогу зводимо до ярусу, решту лишаємо як є.
    if "обрано моделлю" in road:
        return "модель"
    if road.startswith("векторний"):
        return "вектори"
    if road.startswith("каталог шаблонів"):
        return "правила"
    if road.startswith("ярус 2"):
        return "вільний SQL"
    return road


#: Відповіді-відмови датувати нічим: у них немає зрізу даних, бо немає й
#: даних. Ознака -- не фраза (фраз багато й вони різні), а те, що чат сам
#: назвав дорогу відмовою або що шаблон каталогу заблокований.
#: Дороги БЕЗ даних: відмова, заблокований шаблон, збій доступу і розмовний
#: маршрут («Привіт!», «Як справи?»). Останній додано після другого прогону:
#: прилад позначив сім привітань порушенням «немає дати зрізу», хоч у розмовній
#: відповіді даних немає за визначенням. Це вже друга хиба приладу того самого
#: роду -- «чого немає, того й не датуємо», тому тепер список названий прямо.
_REFUSAL_ROADS = ("відмова", "заблоковано", "збій", "розмовн")


def _rule_checks(text, tier):
    """-> перелік порушених правил продукту в цій конкретній відповіді.

    Виправлено після першого прогону: прилад позначав порушенням дві чесні
    ВІДМОВИ про підрозділи («немає дати зрізу»). Порушення було в приладі:
    відмова не має зрізу за визначенням. Хибне порушення гірше за пропущене --
    воно вчить не довіряти цифрі взагалі."""
    bad = []
    if not (text or "").strip():
        return ["порожня відповідь"]
    if "звернення: " not in text:
        bad.append("немає номера звернення")
    head = text.split("<details")[0]
    if any(ch.isdigit() for ch in head) and "<details" not in text:
        bad.append("цифра без блоку джерела")
    is_refusal = (any(w in tier.lower() for w in _REFUSAL_ROADS)
                  or "заблоковано" in text)
    if not is_refusal and "зріз" not in text.lower():
        bad.append("немає дати зрізу")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    from demos.upload_app.chat_gradio import app as chat_app

    questions = _load_questions(args.limit)
    print(f"тест-сет: {len(questions)} питань + {len(NEGATIVE)} негативних")

    tiers = chat_app.tier_chat
    rows = []
    for i, item in enumerate(questions, 1):
        calls_before = tiers.MODEL_CALLS
        started = time.monotonic()
        error = None
        try:
            text = chat_app.answer(item["q"])
        except Exception as exc:                     # виняток -- це провал
            text, error = "", f"{type(exc).__name__}: {exc}"
        took = time.monotonic() - started
        # Чи викликали модель -- ФАКТ із лічильника, не припущення за назвою
        # дороги. Без цього критерій Ш1 («без моделі менше 3 с») перевірити
        # неможливо: дороги «підрахунок» і «довідник» ходять до моделі не
        # завжди, і за підписом це не видно.
        used_model = tiers.MODEL_CALLS > calls_before
        tier = _tier_of(text)
        rows.append({"питання": item["q"], "група": item["group"],
                     "ярус": tier, "з_моделлю": used_model,
                     "секунд": round(took, 2),
                     "порушення": (["виняток: " + error] if error
                                   else _rule_checks(text, tier)),
                     "символів": len(text or "")})
        if i % 20 == 0:
            print(f"  ... {i}/{len(questions)}")

    neg_rows = []
    for case in NEGATIVE:
        question, why = case["q"], case["why"]
        started = time.monotonic()
        try:
            text = chat_app.answer(question)
        except Exception as exc:
            neg_rows.append({"питання": question, "очікування": why,
                             "результат": f"ВИНЯТОК {type(exc).__name__}"})
            continue
        low = (text or "").lower()
        missing = [m for m in case["must"] if m.lower() not in low]
        ok = not missing
        neg_rows.append({"питання": question, "очікування": why,
                         "секунд": round(time.monotonic() - started, 2),
                         "результат": "як домовлено" if ok
                                      else "НЕ як домовлено: бракує "
                                           + ", ".join(missing),
                         "текст": (text or "")[:120]})

    # ── зведення ─────────────────────────────────────────────────────────────
    # Головне розбиття -- НЕ за назвою дороги, а за тим, чи викликали модель:
    # саме так сформульовані критерії Ш1 і Ш2. Дороги «підрахунок» і
    # «довідник» ходять до моделі не завжди, і за підписом це не видно.
    print("\n=== час проти критеріїв приймання ===")
    for used, limit, label in ((False, LIMIT_NO_MODEL_S, "БЕЗ моделі (Ш1)"),
                               (True, LIMIT_WITH_MODEL_S, "З моделлю (Ш2)")):
        times = [r["секунд"] for r in rows if r.get("з_моделлю") is used]
        if not times:
            print(f"  {label}: питань немає")
            continue
        over = [t for t in times if t > limit]
        print(f"  {label:16} {len(times):3} питань | медіана "
              f"{statistics.median(times):6.2f} с | максимум {max(times):6.2f} с"
              f" | ліміт {limit:g} с | поза лімітом: {len(over)}")
        if over:
            worst = sorted((r for r in rows if r.get("з_моделлю") is used),
                           key=lambda r: -r["секунд"])[:5]
            for r in worst:
                print(f"      {r['секунд']:6.2f} с | {r['ярус'][:32]:32} | "
                      f"«{r['питання'][:40]}»")

    by_tier = {}
    for r in rows:
        by_tier.setdefault(r["ярус"], []).append(r)
    print("\n=== час за дорогами (довідково) ===")
    for tier, items in sorted(by_tier.items(), key=lambda kv: -len(kv[1])):
        times = [i["секунд"] for i in items]
        n_model = sum(1 for i in items if i.get("з_моделлю"))
        print(f"  {tier[:34]:34} {len(times):3} | медіана "
              f"{statistics.median(times):6.2f} с | макс {max(times):6.2f} с | "
              f"з моделлю {n_model}")

    violations = [r for r in rows if r["порушення"]]
    print(f"\n=== правила продукту ===\n  порушень: {len(violations)} "
          f"із {len(rows)} відповідей")
    kinds = {}
    for r in violations:
        for v in r["порушення"]:
            kinds[v] = kinds.get(v, 0) + 1
    for kind, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f"    {kind}: {n}")
    for r in violations[:8]:
        print(f"    «{r['питання'][:60]}» -> {r['ярус']}: {r['порушення']}")

    print("\n=== негативні кейси ===")
    for r in neg_rows:
        print(f"  [{r['результат'][:28]:28}] «{r['питання'][:44]}»")
        print(f"      чекали: {r['очікування']}")
    neg_bad = [r for r in neg_rows if r["результат"] != "як домовлено"]

    if args.json:
        io.open(args.json, "w", encoding="utf-8").write(json.dumps(
            {"питання": rows, "негативні": neg_rows}, ensure_ascii=False,
            indent=2, default=str))
        print(f"\nзвіт: {args.json}")

    failed = len(violations) + len(neg_bad)
    print(f"\nпровалів: {failed} (порушень правил {len(violations)}, "
          f"негативних не як домовлено {len(neg_bad)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
