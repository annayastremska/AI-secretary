"""Чи розв'язує модель посилання на попередній хід, якщо дати їй розмову.

Запуск (модель піднята: bash db/scripts/start_local_llm.sh):

    git show origin/anya-pipeline:demos/upload_app/query_catalog.yaml > /tmp/query_catalog.yaml
    python db/scripts/measure_followup_route.py --catalog /tmp/query_catalog.yaml

Каталог береться з гілки Ані через `git show`, щоб не перемикати гілку: чат
живе в `origin/anya-pipeline`, а цей замір -- ні.

## Питання, на яке це відповідає

«Скільки людей у відпустці сьогодні?» -> «1». «а хто?» -> чат ламається.
Питання не в тому, чи потрібна пам'ять, а в тому, ЧИМ вона має бути:
табличкою станів із правилами успадкування в коді, чи просто тим, що модель
бачить розмову.

Тут перевіряється друге, і перевіряється воно порівнянням, а не вірою:

* `--mode none` -- модель бачить ЛИШЕ поточний рядок. Це те, що робить
  `tiers.model_route(question)` сьогодні: історія в чат приходить, але до
  маршрутизатора не доходить. Базова лінія;
* `--mode gold` -- модель бачить попередні ходи з ЕТАЛОННИМ результатом
  (який шаблон і з якими параметрами виконала система). Помилка тут --
  помилка САМЕ ЦЬОГО ходу, а не наслідок попередньої;
* `--mode roll` -- історія з того, що модель напередбачала сама. Різниця
  між `gold` і `roll` і є накопиченням помилки.

Розділення gold/roll узяте з DST-практики: інакше одна рання помилка карає
всі наступні ходи, і не видно, де саме зламалось.

## Що НЕ перевіряється тут навмисно

Не перевіряється відповідь користувачу, SQL і рендер. Оцінюється рівно два
поля: чи обрано правильний шаблон і чи прийшли правильні параметри. Це і є
харнес; усе інше нижче за течією.

Не перевіряється й правиловий шар (`extract_state`, `extract_dates`,
`extract_subdivision`). Він у чаті стоїть ПІСЛЯ моделі й має право скасувати
її вибір: для шаблонів стану `extract_state(question) is None` віддає питання
у `вільний_sql`, а в «а хто?» слова про відпустку немає. Тобто навіть при
100% тут чат лишиться зламаним, доки цю перевірку не перецілять з рядка на
розмову. Замір показує стелю, а не поточну поведінку.

## Чого цей замір НЕ доводить

20 трас, ~21 оцінюваний хід. Одна помилка -- це майже 5 в.п. Якщо базова
лінія і режим з історією розійдуться в рази -- це підстава перебудовувати
маршрутизатор; якщо на кілька ходів -- це шум, і потрібен більший набір,
написаний не тим, хто налаштовує промпт.
"""
import argparse
import datetime
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

import yaml

LLM = os.environ.get("LLM_URL", "http://127.0.0.1:8081/v1/chat/completions")

# Поля моделі -> параметри каталогу. Каталог просить `dims` і `name_pattern`,
# але це вже РОЗВ'ЯЗАНІ значення (список кодів вимірів, ILIKE-шаблон), і
# будує їх код. Модель говорить людською мовою: state, name.
FIELD_OF_PARAM = {"dims": "state", "name_pattern": "name"}
# `query` для normative_search -- це саме питання; його підставляє код, і
# модель про нього не питають.
CODE_FILLED = {"query"}

MODEL_FIELDS = ["state", "on_date", "date_from", "date_to", "subdivision",
                "name", "doc_number"]

SYSTEM = (
    "Ти -- маршрутизатор питань до бази обліку документів військової частини. "
    "НЕ відповідай на питання. Обери один шаблон зі списку і подай параметри.\n"
    "Тобі показують РОЗМОВУ, а не одне питання. Наступний хід часто спирається "
    "на попередній: «а хто?» після «скільки у відпустці» означає той самий "
    "стан і ту саму дату, змінюється лише форма відповіді. Якщо параметр не "
    "названо в останньому ході, але він однозначно випливає з попередніх -- "
    "візьми його звідти і перелічи такі параметри в carried_over. Якщо "
    "попередній хід був про інше, не переноси з нього нічого.\n"
    "Параметри подавай ті, які потрібні ОБРАНОМУ шаблону (вони перелічені "
    "після його назви). Решту лишай null. Нічого не вигадуй."
)

