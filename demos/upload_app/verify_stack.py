# -*- coding: utf-8 -*-
"""Перевірка СТИКУ: апка ↔ пайплайн ↔ база (задача 7.5 плану).

Чому окремий скрипт, а не тест. Тести апки (155 штук) працюють БЕЗ бази й без
моделі -- це навмисно: вони мусять бігати на ноуті за секунди. А стик можна
перевірити лише там, де все живе: на сервері з піднятою базою. Тому це скрипт,
який запускається руками (або в ранбуку демо-дня) і падає кодом виходу.

Чого цей скрипт НЕ робить: не перевіряє ПРАВИЛЬНІСТЬ відповідей проти
еталонів. Це робить `verify_catalog.py` (кожен шаблон проти незалежного
підрахунку по .md) і прилад пайплайна. Тут -- інше питання: чи всі частини
з'єднані і чи говорять вони одне про одне те саме.

Запуск:
    python demos/upload_app/verify_stack.py                 # усе
    python demos/upload_app/verify_stack.py --only db,chat   # підмножина
"""
import argparse
import io
import json
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(APP_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

OK, FAIL, WARN = "OK", "ПРОВАЛ", "УВАГА"
_results = []


def check(name, group="інше"):
    """Декоратор: кожна перевірка -- одна функція, що повертає (стан, текст)."""
    def wrap(fn):
        fn._check = (name, group)
        return fn
    return wrap


# ── 1. Конфіг: апка й пайплайн дивляться в одне місце ────────────────────────

@check("апка читає вихід ТОГО профілю, який передає пайплайну", "config")
def check_output_root():
    from demos.upload_app import app as upapp
    if not os.path.isfile(upapp.CONFIG_PATH):
        return FAIL, f"профіль не знайдено: {upapp.CONFIG_PATH}"
    import yaml
    cfg = yaml.safe_load(io.open(upapp.CONFIG_PATH, encoding="utf-8")) or {}
    declared = (cfg.get("paths") or {}).get("output_dir")
    if not declared:
        return FAIL, "у профілі немає paths.output_dir"
    if not upapp.OUTPUT_ROOT.replace("\\", "/").endswith(declared.strip("/")):
        return FAIL, (f"апка читає {upapp.OUTPUT_ROOT}, а профіль пише в "
                      f"{declared} -- саме ця розбіжність ламала живе "
                      f"завантаження 25.08")
    return OK, f"{declared} (профіль {os.path.basename(upapp.CONFIG_PATH)})"


@check("модель, оголошена в профілі, існує на диску", "config")
def check_model_file():
    from demos.upload_app import app as upapp
    import yaml
    cfg = yaml.safe_load(io.open(upapp.CONFIG_PATH, encoding="utf-8")) or {}
    llm = cfg.get("llm") or {}
    if not llm.get("enabled"):
        return WARN, "LLM у профілі вимкнена -- витяг піде лише детерміновано"
    path = llm.get("model_path") or ""
    full = path if os.path.isabs(path) else os.path.join(PROJECT_ROOT, path)
    if not os.path.isfile(full):
        return FAIL, (f"ваг немає: {full}. Саме так профіль апки був "
                      f"непрацездатний до 25.08")
    gb = os.path.getsize(full) / 2**30
    return OK, f"{os.path.basename(full)} ({gb:.1f} ГБ), n_gpu_layers={llm.get('n_gpu_layers')}"


@check("індекс обробленого читається", "config")
def check_index():
    from demos.upload_app import app as upapp
    if not os.path.isfile(upapp.INDEX_PATH):
        return FAIL, f"немає {upapp.INDEX_PATH}"
    n = sum(1 for line in io.open(upapp.INDEX_PATH, encoding="utf-8")
            if line.strip())
    return OK, f"{n} записів у {os.path.relpath(upapp.INDEX_PATH, PROJECT_ROOT)}"


# ── 2. Завантажувач: апка справді може покласти документ у базу ──────────────

@check("завантажувач БД імпортується (апка кличе його після пайплайна)", "loader")
def check_loader_import():
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "airflow", "plugins"))
    try:
        import ai_secretary_loader
    except Exception as exc:
        return FAIL, f"{type(exc).__name__}: {exc}"
    if not hasattr(ai_secretary_loader, "load"):
        return FAIL, "у модулі немає load() -- апка кличе саме її"
    return OK, "ai_secretary_loader.load доступна"


