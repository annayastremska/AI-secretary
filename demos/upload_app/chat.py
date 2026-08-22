"""Чат «AI-секретар»: відповіді українською за даними РЕАЛЬНОЇ Postgres.

Два яруси (рішення координатора із замовницею):

  Ярус 1 -- каталог SQL-шаблонів (query_catalog.yaml). Маршрутизація:
    спершу правила (регекси/ключові слова), потім локальна модель як
    класифікатор у ЗАКРИТИЙ перелік шаблонів + витяг параметрів (JSON).
    Текст КОЖНОЇ відповіді формує код; модель ніколи не пише текст
    відповіді і ніколи не редагує SQL шаблону (рішення з answer/chat Колі).

  Ярус 2 -- нешаблонне питання: модель складає SELECT сама, але під рейками:
    read-only з'єднання (readonly-користувач + default_transaction_read_only
    у рядку підключення + SET SESSION ... READ ONLY), валідатор перед
    виконанням (один statement, тільки SELECT, allowlist таблиць, примусовий
    LIMIT 200, statement_timeout 5 с), фільтр підтвердженості перевіряє КОД,
    у відповіді позначка «нешаблонний запит» + SQL у згортці. Не склалось --
    чесне «не знайшла», без другої спроби моделі.

Правило продукту: відмова краща за вигадку; чернетка (unconfirmed) у
підрахунки не входить -- непідтверджені показуються окремим числом.
"""
import datetime
import os
import re
import threading
import time

import psycopg
import yaml
from psycopg.rows import dict_row

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(APP_DIR))
CATALOG_PATH = os.path.join(APP_DIR, "query_catalog.yaml")

# Ваги локальної моделі (MamayLM 4B, завантажені
# `python pipeline/scripts/download_model.py --size 4b`). Немає ваг --
# чат живе на правилах і чесно каже це у відповіді.
MODEL_PATH = os.environ.get(
    "CHAT_MODEL_PATH",
    os.path.join(PROJECT_ROOT, "models", "mamaylm-4b-q4_k_m.gguf"))

MODEL_NOTE = ("локальна модель недоступна -- відповідаю лише за "
              "шаблонними питаннями")

# ── Каталог ──────────────────────────────────────────────────────────────────

with open(CATALOG_PATH, encoding="utf-8") as _f:
    _CATALOG = {t["id"]: t for t in yaml.safe_load(_f)["templates"]}

# Стан із питання -> коди вимірів у dimensions.code
STATE_DIMS = {
    "leave": ["leave"],
    "deployment": ["deployment_location"],
    "absent": ["leave", "deployment_location"],
}
STATE_LABEL = {"leave": "у відпустці", "deployment": "у відрядженні",
               "absent": "відсутні (відпустка або відрядження)"}
DIM_LABEL = {"leave": "відпустка", "deployment_location": "відрядження"}
QUEUE_LABEL = {
    "unknown_type": "невідомий тип бланка",
    "unconfirmed_fact": "непідтверджений факт",
    "handwritten": "рукописний документ",
    "qa_sample": "вибіркова перевірка якості",
    "new_person": "нова особа в реєстрі",
}

# ── Підключення до БД ────────────────────────────────────────────────────────
# Увесь чат ходить у базу read-only користувачем (milidoc_readonly) і з
# default_transaction_read_only=on прямо в рядку підключення: навіть якщо
# згенерований SQL проскочить повз валідатор, DML відіб'є САМА БАЗА.


def _read_env():
    vals = {}
    path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip().strip("\"'")
    return vals


def _dsn():
    env = _read_env()
    user = env.get("READONLY_DB_USER", "milidoc_readonly")
    pwd = env.get("READONLY_DB_PASSWORD", "")
    port = env.get("POSTGRES_PORT", "5433")
    db = env.get("APP_DB_NAME", "milidoc")
    return (f"host=localhost port={port} dbname={db} user={user} "
            f"password={pwd} "
            f"options='-c default_transaction_read_only=on "
            f"-c statement_timeout=5000'")


def _connect():
    return psycopg.connect(_dsn(), row_factory=dict_row, autocommit=True)