USER = """Шаблони:
{templates}
- вільний_sql: питання про дані бази, яке не лягає на жоден шаблон
- відмова: питання не про дані бази (погода, поради, прохання щось змінити)

Сьогодні {today}.
{history}
Останній хід користувача: {utterance}"""


def catalog_lines(catalog):
    """Перелік шаблонів для промпта -- з каталогу, як `tiers._catalog_lines`,
    але з ПОТРІБНИМИ ПАРАМЕТРАМИ. Саме цього в теперішньому промпті немає:
    модель заповнює одну плоску схему на всі шаблони й не знає, що
    `list_by_state` просить період, а `count_by_state_on_date` -- одну дату."""
    out = []
    for tid, t in catalog.items():
        need = [FIELD_OF_PARAM.get(p, p) for p in (t.get("params") or [])
                if p not in CODE_FILLED]
        line = f"- {tid}: {t['title']}."
        if need:
            line += " Потрібні: " + ", ".join(need) + "."
        ex = t.get("examples") or []
        if ex:
            line += " Приклади: " + "; ".join(ex[:2])
        out.append(line)
    return "\n".join(out)


def schema_for(catalog):
    """Плоска схема-надмножина, як зараз, плюс двоє відсутніх полів.

    Не `oneOf` під кожен шаблон: конвертація JSON Schema -> граматика в
    llama.cpp таке тягне ненадійно, а коротка схема ще й дешевша за
    semantic accuracy. Відповідність набору параметрів декларації шаблону
    перевіряє код нижче (`conformance`), а не граматика.

    Додано проти теперішньої ROUTE_SCHEMA:
      * subdivision -- його в схемі немає взагалі, підрозділ дістають
        правила з поточного рядка, тому «а в першій роті?» не виражається;
      * carried_over -- що саме модель перенесла. Це не для SQL, це для
        показу людині: «дата з попереднього питання».
    """
    ids = list(catalog) + ["вільний_sql", "відмова"]
    props = {"template": {"type": "string", "enum": ids},
             "state": {"type": ["string", "null"],
                       "enum": ["leave", "deployment", "absent", None]},
             "carried_over": {"type": "array", "items": {"type": "string"}}}
    for f in MODEL_FIELDS:
        props.setdefault(f, {"type": ["string", "null"]})
    return {"type": "object", "properties": props,
            "required": ["template"] + MODEL_FIELDS + ["carried_over"],
            "additionalProperties": False}