# ── 3. Сім функцій стику з базою (contract db.py) ────────────────────────────

_SEAM = ("find_people", "absences_on_date", "returning_on_date",
         "absences_for_person", "document_by_number",
         "count_absent_by_subdivision", "search_reference")


@check("сім функцій стику існують і викликаються на живій базі", "db")
def check_seam_functions():
    import datetime
    sys.path.insert(0, os.path.join(APP_DIR, "chat_gradio"))
    import db
    missing = [n for n in _SEAM if not hasattr(db, n)]
    if missing:
        return FAIL, f"немає функцій: {missing}"
    today = datetime.date.today()
    calls = {
        "find_people": lambda: db.find_people(),
        "absences_on_date": lambda: db.absences_on_date(today),
        "returning_on_date": lambda: db.returning_on_date(today),
        "absences_for_person": lambda: db.absences_for_person("а"),
        "document_by_number": lambda: db.document_by_number("118"),
        "count_absent_by_subdivision": lambda: db.count_absent_by_subdivision(today),
        "search_reference": lambda: db.search_reference("відпустка"),
    }
    broken = []
    for name, call in calls.items():
        try:
            call()
        except Exception as exc:
            broken.append(f"{name}: {type(exc).__name__}")
    if broken:
        return FAIL, "; ".join(broken)
    return OK, f"{len(_SEAM)}/{len(_SEAM)} відповіли без помилок"


@check("база не порожня і чернетки відділені від фактів", "db")
def check_db_counts():
    sys.path.insert(0, os.path.join(APP_DIR, "chat_gradio"))
    import db
    docs = db._query("SELECT count(*) AS n FROM documents")[0]["n"]
    conf = db._query("SELECT count(*) AS n FROM facts WHERE status = 'confirmed'")[0]["n"]
    unconf = db._query("SELECT count(*) AS n FROM facts WHERE status = 'unconfirmed'")[0]["n"]
    people = db.people_total()
    if not docs:
        return FAIL, "у базі 0 документів -- нічого перевіряти"
    if conf and unconf and conf + unconf == 0:
        return FAIL, "статуси фактів не заповнені"
    return OK, (f"документів {docs}, підтверджених фактів {conf}, "
                f"чернеток {unconf}, осіб {people}")


@check("те, що пайплайн віддав, доїхало до бази", "db")
def check_output_vs_db():
    """Порівняння двох незалежних підрахунків: скільки .md у виході проти
    скільких документів у базі. Розбіжність не завжди помилка (документ без
    особи їде як documents-only, дублікат за хешем не переобробляється), тому
    тут не рівність, а СМУГА і явна причина, якщо вийшли з неї."""
    from demos.upload_app import app as upapp
    sys.path.insert(0, os.path.join(APP_DIR, "chat_gradio"))
    import db
    md = 0
    docs_dir = os.path.join(upapp.OUTPUT_ROOT, "documents")
    for root, _, files in os.walk(docs_dir):
        md += sum(1 for f in files if f.endswith(".md"))
    in_db = db._query("SELECT count(*) AS n FROM documents")[0]["n"]
    if not md:
        return WARN, f"у {docs_dir} немає .md -- порівнювати нічого"
    diff = md - in_db
    if abs(diff) > max(5, md * 0.05):
        return FAIL, (f".md у виході {md}, документів у базі {in_db} "
                      f"(різниця {diff}) -- завантажено не все")
    return OK, f".md {md} ↔ у базі {in_db} (різниця {diff})"


# ── 4. Чат: наскрізно, як його бачить людина ─────────────────────────────────

_QUESTIONS = [
    ("Скільком зараз у відпустці?", "підрахунок"),
    ("Хто зараз у відрядженні?", "перелік"),
    ("Скільки людей у 2 роті у відпустці?", "підрозділ -> відмова"),
    ("Яка завтра погода?", "поза домном -> відмова"),
]