def _run_template_sql(sql, params):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


# ── Локальна модель (лінива, один екземпляр на процес) ───────────────────────

_MODEL = None
_MODEL_FAILED = False
_MODEL_LOCK = threading.Lock()


def _get_model():
    """Llama або None. Падіння завантаження запам'ятовується -- не смикаємо
    диск на кожне питання."""
    global _MODEL, _MODEL_FAILED
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        if _MODEL_FAILED or not os.path.exists(MODEL_PATH):
            return None
        try:
            from llama_cpp import Llama
            # n_gpu_layers з оточення: локально 0 (немає CUDA), на
            # GPU-сервері CHAT_N_GPU_LAYERS=-1 -> усі шари 4B у VRAM.
            # Саме ця модель робить маршрутизацію питання, тобто її
            # латентність люди відчувають безпосередньо.
            _MODEL = Llama(model_path=MODEL_PATH, n_ctx=4096,
                           n_gpu_layers=int(os.environ.get(
                               "CHAT_N_GPU_LAYERS", "0")),
                           verbose=False)
        except Exception:
            _MODEL_FAILED = True
            return None
        return _MODEL


def _model_json(system, user, schema):
    """Один структурований виклик: JSON за схемою, temperature 0.
    Виняток нагору не йде -- None означає «модель не відповіла»."""
    model = _get_model()
    if model is None:
        return None
    try:
        with _MODEL_LOCK:
            out = model.create_chat_completion(
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                response_format={"type": "json_object", "schema": schema},
                temperature=0, max_tokens=400)
        import json
        return json.loads(out["choices"][0]["message"]["content"])
    except Exception:
        return None


# ── Витяг параметрів правилами ───────────────────────────────────────────────

MONTHS = {
    "січ": 1, "лют": 2, "берез": 3, "квіт": 4, "трав": 5, "черв": 6,
    "лип": 7, "серп": 8, "верес": 9, "жовт": 10, "листопад": 11, "груд": 12,
}

STOP_CAPS = {"Хто", "Скільки", "Який", "Яка", "Яке", "Які", "Коли", "Де",
             "Чи", "Чому", "Що", "Покажи", "Дай", "Стан", "Список"}

# Агрегати, для яких шаблонів у каталозі немає: і правила, і модельний
# класифікатор їх пропускають прямо в ярус 2 (4B-модель інакше тягне таке
# питання в найближчий знайомий шаблон -- перевірено).
_AGGREGATE = re.compile(
    r"середн|найдовш|найкоротш|сумарн|медіан|максимальн|мінімальн")


def _month_from(text):
    low = text.lower()
    for stem, num in MONTHS.items():
        if stem in low:
            return num
    return None


def extract_dates(question):
    """-> (on_date, date_from, date_to) як date або None.

    «зараз»/«сьогодні» -> сьогодні; ISO-дата; «6 травня [2026]» -> день;
    «у травні [2026]» -> місяць-період. Рік не назвали -- поточний рік
    (дефолт не вигадуємо з нічого: це явне правило, і воно ж озвучується
    датою зрізу у відповіді)."""
    low = question.lower()
    today = datetime.date.today()

    m = re.search(r"\d{4}-\d{2}-\d{2}", question)
    if m:
        d = datetime.date.fromisoformat(m.group(0))
        return d, None, None
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", question)
    if m:
        d = datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        return d, None, None

    month = _month_from(low)
    year_m = re.search(r"\b(20\d{2})\b", question)
    year = int(year_m.group(1)) if year_m else today.year
    if month:
        day_m = re.search(r"\b(\d{1,2})(?:-?го)?\s+[а-яіїєґ]*", low)
        day = None
        if day_m and 1 <= int(day_m.group(1)) <= 31:
            day = int(day_m.group(1))
        if day:
            try:
                return datetime.date(year, month, day), None, None
            except ValueError:
                pass
        first = datetime.date(year, month, 1)
        last = (datetime.date(year + (month == 12), month % 12 + 1, 1)
                - datetime.timedelta(days=1))
        return None, first, last

    if "зараз" in low or "сьогодні" in low or "на даний момент" in low:
        return today, None, None
    return None, None, None


