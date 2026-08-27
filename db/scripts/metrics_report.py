"""Метрики нормативного шляху й бази -- ті, що я можу ДОВЕСТИ, і в тому вигляді,
у якому їх можна поставити на сторінку.

Запуск:
    python db/scripts/metrics_report.py                    # без моделі: 5 і 6
    python db/scripts/metrics_report.py --with-llm         # усі, ~6 хв
    python db/scripts/metrics_report.py --out data/eval/normative-metrics.json

## Навіщо саме ці числа

Домовлено з Андрієм 27.08: сторінка мусить показувати ЦІННІСТЬ, а не інвентар.
«1879 фактів витягнуто» каже, скільки чогось лежить у базі, і нічого не каже
про те, чи бот комусь допоміг.

Метрика №1 («питань закрито з першого разу») тут НЕ рахується навмисно: для неї
потрібен список питань, написаний людиною, яка не крутила ні промптів, ні
каталогу, ні пошуку. Чекаємо на Колю. Усе, що я порахую сам собі, буде
підгонкою -- я на цьому вже двічі спіймався за один день.

## Правило підписів, яке тут витримується

Кожне число віддається разом із полем `means` -- рівно тим реченням, яке має
стояти на екрані, -- і полем `does_not_prove`. Причина: аудит сторінки 27.08
показав, що три з п'яти відсотків були перебільшені НЕ через помилку в запиті,
а через підпис, який обіцяв більше, ніж число означає.

## Що кожна метрика означає й чого не означає

**2. Відмова на питання поза корпусом.** Скільком питанням, відповіді на які в
корпусі немає, ланцюг сказав «не знайдено» замість вигадати. НЕ доводить, що на
решту питань відповідь правильна.

**3. Джерело, що сходиться.** Частка питань, де знайдено ДОСЛІВНУ цитату з тієї
одиниці, яку призначила істина. Дослівність перевіряється підрядком, одиниця --
відпечатком, згенерованим машиною з тексту бази. НЕ доводить, що цитата
відповідає на питання по суті: це б вимагало людського судження.

**5. Документів, яким не потрібна людина.** 204 мінус ті, що мають відкрите
завдання в черзі. НЕ означає «перевірено»: у `review_log` немає ЖОДНОГО
людського запису, тобто ніхто нічого не переглядав. Означає рівно «наш пайплайн
не позначив тут нічого підозрілого» -- та сама межа, що в звірці каталогу
(«база проти нашого ж витягу», не проти правди).

Окремо: 130 завдань `new_person` закрито з резолюцією `matched_by_roster`. Їх
закрив МІЙ СКРИПТ за правилом зі штатки, а не людина, -- у передачі Ані це
названо «закриті людиною», і це варто поправити.

**6. Прилади зелені.** Не цінність, а страховка: скільки моїх самотестів
проходить. Кожен -- на точне значення, не на діапазон (мутаційний аудит Ані
довів, що тест на діапазон не рейка).
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVAL = os.path.join(ROOT, "eval", "retrieval")


def m5_documents_without_human(cur):
    cur.execute("SELECT count(*) FROM documents")
    total = cur.fetchone()[0]
    cur.execute("""
        SELECT count(DISTINCT d.id) FROM documents d
          JOIN review_queue q ON q.document_id = d.id
         WHERE q.resolved_at IS NULL
    """)
    pending = cur.fetchone()[0]
    cur.execute("""
        SELECT count(*) FROM review_queue
         WHERE resolved_at IS NOT NULL AND resolution = 'matched_by_roster'
    """)
    closed_by_rule = cur.fetchone()[0]
    cur.execute("""
        SELECT count(*) FROM review_log
         WHERE changed_by NOT IN ('ai_secretary_loader', 'dedupe_existing_facts',
                                  'reconcile_roster_status')
    """)
    human_edits = cur.fetchone()[0]
    return {
        "value": total - pending,
        "of": total,
        "means": f"{total - pending} із {total} документів внесено так, що "
                 "черга не має до них жодного питання",
        "does_not_prove": "НЕ означає «людина перевірила»: у журналі змін немає "
                          f"жодного людського запису ({human_edits}). Означає "
                          "лише, що наш пайплайн не позначив тут нічого "
                          "підозрілого -- та сама межа, що в звірці каталогу.",
        "detail": {"documents_with_open_task": pending,
                   "queue_closed_by_rule_not_human": closed_by_rule,
                   "human_edits_in_review_log": human_edits},
    }


def m6_instruments(dsn):
    """Самотести, кожен на точне значення. Модель не потрібна."""
    checks = [
        ("резолвер номерів документів",
         [sys.executable, os.path.join("db", "scripts", "resolve_identifier.py"),
          "--self-test"]),
        ("хронологія витіснення фактів",
         [sys.executable, os.path.join("db", "scripts",
                                       "test_insert_fact_chronology.py")]),
    ]
    results = []
    for name, cmd in checks:
        try:
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                               timeout=600, encoding="utf-8", errors="replace")
            ok = r.returncode == 0
        except (subprocess.TimeoutExpired, OSError) as exc:
            ok, r = False, None
            print(f"   {name}: не запустився ({exc})")
        results.append({"name": name, "ok": bool(ok)})
        print(f"   {'OK    ' if ok else 'ПРОВАЛ'} {name}")
    green = sum(1 for x in results if x["ok"])
    return {
        "value": green,
        "of": len(results),
        "means": f"{green} із {len(results)} приладів бази проходить",
        "does_not_prove": "не про якість відповідей: це страховка, що вчорашнє "
                          "не зламалось. Кожен тест -- на точне значення, не на "
                          "діапазон.",
        "detail": {"checks": results},
    }


def m7_chat_dates():
    """Чи ще живий дефект дат у чаті -- окремим індикатором, не «приладом».

    Це не мій код і не моя якість: це стан ЇЇ файла. Але число тут доречне, бо
    воно єдине на сторінці показує, чи застосовано патч: до застосування
    «з 2026-05-10 по 2026-10-10» дає зріз на першій даті (сім разів із семи в
    перевірці перед демо), після -- період.

    Перевіряється проти ЖИВОГО файла, не проти гілки: на сторінці має стояти
    стан того, що зараз відповідає людям.
    """
    tiers = os.path.expanduser(
        "~/anya/ai-secretary/demos/upload_app/chat_gradio/tiers.py")
    if not os.path.exists(tiers):
        return {"value": None, "of": 8,
                "means": "живий файл чата недоступний -- не перевірено",
                "does_not_prove": "", "detail": {"tiers_path": tiers}}
    r = subprocess.run(
        [sys.executable, os.path.join("db", "scripts", "test_extract_dates.py"),
         "--tiers", tiers],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
        encoding="utf-8", errors="replace")
    ok_count = r.stdout.count("OK  ")
    fixed = r.returncode == 0
    print(f"   {'ВИПРАВЛЕНО' if fixed else 'ЩЕ ЖИВИЙ'}: {ok_count} із 8 "
          "випадків розбирається правильно")
    return {
        "value": ok_count, "of": 8,
        "means": (f"{ok_count} із 8 способів написати дату чат розбирає "
                  "правильно" + ("" if fixed else " -- патч ще не застосований")),
        "does_not_prove": "перевіряються рівно ті вісім випадків, що були у "
                          "звіті перед демо, а не всі можливі формати дат.",
        "detail": {"patch_applied": fixed},
    }


def _read_set(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            p = line.rstrip("\n").split("\t")
            rows.append(p)
    return rows


def m2_and_m3(args):
    """Обидві метрики через один ланцюг: видача -> реранкер -> ворота."""
    import quote_with_llm_test as G
    import resolve_identifier as R
    import search_units_test as SU
    from build_units_test import dsn, load_encoder
    from measure_rerank_lift import RERANK_CHARS, load_reranker

    encode, rescore = load_encoder(), load_reranker()

    off_topic = [p[1] for p in _read_set(os.path.join(EVAL, "abstain_v2.tsv"))
                 if p[0] == "off_topic"]
    truth = []
    for name in ("truth_units_v2.tsv", "truth_units_terms.tsv"):
        path = os.path.join(EVAL, name)
        if os.path.exists(path):
            truth += [(p[0], int(p[1]), p[2]) for p in _read_set(path)
                      if len(p) >= 3]

    import ab_embedding_models as AB

    def chain(cur, q, top=2):
        """-> (список (одиниця, цитата_дослівна, модель_каже_є))."""
        res = R.resolve(cur, q)
        if res["status"] == "absent":
            return []
        docs = ([d["id"] for d in res["documents"]]
                if res["status"] == "resolved" else None)
        sq = res.get("rest") or q
        vec = str(encode(["query: " + sq])[0])
        fused = SU.dedupe_by_text(
            cur, SU.rrf_merge(SU.lexical(cur, sq, docs=docs),
                              SU.semantic(cur, vec, docs=docs)),
            SU.canon_map(cur))
        if fused:
            pool = fused[:50]
            texts = [SU.quote_of(cur, d, b)[0][:RERANK_CHARS]
                     for (d, b), _m in pool]
            sc = rescore(q, texts)
            order = sorted(range(len(sc)), key=lambda j: -sc[j])
            fused = [pool[j] for j in order] + fused[50:]
        out = []
        cache = {}
        for (doc_id, base), _meta in fused[:top]:
            title, ident = SU.identity(cur, doc_id, cache)
            body, _w, _t = SU.quote_of(cur, doc_id, base)
            data, _u, _dt, _raw, truncated = G.ask(
                q, title[:70], ident, base[:60], body)
            if truncated:
                continue
            quote = (data.get("quote") or "").strip()
            exact = bool(quote) and G.norm(" ".join(quote.split())) in G.norm(body)
            out.append(((doc_id, base), exact, bool(data.get("answers"))))
        return out

    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL hnsw.ef_search = 200")
        print("\n2. відмова на питання поза корпусом")
        refused = 0
        for q in off_topic:
            got = chain(cur, q)
            said_yes = any(a for _u, _e, a in got)
            refused += not said_yes
            print(f"   {'відмовився' if not said_yes else 'ВІДПОВІВ (погано)'}"
                  f"  {q[:62]}")

        print("\n3. джерело, що сходиться")
        ok3 = 0
        for q, doc_id, needle in truth:
            ids = AB.correct_units(cur, doc_id, needle)
            got = chain(cur, q)
            hit = False
            for unit, exact, answers in got:
                if not (exact and answers):
                    continue
                if _unit_ids(cur, unit) & ids:
                    hit = True
                    break
            ok3 += hit
            print(f"   {'OK    ' if hit else 'ні    '} {q[:62]}")

    m2 = {
        "value": refused, "of": len(off_topic),
        "means": f"на {refused} із {len(off_topic)} питань поза корпусом "
                 "система сказала «не знайдено» замість вигадати",
        "does_not_prove": "не доводить, що на решту питань відповідь правильна.",
    }
    m3 = {
        "value": ok3, "of": len(truth),
        "means": f"для {ok3} із {len(truth)} питань знайдено ДОСЛІВНУ цитату з "
                 "тієї одиниці документа, яку призначила істина",
        "does_not_prove": "не доводить, що цитата відповідає на питання по "
                          "суті: це вимагало б людського судження. Доведено "
                          "лише дослівність (підрядок оригіналу) і те, що "
                          "одиниця та сама.",
    }
    return m2, m3


def _unit_ids(cur, key):
    """id одиниць логічного уривка (документ, мітка)."""
    doc_id, base = key
    cur.execute("SELECT id FROM document_units WHERE document_id = %s "
                "AND base_label = %s", (doc_id, base))
    return {r[0] for r in cur.fetchall()}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--with-llm", action="store_true",
                    help="рахувати метрики 2 і 3 (потрібна піднята модель)")
    ap.add_argument("--out", default="")
    ap.add_argument("--today", default=datetime.date.today().isoformat())
    args = ap.parse_args()

    from build_units_test import dsn
    report = {"measured_at": args.today, "metrics": {}}

    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        print("5. документів, яким не потрібна людина")
        m5 = m5_documents_without_human(cur)
        print(f"   {m5['value']} із {m5['of']}")
        report["metrics"]["documents_without_human"] = m5

    print("\n6. прилади бази")
    report["metrics"]["instruments_green"] = m6_instruments(dsn())

    print("\n7. дефект дат у чаті")
    report["metrics"]["chat_date_defect"] = m7_chat_dates()

    if args.with_llm:
        m2, m3 = m2_and_m3(args)
        report["metrics"]["refused_outside_corpus"] = m2
        report["metrics"]["source_checks_out"] = m3
    else:
        print("\n2 і 3 пропущено (нема --with-llm)")

    print(f"\n{'=' * 70}")
    for key, m in report["metrics"].items():
        print(f"{key}: {m['value']}/{m['of']}")
        print(f"   на екран: {m['means']}")
        print(f"   не доводить: {m['does_not_prove']}")

    if args.out:
        path = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"\nзаписано {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
