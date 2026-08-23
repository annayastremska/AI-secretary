"""Сім цифр одною командою: «не ламає» як діф, а не як відчуття.

Причина існування. Правило Ані від 23.08.2026 -- після КОЖНОЇ правки
перевіряти, чи не зламалось щось в іншому місці. Доти ці заміри
збирались руками, по одному, з різними прапорцями й з різних папок, тому
перевірка була дорога -- а дорогу перевірку пропускають. Тут вона одна
команда:

    python -m eval.check_all

Порівняння йде з `eval/baseline.json` -- він У РЕПО навмисно, а не в
`data/eval/reports/` (ті звіти .gitignore не пускає). Очікувані цифри мусять
лежати в історії коду: тоді «цифра змінилась» видно в діффі, а оновлення
базової лінії -- окремий коміт із причиною, а не тихий перезапис файлу на
чиємусь диску.

    python -m eval.check_all --save    -- переписати базову лінію (лише коли
                                          зміна цифри свідома й пояснена)
    python -m eval.check_all --only tests,leave   -- підмножина під час роботи

Код виходу 1, якщо хоч одна цифра стала гіршою. Тобто скрипт можна ставити
перед комітом.
"""
import argparse
import collections
import glob
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baseline.json")

# Набори, на яких міряємо. Шляхи тут, а не в документації, бо документацію
# ніхто не оновлює, а цей файл падає, якщо шлях зник.
LEAVE_DOCX = "data/eval/samples/leave/synthetic-2026-05/docx"
DEPLOYMENT_DOCX = "data/eval/samples/deployment/synthetic-2026-05/docx"
NORMATIVE_DIR = "data/eval/samples/normative"
DEMO_VALIDATOR = "data/eval/samples/demo-story/validate_demo_set.py"
# Демо-набір міряється ще й ПОЛЬОВО, не лише валідатором зв'язності. Додано
# 23.08.2026 після того, як два нові поля просіли саме тут (story 248 -> 258/265,
# 7 хибних невірних), а `check_all` цього не побачив: валідатор перевіряє
# ЛОГІКУ набору, а не витяг. Тобто перевірка «не ламає» пропускала той корпус,
# який найближчий до демо.
DEMO_STORY_DOCX = "data/eval/samples/demo-story/story"
DEMO_STORY_PDF = "data/eval/samples/demo-story/story-pdf"
DEMO_STORY_EVAL = "data/eval/demo-story"