def extract_state(question):
    low = question.lower()
    if re.search(r"відпуст", low):
        return "leave"
    if re.search(r"відрядж", low):
        return "deployment"
    if re.search(r"відсутн|поза частин", low):
        return "absent"
    return None


def extract_doc_number(question):
    # «№ 109», «документ 204», «документі №473»
    m = re.search(r"№\s*([\w\d]+)", question)
    if m:
        return m.group(1)
    m = re.search(r"документ[а-яіїєґ]*\s+(\d+)", question, re.I)
    if m:
        return m.group(1)
    return None


def extract_name(question):
    """Слово з великої літери (не питальне), яке справді є в реєстрі осіб.
    Відмінок ловимо обрізанням хвоста: «Усика» -> «Усик» -> ILIKE 'Усик%'."""
    for tok in re.findall(r"[А-ЯІЇЄҐ][А-ЯІЇЄҐа-яіїєґ']{2,}", question):
        if tok in STOP_CAPS:
            continue
        for cut in (0, 1, 2):
            stem = tok[:len(tok) - cut] if cut else tok
            if len(stem) < 3:
                break
            try:
                rows = _run_template_sql(
                    "SELECT 1 FROM objects WHERE canonical_name ILIKE %(p)s "
                    "LIMIT 1", {"p": f"%{stem}%"})
            except psycopg.Error:
                return None
            if rows:
                return stem
    return None


def rules_route(question):
    """-> (template_id, params) або None, якщо правила не впізнали питання."""
    low = question.lower()
    on_date, date_from, date_to = extract_dates(question)
    state = extract_state(question)
    doc_number = extract_doc_number(question)
    today = datetime.date.today()

    if doc_number and re.search(r"документ|наказ|квит|посвідч|№", low):
        return "doc_by_number", {"doc_number": doc_number}

    if re.search(r"черг|перевірк|рев'?ю", low) and re.search(r"скільки|що|яких", low):
        return "review_queue_count", {}

    if "непідтвердж" in low or "не підтвердж" in low:
        return "unconfirmed_count", {}

    if re.search(r"скільки\s+документів", low):
        return "documents_count", {}

    # Агрегати, яких у каталозі немає (середнє, мін/макс, суми) -- правила
    # не хапають: такі питання йдуть одразу в ярус 2 (див. answer_question).
    if _AGGREGATE.search(low):
        return None

    if state:
        dims = STATE_DIMS[state]
        if re.search(r"^хто\b|хто\s", low) or "список" in low or "покажи" in low:
            d_from = date_from or on_date or today
            d_to = date_to or on_date or today
            return "list_by_state", {"dims": dims, "state": state,
                                     "date_from": d_from, "date_to": d_to}
        if re.search(r"скільки|кількість", low):
            if date_from and date_to:
                return "count_by_state_period", {
                    "dims": dims, "state": state,
                    "date_from": date_from, "date_to": date_to}
            return "count_by_state_on_date", {
                "dims": dims, "state": state, "on_date": on_date or today}

    name = extract_name(question)
    if name and re.search(r"що відомо|де зараз|стан|про ", low):
        return "person_status", {"name_pattern": f"%{name}%", "name": name}
    if name and len(question.split()) <= 4:
        return "person_status", {"name_pattern": f"%{name}%", "name": name}

    return None


# ── Модель як класифікатор у закритий перелік (ярус 1, друга спроба) ─────────

ROUTE_IDS = list(_CATALOG) + ["вільний_sql", "відмова"]

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "template": {"type": "string", "enum": ROUTE_IDS},
        "state": {"type": ["string", "null"],
                  "enum": ["leave", "deployment", "absent", None]},
        "on_date": {"type": ["string", "null"]},
        "date_from": {"type": ["string", "null"]},
        "date_to": {"type": ["string", "null"]},
        "name": {"type": ["string", "null"]},
        "doc_number": {"type": ["string", "null"]},
    },
    "required": ["template", "state", "on_date", "date_from", "date_to",
                 "name", "doc_number"],
    "additionalProperties": False,
}


