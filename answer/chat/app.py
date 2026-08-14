# Вікно чата тестового стенду. Капстоун №21, епік corp-study#84 «Чат».
#
# Схема роботи:
#   питання → маршрутизація (окремий виклик моделі через OpenRouter або правила)
#           → дорога: підрахунок | довідник | цитата | діагностика | відмова
#   Живі дороги: підрахунок (сім функцій db.py), довідник (search_reference),
#   відмова. «Цитата» і «діагностика» розпізнаються, але відповідь чесна:
#   таких даних на стенді немає.
#
# Рішення, прийняті самостійно (Ден недоступний):
#   - Текст КОЖНОЇ відповіді формує код (шаблонні рядки), не модель.
#     Модель робить лише два структуровані виклики: маршрут і параметри.
#     Так модель фізично не може вигадати цифру чи цитату.
#   - Порядок перебору доріг у правилах: діагностика → довідник → цитата →
#     підрахунок → відмова (як у постановці задачі; у ТЗ #52 помилка).
#     Виняток: явні сутності підрахунку (№ документа, дата ISO) перемагають
#     ключові слова інших доріг, інакше «Хто у відпустці за квитком №301»
#     пішов би в довідник через слово «відпустка».
#   - Дата в правилах розпізнається лише як YYYY-MM-DD. Словесні дати
#     («15 травня») без моделі не розбираються — тоді одне уточнення.
#   - Якщо після уточнення дати все одно немає — чесна відмова, дефолтну
#     дату не підставляємо.

import os
import sys
import json
import re
import ssl
import urllib.request
import urllib.error

os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import db  # noqa: E402  (сім функцій стику, docs/contracts/2026-08-14_chat-db-interface.md)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-2.5-flash"
WARN_FALLBACK = "⚠️ модель недоступна, маршрут обрано правилами"
CLARIFY_MARK = "🔎 уточнення"

ROUTES = ["діагностика", "довідник", "цитата", "підрахунок", "відмова"]
INTENTS = ["хто_відсутній", "хто_повертається", "документи_людини",
           "документ_за_номером", "зведення_по_підрозділах"]

SUBDIVISIONS = ["1-ша механізована рота", "2-га механізована рота",
                "3-тя механізована рота", "Взвод забезпечення",
                "Управління батальйону"]

# ── OpenRouter ───────────────────────────────────────────────────────────────


