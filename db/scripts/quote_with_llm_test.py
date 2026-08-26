"""Чи відповідає знайдена одиниця на питання, і яка саме цитата -- через модель.

Запуск (модель піднята: bash db/scripts/start_local_llm.sh):
    python db/scripts/quote_with_llm_test.py --questions eval/retrieval/probe5.txt

## Дві задачі, які тут віддані моделі, і чому саме ці

Одруківки НЕ віддані: заміряно, що вектор на них майже не реагує (косинус
0.8553 з одруківками проти 0.8607 без -- падіння 0.005), тому нормалізація
запиту моделлю нічого не купує.

Віддані натомість:

1. **«Чи відповідає взагалі»** -- те, що косинусом робиться погано. Градієнт
   у косинуса є (є відповідь 0.887-0.909, немає 0.855-0.861, не по темі
   0.798-0.827), але зазор між «немає відповіді» і «не по темі» лише 0.03, а
   поріг мусить стояти близько 0.87. Модель читає текст і може сказати «тут
   про інше» без калібрування.

2. **Точна цитата** -- витягнути з одиниці саме те речення, що відповідає.
   Одиниця буває на 2000 символів, а норма в ній -- один рядок.

## Чому це надійний режим, а не вгадування

Обидві задачі -- «прочитай наданий текст і скажи про нього», а не «згадай». І
головне: **цитату можна перевірити механічно** -- вона мусить бути дослівним
підрядком джерела. Якщо ні, ми її ВІДКИДАЄМО, а не показуємо. Тобто галюцинація
тут ловиться перевіркою, а не довірою до моделі.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "db", "scripts"))

import search_units_test as SU  # noqa: E402
import extract_document_identity as E  # noqa: E402

LLM = os.environ.get("LLM_URL", "http://127.0.0.1:8081/v1/chat/completions")

SYSTEM = (
    "Ти працюєш із нормативними документами Збройних Сил України. "
    "Ти НЕ переказуєш і НЕ додумуєш: ти або знаходиш у наданому тексті "
    "дослівну відповідь, або кажеш, що її там немає. "
    "Відповідай лише JSON, без пояснень поза ним."
)

USER = """Питання військовослужбовця:
{question}

Нижче -- фрагмент нормативного документа, знайдений пошуком. Він МОЖЕ бути
не про те: пошук помиляється.

--- ДОКУМЕНТ: {title} ({ident}), {addr} ---
{body}
--- КІНЕЦЬ ФРАГМЕНТА ---

Зроби дві речі.

1. Вирішіть, чи цей фрагмент справді відповідає на питання. Критерій строгий:
   відповідає, тільки якщо в тексті є САМЕ те, що запитали (строк, число, хто
   саме, який порядок). Якщо текст про сусідню тему, про інший вид відпустки,
   про інший орган -- це НЕ відповідь.

2. Якщо відповідає -- виберіть із тексту речення (одне-три), які містять
   відповідь. Скопіюйте їх ДОСЛІВНО, символ за символом, без жодних змін:
   не виправляйте розділові знаки, не скорочуйте, не додавайте слів. Ми
   перевіряємо, що ваша цитата є точним підрядком документа, і відкидаємо її,
   якщо це не так.

Поверни рівно такий JSON:
{{"answers": true|false, "why": "<коротко, чому саме так>", "quote": "<дослівна цитата або порожньо>"}}"""


def ask(question, title, ident, addr, body, max_tokens=900):
    payload = json.dumps({
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER.format(
                question=question, title=title, ident=ident, addr=addr, body=body)},
        ],
        "temperature": 0, "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(LLM, data=payload,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    r = json.load(urllib.request.urlopen(req, timeout=300))
    txt = r["choices"][0]["message"]["content"]
    usage = r.get("usage", {})
    fin = r["choices"][0].get("finish_reason")
    m = re.search(r"\{.*\}", txt, re.S)
    try:
        data = json.loads(m.group()) if m else {}
    except Exception:
        data = {}
    # Обрізаний вивід -> порожній розбір -> answers=false, тобто ТИХА ВІДМОВА
    # з правильної одиниці. Саме так «які підстави для звільнення» отримало
    # відмову при знайденій статті 26/5. Тепер це видно окремим станом, а не
    # прикидається судженням моделі.
    truncated = fin == "length" or (not data and txt.strip())
    return data, usage, time.time() - t0, txt, truncated


def norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--top", type=int, default=2,
                    help="скільком верхнім одиницям задавати питання")
    args = ap.parse_args(argv)

    with open(args.questions, encoding="utf-8") as f:
        questions = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    from build_units_test import load_encoder, dsn
    encode = load_encoder()
    cost = {"calls": 0, "s": 0.0, "in": 0, "out": 0}

    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        for q in questions:
            vec = str(encode(["query: " + q])[0])
            fused = SU.rrf_merge(SU.lexical(cur, q), SU.semantic(cur, vec))
            print(f"\n{'='*78}\nПИТАННЯ: {q}")
            if not fused:
                print("  пошук нічого не дав")
                continue
            cache, verdicts = {}, []
            for (doc_id, base), meta in fused[:args.top]:
                title, ident = SU.identity(cur, doc_id, cache)
                body, was_split, trimmed = SU.quote_of(cur, doc_id, base)
                data, usage, dt, raw, truncated = ask(q, title[:70], ident,
                                                      base[:60], body)
                cost["calls"] += 1
                cost["s"] += dt
                cost["in"] += usage.get("prompt_tokens", 0)
                cost["out"] += usage.get("completion_tokens", 0)

                answers = bool(data.get("answers"))
                if truncated:
                    print(f"\n  [{base[:44]}] ⚠ ВИВІД ОБРІЗАНО -- це не вердикт "
                          f"моделі, а збій розбору ({dt:.1f} с)")
                    verdicts.append((None, False))
                    continue
                quote = norm(data.get("quote"))
                # ГОЛОВНА перевірка: цитата мусить бути дослівним підрядком.
                exact = bool(quote) and quote in norm(body)
                verdicts.append((answers, exact))
                mark = "ВІДПОВІДАЄ" if answers else "не відповідає"
                print(f"\n  [{base[:44]}] {title[:44]}")
                print(f"    модель: {mark}   цитата дослівна: "
                      f"{'ТАК' if exact else ('НІ -- ВІДКИНУТО' if quote else 'немає')}"
                      f"   {dt:.1f} с")
                print(f"    чому: {norm(data.get('why'))[:150]}")
                if quote:
                    print(f"    цитата: {quote[:220]}")
            if not any(a for a, _ in verdicts):
                print("\n  --> ВІДМОВА: жоден фрагмент не відповідає на питання")

    print(f"\n{'='*78}\nвартість: {cost['calls']} викликів, {cost['s']:.1f} с "
          f"({cost['s']/max(1,cost['calls']):.1f} с/виклик), "
          f"токенів у {cost['in']} / з {cost['out']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