def _route_system():
    lines = ["Ти -- маршрутизатор питань до бази обліку документів "
             "військової частини. НЕ відповідай на питання. Обери один "
             "шаблон зі списку і витягни параметри.", "Шаблони:"]
    for tid, t in _CATALOG.items():
        ex = "; ".join(t.get("examples", [])[:2])
        lines.append(f"- {tid}: {t['title']}. Приклади: {ex}")
    lines.append("- вільний_sql: питання про дані бази, яке не лягає на "
                 "жоден шаблон")
    lines.append("- відмова: питання не про дані бази (погода, поради, "
                 "прохання щось змінити чи видалити)")
    lines.append(
        "Параметри: state -- leave (відпустка) / deployment (відрядження) / "
        "absent (відсутні взагалі) / null. Дати -- YYYY-MM-DD або null; "
        "«зараз»/«сьогодні» -> сьогоднішня дата "
        f"({datetime.date.today().isoformat()}). name -- прізвище або ПІБ. "
        "doc_number -- номер документа без «№». Нічого не вигадуй: якщо "
        "параметра в питанні немає -- null.")
    return "\n".join(lines)


def model_route(question):
    """-> (template_id|'вільний_sql'|'відмова', params) або None."""
    data = _model_json(_route_system(), question, ROUTE_SCHEMA)
    if not data or data.get("template") not in ROUTE_IDS:
        return None
    tid = data["template"]
    if tid in ("вільний_sql", "відмова"):
        return tid, {}
    params = {}
    state = data.get("state")

    def _date(key):
        v = data.get(key)
        try:
            return datetime.date.fromisoformat(v) if v else None
        except ValueError:
            return None

    today = datetime.date.today()
    if tid in ("count_by_state_on_date", "count_by_state_period",
               "list_by_state"):
        # Модель обрала шаблон стану, а в питанні немає ЖОДНОГО слова про
        # відпустку/відрядження/відсутність -- не віримо вибору (маленька
        # модель тягне незнайомі питання в найближчий шаблон), віддаємо в
        # ярус 2. Це перевірка кодом, не довіра до моделі.
        if extract_state(question) is None:
            return "вільний_sql", {}
        if state not in STATE_DIMS:
            state = extract_state(question)
        params["dims"] = STATE_DIMS[state]
        params["state"] = state
    if tid == "count_by_state_on_date":
        params["on_date"] = _date("on_date") or today
    if tid in ("count_by_state_period", "list_by_state"):
        params["date_from"] = _date("date_from") or _date("on_date") or today
        params["date_to"] = _date("date_to") or _date("on_date") or today
    if tid == "person_status":
        name = (data.get("name") or "").strip()
        if not name:
            return None
        params["name_pattern"] = f"%{name}%"
        params["name"] = name
    if tid == "doc_by_number":
        num = (data.get("doc_number") or "").strip().lstrip("№").strip()
        if not num:
            return None
        params["doc_number"] = num
    return tid, params


# ── Склад відповіді (формує КОД): цифра/список -> дата зрізу -> знаменник ->
#    непідтверджені окремо -> джерело ─────────────────────────────────────────


def _people_total():
    try:
        rows = _run_template_sql("SELECT COUNT(*) AS n FROM people", {})
        return rows[0]["n"]
    except psycopg.Error:
        return None


def _plural(n, one, few, many):
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} {few}"
    return f"{n} {many}"


def _people(n):
    return _plural(n, "особа", "особи", "осіб")


def _sql_params(template_id, params):
    t = _CATALOG[template_id]
    return {k: v for k, v in params.items() if k in ("dims", "on_date",
            "date_from", "date_to", "name_pattern", "doc_number")}, t


def _fmt_period(r):
    a, b = r.get("valid_from"), r.get("valid_to")
    if a and b:
        return f"{a} — {b}"
    if a:
        return f"з {a}"
    return "період не вказано"