def build_ssl_context():
    """Python з python.org часто не має кореневих сертифікатів — беремо з certifi."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


SSL_CONTEXT = build_ssl_context()


def _read_env_file():
    vals = {}
    path = os.path.join(HERE, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip().strip("\"'")
    return vals


_ENV = _read_env_file()


def get_api_key():
    # Ключ ніколи не друкується і не логується
    return (os.environ.get("OPENROUTER_API_KEY", "").strip()
            or _ENV.get("OPENROUTER_API_KEY", ""))


def get_model():
    return (os.environ.get("OPENROUTER_MODEL", "").strip()
            or _ENV.get("OPENROUTER_MODEL", "") or DEFAULT_MODEL)


def openrouter_json(system, user, schema, schema_name):
    """Один структурований виклик: json_schema (strict), temperature 0."""
    body = json.dumps({
        "model": get_model(),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": schema_name, "strict": True, "schema": schema}},
    }).encode("utf-8")
    req = urllib.request.Request(
        OPENROUTER_URL, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + get_api_key()})
    with urllib.request.urlopen(req, timeout=60, context=SSL_CONTEXT) as r:
        data = json.load(r)
    return json.loads(data["choices"][0]["message"]["content"])


ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": ROUTES},
        "clarify_question": {"type": ["string", "null"]},
    },
    "required": ["route", "clarify_question"],
    "additionalProperties": False,
}

ROUTE_SYSTEM = """Ти — маршрутизатор питань у системі обліку відсутностей \
військової частини. Не відповідай на питання. Обери одну дорогу:
- підрахунок: хто відсутній/повертається на дату, документи людини, документ \
за номером, зведення по підрозділах. Приклади: «Хто поза частиною 15 травня?», \
«Хто у відпустці за квитком №301?», «Скільки відсутніх по ротах?», \
«Що відомо про Петренка?», «Де зараз Петренко?», «Хто буде на місці 15 травня?», \
«Який номер і дата видачі в №304?» (питання про документ №NNN — завжди \
підрахунок, навіть якщо в номері дивні символи)
- довідник: як щось оформити, порядок, правила, строки, інструкції. \
Приклад: «За скільки днів подавати рапорт на відпустку?»
- цитата: просять дослівний текст наказу чи документа.
- діагностика: стан чи несправність техніки і систем.
- відмова: питання не лягає на жодну дорогу.
clarify_question заповнюй тільки якщо без уточнення відповісти не можна \
(наприклад, не названа дата), інакше null."""

PARAMS_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "date": {"type": ["string", "null"]},
        "subdivision": {"type": ["string", "null"]},
        "name": {"type": ["string", "null"]},
        "doc_number": {"type": ["string", "null"]},
    },
    "required": ["intent", "date", "subdivision", "name", "doc_number"],
    "additionalProperties": False,
}

PARAMS_SYSTEM = """Витягни параметри запиту до бази відсутностей. intent:
- хто_відсутній: хто поза частиною в конкретний день
- хто_повертається: у кого в цей день закінчується відсутність
- документи_людини: документи конкретної людини (є ПІБ або UNIT-XXXX)
- документ_за_номером: питання про документ №NNN (квиток, наказ)
- зведення_по_підрозділах: скільки відсутніх по кожному підрозділу
date — у форматі YYYY-MM-DD або null. subdivision — назва підрозділу як у \
питанні або null. name — ПІБ чи його частина, або UNIT-XXXX, або null. \
doc_number — номер документа разом зі знаком №, або null.
Приклади:
- «Хто з 2-ї роти буде на місці 2026-05-15?» → intent=хто_відсутній, \
subdivision=2-ї роти, date=2026-05-15. «Буде на місці» питає командир, що \
складає наряд, — йому потрібен список відсутніх, не присутніх.
- «Хто ще не повернувся станом на 2026-05-20?» → intent=хто_відсутній, \
date=2026-05-20 (питають про тих, хто досі відсутній)
- «Хто має повернутись 2026-05-24?» → intent=хто_повертається, date=2026-05-24
- «Що відомо про Петренка?» або «Де зараз Петренко?» → \
intent=документи_людини, name=Петренко"""


# ── Витяг параметрів правилами (fallback і нормалізація) ─────────────────────


def normalize_subdivision(text):
    """'2-ї механізованої роти' → '2-га механізована рота'. Не впізнав → None."""
    if not text:
        return None
    low = text.lower()
    for s in SUBDIVISIONS:
        if low == s.lower():
            return s
    m = re.search(r"([123])\s*-?\s*[а-яіїєґ]*\s*механізован", low)
    if m:
        return SUBDIVISIONS[int(m.group(1)) - 1]
    if "забезпеченн" in low:
        return "Взвод забезпечення"
    if "управлінн" in low:
        return "Управління батальйону"
    return None


def extract_date(text):
    m = re.search(r"\d{4}-\d{2}-\d{2}", text or "")
    return m.group(0) if m else None


def extract_doc_number(text):
    # \w у Python ловить і кириличну О — шум OCR (№3О4) лишається як є
    m = re.search(r"№\s*(\w+)", text or "")
    return "№" + m.group(1) if m else None


STOP_CAPS = {"Хто", "Скільки", "Який", "Яка", "Яке", "Які", "Коли", "Де",
             "Чи", "Чому", "Що", "Покажи", "Дай", "Але", "UNIT"}


def extract_name(text):
    """Слово з великої літери, що знаходиться в реєстрі або в документах."""
    m = re.search(r"UNIT-\d{4}", text)
    if m:
        return m.group(0)
    for tok in re.findall(r"[А-ЯІЇЄҐ][а-яіїєґ']{3,}", text):
        if tok in STOP_CAPS:
            continue
        # відмінок: «Цісика» → пробуємо і обрізані форми («Цісик», «Ціси»)
        for stem in (tok, tok[:-1], tok[:-2]):
            if len(stem) < 4:
                break
            if db.find_people(name=stem) or db.absences_for_person(stem, only_active=False):
                return stem
    return None


def rules_params(question):
    low = question.lower()
    params = {
        "date": extract_date(question),
        "subdivision": normalize_subdivision(question),
        "name": None,
        "doc_number": extract_doc_number(question),
        "intent": None,
    }
    if params["doc_number"]:
        params["intent"] = "документ_за_номером"
        return params
    params["name"] = extract_name(question)
    if "на місці" in low or "не поверн" in low:
        # «буде на місці», «ще не повернувся» — питають про список відсутніх
        params["intent"] = "хто_відсутній"
    elif "поверта" in low or "поверн" in low:
        params["intent"] = "хто_повертається"
    elif "підрозділ" in low or "зведенн" in low or "по ротах" in low:
        params["intent"] = "зведення_по_підрозділах"
    elif params["name"]:
        params["intent"] = "документи_людини"
    elif "хто" in low and re.search(r"відсутн|поза частин|відпустк|відрядж", low):
        params["intent"] = "хто_відсутній"
    elif "скільки" in low and "відсутн" in low:
        params["intent"] = "зведення_по_підрозділах"
    return params


DIAG_WORDS = ("несправн", "не запуска", "не працює", "діагност", "полама",
              "зламал", "стан техніки", "стан систем")
QUOTE_WORDS = ("цитат", "процитуй", "дослівно", "повний текст", "текст наказу",
               "текст документа")
REF_WORDS = ("як ", "поряд", "правил", "інструкц", "строк", "підпис",
             "рапорт", "оформ", "підключ", "мереж", "генератор", "паспорт",
             "що робити", "чи можна", "дозвол")


def rules_route(question):
    """Порядок перебору: діагностика → довідник → цитата → підрахунок → відмова.
    Явні сутності підрахунку (№, дата, «хто…») мають пріоритет — див. шапку."""
    low = question.lower()
    p = rules_params(question)
    if any(w in low for w in DIAG_WORDS):
        return "діагностика"
    # явне прохання про дослівний текст — цитата, навіть якщо в питанні є №
    if any(w in low for w in QUOTE_WORDS):
        return "цитата"
    if p["intent"] and (p["doc_number"] or p["date"] or p["name"]
                        or p["intent"] == "зведення_по_підрозділах"
                        or low.startswith("хто")):
        return "підрахунок"
    if any(w in low for w in REF_WORDS) and db.search_reference(question):
        return "довідник"
    # «скільки…» без розпізнаного intent — усе одно спроба підрахунку:
    # dispatch_count поверне None, і питання піде далі як «цитата»
    if p["intent"] or "скільки" in low or "кільк" in low:
        return "підрахунок"
    return "відмова"


# ── Шматки відповіді ─────────────────────────────────────────────────────────


def footer(route, source="—", cut=None):
    lines = [f"дорога: {route}", f"джерело: {source}"]
    if cut:
        lines.append(cut)
    return "\n\n" + "\n".join(lines)


def unconfirmed_note(date):
    """Скільки чинних записів на дату без service_id — їх не видно
    в розбивці по підрозділах."""
    n = sum(1 for r in db.absences_on_date(date) if not r["service_id"])
    return (f"зріз: {date} · непідтверджених записів (без service_id): {n} — "
            "їх не видно в розбивці по підрозділах")


def person_label(row):
    name = row["person_name_raw"] or "(ПІБ у документі порожній)"
    mark = "" if row["service_id"] else " — не підтверджено реєстром"
    return name + mark


def doc_source(rows):
    return ", ".join(f"{r['doc_number']} ({r['source_file']})" for r in rows) or "—"


def describe_doc(r):
    """Один документ: рядок фактів + чесні позначки про дефекти даних."""
    period = f"{r['date_from']} – {r['date_to']}" if (r["date_from"] or r["date_to"]) else "період не вказано"
    out = [f"{r['doc_number']} від {r['doc_date']} · {r['doc_type']} · "
           f"{period} · {r['place']} · статус: {r['status']}"]
    if r["reason"]:
        out.append(f"  підстава: {r['reason']}")
    empty = [label for key, label in [
        ("service_id", "service_id"), ("person_name_raw", "ПІБ"),
        ("date_from", "дата початку"), ("date_to", "дата завершення")]
        if not r[key]]
    if empty:
        out.append(f"  ⚠️ порожні поля: {', '.join(empty)} — з документа їх "
                   "встановити не можна, нічого не підставляємо")
    if r["date_from"] and r["date_to"] and r["date_to"] < r["date_from"]:
        out.append(f"  ⚠️ суперечність у документі: дата завершення "
                   f"({r['date_to']}) раніше за дату початку ({r['date_from']}). "
                   "Показано як є, не виправляємо")
    if r["status"] == "скасований":
        why = (f"скасований документом {r['superseded_by']}"
               if r["superseded_by"] else "скасований")
        out.append(f"  документ не діє: {why}")
    return "\n".join(out)


# ── Дорога «підрахунок»: диспетчер intent → функція db.py ────────────────────


def answer_absent(date, subdivision, not_returned=False):
    rows = db.absences_on_date(date, subdivision=subdivision)
    where = f" з підрозділу «{subdivision}»" if subdivision else ""
    items = [f"- {person_label(r)} · {r['doc_type']} {r['doc_number']}, "
             f"{r['date_from']} – {r['date_to']}, {r['place']}" for r in rows]
    if not_returned:
        # питання №3 замовника: відмітки про фактичне повернення в даних
        # немає (docs/contracts/2026-08-14_chat-db-interface.md, «Що з цього НЕ покривається») — застереження першим
        head = ("У документах немає відмітки про фактичне повернення, тому "
                "відповісти точно система не може. За документами відсутність "
                "ще триває у:")
        tail = "\n".join(items) if items else (
            f"— ні в кого: на {date}{where} чинних документів про "
            "відсутність немає.")
        body = head + "\n" + tail
    elif not rows:
        body = f"На {date}{where} чинних документів про відсутність немає."
    else:
        body = f"Поза частиною {date}{where} — {len(rows)}:\n" + "\n".join(items)
    return body + footer("підрахунок", doc_source(rows), unconfirmed_note(date))


def answer_returning(date, subdivision):
    rows = db.returning_on_date(date, subdivision=subdivision)
    where = f" по підрозділу «{subdivision}»" if subdivision else ""
    if not rows:
        body = (f"На {date}{where} немає чинних документів, у яких цього дня "
                "закінчується відсутність.")
    else:
        items = [f"- {person_label(r)} · {r['doc_type']} {r['doc_number']}, "
                 f"відсутність до {r['date_to']}" for r in rows]
        body = f"Повертаються {date}{where}:\n" + "\n".join(items)
    body += ("\n\nВажливо: це дата завершення за документом. Відмітки про "
             "фактичне повернення в даних немає.")
    return body + footer("підрахунок", doc_source(rows), unconfirmed_note(date))


def answer_person(name):
    people = db.find_people(name=name)
    rows = db.absences_for_person(name, only_active=False)
    parts = []
    if people:
        p = people[0]
        parts.append(f"У реєстрі: {p['full_name']}, {p['rank']}, "
                     f"{p['position_title']}, {p['subdivision']}.")
        if len(people) > 1:
            parts.append(f"(збігів у реєстрі за «{name}»: {len(people)}, "
                         "показано першого; уточніть ПІБ повніше)")
    else:
        parts.append(f"У реєстрі частини людини за «{name}» немає." + (
            " Документи нижче знайдено за ПІБ у самому документі — "
            "реєстром вони не підтверджені." if rows else ""))
    if not rows:
        parts.append("Документів про відсутність немає — отже, за документами "
                     "людина у частині. Це нормальна відповідь, не помилка.")
    else:
        parts.append(f"Документи ({len(rows)}):")
        parts += [describe_doc(r) for r in rows]
    return "\n".join(parts) + footer("підрахунок", doc_source(rows))


def doc_number_warning(doc_number):
    """№3О4 — кирилична «О» серед цифр: шум OCR. Назвати, не виправляти."""
    tail = (doc_number or "").lstrip("№").strip()
    if tail and not tail.isdigit():
        return (f"⚠️ у номері документа нецифровий символ (можливий шум "
                f"розпізнавання): №{tail} — місце, де система не впевнена")
    return None


def answer_doc(doc_number):
    rows = db.document_by_number(doc_number)
    warn = doc_number_warning(doc_number)
    if not rows:
        body = (f"Документа {doc_number} у базі стенду немає. Номер не "
                "виправляємо і схожих не підставляємо.")
        if warn:
            body += "\n" + warn
        return body + footer("підрахунок", "—")
    parts = []
    if warn:
        parts.append(warn)
    if len(rows) > 1:
        parts.append(f"Знайдено {len(rows)} документи з номером {doc_number} — "
                     "показуємо обидва:")
        parts += [describe_doc(r) for r in rows]
        active = [r for r in rows if r["status"] == "чинний"]
        latest = max(active or rows, key=lambda r: r["doc_date"])
        parts.append(f"Чинним вважається пізніший — від {latest['doc_date']} "
                     f"({latest['source_file']}).")
    else:
        parts.append(describe_doc(rows[0]))
    return "\n".join(parts) + footer("підрахунок", doc_source(rows))


def answer_summary(date):
    rows = db.count_absent_by_subdivision(date)
    items = [f"- {r['subdivision']}: відсутні {r['absent']} з {r['total']}"
             for r in rows]
    total_abs = sum(r["absent"] for r in rows)
    total = sum(r["total"] for r in rows)
    body = (f"Зведення по підрозділах на {date}:\n" + "\n".join(items) +
            f"\nРазом: відсутні {total_abs} з {total}.")
    src = "absences + people (розрахунок по чинних документах)"
    return body + footer("підрахунок", src, unconfirmed_note(date))


def dispatch_count(params, clarified, clarify_hint, question=""):
    """→ готова відповідь, або ('clarify', текст), або None = шаблон не підійшов."""
    intent = params.get("intent")
    date = extract_date(params.get("date") or "")
    sub = normalize_subdivision(params.get("subdivision") or "")
    name = (params.get("name") or "").strip() or None
    doc_number = params.get("doc_number")

    if intent == "документ_за_номером" and doc_number:
        return answer_doc(doc_number)
    if intent == "документи_людини" and name:
        return answer_person(name)
    if intent in ("хто_відсутній", "хто_повертається", "зведення_по_підрозділах"):
        if not date:
            if not clarified:
                q = clarify_hint or "За яку дату рахувати? Назвіть у форматі YYYY-MM-DD (дані стенду — травень 2026)."
                return ("clarify", q)
            return ("Відповісти не можу: дата зрізу так і не названа у форматі "
                    "YYYY-MM-DD, а підставляти дату самостійно система не має "
                    "права." + footer("відмова"))
        if intent == "хто_відсутній":
            return answer_absent(date, sub,
                                 not_returned="поверн" in question.lower())
        if intent == "хто_повертається":
            return answer_returning(date, sub)
        return answer_summary(date)
    if intent == "документи_людини" and not name:
        if not clarified:
            return ("clarify", "Про кого йдеться? Назвіть прізвище або номер UNIT-XXXX.")
        return ("Відповісти не можу: не вдалося зрозуміти, про яку людину "
                "йдеться." + footer("відмова"))
    return None  # шаблон підрахунку не підійшов → далі пробуємо «цитату»


# ── Нежива дорога і відмова — чесні шаблони ──────────────────────────────────


ANSWER_QUOTE = ("Такого типу даних на стенді немає: повних текстів наказів і "
                "документів система не зберігає. Є лише облікові поля "
                "(номер, дати, тип, статус, місце, файл-джерело). Бракує: "
                "самі тексти документів." + footer("цитата"))

ANSWER_DIAG = ("Такого типу даних на стенді немає: телеметрії, журналів стану "
               "техніки й систем стенд не має. Є реєстр людей, документи про "
               "відсутність і три довідкові документи. Бракує: дані про "
               "фактичний стан техніки." + footer("діагностика"))

ANSWER_REFUSE = ("На це система відповісти не може, бо питання не лягає на "
                 "жодну з її доріг: підрахунок відсутностей за документами, "
                 "довідник внутрішніх документів, цитати чи діагностика."
                 + footer("відмова"))

# питання №6 замовника (docs/contracts/2026-08-14_chat-db-interface.md): відмова з конкретною причиною
ANSWER_NO_QUOTA = ("Відповісти не можу: в базі немає норми, скільки людей "
                   "одночасно дозволено відпустити з підрозділу; це питання "
                   "до замовника." + footer("відмова"))


def is_quota_question(low):
    return bool("чи можна" in low and re.search(
        r"(відпустити|дозволити|випустити)\s+ще|ще\s+одн", low))


def answer_reference(question):
    hits = db.search_reference(question, limit=3)
    if not hits:
        return ("У довіднику частини нічого не знайшлося за цим питанням. "
                "У ньому лише три документи: порядок оформлення відпустки, "
                "підключення до службової мережі, техпаспорт ДГУ-10."
                + footer("довідник"))
    best = hits[0]
    src = (f"{best['doc_title']}, розділ {best['section_number']} "
           f"({best['source_note']})")
    quote = "\n".join("> " + line for line in best["text"].splitlines() if line)
    body = (f"Найближчий розділ довідника — «{best['section_title']}» "
            f"(розділ {best['section_number']}):\n\n{quote}")
    if len(hits) > 1:
        more = "; ".join(f"{h['doc_title']}, розділ {h['section_number']}"
                         for h in hits[1:])
        body += f"\n\nЩе дотичні розділи: {more}."
    return body + footer("довідник", src)


# ── Головна функція. Викликається і з Gradio, і без нього ────────────────────


def _merge_clarification(question, history):
    """Попередня відповідь бота — уточнення? Склеїти з питанням до нього.
    Стану не тримаємо: все читається з history. Друге уточнення заборонене."""
    msgs = []
    for m in history or []:
        role = m.get("role") if isinstance(m, dict) else None
        content = m.get("content") if isinstance(m, dict) else None
        if role in ("user", "assistant") and isinstance(content, str):
            msgs.append((role, content))
    if msgs and msgs[-1][0] == "assistant" and msgs[-1][1].startswith(CLARIFY_MARK):
        prev_user = next((c for r, c in reversed(msgs[:-1]) if r == "user"), "")
        return f"{prev_user}\n{question}", True
    return question, False


def answer(question, history=None):
    question = (question or "").strip()
    if not question:
        return "Поставте питання." + footer("відмова")
    merged, clarified = _merge_clarification(question, history)
    if is_quota_question(merged.lower()):
        # детерміновано, без моделі: причина відмови відома наперед
        return ANSWER_NO_QUOTA

    model_ok = bool(get_api_key())  # без ключа мережу навіть не пробуємо
    route, clarify_hint, params, fallback = None, None, None, not model_ok
    if model_ok:
        try:
            r = openrouter_json(ROUTE_SYSTEM, merged, ROUTE_SCHEMA, "route")
            route = r.get("route") if r.get("route") in ROUTES else None
            clarify_hint = r.get("clarify_question") or None
            if route == "підрахунок":
                p = openrouter_json(PARAMS_SYSTEM, merged, PARAMS_SCHEMA, "params")
                if p.get("intent") in INTENTS:
                    params = p
        except Exception:
            route = None
        if route is None:
            fallback = True
    if fallback or route is None:
        route = rules_route(merged)
    elif route == "відмова" and extract_doc_number(merged):
        # явна сутність підрахунку (№ документа) перемагає — див. шапку.
        # Модель інколи відмовляє на номер із шумом OCR (№3О4)
        route = "підрахунок"
    if route == "підрахунок" and params is None:
        params = rules_params(merged)

    if route == "підрахунок":
        result = dispatch_count(params, clarified, clarify_hint, merged)
        if isinstance(result, tuple) and result[0] == "clarify":
            out = f"{CLARIFY_MARK}\n{result[1]}" + footer("підрахунок (потрібне уточнення)")
        elif result is None:
            # порядок перебору: підрахунок не підійшов → цитата, відмова остання
            out = ("Порахувати це за наявними полями не можна (у документах "
                   "немає таких даних).\n" + ANSWER_QUOTE)
        else:
            out = result
    elif route == "довідник":
        out = answer_reference(merged)
    elif route == "цитата":
        out = ANSWER_QUOTE
    elif route == "діагностика":
        out = ANSWER_DIAG
    else:
        out = ANSWER_REFUSE

    if fallback:
        if out.startswith(CLARIFY_MARK):
            out = CLARIFY_MARK + "\n" + WARN_FALLBACK + out[len(CLARIFY_MARK):]
        else:
            out = WARN_FALLBACK + "\n\n" + out
    return out


# ── Вікно ────────────────────────────────────────────────────────────────────


def main():
    import gradio as gr

    status = (f"модель: {get_model()} (OpenRouter)" if get_api_key()
              else "ключа OPENROUTER_API_KEY немає — працюють правила (⚠️ у відповідях)")

    demo = gr.ChatInterface(
        fn=answer,
        title="Чат обліку особового складу — тестова версія",
        description=(
            "Питання звичайними словами — відповідь із документів, з назвою "
            "джерела. Всі дані вигадані, за травень 2026. "
            "Приклади питань — під полем вводу. "
            f"Технічний стан: {status}."),
        examples=[
            "Хто з 2-ї механізованої роти буде поза частиною 2026-05-15?",
            "Хто повертається 2026-05-24?",
            "Хто у відпустці за квитком №301?",
            "Скільки відсутніх по підрозділах на 2026-05-15?",
            "За скільки днів подавати рапорт на відпустку?",
            "Що робити, якщо захворів у відпустці?",
        ],
    )
    demo.launch(server_name="127.0.0.1", server_port=7860, share=False,
                inbrowser=False)


if __name__ == "__main__":
    main()