def _run(argv):
    """Підпроцес із UTF-8: без цього український вивід на Windows-консолі
    ламається в cp1251 і парсер бачить сміття замість цифр."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.run(argv, cwd=PROJECT_ROOT, env=env,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc


def measure_tests():
    """pytest: скільки пройшло, скільки впало. xfailed рахуємо окремо --
    очікуваний провал не є провалом, але його зникнення теж сигнал."""
    proc = _run([sys.executable, "-m", "pytest", "eval/tests", "-q"])
    tail = (proc.stdout or "") + (proc.stderr or "")
    got = {}
    for name in ("passed", "failed", "error", "errors", "xfailed", "skipped"):
        m = re.search(rf"(\d+) {name}\b", tail)
        if m:
            got[name.rstrip("s") if name == "errors" else name] = int(m.group(1))
    return {
        "passed": got.get("passed", 0),
        "failed": got.get("failed", 0) + got.get("error", 0),
        "xfailed": got.get("xfailed", 0),
        "exit_code": proc.returncode,
    }


def measure_eval(input_dir, eval_dir=None):
    """Прилад через власний CLI + JSON-звіт. Парсити екран не треба: у звіті
    вже лежить і склад знаменника, і частка підтверджених (A-01/A-09)."""
    fd, report = tempfile.mkstemp(suffix=".json", prefix="check-")
    os.close(fd)
    try:
        argv = [sys.executable, "-m", "eval.evaluate", "--no-llm",
                "--input", input_dir, "--report", report]
        if eval_dir:
            argv += ["--eval-dir", eval_dir]
        proc = _run(argv)
        try:
            with io.open(report, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {"error": "звіт не прочитався",
                    "exit_code": proc.returncode,
                    "stderr": (proc.stderr or "")[-500:]}
        totals = data.get("totals") or {}
        groups = totals.get("by_group") or {}
        rate = data.get("confirmed_rate") or {}
        return {
            "ok": totals.get("ok"),
            "total": totals.get("total"),
            # Польова частина окремо: саме вона просідає, коли ламається
            # витяг. Загальна цифра її розмиває інваріантами й порожніми.
            "fields_ok": (groups.get("field") or {}).get("ok"),
            "fields_total": (groups.get("field") or {}).get("total"),
            "confirmed": rate.get("confirmed"),
            "documents": rate.get("documents"),
            "ungrounded_refusals": len(rate.get("ungrounded_refusals") or []),
            "mapping_problems": len(data.get("mapping_problems") or []),
            "exit_code": proc.returncode,
        }
    finally:
        try:
            os.remove(report)
        except OSError:
            pass


def measure_normative():
    """Нормативний корпус: скільки документів пройшли як нормативні без
    фактів. Ця цифра ловить саме той клас регресій, який двічі стріляв у
    рев'ю 22.08 (C-01): бланк, що став законом, і закон, що став бланком."""
    sys.path.insert(0, PROJECT_ROOT)
    from pipeline.config import load_config
    from pipeline.run import build_resources, process_file

    cfg = load_config("config.yaml", project_root=PROJECT_ROOT)
    cfg["llm"]["enabled"] = False
    out = os.path.join(tempfile.gettempdir(), "check-all-normative")
    os.makedirs(out, exist_ok=True)
    cfg["paths"]["output_dir"] = out
    cfg["storage"]["local_root"] = out
    # Архівування вимкнене: інакше перевірка ВИНОСИТЬ зразки з репозиторію
    # (ця пастка вже спрацьовувала на data/samples/ у серпні).
    cfg["intake"]["archive"] = False

    res = build_resources(cfg, force_no_llm=True)
    res["store"] = None
    files = [p for p in glob.glob(os.path.join(PROJECT_ROOT, NORMATIVE_DIR, "*"))
             if os.path.isfile(p) and not p.endswith(".md")]
    counter = collections.Counter()
    deviations = []
    for path in sorted(files):
        meta = process_file(path, res, cfg, reprocess=True) or {}
        status, domain = meta.get("status"), meta.get("domain")
        facts = meta.get("facts") or []
        counter[(status, domain)] += 1
        if status != "confirmed" or domain != "normative" or facts:
            deviations.append({"file": os.path.basename(path), "status": status,
                               "domain": domain, "facts": len(facts)})
    good = counter[("confirmed", "normative")]
    return {
        "documents": len(files),
        "confirmed_normative": good,
        "deviations": len(deviations),
        # Перелік, а не лише число: коли цифра змінюється, потрібно бачити,
        # ЯКИЙ документ переїхав, інакше діф нічого не пояснює.
        "deviation_files": sorted(d["file"] for d in deviations),
    }


def measure_demo_set():
    """Валідатор демо-набору: чи лишився набір логічно зв'язним. Він міряє
    ДАНІ, не код, але стоїть тут же -- правка нормалізації чи схеми може
    зробити набір несумісним із власним еталоном."""
    proc = _run([sys.executable, DEMO_VALIDATOR])
    tail = (proc.stdout or "") + (proc.stderr or "")
    m = re.search(r"усього перевірок:\s*(\d+);\s*підходить:\s*(\d+);\s*"
                  r"не підходить:\s*(\d+)", tail)
    if not m:
        return {"error": "не знайшов рядок підсумку", "exit_code": proc.returncode}
    return {"total": int(m.group(1)), "ok": int(m.group(2)),
            "bad": int(m.group(3)), "exit_code": proc.returncode}


MEASUREMENTS = {
    "tests": ("тести", measure_tests),
    "leave": ("leave docx", lambda: measure_eval(LEAVE_DOCX)),
    "deployment": ("deployment docx", lambda: measure_eval(DEPLOYMENT_DOCX)),
    "story": ("демо-історія docx", lambda: measure_eval(DEMO_STORY_DOCX,
                                                        DEMO_STORY_EVAL)),
    "story_pdf": ("демо-історія pdf", lambda: measure_eval(DEMO_STORY_PDF,
                                                           DEMO_STORY_EVAL)),
    "normative": ("нормативний корпус", measure_normative),
    "demo": ("демо-набір (звʼязність)", measure_demo_set),
}