def run_template(template_id, params):
    """Виконати шаблон і скласти текст відповіді. -> (text, source_lines)."""
    sql_params, t = _sql_params(template_id, params)
    rows = _run_template_sql(t["sql"], sql_params)
    unconfirmed = None
    if t.get("sql_unconfirmed"):
        unconfirmed = _run_template_sql(t["sql_unconfirmed"], sql_params)

    total = _people_total()
    denom = (f" (усього в реєстрі {_people(total)})" if total else "")
    state_lbl = STATE_LABEL.get(params.get("state"), "")
    lines = []

    if template_id == "count_by_state_on_date":
        n = rows[0]["n"]
        u = unconfirmed[0]["n"] if unconfirmed else 0
        lines.append(f"{_people(n)} {state_lbl}{denom}.")
        lines.append(f"Зріз: на {params['on_date']} (за підтвердженими фактами).")
        lines.append(f"Непідтверджених (чернетки, у підрахунок не входять): {u}.")

    elif template_id == "count_by_state_period":
        n = rows[0]["n"]
        u = unconfirmed[0]["n"] if unconfirmed else 0
        lines.append(f"{_people(n)} {state_lbl}{denom}.")
        lines.append(f"Зріз: період {params['date_from']} — {params['date_to']} "
                     "(за підтвердженими фактами).")
        lines.append(f"Непідтверджених (у підрахунок не входять): {u}.")

    elif template_id == "list_by_state":
        u_rows = unconfirmed or []
        if not rows:
            lines.append(f"Нікого: підтверджених записів «{state_lbl}» за "
                         f"{params['date_from']} — {params['date_to']} немає.")
        else:
            lines.append(f"{_people(len({r['name'] for r in rows}))} {state_lbl}:")
            for r in rows:
                lines.append(f"- {r['name']} — {DIM_LABEL.get(r['dim'], r['dim'])}"
                             f", {_fmt_period(r)} (документ №{r['source_doc_id']} у базі)")
        lines.append(f"Зріз: {params['date_from']} — {params['date_to']}{denom}.")
        if u_rows:
            lines.append(f"Окремо непідтверджені (потребують перевірки людиною, "
                         f"у підсумок не входять): {len(u_rows)}")
            for r in u_rows:
                lines.append(f"- {r['name']} — {DIM_LABEL.get(r['dim'], r['dim'])}"
                             f", {_fmt_period(r)} — непідтверджено")
        else:
            lines.append("Непідтверджених записів за цей період: 0.")

    elif template_id == "person_status":
        if not rows:
            lines.append(f"Не знайшла: у реєстрі немає особи за "
                         f"«{params.get('name', params['name_pattern'])}».")
        else:
            by_person = {}
            for r in rows:
                by_person.setdefault(r["name"], []).append(r)
            if len(by_person) > 1:
                lines.append(f"Знайдено {len(by_person)} осіб за запитом — "
                             "уточніть ПІБ повніше.")
            for name, facts in by_person.items():
                lines.append(f"{name}:")
                for r in facts:
                    mark = ("підтверджено" if r["status"] == "confirmed"
                            else "НЕ підтверджено, потребує перевірки")
                    period = ""
                    if r["valid_from"] or r["valid_to"]:
                        period = f", {_fmt_period(r)}"
                    lines.append(f"- {r['dim_name']}: {r['value']}{period} "
                                 f"[{mark}; документ №{r['source_doc_id']} у базі]")
        lines.append(f"Зріз: стан бази на {datetime.date.today()}.")

    elif template_id == "doc_by_number":
        if not rows:
            lines.append(f"Не знайшла документа з номером "
                         f"№{params['doc_number']} у базі. Номер не виправляю "
                         "і схожих не підставляю.")
        else:
            doc_ids = sorted({r["source_doc_id"] for r in rows})
            if len(doc_ids) > 1:
                lines.append(f"Документів із номером №{params['doc_number']} "
                             f"у базі {len(doc_ids)} — показую всі.")
            for did in doc_ids:
                facts = [r for r in rows if r["source_doc_id"] == did]
                who = facts[0]["name"]
                n_unc = sum(1 for r in facts if r["status"] != "confirmed")
                lines.append(f"Документ №{params['doc_number']} "
                             f"(запис №{did} у базі) — {who}:")
                for r in facts:
                    mark = ("" if r["status"] == "confirmed"
                            else " [НЕ підтверджено]")
                    period = ""
                    if r["valid_from"] or r["valid_to"]:
                        period = f", {_fmt_period(r)}"
                    lines.append(f"- {r['dim_name']}: {r['value']}{period}{mark}")
                if n_unc:
                    lines.append(f"Непідтверджених полів у документі: {n_unc} — "
                                 "вони не входять у підрахунки, доки людина "
                                 "не підтвердить.")

    elif template_id == "review_queue_count":
        if not rows:
            lines.append("Черга перевірки порожня: 0 нерозв'язаних записів.")
        else:
            total_q = sum(r["n"] for r in rows)
            lines.append(f"У черзі перевірки {_plural(total_q, 'запис', 'записи', 'записів')}:")
            for r in rows:
                lines.append(f"- {QUEUE_LABEL.get(r['queue_type'], r['queue_type'])}: {r['n']}")
        lines.append(f"Зріз: стан бази на {datetime.date.today()}.")

    elif template_id == "unconfirmed_count":
        n = rows[0]["n"]
        docs = rows[0]["docs"]
        lines.append(f"Непідтверджених фактів: {n} "
                     f"(у {_plural(docs or 0, 'документі', 'документах', 'документах')}).")
        lines.append("Це чернетки: у підрахунки вони не входять, доки людина "
                     "не підтвердить.")
        lines.append(f"Зріз: стан бази на {datetime.date.today()}.")

    elif template_id == "documents_count":
        total_d = sum(r["n"] for r in rows)
        lines.append(f"Документів у базі: {total_d}.")
        for r in rows:
            lines.append(f"- {r['domain']}: {r['n']}")
        lines.append(f"Зріз: стан бази на {datetime.date.today()}.")

    source = [f"шаблон каталогу: {template_id} ({t['title']})",
              "SQL шаблону:", t["sql"].strip()]
    return "\n".join(lines), source


