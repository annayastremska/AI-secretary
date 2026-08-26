"""Порівнює три способи визначити ідентифікатор документа: точність і вартість.

Запуск (модель мусить бути піднята: bash db/scripts/start_local_llm.sh):
    python db/scripts/compare_identity_methods.py
    python db/scripts/compare_identity_methods.py --only A,B

## Питання, на яке це відповідає

«Чи не перенаситили ми все регулярками?» -- і його не варто вирішувати на
смак, бо є звірена вручну істина на частину корпусу.

Три варіанти:

* **A -- лише регулярки.** Те, що зроблено в `extract_document_identity.py`.
* **B -- регулярки знаходять КАНДИДАТІВ, модель обирає.** Механічна частина
  (знайти всі рядки, схожі на ідентифікатор) лишається механічною -- вона
  повна й без судження. Судження (котрий із них ВЛАСНИЙ) віддається моделі.
  Вивід перевіряється точно: мусить бути одним із номерів кандидатів або
  явна відмова.
* **C -- модель читає голову й видає ідентифікатор сама.** Без кандидатів.

## Чому саме такий поділ, а не «все моделі» чи «все регулярками»

Власне ж дослідження команди (`normative-docs-subsystem.md` §4) фіксує: моделі
надійно витягують ПРЯМО НАПИСАНЕ і провалюються там, де треба домислити
відсутнє. Вибір із кандидатів, наявних у тексті, -- це надійний режим. Вільне
витягування -- ненадійний, і саме тому C тут не для того, щоб перемогти, а щоб
було видно ціну свободи.

## Головна метрика -- не та, що здається

Найцікавіше не «скільки вгадав», а **чи відмовляється, коли ідентифікатора в
тексті немає**. Таких документів 11 із 41: закон свого номера не несе, указ
теж. Впевнено назвати номер там, де його немає, -- гірше за відмову, бо саме
так у поле потрапляє номер сусіднього документа зі блоку «Із змінами».
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

import extract_document_identity as E  # noqa: E402

LLM = os.environ.get("LLM_URL", "http://127.0.0.1:8081/v1/chat/completions")
TRUTH = os.path.join(PROJECT_ROOT, "eval", "identity", "ground_truth.tsv")

# Кандидати. Це навмисно ШИРОКИЙ і тупий набір шаблонів: його задача --
# нічого не пропустити, а не вибрати правильне. Вибір -- не його робота.
CAND_PATTERNS = [
    r"НД\s*ТЗІ\s*\d\.\d\s*-\s*\d{3}\s*-\s*\d{2,4}",
    r"наказ\w*\s+(?:[^\n]{0,40}?\s+)?№\s*\d+(?:\s+від\s+\d{2}\.\d{2}\.\d{4})?",
    r"№\s*\d+\s*(?:від|/)\s*[\d.]{4,10}",
    r"№\s*\d+[-–]\s*[IVXLC]+",
    r"Відомост\w*\s+Верховн\w*\s+Рад\w*[^)]{0,40}\)?,?\s*\d{4}\s*,\s*№\s*[\d-]+\s*,\s*ст\.?\s*\d+",
    r"(?:ЗАТВЕРДЖЕНО|затверджен\w*)[^\n]{0,80}№\s*\d+",
    r"\d{2}\.\d{2}\.\d{4}\s*№\s*\d+",
]


def candidates(head):
    """Усі рядки, схожі на ідентифікатор, без будь-якого судження."""
    spans = []
    for pat in CAND_PATTERNS:
        for m in re.finditer(pat, head, re.I):
            spans.append((m.start(), m.end(), " ".join(m.group(0).split())))
    spans.sort()
    out, seen = [], set()
    for s, e, txt in spans:
        key = E.normalize_key(txt)
        # Поглинання: довший кандидат, що містить коротший, лишається сам.
        if any(s >= s2 and e <= e2 for s2, e2, _ in spans if (s2, e2) != (s, e)):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(txt)
    return out


def context_for_llm(text):
    """Однаковий вхід для всіх варіантів: голова + підписний блок.

    Без цього порівняння нечесне: регулярки шукають підпис по ВСЬОМУ тексту
    (у Кримінальному кодексі він на позиції 639479), а моделі показувалась
    лише голова. Пошук підпису -- операція механічна, тому вона спільна;
    відрізняється тільки те, ЯК із цього вибирається власний номер.
    """
    head = E.head_of(text)
    sig, sig_date = E.signature_number(text)
    if sig:
        head += f"\n\n[підписний блок документа] № {sig}"
        if sig_date:
            head += f" від {sig_date}"
    return head


def ask(messages, max_tokens=120):
    body = json.dumps({"messages": messages, "temperature": 0,
                       "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(LLM, data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    r = json.load(urllib.request.urlopen(req, timeout=180))
    return (r["choices"][0]["message"]["content"],
            r.get("usage", {}), time.time() - t0)


PICK_SYSTEM = (
    "Ти визначаєш, який ідентифікатор належить САМОМУ документу, а не тим "
    "документам, на які він посилається. Відповідай лише числом."
)
PICK_USER = """Ось титульний блок документа:

--- ПОЧАТОК ---
{head}
--- КІНЕЦЬ ---

У ньому знайдено такі рядки, схожі на ідентифікатор:
{opts}
0. жоден із них не є власним ідентифікатором цього документа

Котрий рядок є ВЛАСНИМ ідентифікатором цього документа? Пам'ятай: документ
часто згадує інші документи -- зміни до нього, документи, які він скасовує,
стандарти, на які він посилається. Їхні номери НЕ є його власним.

Відповідь -- одне число."""

FREE_SYSTEM = (
    "Ти витягуєш реквізити нормативного документа. Відповідай лише JSON."
)
FREE_USER = """Ось титульний блок документа:

--- ПОЧАТОК ---
{head}
--- КІНЕЦЬ ---

Поверни JSON: {{"identifier": "<власний ідентифікатор документа>", "title": "<назва>"}}
Якщо власного ідентифікатора в тексті НЕМАЄ -- поверни "identifier": null.
Не вигадуй номер, якого в тексті немає."""


def load_truth():
    truth = {}
    with open(TRUTH, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            truth[int(parts[0])] = (parts[1].strip(), parts[2].strip())
    return truth


def canonical(s):
    """Ідентифікатор -> (тип, розрізнювальні частини). None, якщо немає.

    Порівнювати ідентифікатори як РЯДКИ -- помилка, і вона знецінює весь
    замір. Перша версія цього скрипта саме так і робила, і рахувала за хибу
    те, що модель відповіла `НАКАЗ 20.11.2017 № 606` замість
    `наказ № 606 від 20.11.2017`, або `1999, № 22-23, ст.196` без префікса
    «ВВР». Це та сама відповідь у порядку самого документа. Міряти треба
    «чи вказано на той самий документ», а не «чи записано моїм формулюванням»
    -- інакше замір карає за стиль і мовчить про суть.
    """
    if not s or str(s).strip().upper() == "NONE":
        return None
    s = E.normalize_key(str(s))
    m = re.search(r"нд?тзі(\d\.\d-\d{3}-\d{2,4})", s)
    if m:
        return ("tzi", m.group(1))
    # ВВР перевіряємо ПЕРЕД наказом: у ньому теж є «№», але є й «ст.»
    m = re.search(r"(\d{4}),?№([\d-]+),?ст\.?(\d+)", s)
    if m:
        return ("vvr", m.group(1), m.group(2), m.group(3))
    # Номер закону («550-хіv», «80/94-вр») і номер указу («1153/2008») мусять
    # стояти ПЕРЕД загальним «№ NNN»: інакше закон 550-XIV і наказ № 550
    # злипаються в одне, а це різні документи.
    m = re.search(r"(\d{2,5})-([івхлс]+)\b", s) or re.search(r"(\d{2,5})-([ivxlc]+)\b", s)
    if m:
        return ("law", m.group(1), m.group(2))
    m = re.search(r"(\d+)/(\d+)-вр", s)
    if m:
        return ("law_vr", m.group(1), m.group(2))
    m = re.search(r"(\d{1,5})/(\d{4})", s)
    if m:
        return ("decree", m.group(1), m.group(2))
    num = re.search(r"№(\d+)", s)
    date = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", s)
    if num:
        return ("order", num.group(1), date.groups() if date else None)
    return ("raw", s)


def same(a, b):
    """b -- істина, може містити рівноправні варіанти через `|`."""
    if b and "|" in str(b):
        return any(same(a, alt) for alt in str(b).split("|"))
    ca, cb = canonical(a), canonical(b)
    if ca is None or cb is None:
        return ca is None and cb is None
    # Наказ без дати проти наказу з датою -- той самий, якщо номер збігся:
    # дата не завжди стоїть у титулі, і карати за її відсутність нема за що.
    if ca[0] == cb[0] == "order":
        return ca[1] == cb[1] and (ca[2] is None or cb[2] is None or ca[2] == cb[2])
    return ca == cb


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", default="A,B,C")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    want = set(args.only.split(","))

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    truth = load_truth()

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""SELECT id, text_content FROM documents
                        WHERE domain='normative' AND text_content IS NOT NULL
                        ORDER BY id""")
        docs = cur.fetchall()
    if args.limit:
        docs = docs[:args.limit]

    results = {v: {} for v in want}
    cost = {v: {"s": 0.0, "in": 0, "out": 0, "calls": 0} for v in want}

    for doc_id, text in docs:
        head = context_for_llm(text)
        cands = candidates(head)

        if "A" in want:
            results["A"][doc_id] = E.extract(text)["identifier"]

        if "B" in want:
            if not cands:
                results["B"][doc_id] = None
            else:
                opts = "\n".join(f"{i}. {c}" for i, c in enumerate(cands, 1))
                out, usage, dt = ask(
                    [{"role": "system", "content": PICK_SYSTEM},
                     {"role": "user", "content": PICK_USER.format(head=head[:2200], opts=opts)}],
                    max_tokens=8)
                cost["B"]["s"] += dt
                cost["B"]["in"] += usage.get("prompt_tokens", 0)
                cost["B"]["out"] += usage.get("completion_tokens", 0)
                cost["B"]["calls"] += 1
                m = re.search(r"\d+", out)
                idx = int(m.group()) if m else 0
                results["B"][doc_id] = cands[idx - 1] if 1 <= idx <= len(cands) else None

        if "C" in want:
            out, usage, dt = ask(
                [{"role": "system", "content": FREE_SYSTEM},
                 {"role": "user", "content": FREE_USER.format(head=head[:2200])}],
                max_tokens=200)
            cost["C"]["s"] += dt
            cost["C"]["in"] += usage.get("prompt_tokens", 0)
            cost["C"]["out"] += usage.get("completion_tokens", 0)
            cost["C"]["calls"] += 1
            ident = None
            jm = re.search(r"\{.*\}", out, re.S)
            if jm:
                try:
                    ident = json.loads(jm.group()).get("identifier")
                except Exception:
                    ident = None
            results["C"][doc_id] = ident

    # ── звіт ────────────────────────────────────────────────────────────────
    subsets = {
        "усі": [d for d, _ in docs if d in truth],
        "list (незалежна істина)": [d for d, _ in docs
                                    if d in truth and truth[d][1] == "list"],
        "де ідентифікатор Є": [d for d, _ in docs
                               if d in truth and truth[d][0] != "NONE"],
        "де ідентифікатора НЕМА": [d for d, _ in docs
                                   if d in truth and truth[d][0] == "NONE"],
    }
    names = {"A": "лише регулярки", "B": "регулярки+модель обирає",
             "C": "модель вільно"}
    print(f"\n{'підмножина':28} {'n':>3}  " +
          "  ".join(f"{names[v]:>24}" for v in sorted(want)))
    for label, ids in subsets.items():
        row = f"{label:28} {len(ids):>3}  "
        for v in sorted(want):
            ok = sum(1 for d in ids if same(results[v].get(d), truth[d][0]))
            row += f"{ok:>3}/{len(ids):<3} = {ok/max(1,len(ids))*100:>5.1f}%".rjust(26)
        print(row)

    print(f"\n{'вартість':28}")
    for v in sorted(want):
        c = cost[v]
        if not c["calls"]:
            print(f"  {names[v]:26} 0 викликів моделі")
            continue
        print(f"  {names[v]:26} {c['calls']} викликів, {c['s']:.1f} с усього "
              f"({c['s']/c['calls']:.2f} с/док), токенів у {c['in']} / з {c['out']}")

    print("\n── РОЗБІЖНОСТІ ─────────────────────────────────────────────────")
    for doc_id, _ in docs:
        if doc_id not in truth:
            continue
        got = {v: results[v].get(doc_id) for v in sorted(want)}
        if all(same(g, truth[doc_id][0]) for g in got.values()):
            continue
        print(f"  doc {doc_id}  [{truth[doc_id][1]}]  істина: {truth[doc_id][0]}")
        for v in sorted(want):
            mark = "OK " if same(got[v], truth[doc_id][0]) else "ХИБ"
            print(f"      {mark} {names[v]:26} {got[v]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