# Що вважається погіршенням. Ключ -> (шлях у результаті, напрямок).
# "up" = більше краще, "down" = менше краще. Усе, чого тут немає,
# показується, але вироку не виносить.
WORSE_IF = {
    "tests": [("passed", "up"), ("failed", "down")],
    "leave": [("ok", "up"), ("fields_ok", "up"), ("mapping_problems", "down"),
              ("ungrounded_refusals", "down")],
    "deployment": [("ok", "up"), ("fields_ok", "up"), ("mapping_problems", "down"),
                   ("ungrounded_refusals", "down")],
    "story": [("ok", "up"), ("fields_ok", "up"), ("mapping_problems", "down"),
              ("ungrounded_refusals", "down")],
    "story_pdf": [("ok", "up"), ("fields_ok", "up"), ("mapping_problems", "down"),
                  ("ungrounded_refusals", "down")],
    "normative": [("confirmed_normative", "up"), ("deviations", "down")],
    "demo": [("ok", "up"), ("bad", "down")],
}


def _fmt(key, value):
    if not isinstance(value, dict):
        return str(value)
    if "error" in value:
        return f"ПОМИЛКА: {value['error']}"
    if key == "tests":
        tail = f", xfail {value['xfailed']}" if value.get("xfailed") else ""
        return (f"{value['passed']} passed, {value['failed']} failed{tail}")
    if key in ("leave", "deployment", "story", "story_pdf"):
        return (f"{value['ok']}/{value['total']} усього, "
                f"{value['fields_ok']}/{value['fields_total']} польових, "
                f"підтверджено {value['confirmed']}/{value['documents']}")
    if key == "normative":
        return (f"{value['confirmed_normative']}/{value['documents']} "
                f"нормативних, відхилень {value['deviations']}")
    if key == "demo":
        return f"{value['ok']}/{value['total']}, не підходить {value['bad']}"
    return json.dumps(value, ensure_ascii=False)


def compare(key, old, new):
    """Повертає перелік погіршень людською мовою."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return []
    if "error" in new:
        return [f"{key}: замір не відбувся ({new['error']})"]
    problems = []
    for field, direction in WORSE_IF.get(key, []):
        a, b = old.get(field), new.get(field)
        if a is None or b is None or a == b:
            continue
        worse = b < a if direction == "up" else b > a
        if worse:
            problems.append(f"{key}.{field}: було {a}, стало {b}")
    return problems


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    parser = argparse.ArgumentParser(
        description="П'ять замірів пайплайну одною командою")
    parser.add_argument("--save", action="store_true",
                        help="переписати eval/baseline.json поточними цифрами")
    parser.add_argument("--only", default=None,
                        help="через кому: " + ",".join(MEASUREMENTS))
    args = parser.parse_args(argv)

    wanted = list(MEASUREMENTS)
    if args.only:
        wanted = [k.strip() for k in args.only.split(",") if k.strip()]
        unknown = [k for k in wanted if k not in MEASUREMENTS]
        if unknown:
            print(f"невідомі заміри: {', '.join(unknown)}", file=sys.stderr)
            return 2

    baseline = {}
    if os.path.exists(BASELINE_PATH):
        with io.open(BASELINE_PATH, encoding="utf-8") as f:
            baseline = json.load(f)
    old = baseline.get("measurements") or {}

    results, problems = {}, []
    for key in wanted:
        label, fn = MEASUREMENTS[key]
        started = time.time()
        print(f"[{label}] …", flush=True)
        results[key] = fn()
        results[key]["seconds"] = round(time.time() - started, 1)
        print(f"[{label}] {_fmt(key, results[key])}  "
              f"({results[key]['seconds']} с)", flush=True)
        if key in old:
            problems += compare(key, old[key], results[key])

    print("\n=== зведення ===")
    for key in wanted:
        was = _fmt(key, old[key]) if key in old else "(не було в базовій лінії)"
        now = _fmt(key, results[key])
        mark = "=" if was == now else "≠"
        print(f"  {mark} {MEASUREMENTS[key][0]:22} {now}")
        if was != now:
            print(f"      було: {was}")

    dev = (results.get("normative") or {}).get("deviation_files")
    if dev:
        print("  нормативні відхилення:", ", ".join(d[:50] for d in dev))

    if problems:
        print("\n!! ПОГІРШЕННЯ:")
        for p in problems:
            print("  -", p)
        print("  ^ або правка неправильна, або цифра до неї була неправдива. "
              "Третього варіанту немає -- розберись, який із двох, і напиши це "
              "в коміті.")
    elif old:
        print("\nпогіршень немає.")

    if args.save:
        payload = {
            "_чому_в_репо": "Очікувані цифри мусять лежати в історії коду: "
                            "тоді зміна видна в діффі, а не лише на чиємусь "
                            "диску. Оновлювати ЛИШЕ разом із поясненням у "
                            "коміті.",
            "measurements": {**old, **results},
        }
        with io.open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
        print(f"\nбазову лінію перезаписано: {BASELINE_PATH}")

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