# ── Ярус 2: нешаблонний SELECT під рейками ───────────────────────────────────

ALLOWED_TABLES = {"documents", "facts", "dimensions", "dimension_values",
                  "objects", "object_aliases", "object_kinds", "people",
                  "review_queue", "document_types", "equipment", "tasks"}

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|copy|"
    r"call|do|merge|comment|vacuum|analyze|set|reset|listen|notify|lock|"
    r"refresh|reindex|cluster|prepare|deallocate|begin|commit|rollback|"
    r"savepoint|into|pg_sleep|pg_read_file|pg_write|lo_import|lo_export|"
    r"dblink|current_setting)\b", re.I)


def validate_sql(sql):
    """-> (очищений SQL, None) або (None, причина відмови)."""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        return None, "порожній запит"
    if ";" in sql:
        return None, "кілька statement в одному запиті заборонені"
    if "--" in sql or "/*" in sql:
        return None, "коментарі в SQL заборонені"
    if not re.match(r"^select\b", sql, re.I):
        return None, "дозволений лише SELECT"
    if _FORBIDDEN.search(sql):
        return None, "запит містить заборонені слова (зміна даних/налаштувань)"
    tables = re.findall(r"\b(?:from|join)\s+([a-zA-Z_][\w.\"]*)", sql, re.I)
    for tbl in tables:
        name = tbl.strip('"').split(".")[-1].lower()
        if name not in ALLOWED_TABLES:
            return None, f"таблиця «{tbl}» поза дозволеним переліком"
    if not tables and "(" not in sql:
        return None, "запит без таблиці"
    # Фільтр підтвердженості перевіряє КОД, не довіра до моделі: підрахунок
    # по facts без фільтра confirmed не виконується взагалі.
    if re.search(r"\bfacts\b", sql, re.I) and "confirmed" not in sql.lower():
        return None, ("запит читає facts без фільтра підтвердженості "
                      "(status = 'confirmed') -- такий підрахунок змішав би "
                      "чернетки з фактами")
    m = re.search(r"\blimit\s+(\d+)", sql, re.I)
    if m:
        if int(m.group(1)) > 200:
            sql = re.sub(r"\blimit\s+\d+", "LIMIT 200", sql, flags=re.I)
    else:
        sql += " LIMIT 200"
    return sql, None


