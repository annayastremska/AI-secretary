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

2. Якщо відповідає -- виберіть із тексту НЕ БІЛЬШЕ ДВОХ речень, які містять
   саму відповідь. Скопіюйте їх ДОСЛІВНО, символ за символом, без жодних змін:
   не виправляйте розділові знаки, не скорочуйте, не додавайте слів. Ми
   перевіряємо, що ваша цитата є точним підрядком документа, і відкидаємо її,
   якщо це не так.

   ВАЖЛИВО про довгі переліки. Якщо відповідь -- це довгий перелік (підстави,
   умови, види), НЕ переписуйте весь перелік. Візьміть рядок, який його
   вводить, і не більше двох перших елементів. Повний текст користувач побачить
   окремо: ваша задача -- вказати МІСЦЕ відповіді, а не переписати документ.
   Цитата довша за 400 символів не приймається.

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


# Символи, які модель тихо «виправляє», переписуючи цитату: кручений апостроф
# у «військовозобов’язаних», різні тире, різні лапки, нерозривний пробіл.
# Без цього зведення підрядкова перевірка відкидала цілком доречні цитати --
# на позитивному наборі так згинули дві з п'яти відповідей.
CONFUSE = {
    "’": "'", "‘": "'", "`": "'", "´": "'", "ʼ": "'", "＇": "'",
    "–": "-", "—": "-", "‐": "-", "‑": "-", "−": "-", "―": "-",
    "«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "‟": '"',
    " ": " ", " ": " ", " ": " ",
}
_TRANS = str.maketrans(CONFUSE)


def norm(s):
    """Зведення для ПОРІВНЯННЯ: пробіли, апострофи, тире, лапки, регістр.

    Регістр теж: модель іноді починає цитату з великої літери там, де в
    оригіналі мала (бо «це ж початок речення»). Для перевірки «чи це справді
    з документа» регістр не має значення -- показуємо ми все одно оригінал.
    """
    s = (s or "").translate(_TRANS)
    return re.sub(r"\s+", " ", s).strip().casefold()


def lexemes(cur, text):
    """Леми тексту за тим самим українським словником, що й пошук.

    Не наївне порівняння слів: «обчислювальна» і «обчислювальної» -- одна лема,
    а рядково вони різні. Службові слова словник відкидає сам, тому окремого
    списку стоп-слів тут не треба.
    """
    cur.execute("SELECT unnest(tsvector_to_array(to_tsvector('ukrainian', %s)))",
                (text,))
    return {r[0] for r in cur.fetchall()}


def overlap(cur, question, quote):
    """Частка лем питання, присутніх у цитаті.

    Друга перевірка поверх підрядкової. Підрядкова ловить ВИГАДКУ (цитати, якої
    в документі немає), але не ловить НЕДОРЕЧНІСТЬ: на питанні «що таке
    обчислювальна система» модель видала дослівний, але сторонній перелік
    документів для експертизи -- і сказала, що це визначення.
    """
    q = lexemes(cur, question)
    # Леми, яких у корпусі немає ЖОДНОГО разу, з знаменника прибираємо: це
    # одруківки й слова поза корпусом, і вони не можуть бути в жодній цитаті.
    # Без цього питання «за скільки днів подавати рапорд на відпустак» давало
    # збіг 0.00 через ВЛАСНІ одруківки, а не через недоречність цитати.
    dropped = set()
    if q:
        # `plainto_tsquery('simple', ...)` -- бо в `tsv` уже лежать леми
        # Hunspell, і повторно стемити їх не треба, лише привести регістр.
        # Перша версія цього запиту склеювала tsquery з рядка через `||` і
        # ламалась на лапках -- `known` виходив порожнім, тому ВСІ леми
        # вважались невідомими і збіг ставав 1.00 у кожному випадку.
        cur.execute(f"""
            SELECT l FROM unnest(%s::text[]) AS l
             WHERE EXISTS (SELECT 1 FROM {SU.UNITS} u
                            WHERE u.tsv @@ plainto_tsquery('simple', l))
        """, (sorted(q),))
        known = {r[0] for r in cur.fetchall()}
        dropped = q - known
        q = known
    if not q:
        return 1.0, dropped
    a = lexemes(cur, quote)
    missing = q - a
    return len(q & a) / len(q), missing


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-overlap", type=float, default=0.5,
                    help="нижче цієї частки лем питання цитата позначається "
                         "як підозріла")
    ap.add_argument("--questions", required=True)
    ap.add_argument("--top", type=int, default=2,
                    help="скільком верхнім одиницям задавати питання")
    ap.add_argument("--rerank", type=int, default=0,
                    help="розмір пулу реранкера; 0 -- без реранкера. "
                         "Заміряно: пул 50 підняв правильну одиницю в топ-2 "
                         "з 2/5 до 4/5 за 0.57 с")
    args = ap.parse_args(argv)

    with open(args.questions, encoding="utf-8") as f:
        questions = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    from build_units_test import load_encoder, dsn
    encode = load_encoder()
    rescore = None
    if args.rerank:
        from measure_rerank_lift import load_reranker, RERANK_CHARS
        rescore = load_reranker()
    cost = {"calls": 0, "s": 0.0, "in": 0, "out": 0, "rr": 0.0}

    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        for q in questions:
            vec = str(encode(["query: " + q])[0])
            fused = SU.dedupe_by_text(
                cur, SU.rrf_merge(SU.lexical(cur, q), SU.semantic(cur, vec)),
                SU.canon_map(cur))
            if rescore and fused:
                pool = fused[:args.rerank]
                texts = [SU.quote_of(cur, d, b)[0][:RERANK_CHARS]
                         for (d, b), _m in pool]
                t0 = time.time()
                sc = rescore(q, texts)
                cost["rr"] += time.time() - t0
                order = sorted(range(len(sc)), key=lambda j: -sc[j])
                fused = [pool[j] for j in order] + fused[args.rerank:]
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
                # Зведений вигляд -- ЛИШЕ для порівняння; показуємо оригінал.
                # Інакше цитата в підвалі відповіді їде в нижньому регістрі.
                quote_raw = re.sub(r"\s+", " ", (data.get("quote") or "")).strip()
                exact = bool(quote_raw) and norm(quote_raw) in norm(body)
                quote = quote_raw
                ov, missing = (overlap(cur, q, quote_raw) if quote_raw
                               else (0.0, set()))
                verdicts.append((answers, exact))
                mark = "ВІДПОВІДАЄ" if answers else "не відповідає"
                print(f"\n  [{base[:44]}] {title[:44]}")
                print(f"    модель: {mark}   цитата дослівна: "
                      f"{'ТАК' if exact else ('НІ -- ВІДКИНУТО' if quote else 'немає')}"
                      f"   {dt:.1f} с")
                print(f"    чому: {str(data.get('why') or '')[:150]}")
                if quote_raw:
                    flag = "  ⚠ ПІДОЗРІЛА" if ov < args.min_overlap else ""
                    print(f"    збіг лем питання: {ov:.2f}{flag}"
                          + (f"   немає: {sorted(missing)[:5]}" if missing else ""))
                if quote:
                    print(f"    цитата: {quote[:220]}")
            if not any(a for a, _ in verdicts):
                print("\n  --> ВІДМОВА: жоден фрагмент не відповідає на питання")

    print(f"\n{'='*78}\nвартість: {cost['calls']} викликів, {cost['s']:.1f} с "
          f"({cost['s']/max(1,cost['calls']):.1f} с/виклик), "
          f"токенів у {cost['in']} / з {cost['out']}"
          + (f"; реранкер {cost['rr']:.1f} с усього" if cost["rr"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
