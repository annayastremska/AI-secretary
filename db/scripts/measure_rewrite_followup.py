"""Що надійніше: дати моделі розмову чи дати їй уже розгорнуте питання.

Запуск (модель піднята):
    git show origin/anya-pipeline:demos/upload_app/query_catalog.yaml > /tmp/qc.yaml
    python db/scripts/measure_rewrite_followup.py --today 2026-08-27

## Питання

Заміряно, що передати маршрутизаторові розмову дає 17/21 замість 12/21. Але є
другий спосіб, поширений у практиці (LangChain `create_history_aware_retriever`,
LlamaIndex `CondenseQuestionChatEngine`, mnemostack `rewrite_followup`):
спершу одним викликом переписати репліку в САМОДОСТАТНЄ питання, і лише потім
маршрутизувати -- уже без історії.

    «а хто?»  ->  «Хто у відпустці на 2026-10-10?»  ->  маршрутизатор

У переписування є структурна перевага, якої немає в «дати розмову»: воно лікує
не лише маршрутизатор, а й ПРАВИЛОВИЙ шар. `extract_state("а хто?")` дає None
(слова про відпустку немає), і чат віддає питання у вільний_sql -- тому навіть
21/21 на маршрутизації не зробив би «а хто?» робочим. На розгорнутому питанні
ті самі правила спрацюють.

Ціна -- другий виклик моделі: ~2.4 с додатково.

## Чому це не привід тягнути залежність

Перевірено: `mnemostack/recall/followup.py` існує (Apache 2.0), але це 4.6 КБ --
промпт, один виклик, try/except і лічильник метрик. Плюс 7 зірок і англійський
промпт, який нам однаково писати свій. Тому тут узята ІДЕЯ, а не пакет:
`rewrite()` нижче -- ті самі 30 рядків, і вони під нашу мову.

## Що робить порівняння чесним

Історія в обох режимах ОДНАКОВА -- та сама `turn_line()`, що в `gold`. Отже
різниця відноситься до способу використання історії, а не до різного входу.

Обидва набори:
* `followups.tsv` -- 21 хід, на ньому я вже двічі підганяв каталог і промпт;
* `followups_held_out.tsv` -- питання ДОСЛІВНО зі звіту перед демо, я їх не
  бачив, коли щось налаштовував. Це набір, на якому число щось значить.
"""
import argparse
import contextlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml  # noqa: E402

import measure_followup_route as M  # noqa: E402
from measure_catalog_variants import NEW, OLD  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ПЕРША ВЕРСІЯ ЦЬОГО ПРОМПТА ПРОВАЛИЛАСЬ, і провал був мій, не моделі. У
# прикладі стояло «Хто у відпустці на цю саму дату?» з припискою «з
# підставленою датою» -- модель скопіювала ЗАГОТОВКУ й приписку
# проігнорувала. На виході виходило «Хто у відрядженні на цю саму дату?»,
# маршрутизатор дати не бачив і падав на «сьогодні».
#
# Звідси два правила нижче, яких не було: заборона слів-заготовок і приклад,
# у якому значення ПІДСТАВЛЕНЕ, а не назване. Дефект видно з самого виводу,
# без звірки з очікуваннями: «на цю саму дату» не є самодостатнім питанням за
# визначенням задачі.
REWRITE_SYSTEM = (
    "Ти переписуєш останню репліку розмови в САМОДОСТАТНЄ питання -- таке, що "
    "зрозуміле без попередніх ходів.\n"
    "Правила:\n"
    "1. Підставляй САМІ ЗНАЧЕННЯ, а не посилання на них. Дату пиши як "
    "2026-10-10, підрозділ називай, стан називай.\n"
    "2. ЗАБОРОНЕНО писати «ця сама дата», «той самий підрозділ», «як раніше», "
    "«те саме» і будь-які подібні заготовки. Якщо значення нема звідки взяти "
    "-- не згадуй його зовсім.\n"
    "3. Якщо остання репліка лише ЗВУЖУЄ попереднє питання (називає підрозділ "
    "чи іншу дату), збережи стан і метрику попереднього ходу.\n"
    "4. Не додавай нічого, чого не було в розмові. Не відповідай на питання.\n"
    "5. Якщо репліка вже самодостатня -- поверни її БЕЗ ЗМІН, слово в слово.\n"
    "6. Якщо остання репліка про іншу тему, ніж попередні, -- не переноси з "
    "них ні дати, ні підрозділу, ні стану.\n"
    "7. Одне речення українською. Нічого, крім самого питання.\n"
    "\n"
    "Приклад.\n"
    "Розмова: [1] Скільки осіб у відпустці 2026-10-10?\n"
    "Остання репліка: А у відрядженні?\n"
    "Самодостатнє питання: Скільки осіб у відрядженні 2026-10-10?"
)