SQL_SCHEMA = {
    "type": "object",
    "properties": {"sql": {"type": "string"}},
    "required": ["sql"],
    "additionalProperties": False,
}

DB_SCHEMA_HINT = """Схема PostgreSQL (тільки читання):
- documents(id, source_kind, status, domain, uploaded_at, processed_at) -- реєстр документів; domain: leave/deployment/...
- objects(id, kind_id, canonical_name) -- особи та інші об'єкти; kind_id -> object_kinds(id, code)
- people(object_id, last_name, first_name, patronymic, ...) -- розширення осіб
- dimensions(id, code, name, validity_model) -- виміри фактів: rank, leave, leave_place, leave_days, deployment_location, deployment_days, document_number, document_date, order_number, unit_to_report...
- facts(id, object_id -> objects, dimension_id -> dimensions, value text, valid_from date, valid_to date, status 'confirmed'/'unconfirmed'/'rejected', confidence, source_doc_id -> documents)
- review_queue(id, document_id, fact_id, queue_type, created_at, resolved_at)
Правила: РІВНО ОДИН SELECT, без крапки з комою, без коментарів, без DML/DDL.
Якщо читаєш facts -- ОБОВ'ЯЗКОВО фільтр f.status = 'confirmed'.
Додай LIMIT 200. Різниця двох date у PostgreSQL -- уже ЦІЛЕ число днів
(без EXTRACT): тривалість = f.valid_to - f.valid_from + 1.
Конкретний вимір фільтруй через JOIN dimensions d ... AND d.code = '...'.
valid_from/valid_to заповнені лише у вимірах 'leave' і 'deployment_location';
числові тривалості лежать як текст у f.value вимірів 'leave_days' /
'deployment_days' (приводь: f.value::int)."""


def tier2_answer(question):
    """-> (text, source_lines). Не склалось -> чесна відмова, без другої
    спроби моделі «аби щось віддати»."""
    refuse = ("Не знайшла відповіді: питання не лягає на жоден шаблон, а "
              "скласти безпечний запит не вдалося. Спробуйте перефразувати "
              "(наприклад, назвіть стан -- відпустка/відрядження, дату або "
              "номер документа).")
    data = _model_json(
        "Ти складаєш ОДИН SQL SELECT до PostgreSQL за питанням користувача. "
        "Поверни JSON {\"sql\": \"...\"}. Нічого не пояснюй.\n" + DB_SCHEMA_HINT,
        question, SQL_SCHEMA)
    if not data:
        return refuse, ["нешаблонний запит: модель не відповіла"]
    sql, reason = validate_sql(data.get("sql", ""))
    if sql is None:
        return (f"Не знайшла: згенерований запит відхилено валідатором "
                f"({reason}). Спробуйте перефразувати питання."), \
               ["нешаблонний запит, відхилено валідатором",
                f"причина: {reason}", "SQL моделі:", str(data.get("sql", ""))]
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                # Друга лінія оборони понад параметром у DSN: read-only на
                # рівні сесії -- DML відіб'є сама база, не лише наш парсер.
                cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
                cur.execute(sql)
                rows = cur.fetchall()
    except psycopg.Error as exc:
        return ("Не знайшла: запит до бази не виконався "
                f"({type(exc).__name__}). Спробуйте перефразувати."), \
               ["нешаблонний запит, помилка виконання", "SQL:", sql]
    src = ["НЕШАБЛОННИЙ ЗАПИТ: SQL склала модель, виконано read-only "
           "з валідатором (один SELECT, allowlist таблиць, LIMIT 200, "
           "таймаут 5 с)", "SQL:", sql]
    if not rows or all(v is None for r in rows for v in r.values()):
        return ("Не знайшла: запит виконано, але даних немає. "
                "Спробуйте перефразувати питання."), src
    lines = ["Відповідь на нешаблонний запит (перевірте джерело у згортці):"]
    cols = list(rows[0].keys())
    lines.append(" | ".join(cols))
    for r in rows[:50]:
        lines.append(" | ".join("" if r[c] is None else str(r[c]) for c in cols))
    if len(rows) > 50:
        lines.append(f"... і ще {len(rows) - 50} рядків (обрізано)")
    try:
        u = _run_template_sql(
            "SELECT COUNT(*) AS n FROM facts WHERE status = 'unconfirmed'", {})
        lines.append(f"Довідково: непідтверджених фактів у базі {u[0]['n']} — "
                     "у підрахунки вони не входять.")
    except psycopg.Error:
        pass
    return "\n".join(lines), src