@check("чат відповідає на живій базі й тримає правила у відповіді", "chat")
def check_chat_answers():
    sys.path.insert(0, os.path.join(APP_DIR, "chat_gradio"))
    from demos.upload_app.chat_gradio import app as chat_app
    problems, notes = [], []
    for question, kind in _QUESTIONS:
        try:
            out = chat_app.answer(question)
        except Exception as exc:
            problems.append(f"«{question}»: {type(exc).__name__}")
            continue
        if not (out or "").strip():
            problems.append(f"«{question}»: порожня відповідь")
            continue
        if "звернення: " not in out:
            problems.append(f"«{question}»: немає номера звернення")
        if "недоступна" in out:
            problems.append(f"«{question}»: база недоступна")
        notes.append(f"{kind}: {len(out)} символів")
    if problems:
        return FAIL, "; ".join(problems)
    return OK, "; ".join(notes)


@check("питання про підрозділ дає відмову, а не число", "chat")
def check_subdivision_refusal():
    from demos.upload_app.chat_gradio import app as chat_app
    out = chat_app.answer("Скільки людей у 2 роті зараз у відпустці?")
    low = (out or "").lower()
    if "підрозділ" not in low:
        return FAIL, f"немає чесної відмови про підрозділи: {low[:160]}"
    return OK, "відмова на місці (штатки в базі немає)"


@check("модель у чаті доступна і на карті", "chat")
def check_model_runtime():
    from demos.upload_app.chat_gradio import app as chat_app
    tiers = chat_app.tier_chat
    if not os.path.exists(tiers.MODEL_PATH):
        return WARN, (f"ваг чата немає ({tiers.MODEL_PATH}) -- чат живе на "
                      f"правилах і векторах")
    try:
        from pipeline.llm.cuda_preload import preload
        preload()
        from llama_cpp import llama_cpp as c
        offload = bool(c.llama_supports_gpu_offload())
    except Exception as exc:
        return FAIL, f"llama_cpp не завантажився: {type(exc).__name__}: {exc}"
    if not offload:
        return WARN, ("llama_cpp без CUDA -- відповіді з моделлю будуть "
                      "десятки секунд (див. пастку в requirements.txt)")
    return OK, "llama_cpp із CUDA, offload доступний"


@check("ярус вільного SQL: стан і рейки", "chat")
def check_free_sql():
    from demos.upload_app.chat_gradio import app as chat_app
    tiers = chat_app.tier_chat
    bad = []
    for sql in ("DELETE FROM facts", "SELECT 1; DROP TABLE facts",
                "UPDATE facts SET status='confirmed'"):
        got, _ = tiers.validate_sql(sql)
        if got is not None:
            bad.append(sql)
    if bad:
        return FAIL, f"валідатор пропустив небезпечне: {bad}"
    state = "увімкнений" if tiers.FREE_SQL_ENABLED else "вимкнений"
    return OK, f"{state}; валідатор відбиває DML і кілька statement'ів"


# ── Прогін ───────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="",
                    help="групи через кому: config,loader,db,chat")
    ap.add_argument("--json", default=None, help="куди покласти звіт")
    args = ap.parse_args()
    groups = {g.strip() for g in args.only.split(",") if g.strip()}

    checks = [fn for fn in globals().values()
              if callable(fn) and hasattr(fn, "_check")]
    print(f"перевірок: {len(checks)}"
          + (f" (групи: {sorted(groups)})" if groups else ""))
    failed = 0
    for fn in checks:
        name, group = fn._check
        if groups and group not in groups:
            continue
        try:
            state, detail = fn()
        except Exception as exc:                     # сама перевірка впала
            state, detail = FAIL, f"перевірка впала: {type(exc).__name__}: {exc}"
        if state == FAIL:
            failed += 1
        mark = {OK: "  OK  ", FAIL: "ПРОВАЛ", WARN: "УВАГА "}[state]
        print(f"[{mark}] {group:6} | {name}\n         {detail}")
        _results.append({"група": group, "перевірка": name,
                         "стан": state, "деталі": detail})

    if args.json:
        io.open(args.json, "w", encoding="utf-8").write(
            json.dumps({"перевірки": _results}, ensure_ascii=False, indent=2))
        print(f"звіт: {args.json}")

    print(f"\nпровалів: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