def rewrite(history, utterance, today, cost):
    """-> (нове питання, секунди). Ніколи не падає: при будь-якій помилці або
    порожньому виводі віддається оригінал. Переписування не має права зробити
    гірше, ніж було, лише не допомогти."""
    if not history:
        return utterance, 0.0
    user = ("Розмова:\n" + "\n".join(history)
            + f"\n\nСьогодні {today}.\nОстання репліка: {utterance}\n"
            "Самодостатнє питання:")
    payload = json.dumps({
        "messages": [{"role": "system", "content": REWRITE_SYSTEM},
                     {"role": "user", "content": user}],
        "temperature": 0, "max_tokens": 120,
    }).encode()
    req = urllib.request.Request(
        M.LLM, data=payload, headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        r = json.load(urllib.request.urlopen(req, timeout=300))
        txt = (r["choices"][0]["message"]["content"] or "").strip()
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        return utterance, time.time() - t0
    dt = time.time() - t0
    cost["calls"] += 1
    cost["s"] += dt
    txt = txt.strip().strip('"').split("\n")[0].strip()
    if not txt or len(txt) > 300:
        return utterance, dt
    return txt, dt


def run_rewrite(catalog, schema, traces, today, verbose=False):
    """Той самий облік, що в M.run, але з переписуванням перед маршрутизацією.
    Історія -- ідентична gold (turn_line з еталонних попередніх ходів)."""
    t_ok = p_ok = n = 0
    complaints, bad_inherit, examples = [], 0, []
    cost = {"calls": 0, "s": 0.0}
    for name in sorted(traces):
        turns = traces[name]
        history = []
        for i, t in enumerate(turns):
            eq, _, _ = M.parse_expect(t["params"])
            if i == 0:
                history.append(M.turn_line(t["turn"], t["utterance"],
                                           t["template"], eq))
                continue
            standalone, _dt = rewrite(history, t["utterance"], today, cost)
            data, _dt2, _raw = M.ask(catalog, schema, [], standalone, today)
            history.append(M.turn_line(t["turn"], t["utterance"],
                                       t["template"], eq))
            if t["kind"] == "disputed":
                continue
            ok_t, ok_p, bad = M.check(data, t["template"], t["params"])
            n += 1
            t_ok += ok_t
            p_ok += ok_t and ok_p
            bad_inherit += sum(1 for b in bad if "успадковано хибно" in b)
            if not (ok_t and ok_p):
                complaints.append((name, t["kind"]))
                examples.append(f"  ✗ {name}/{t['turn']} «{t['utterance'][:44]}»\n"
                                f"      переписав: «{standalone[:80]}»\n"
                                f"      {'; '.join(bad) if bad else 'шаблон не той'}")
            elif verbose:
                examples.append(f"  ok {name}/{t['turn']} «{t['utterance'][:36]}» "
                                f"-> «{standalone[:70]}»")
    return t_ok, p_ok, n, complaints, bad_inherit, examples, cost


def load_catalog(path):
    with open(path, encoding="utf-8") as fh:
        return {t["id"]: t for t in yaml.safe_load(fh)["templates"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", default=os.path.join(
        ROOT, "eval", "chat", "query_catalog_v1.yaml"))
    ap.add_argument("--today", required=True)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if OLD not in M.SYSTEM:
        raise SystemExit("фрагмент SYSTEM не знайдено -- прилад змінився")
    M.SYSTEM = M.SYSTEM.replace(OLD, NEW)   # виправлення промпту -- у всіх режимах

    catalog = load_catalog(args.catalog)
    schema = M.schema_for(catalog)
    sets = [("підганяний (21 хід)", os.path.join(ROOT, "eval", "chat", "followups.tsv")),
            ("HELD-OUT зі звіту", os.path.join(ROOT, "eval", "chat",
                                               "followups_held_out.tsv"))]

    print(f"каталог: {len(catalog)} шаблонів, промпт виправлений, "
          f"сьогодні {args.today}\n")
    table = []
    for set_name, path in sets:
        traces = M.load_traces(path, args.today)
        print(f"══ набір: {set_name} ({len(traces)} трас)")
        row = {"set": set_name}
        for mode in ("none", "gold"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                t_ok, p_ok, n, _d, _times, _c = M.run(
                    catalog, schema, traces, mode, args.today, False)
            row[mode] = (t_ok, p_ok, n)
            row[mode + "_bad"] = buf.getvalue().count("успадковано хибно")
            print(f"   {mode:<8} шаблон {t_ok}/{n}  +параметри {p_ok}/{n}  "
                  f"хибних успадкувань {row[mode + '_bad']}")
        t_ok, p_ok, n, _compl, bad, examples, cost = run_rewrite(
            catalog, schema, traces, args.today, args.verbose)
        row["rewrite"] = (t_ok, p_ok, n)
        row["rewrite_bad"] = bad
        print(f"   {'rewrite':<8} шаблон {t_ok}/{n}  +параметри {p_ok}/{n}  "
              f"хибних успадкувань {bad}   "
              f"(+{cost['calls']} викликів, {cost['s']:.0f} с)")
        for ex in examples:
            print(ex)
        table.append(row)
        print()

    print("=" * 76)
    print(f"{'набір':<24}{'none':>12}{'gold':>12}{'rewrite':>12}"
          f"{'хибн. усп. (n/g/r)':>20}")
    for row in table:
        total = row["none"][2]
        cells = [f"{row[m][1]}/{total}" for m in ("none", "gold", "rewrite")]
        bad = "/".join(str(row[m + "_bad"]) for m in ("none", "gold", "rewrite"))
        print(f"{row['set']:<24}{cells[0]:>12}{cells[1]:>12}{cells[2]:>12}"
              f"{bad:>20}")
    print("\nРішення про переписування -- ТАК, якщо на held-out воно не гірше\n"
          "за gold І не дає хибних успадкувань. Число на підганяному наборі\n"
          "показове лише як контроль проти регресу: на ньому я вже правив\n"
          "каталог і промпт, дивлячись на його ж провали.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