# ── Головна точка входу ──────────────────────────────────────────────────────

REFUSE_TEXT = ("Це питання не про дані бази документів. Я відповідаю про "
               "відпустки, відрядження, документи та чергу перевірки.")

# Явно деструктивні прохання ріжемо правилами ще ДО моделі: чат нічого не
# змінює в базі за визначенням (read-only), і чесно про це каже.
_DESTRUCTIVE = re.compile(
    r"вида[лл]|знищ|очист|drop\b|delete\b|truncate\b|update\b|insert\b|"
    r"зміни\s|встанов\s|підтверд[иь]\b", re.I)


def answer_question(question):
    """-> dict {text, source, tier, template, elapsed_s, model_used}."""
    started = time.perf_counter()
    question = (question or "").strip()
    meta = {"tier": None, "template": None, "model_used": False}

    if not question:
        text, source = "Порожнє питання.", []
    elif len(question) > 500:
        text, source = "Занадто довге питання (понад 500 символів).", []
    elif _DESTRUCTIVE.search(question):
        text = ("Відхилено: чат працює з базою лише в режимі читання і не "
                "змінює та не видаляє дані. Зміни в базі -- через сторінку "
                "завантаження і перевірку людиною.")
        source = ["зупинено правилами ще до звернення до бази чи моделі"]
        meta["tier"] = "guard"
    else:
        routed = rules_route(question)
        if routed is None and _AGGREGATE.search(question.lower()):
            # агрегатне питання без шаблону -- одразу ярус 2
            if _get_model() is None:
                text = ("Не знайшла шаблону для цього питання. " + MODEL_NOTE +
                        ". Спробуйте перефразувати простіше "
                        "(скільки/хто/на яку дату).")
                source = ["агрегатне питання поза каталогом; модель недоступна"]
                meta["tier"] = "rules_only_fallback"
            else:
                text, source = tier2_answer(question)
                meta.update(tier="tier2_free_sql", model_used=True)
        elif routed:
            tid, params = routed
            try:
                text, source = run_template(tid, params)
            except psycopg.Error as exc:
                text = f"База даних недоступна: {type(exc).__name__}."
                source = []
            meta.update(tier="template_rules", template=tid)
        else:
            m_routed = model_route(question)
            if m_routed is None and _get_model() is None:
                text = ("Не знайшла шаблону для цього питання. " + MODEL_NOTE +
                        ". Спробуйте одне з питань-прикладів або "
                        "перефразуйте (стан/дата/ПІБ/№ документа).")
                source = ["правила не впізнали питання; модель недоступна"]
                meta["tier"] = "rules_only_fallback"
            elif m_routed is None:
                text = ("Не знайшла: не вдалося зіставити питання зі "
                        "шаблоном. Спробуйте перефразувати.")
                source = ["модель не змогла обрати шаблон"]
                meta.update(tier="model_route_failed", model_used=True)
            else:
                tid, params = m_routed
                meta["model_used"] = True
                if tid == "відмова":
                    text, source = REFUSE_TEXT, ["модель-класифікатор: відмова"]
                    meta["tier"] = "model_refuse"
                elif tid == "вільний_sql":
                    text, source = tier2_answer(question)
                    meta["tier"] = "tier2_free_sql"
                else:
                    try:
                        text, source = run_template(tid, params)
                    except psycopg.Error as exc:
                        text = f"База даних недоступна: {type(exc).__name__}."
                        source = []
                    meta.update(tier="template_model", template=tid)

    return {"text": text, "source": source,
            "elapsed_s": round(time.perf_counter() - started, 1), **meta}