def ask(catalog, schema, history, utterance, today):
    """-> (data, секунди, сире_повідомлення). data=None, якщо не розібрано."""
    hist = ""
    if history:
        hist = "Розмова:\n" + "\n".join(history) + "\n"
    payload = json.dumps({
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER.format(
                templates=catalog_lines(catalog), today=today,
                history=hist, utterance=utterance)},
        ],
        "temperature": 0, "max_tokens": 300,
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "route", "schema": schema}},
    }).encode()
    req = urllib.request.Request(LLM, data=payload,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        r = json.load(urllib.request.urlopen(req, timeout=300))
    except urllib.error.URLError as exc:
        sys.exit(f"модель недоступна ({exc}). Підняти: "
                 f"bash db/scripts/start_local_llm.sh")
    txt = r["choices"][0]["message"]["content"]
    try:
        return json.loads(txt), time.time() - t0, txt
    except ValueError:
        # Не JSON при заявленій json_schema -- це не примха моделі, а
        # відомий fail-open llama.cpp: сервер приймає схему й не застосовує
        # її. Тому вивід тут завжди розбирається окремо, а не приймається
        # на віру.
        return None, time.time() - t0, txt


def turn_line(n, utterance, template, params):
    """Один хід в історії. Показуємо не лише репліку, а ЧИМ система
    відповіла: без цього модель не знає, що «сьогодні» вже розв'язалось у
    конкретну дату, і «а хто?» нема від чого відштовхнути."""
    got = ", ".join(f"{k}={v}" for k, v in params.items() if v) or "без параметрів"
    return (f"Хід {n}. Користувач: {utterance}\n"
            f"        Система виконала: {template} ({got})")


def parse_expect(cell):
    """`state=leave;!on_date;subdivision=+` -> (рівності, мусить бути, має не бути)."""
    eq, present, absent = {}, [], []
    for part in (cell or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if part.startswith("!"):
            absent.append(part[1:])
        elif part.endswith("=+"):
            present.append(part[:-2])
        elif "=" in part:
            k, v = part.split("=", 1)
            eq[k] = v
    return eq, present, absent


def check(data, exp_template, exp_params):
    """-> (шаблон_ок, параметри_ок, список претензій)."""
    got_t = (data or {}).get("template")
    if exp_template.startswith("!"):
        t_ok = got_t != exp_template[1:]
    else:
        t_ok = got_t == exp_template
    eq, present, absent = parse_expect(exp_params)
    bad = []
    for k, v in eq.items():
        g = (data or {}).get(k)
        if (g or "").strip().casefold() != v.strip().casefold():
            bad.append(f"{k}={g!r} замість {v!r}")
    for k in present:
        if not ((data or {}).get(k) or "").strip():
            bad.append(f"{k} порожній")
    for k in absent:
        if ((data or {}).get(k) or "").strip():
            bad.append(f"{k}={(data or {}).get(k)!r} успадковано хибно")
    return t_ok, not bad, bad


def conformance(catalog, data):
    """Чого бракує обраному шаблону за його ж декларацією `params:`.

    Це та перевірка, яку в запропонованій конструкції робить КОД замість
    граматики: модель обрала шаблон -- код звіряє набір значень із каталогом
    і, якщо чогось нема, питає, а не підставляє мовчки «сьогодні»."""
    tid = (data or {}).get("template")
    if tid not in catalog:
        return []
    missing = []
    for p in catalog[tid].get("params") or []:
        if p in CODE_FILLED:
            continue
        f = FIELD_OF_PARAM.get(p, p)
        if not ((data or {}).get(f) or "").strip():
            missing.append(p)
    return missing


def load_traces(path, today):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            c = line.rstrip("\n").split("\t")
            if c[0] == "trace":
                continue
            c += [""] * (6 - len(c))
            rows.append({"trace": c[0], "turn": int(c[1]), "kind": c[2],
                         "utterance": c[3].replace("{today}", today),
                         "template": c[4],
                         "params": c[5].replace("{today}", today)})
    traces = {}
    for r in rows:
        traces.setdefault(r["trace"], []).append(r)
    for turns in traces.values():
        turns.sort(key=lambda r: r["turn"])
    return traces


def run(catalog, schema, traces, mode, today, verbose):
    """-> (правильних шаблонів, правильних повністю, оцінених, спірні, часи, провали)."""
    t_ok = p_ok = n = 0
    disputed, times, complaints = [], [], []
    for name in sorted(traces):
        turns = traces[name]
        history = []
        for i, t in enumerate(turns):
            first = i == 0
            if first and mode != "roll":
                # Перший хід самодостатній -- у gold/none його не питаємо,
                # лише кладемо еталон в історію.
                eq, _, _ = parse_expect(t["params"])
                history.append(turn_line(t["turn"], t["utterance"],
                                         t["template"], eq))
                continue
            data, dt, _raw = ask(catalog, schema,
                                 [] if mode == "none" else history,
                                 t["utterance"], today)
            times.append(dt)
            if mode == "roll":
                got = {f: (data or {}).get(f) for f in MODEL_FIELDS}
                history.append(turn_line(t["turn"], t["utterance"],
                                         (data or {}).get("template", "?"),
                                         {k: v for k, v in got.items() if v}))
            else:
                eq, _, _ = parse_expect(t["params"])
                history.append(turn_line(t["turn"], t["utterance"],
                                         t["template"], eq))
            if first:
                continue
            ok_t, ok_p, bad = check(data, t["template"], t["params"])
            miss = conformance(catalog, data)
            note = "; ".join(bad)
            if miss:
                note += ("; " if note else "") + \
                    "не заповнено за каталогом: " + ", ".join(miss)
            if data is None:
                note = "вивід не JSON (схема не застосувалась?); " + note
            if t["kind"] == "disputed":
                disputed.append((name, t, data, note))
                continue
            n += 1
            t_ok += ok_t
            p_ok += ok_t and ok_p
            if verbose or not (ok_t and ok_p):
                mark = "✓" if ok_t and ok_p else "✗"
                print(f"  {mark} {name}/{t['turn']:<2} {t['kind']:<18} "
                      f"«{t['utterance'][:34]}»")
                if not ok_t:
                    print(f"       шаблон {(data or {}).get('template')} "
                          f"замість {t['template']}")
                if note:
                    print(f"       {note}")
            if not (ok_t and ok_p):
                complaints.append((name, t["kind"]))
    return t_ok, p_ok, n, disputed, times, complaints


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", default="eval/chat/followups.tsv")
    ap.add_argument("--catalog", default="/tmp/query_catalog.yaml")
    ap.add_argument("--today", default=datetime.date.today().isoformat())
    ap.add_argument("--mode", default="none,gold",
                    help="none | gold | roll, через кому")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="друкувати й правильні ходи")
    args = ap.parse_args()

    with open(args.catalog, encoding="utf-8") as fh:
        catalog = {t["id"]: t for t in yaml.safe_load(fh)["templates"]}
    schema = schema_for(catalog)
    traces = load_traces(args.traces, args.today)
    print(f"каталог: {len(catalog)} шаблонів   траси: {len(traces)}   "
          f"сьогодні: {args.today}\n")

    summary = {}
    for mode in [m.strip() for m in args.mode.split(",") if m.strip()]:
        title = {"none": "без історії (як у чаті сьогодні)",
                 "gold": "історія з еталонних попередніх ходів",
                 "roll": "історія з власних передбачень (накопичення)"}[mode]
        print(f"── режим: {title} " + "─" * max(0, 52 - len(title)))
        t_ok, p_ok, n, disputed, times, complaints = run(
            catalog, schema, traces, mode, args.today, args.verbose)
        summary[mode] = (t_ok, p_ok, n)
        print(f"  шаблон правильний:   {t_ok}/{n}")
        print(f"  шаблон + параметри:  {p_ok}/{n}")
        if times:
            times.sort()
            p95 = times[min(len(times) - 1, int(len(times) * 0.95))]
            print(f"  латентність: med {statistics.median(times):.1f} c, "
                  f"p95 {p95:.1f} c, викликів {len(times)}")
        if complaints:
            by = {}
            for _name, kind in complaints:
                by[kind] = by.get(kind, 0) + 1
            print("  провалені класи: "
                  + ", ".join(f"{k} ×{v}" for k, v in sorted(by.items())))
        for name, t, data, note in disputed:
            print(f"  ? {name}/{t['turn']} спірна траса «{t['utterance']}» -> "
                  f"{(data or {}).get('template')}"
                  + (f"  [{note}]" if note else ""))
            print("      мітку цієї траси вирішує продукт, "
                  "у підсумок вона не входить")
        print()

    if "none" in summary and "gold" in summary:
        _, a, n = summary["none"]
        _, b, _ = summary["gold"]
        print("── підсумок " + "─" * 53)
        print(f"  без історії {a}/{n}  ->  з історією {b}/{n}   "
              f"різниця {b - a:+d}")
        print("  Різниця в рази означає, що пам'ять -- це те, ЩО МОДЕЛЬ БАЧИТЬ,")
        print("  а не таблиця станів у коді. Різниця в кілька ходів -- шум,")
        print("  і потрібен більший набір, написаний не автором промпта.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
