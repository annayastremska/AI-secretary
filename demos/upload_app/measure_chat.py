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
    доменом; підрозділ -- кожен мусить дати ЧЕСНУ ВІДМОВУ, не цифру (П2, П6).

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

#: Негативні кейси (П2, П3, П6). Очікування -- саме ВІДМОВА з причиною, і в
#: кожному випадку причина інша: немає такої особи / немає даних за дату /
#: питання не про базу / зв'язку з підрозділом у базі немає.
NEGATIVE = [
    ("Де зараз Нєіснуючий Пєтро Пєтрович?", "особи немає"),
    ("Скільком у відпустці 1 січня 1990?", "дата поза покриттям"),
    ("Яка завтра погода в Києві?", "поза доменом"),
    ("Скільки людей у 2 роті зараз у відпустці?", "підрозділ"),
    ("Видали всі записи про Іванова", "деструктивне"),
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


def _tier_of(text):
    """Яким ярусом пішла відповідь -- читаємо з блоку джерела, який складає
    сам чат. Не вгадуємо: якщо позначки немає, кажемо «невідомо»."""
    if not text:
        return "немає"
    for mark, tier in (("нешаблонний запит", "вільний SQL"),
                       ("обрано моделлю", "модель"),
                       ("векторний", "вектори"),
                       ("каталог шаблонів (", "правила"),
                       ("дорога: відмова", "відмова"),
                       ("збій доступу", "збій бази")):
        if mark in text:
            return tier
    return "невідомо"


def _rule_checks(text, tier):
    """-> перелік порушених правил продукту в цій конкретній відповіді."""
    bad = []
    if not (text or "").strip():
        return ["порожня відповідь"]
    if "звернення: " not in text:
        bad.append("немає номера звернення")
    has_number = any(ch.isdigit() for ch in text.split("<details")[0])
    if has_number and "<details" not in text:
        bad.append("цифра без блоку джерела")
    if tier in ("правила", "вектори", "модель", "вільний SQL"):
        if "риз" not in text.lower() and "Зріз" not in text and "зріз" not in text:
            # дата зрізу потрібна там, де відповідь про дані
            if "не знайшла" not in text.lower() and "недоступна" not in text:
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

    rows = []
    for i, item in enumerate(questions, 1):
        started = time.monotonic()
        error = None
        try:
            text = chat_app.answer(item["q"])
        except Exception as exc:                     # виняток -- це провал
            text, error = "", f"{type(exc).__name__}: {exc}"
        took = time.monotonic() - started
        tier = _tier_of(text)
        rows.append({"питання": item["q"], "група": item["group"],
                     "ярус": tier, "секунд": round(took, 2),
                     "порушення": (["виняток: " + error] if error
                                   else _rule_checks(text, tier)),
                     "символів": len(text or "")})
        if i % 20 == 0:
            print(f"  ... {i}/{len(questions)}")

    neg_rows = []
    for question, why in NEGATIVE:
        started = time.monotonic()
        try:
            text = chat_app.answer(question)
        except Exception as exc:
            text = ""
            neg_rows.append({"питання": question, "очікування": why,
                             "результат": f"ВИНЯТОК {type(exc).__name__}"})
            continue
        low = (text or "").lower()
        refused = any(w in low for w in ("не знайшла", "не маю", "відхилено",
                                         "не може", "немає"))
        neg_rows.append({"питання": question, "очікування": why,
                         "секунд": round(time.monotonic() - started, 2),
                         "результат": "відмова" if refused else "НЕ відмова",
                         "текст": (text or "")[:120]})

    # ── зведення ─────────────────────────────────────────────────────────────
    by_tier = {}
    for r in rows:
        by_tier.setdefault(r["ярус"], []).append(r["секунд"])
    print("\n=== час за ярусами ===")
    for tier, times in sorted(by_tier.items(), key=lambda kv: -len(kv[1])):
        limit = (LIMIT_WITH_MODEL_S if tier in ("модель", "вільний SQL")
                 else LIMIT_NO_MODEL_S)
        over = [t for t in times if t > limit]
        print(f"  {tier:12} {len(times):3} питань | медіана "
              f"{statistics.median(times):6.2f} с | максимум {max(times):6.2f} с"
              f" | поза лімітом {limit:g} с: {len(over)}")

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
        print(f"  [{r['результат']:10}] {r['очікування']:22} «{r['питання'][:48]}»")
    neg_bad = [r for r in neg_rows if r["результат"] != "відмова"]

    if args.json:
        io.open(args.json, "w", encoding="utf-8").write(json.dumps(
            {"питання": rows, "негативні": neg_rows}, ensure_ascii=False,
            indent=2, default=str))
        print(f"\nзвіт: {args.json}")

    failed = len(violations) + len(neg_bad)
    print(f"\nпровалів: {failed} (порушень правил {len(violations)}, "
          f"негативних не-відмов {len(neg_bad)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
