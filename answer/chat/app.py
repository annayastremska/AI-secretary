# Вікно чата тестового стенду — варіант на локальній MamayLM (Ollama) замість
# OpenRouter. Дубльовано з ../app.py (Денис), щоб не чіпати основний файл --
# порівняти окремо, перш ніж міняти дефолт (латентність CPU-моделі реальна,
# див. коментар біля mamaylm_json нижче).
#
# Схема роботи -- та сама, що в оригіналі, тільки маршрутизатор ходить у
# локальну Ollama, не в хмару:
#   питання → маршрутизація (окремий виклик MamayLM через Ollama або правила)
#           → дорога: підрахунок | довідник | цитата | діагностика | відмова
#   Живі дороги: підрахунок (сім функцій db.py), довідник (search_reference),
#   відмова. «Цитата» і «діагностика» розпізнаються, але відповідь чесна:
#   таких даних на стенді немає.
#
# Рішення з оригіналу, лишені без змін (Ден недоступний):
#   - Текст КОЖНОЇ відповіді формує код (шаблонні рядки), не модель.
#     Модель робить лише два структуровані виклики: маршрут і параметри.
#     Так модель фізично не може вигадати цифру чи цитату.
#   - Порядок перебору доріг у правилах: діагностика → довідник → цитата →
#     підрахунок → відмова. Виняток: явні сутності підрахунку (№ документа,
#     дата ISO) перемагають ключові слова інших доріг.
#   - Якщо після уточнення дати все одно немає — чесна відмова, дефолтну
#     дату не підставляємо.
#   - Дані стенду -- травень 2026: якщо в питанні рік не названо, модель
#     сама підставляє 2026 (виправлення від 14.08, перенесено сюди теж).

import os
import sys
import json
import re
import difflib
import datetime
import urllib.request
import urllib.error

os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import db  # noqa: E402  (сім функцій стику, docs/contracts/2026-08-14_chat-db-interface.md)

WARN_FALLBACK = "⚠️ локальна модель недоступна, маршрут обрано правилами"
CLARIFY_MARK = "🔎 уточнення"

# Військовий етикет доповіді: вітання на вході, кожна відповідь — з «Доповідаю».
# Це обгортка над текстом, а не частина фактів: шаблони відповідей нижче не
# чіпаємо, інакше довелось би правити кожен із них і тест на них.
GREETING = "Бажаю здоров'я! Готовий доповідати за обліком особового складу."
REPORT_MARK = "Доповідаю"

ROUTES = ["діагностика", "довідник", "цитата", "підрахунок", "відмова"]
INTENTS = ["хто_відсутній", "хто_повертається", "документи_людини",
           "документ_за_номером", "зведення_по_підрозділах"]

SUBDIVISIONS = ["1-ша механізована рота", "2-га механізована рота",
                "3-тя механізована рота", "Взвод забезпечення",
                "Управління батальйону"]

# ── MamayLM через Ollama (локально, без хмари) ───────────────────────────────


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

DEFAULT_MODEL = "mamaylm-4b"  # ~2х швидше за 12B на CPU (виміряно: ~44с проти ~90с
                              # на два виклики маршрут+параметри), та сама якість
                              # на перевірених питаннях. Повернутись на 12B --
                              # OLLAMA_MODEL=mamaylm в .env або env-змінній.
OLLAMA_HOST = (os.environ.get("OLLAMA_HOST", "").strip()
               or _ENV.get("OLLAMA_HOST", "") or "http://localhost:11434")


def get_model():
    return (os.environ.get("OLLAMA_MODEL", "").strip()
            or _ENV.get("OLLAMA_MODEL", "") or DEFAULT_MODEL)


def ollama_available():
    """Швидка перевірка -- Ollama взагалі відповідає, ще до дорогого виклику
    моделі. Так само чесно, як і перевірка ключа в оригіналі: без цього
    мережу навіть не пробуємо."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def mamaylm_json(system, user, schema, schema_name):
    """Один структурований виклик до локальної Ollama: та сама ідея, що
    openrouter_json в оригіналі (system, user, schema) -> розпарсений dict,
    тільки JSON-схема йде в параметр format (Ollama-нативний механізм), не в
    OpenAI-стиль response_format.

    Таймаут навмисно великий (120с, не 60): 12B на CPU без GPU реально
    повільніший за хмарну модель -- одне структуроване рішення тут коштує
    секунди, не долі секунди. schema_name не використовується (Ollama не
    вимагає імені схеми), лишений у сигнатурі для сумісності виклику з
    оригіналом."""
    body = json.dumps({
        "model": get_model(),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "stream": False,
        "format": schema,
        "options": {"temperature": 0},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.load(r)
    return json.loads(data["message"]["content"])


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
ФОРМАТ ВХОДУ. Тобі можуть дати два рядки — «Попереднє:» і «Поточне:». Тоді:
- дату, підрозділ, ПІБ, номер документа бери ТІЛЬКИ з рядка «Поточне:»;
- намір бери з «Поточне:», якщо він там названий; якщо ні («а 23 травня?», \
«а по 2 роті?») — з рядка «Попереднє:»;
- рядок «Попереднє:» потрібен ЛИШЕ для наміру, жодних інших значень звідти \
не бери.
date — у форматі YYYY-MM-DD або null. Дані стенду охоплюють ТРАВЕНЬ 2026 РОКУ: \
якщо в питанні названо день і місяць без року («24 травня», «15-го»), рік — \
2026, склади дату сам, не став null і не проси уточнення лише через \
відсутність року. subdivision — назва підрозділу як у питанні або null. \
name — ПІБ чи його частина, або UNIT-XXXX, або null. doc_number — номер \
документа разом зі знаком №, або null.
Якщо запит складається з кількох рядків — це попереднє питання і коротке \
уточнення до нього. Тоді date, subdivision, name, doc_number бери ЛИШЕ з \
ОСТАННЬОГО рядка (він головний і скасовує старі значення з попередніх \
рядків -- ігноруй дати/номери з попередніх рядків повністю), а intent — з \
останнього рядка, якщо там є явний намір, інакше — успадкуй з попередніх \
рядків.
Приклади:
- «Скільки людей повернуться 24 травня?» → intent=хто_повертається, \
date=2026-05-24 (рік не назвали — підставили 2026, бо весь стенд про цей рік)
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
    # «механізован» не вимагаємо: люди пишуть просто «по 2 роті». Рот у
    # частині лише три, тож саме слово «рота» вже однозначне. Без цього
    # уточнення «а по 2 роті?» мовчки давало зведення по всій частині --
    # підрозділ із питання просто зникав.
    m = re.search(r"([123])\s*-?\s*[а-яіїєґ]*\s*(?:механізован|рот)", low)
    if m:
        return SUBDIVISIONS[int(m.group(1)) - 1]
    m = re.search(r"(перш|друг|трет)[а-яіїєґ]*\s*(?:механізован|рот)", low)
    if m:
        return SUBDIVISIONS[{"перш": 1, "друг": 2, "трет": 3}[m.group(1)] - 1]
    if "забезпеченн" in low:
        return "Взвод забезпечення"
    if "управлінн" in low:
        return "Управління батальйону"
    return None


# Стенд накриває травень 2026 -- рік у словесній даті («23 травня») людина
# не називає майже ніколи, тож підставляємо його самі. Місяці -- стемами, бо
# форма відмінюється («травня», «травні», «травень»).
STAND_YEAR = 2026
MONTHS = {"січ": 1, "лют": 2, "берез": 3, "квіт": 4, "трав": 5, "черв": 6,
          "лип": 7, "серп": 8, "верес": 9, "жовт": 10, "листопад": 11,
          "груд": 12}

# Відносні дати рахуються від СПРАВЖНЬОГО сьогодні, а не від травня 2026.
# Стенд накриває травень, тож «сьогодні» майже напевно дасть порожньо -- і це
# правильна відповідь: у підвалі видно зріз, людина одразу бачить, що на цю
# дату документів немає. Раніше «сьогодні» не розпізнавалось узагалі, чат
# питав «за яку дату рахувати?» і виглядало, ніби він не зрозумів слова.
RELATIVE_DAYS = {"позавчора": -2, "вчора": -1, "сьогодні": 0, "сьогоднi": 0,
                 "завтра": 1, "післязавтра": 2}

# Повні форми -- для нечіткого збігу з одруківками. Стем-збіг вище ловить лише
# помилки в ХВОСТІ слова («травнч»), бо там початок цілий. Помилка в самому
# корені («тралня», «трвня») стем не проходить, а люди друкують саме так.
# Доти одруківки виправляла модель -- але дату в неї забрали (вона підставляла
# дати, яких у питанні не було), тож розпізнавати мусимо самі.
MONTH_FORMS = {"січня": 1, "лютого": 2, "березня": 3, "квітня": 4,
               "травня": 5, "червня": 6, "липня": 7, "серпня": 8,
               "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12}


def _match_month(word):
    """Номер місяця за словом: точний стем, інакше найближче за написанням."""
    for stem, mon in MONTHS.items():
        if word.startswith(stem):
            return mon
    near = difflib.get_close_matches(word, MONTH_FORMS, n=1, cutoff=0.7)
    return MONTH_FORMS[near[0]] if near else None


def extract_date(text):
    """ISO-форма -- як було. Плюс словесна («23 травня», «23-го травня»).

    Без словесної форми найприродніше уточнення («а 23 травня?») лишало слот
    date порожнім, і дату доводилось сподіватись отримати від моделі -- а та
    в непевних випадках підставляла дату з ПРИКЛАДУ в системному промпті.
    «2-га механізована рота» сюди не потрапляє: «механізована» не починається
    з жодного стема місяця.
    """
    text = text or ""
    m = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if m:
        return m.group(0)
    m = re.search(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\b", text)   # 15.05.2026
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    low = text.lower()
    for word, shift in RELATIVE_DAYS.items():
        if word in low:
            return str(datetime.date.today() + datetime.timedelta(days=shift))
    m = re.search(r"(\d{1,2})\s*(?:-?[а-яіїєґ]{1,3})?\s+([а-яіїєґ]+)", text.lower())
    if m and 1 <= int(m.group(1)) <= 31:
        mon = _match_month(m.group(2))
        if mon:
            return f"{STAND_YEAR}-{mon:02d}-{int(m.group(1)):02d}"
    return None


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
    elif "хто" in low and re.search(r"відсутн|поза частин|відпуст|відрядж", low):
        # "відпуст" не "відпустк" -- місцевий відмінок «у відпустці» не містить
        # «к», інакше ця гілка мовчки не спрацьовує на відмінювані форми
        params["intent"] = "хто_відсутній"
    elif "скільки" in low and re.search(r"відпуст|відрядж|поза частин", low):
        # «а скільки у відпустці?» -- питають кількість поза частиною.
        # Стоїть ПІСЛЯ гілки зі «зведенням», щоб «скільки відсутніх по
        # підрозділах» і далі йшло у зведення, а не сюди.
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
    """Джерело не зникає (правило ТЗ #52), але й не лізе поперед відповіді:
    перший екран — цифра й пояснення, посилання розгортається кліком."""
    lines = [f"джерело: {source}", f"дорога: {route}"]
    if cut:
        lines.insert(1, cut)
    return ("\n\n<details class=\"src\"><summary>джерело</summary>"
            + "<br>".join(lines) + "</details>")


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


CRITICAL_FIELDS = [("service_id", "service_id"), ("person_name_raw", "ПІБ"),
                   ("date_from", "дата початку"), ("date_to", "дата завершення")]


def empty_fields(r):
    return [label for key, label in CRITICAL_FIELDS if not r[key]]


def doc_lead(r):
    """Перше речення про документ: що з нього випливає, а не перелік полів.
    Дефект даних — це і є відповідь, тому він іде першим, не приміткою."""
    if empty_fields(r):
        return (f"**Установити не можна** — у {r['doc_number']} не заповнені: "
                f"{', '.join(empty_fields(r))}. Нічого не підставляємо.")
    if r["date_to"] < r["date_from"]:
        return (f"**У {r['doc_number']} суперечність** — завершення "
                f"({r['date_to']}) раніше за початок ({r['date_from']}). "
                "Показано як є, не виправляємо.")
    who = r["person_name_raw"] or "ПІБ не вказано"
    tail = "" if r["status"] == "чинний" else f", {r['status']}"
    return (f"**{who}** — {r['doc_type']} {r['doc_number']}, "
            f"{r['date_from']} – {r['date_to']}{tail}.")


def describe_doc(r):
    """Картка документа списком — для випадків, коли документів кілька."""
    period = (f"{r['date_from']} – {r['date_to']}"
              if (r["date_from"] or r["date_to"]) else "період не вказано")
    out = [f"- **{r['doc_number']}** · {r['doc_type']} · {period} · {r['status']}"]
    detail = " · ".join(x for x in (r["reason"], r["place"]) if x)
    if detail:
        out.append(f"  {detail}")
    if empty_fields(r):
        out.append(f"  ⚠️ порожні поля: {', '.join(empty_fields(r))} — з "
                   "документа їх встановити не можна")
    if r["date_from"] and r["date_to"] and r["date_to"] < r["date_from"]:
        out.append(f"  ⚠️ суперечність: завершення ({r['date_to']}) раніше за "
                   f"початок ({r['date_from']}). Показано як є")
    if r["status"] == "скасований":
        why = (f"скасований документом {r['superseded_by']}"
               if r["superseded_by"] else "скасований")
        out.append(f"  документ не діє: {why}")
    return "\n".join(out)


# ── Дорога «підрахунок»: диспетчер intent → функція db.py ────────────────────


def people_count(rows):
    """Скільки РІЗНИХ людей у вибірці.

    Рядок -- це документ, а не людина: на одну дату в людини може бути два
    чинні документи одночасно (у стенді Бабко Павло Ярославович на
    2026-05-15 -- відрядження №201 і відпустка №501, overlap_001.pdf), і
    тоді len(rows) завищує кількість людей. Без цього поруч у чаті стояли
    «16 осіб поза частиною на 2026-05-15» і «14 з 300 відсутні» на ту саму
    дату -- обидва числа правильні, але назване однаково, і виглядало як
    помилка в даних.

    Записи без service_id не склеюємо: вони не підтверджені реєстром, і чи
    це та сама людина -- невідомо. Тому кожен рахується окремо.
    """
    known = {r["service_id"] for r in rows if r["service_id"]}
    return len(known) + sum(1 for r in rows if not r["service_id"])


def count_head(rows):
    """«15 осіб (16 документів)» -- друге число лише коли воно інше."""
    n = people_count(rows)
    head = f"**{plural_people(n)}**"
    if len(rows) != n:
        head += f" ({len(rows)} документів)"
    return head


def plural_people(n):
    """«3 особи» / «5 осіб» — цифра має читатись як фраза, не як «3 людина»."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} особа"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} особи"
    return f"{n} осіб"


def answer_absent(date, subdivision, not_returned=False):
    rows = db.absences_on_date(date, subdivision=subdivision)
    where = f" ({subdivision})" if subdivision else ""
    items = [f"- {person_label(r)} — {r['doc_type']} {r['doc_number']}, "
             f"до {r['date_to']}" for r in rows]
    if not rows:
        body = f"0 — на {date}{where} чинних документів про відсутність немає."
    else:
        body = (f"{count_head(rows)} поза частиною на {date}{where}.\n"
                + "\n".join(items))
    if not_returned:
        # питання №3 замовника: відмітки про фактичне повернення в даних немає
        # (контракт, «Що з цього НЕ покривається») — застереження одним рядком
        body += ("\n\nЦе ті, у кого відсутність триває за документами; "
                 "відмітки про фактичне повернення в даних немає.")
    return body + footer("підрахунок", doc_source(rows), unconfirmed_note(date))


def answer_returning(date, subdivision):
    rows = db.returning_on_date(date, subdivision=subdivision)
    where = f" ({subdivision})" if subdivision else ""
    if not rows:
        body = f"0 — на {date}{where} нічия відсутність не завершується."
    else:
        items = [f"- {person_label(r)} — {r['doc_type']} {r['doc_number']}"
                 for r in rows]
        body = (f"{count_head(rows)} повертаються {date}{where}.\n"
                + "\n".join(items)
                + "\n\nЦе дата завершення за документом; фактичне повернення "
                  "в даних не фіксується.")
    return body + footer("підрахунок", doc_source(rows), unconfirmed_note(date))


def answer_person(name):
    people = db.find_people(name=name)
    rows = db.absences_for_person(name, only_active=False)
    active = [r for r in rows if r["status"] == "чинний"]
    parts = []
    # Перший рядок — стан людини, не перелік документів
    if not rows:
        parts.append("**За документами у частині** — документів про "
                     "відсутність немає.")
    elif active:
        a = active[0]
        parts.append(f"**Поза частиною** — {a['doc_type']} {a['doc_number']}"
                     + (f", до {a['date_to']}." if a["date_to"] else "."))
    else:
        parts.append("**За документами у частині** — всі документи на цю "
                     "людину скасовані.")
    if people:
        p = people[0]
        parts.append(f"{p['full_name']} · {p['rank']} · {p['position_title']} · "
                     f"{p['subdivision']}.")
        if len(people) > 1:
            parts.append(f"У реєстрі {len(people)} збіги за «{name}» — "
                         "уточніть ПІБ повніше.")
    else:
        parts.append(f"У реєстрі частини людини за «{name}» немає"
                     + (" — документи знайдено за ПІБ у самому документі, "
                        "реєстром вони не підтверджені." if rows else "."))
    if rows:
        parts.append(f"\nДокументи ({len(rows)}):")
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
    if len(rows) > 1:
        active = [r for r in rows if r["status"] == "чинний"]
        latest = max(active or rows, key=lambda r: r["doc_date"])
        parts.append(f"**Два документи з номером {doc_number}** — діє пізніший, "
                     f"від {latest['doc_date']}.")
        parts += [describe_doc(r) for r in rows]
    else:
        r = rows[0]
        parts.append(doc_lead(r))
        detail = " · ".join(x for x in (r["doc_type"], r["reason"], r["place"],
                                        r["status"]) if x)
        parts.append(detail)
        if r["status"] == "скасований" and r["superseded_by"]:
            parts.append(f"Не діє: скасований документом {r['superseded_by']}.")
    if warn:
        parts.append(warn)
    return "\n".join(parts) + footer("підрахунок", doc_source(rows))


def answer_summary(date):
    rows = db.count_absent_by_subdivision(date)
    total_abs = sum(r["absent"] for r in rows)
    total = sum(r["total"] for r in rows)
    items = [f"- {r['subdivision']} — {r['absent']} з {r['total']}"
             for r in rows]
    body = (f"**{total_abs} з {total}** відсутні на {date}.\n" + "\n".join(items))
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
    # Конкретика першою: перші два речення розділу — це, як правило, сама
    # норма (строк, хто дозволяє), решта — умови й винятки. Текст не
    # переписуємо й не переказуємо: ріжемо по крапці, обидві частини — дослівні.
    text = " ".join(best["text"].split())
    parts_sent = re.split(r"(?<=[.!?])\s+", text)
    lead, rest = parts_sent[0], " ".join(parts_sent[1:])
    body = f"**{lead}**\n\n_{best['section_title']}, розділ {best['section_number']}_"
    if rest:
        body += ("\n\n<details class=\"src\"><summary>повний текст розділу"
                 "</summary>" + rest + "</details>")
    cut = None
    if len(hits) > 1:
        cut = "ще дотичні: " + "; ".join(
            f"{h['doc_title']}, розділ {h['section_number']}" for h in hits[1:])
    return body + footer("довідник", src, cut)


# ── Головна функція. Викликається і з Gradio, і без нього ────────────────────


def _history_text(content):
    """Gradio 6 віддає content або рядком, або мультимодальним списком
    [{'text': ..., 'type': 'text'}, ...] (навіть для звичайного тексту без
    жодних файлів). Без цієї розпаковки isinstance(content, str) мовчки
    відкидає всю історію -- саме так злиття нижче ніколи не спрацьовувало."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def _is_followup(question):
    """Чи це коротке допитування до попереднього питання («а 23 травня?»,
    «а по 2 роті?»), а не самостійне питання.

    Ознака — питання несе сутність підрахунку (дату, підрозділ, номер
    документа), але не несе власного наміру. Без цієї перевірки склейка
    спрацьовувала на КОЖНОМУ питанні без наміру, а це всі питання до
    довідника: «За скільки днів подавати рапорт?» після будь-якого
    підрахунку клеїлось із ним і йшло в підрахунок, віддаючи зведення
    замість цитати з документа.
    """
    return bool(extract_date(question)
                or normalize_subdivision(question)
                or extract_doc_number(question))


# ── Перенесення слотів між ходами ───────────────────────────────────────────
#
# Замість склеювати попереднє питання з поточним в один рядок і сподіватись,
# що модель візьме потрібну половину (саме там 4B стабільно брала дату з
# ПЕРШОГО рядка), модель бачить РІВНО ОДНЕ питання, а порожні слоти
# добираються кодом з попереднього ходу. Детерміновано й без здогадок про
# те, «схоже це на уточнення чи ні».

# Які слоти взагалі має сенс тягнути для кожного наміру. Завдяки цьому
# «Хто у відпустці за квитком №301?» після питання з датою НЕ успадкує ту
# дату: для документ_за_номером date не в списку.
INTENT_SLOTS = {
    "хто_відсутній": ("date", "subdivision"),
    "хто_повертається": ("date", "subdivision"),
    "зведення_по_підрозділах": ("date",),
    "документи_людини": ("name",),
    "документ_за_номером": ("doc_number",),
}
SLOT_KEYS = ("intent", "date", "subdivision", "name", "doc_number")
STATE_RE = re.compile(r"<!--slots:(.*?)-->", re.S)


def _state_marker(params):
    """Розпізнані слоти -- у саме повідомлення бота, прихованим HTML-коментарем.
    Так зберігається принцип «стану не тримаємо, все читається з history»:
    окремий gr.State не потрібен, а користувач цього не бачить."""
    keep = {k: params.get(k) for k in SLOT_KEYS}
    return f"\n<!--slots:{json.dumps(keep, ensure_ascii=False)}-->"


# Питання складається практично лише з числа: «а 22?», «22», «а 15-го?»,
# «а 22 числа?». Саме така вимога і рятує від хибних спрацювань: у «а по 2
# роті?» після числа стоїть іменник, тож воно сюди не підходить і 2-м числом
# не стане.
BARE_DAY_RE = re.compile(
    r"^\W*(?:а|і|й|та|ну)?\s*(\d{1,2})\s*(?:-?[гґ]о|-?[ає]|числа)?\s*[?!.]*$")


def _refine_day(question, prev_date):
    """«а 22?» після відповіді за 23 травня -> 2026-05-22.

    День без місяця extract_date розпізнати не може, тому доти дата
    успадковувалась цілком: на «а 22?» чат відповідав за попереднє число і
    навіть не показував, що не зрозумів. Місяць і рік беремо з попередньої
    дати -- людина уточнює день у межах уже названого місяця.
    """
    if not prev_date:
        return None
    m = BARE_DAY_RE.match((question or "").strip().lower())
    if not m:
        return None
    try:
        base = datetime.date.fromisoformat(prev_date)
        return str(base.replace(day=int(m.group(1))))
    except ValueError:
        return None          # 31 квітня і подібне -- не вигадуємо


def _last_user_question(history):
    """Попереднє питання -- як позначений контекст для наміру.

    Береться останнє питання, у якому намір ВЗАГАЛІ Є: ланцюжок уточнень
    («а 23 травня?» -> «а 22?») інакше давав моделі для контексту такий самий
    безнамірний рядок, і успадковувати їй було нізвідки -- на «а 22?» намір
    злітав на дефолтний. Якщо з наміром не знайшлось, беремо просто останнє.
    """
    questions = [
        _history_text(m.get("content")).strip()
        for m in (history or [])
        if isinstance(m, dict) and m.get("role") == "user"
        and _history_text(m.get("content")).strip()
    ]
    for text in reversed(questions):
        if rules_params(text)["intent"]:
            return text
    return questions[-1] if questions else None


def _params_input(question, history):
    """Вхід для витягу параметрів: два ПОЗНАЧЕНІ рядки, а не склеєний текст.

    Саме позначення вирішило обидві поломки склейки на 4B: модель перестала
    брати дату з першого рядка (тепер сутності явно з «Поточне:») і почала
    успадковувати намір там, де в питанні його немає (з «Попереднє:»).
    Перевірено: з prev=«хто повертається» намір хто_повертається, з
    prev=«хто відсутній» -- хто_відсутній, дата в обох з поточного рядка.
    """
    prev = _last_user_question(history)
    if not prev:
        return question
    return f"Попереднє: {prev}\nПоточне: {question}"


def _read_state(history):
    """Слоти з останньої відповіді бота, де вони були."""
    for m in reversed(history or []):
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        found = STATE_RE.search(_history_text(m.get("content")))
        if found:
            try:
                return json.loads(found.group(1))
            except ValueError:
                return None
    return None




def _carry_over(params, prev):
    """Порожні слоти -- з попереднього ходу, але лише ті, що доречні наміру."""
    if not prev:
        return params
    out = dict(params)
    if not out.get("intent"):
        out["intent"] = prev.get("intent")
    for slot in INTENT_SLOTS.get(out.get("intent"), ()):
        if not out.get(slot):
            out[slot] = prev.get(slot)
    return out


def _answers_clarification(question):
    """Чи це справді ВІДПОВІДЬ на уточнення, а не нове питання.

    Уточнення завжди просить конкретну сутність -- дату або людину. Тому
    відповіддю вважаємо лише репліку, що цю сутність несе. Інакше людина,
    яка після «За яку дату рахувати?» ставить наступне питання («а скільки
    у відпустці?»), потрапляла в глухий кут: репліку зараховували як
    відповідь, дати в ній не було, а повторно перепитувати вже не можна --
    і замість відповіді йшла жорстка відмова.
    """
    return bool(extract_date(question) or extract_doc_number(question)
                or extract_name(question))


def _merge_clarification(question, history):
    """Попередня відповідь бота — уточнення? Склеїти з питанням до нього.
    Стану не тримаємо: все читається з history. Друге уточнення заборонене.

    Другий випадок -- коротке допитування без власного наміру («а 23
    травня?», «а по 2 роті?»): rules_params на ньому самому дає intent=None.
    Тоді шукаємо назад останнє повідомлення користувача, де намір був явний,
    і клеїмо їх -- інакше роутер/екстрактор параметрів бачать лише «а 23
    травня?» і підставляють дефолтний намір замість того, що мав на увазі
    користувач.

    Склейка лишилась ТІЛЬКИ для випадку «бот спитав -- людина відповіла»:
    там відповідь справді не має сенсу окремо від питання бота. Колишню
    другу гілку (склейка коротких уточнень через regex-гейт _is_followup)
    прибрано -- її замінило перенесення слотів (_carry_over вище). Гейт
    пропускав лише ISO-дати й номери, тож найприродніші форми («а 23
    травня?», «а по 2 роті?») повз нього не проходили, а те, що проходило,
    4B все одно псувала, беручи дату з першого рядка склеєного тексту."""
    msgs = []
    for m in history or []:
        role = m.get("role") if isinstance(m, dict) else None
        content = _history_text(m.get("content")) if isinstance(m, dict) else ""
        if role in ("user", "assistant") and content:
            msgs.append((role, content))
    if (msgs and msgs[-1][0] == "assistant"
            and msgs[-1][1].startswith(CLARIFY_MARK)
            and _answers_clarification(question)):
        prev_user = next((c for r, c in reversed(msgs[:-1]) if r == "user"), "")
        return f"{prev_user}\n{question}", True
    return question, False


def _as_report(text):
    """«Доповідаю: …» перед першим рядком відповіді. Уточнення лишається
    уточненням (це запит, не доповідь), решта — доповідь по формі."""
    if text.startswith(CLARIFY_MARK) or text.startswith(REPORT_MARK):
        return text
    head, sep, tail = text.partition("\n")
    head = head[0].lower() + head[1:] if head[:1].isupper() else head
    return f"{REPORT_MARK}: {head}{sep}{tail}"


def answer(question, history=None):
    question = (question or "").strip()
    if not question:
        return "Поставте питання." + footer("відмова")
    merged, clarified = _merge_clarification(question, history)
    if is_quota_question(merged.lower()):
        # детерміновано, без моделі: причина відмови відома наперед
        return ANSWER_NO_QUOTA

    model_ok = ollama_available()  # без Ollama мережу навіть не пробуємо
    route, clarify_hint, params, fallback = None, None, None, not model_ok
    if model_ok:
        try:
            r = mamaylm_json(ROUTE_SYSTEM, merged, ROUTE_SCHEMA, "route")
            route = r.get("route") if r.get("route") in ROUTES else None
            clarify_hint = r.get("clarify_question") or None
            if route == "підрахунок":
                # НЕ merged: параметри витягуються з позначеного
                # «Попереднє:/Поточне:», щоб сутності бралися з поточного
                # питання, а намір міг успадкуватись
                p = mamaylm_json(PARAMS_SYSTEM, _params_input(question, history),
                                 PARAMS_SCHEMA, "params")
                # None теж приймаємо: це «наміру в питанні немає», його
                # добере _carry_over. Відкидаємо лише сміття поза enum.
                if p.get("intent") in INTENTS or p.get("intent") is None:
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

    # Слоти, яких у питанні не було, добираємо з попереднього ходу. Робиться
    # ПІСЛЯ моделі й правил: спершу чесно дивимось, що є в самому питанні.
    if route == "підрахунок" and params is not None:
        # дата з правил надійніша за модельну: правила читають саме текст
        # питання, модель може підставити дату з прикладу в промпті
        # Дата -- ЛИШЕ з правил і лише з поточного питання. Модель тут не
        # авторитет: вона підставляла то дату зі стенду (на «скільки сьогодні
        # повертаються?» відповідала за 2026-05-24), то дату з прикладу у
        # власному промпті. Правила читають буквальний текст питання й
        # покривають ISO, «23 травня», «15.05.2026» і «сьогодні/вчора/завтра»;
        # чого вони не впізнали -- того в питанні й немає, слот лишається
        # порожнім і його добере перенесення з попереднього ходу.
        params = dict(params, date=extract_date(question))
        prev_state = _read_state(history)
        if not params["date"]:
            params["date"] = _refine_day(question, (prev_state or {}).get("date"))
        params = _carry_over(params, prev_state)
        # Запобіжник проти зайвого успадкування: якщо ПОТОЧНЕ питання само
        # однозначно називає намір (№документа, ПІБ, «хто повертається»...),
        # він головніший за успадкований. Без цього «Хто у відпустці за
        # квитком №301?» після питання з датою відповідало старим наміром і
        # старою датою -- розмітка «Попереднє:» робить модель надто охочою
        # тягнути контекст. Правила тут працюють лише НА ПІДТВЕРДЖЕННЯ:
        # не знайшли наміру -- нічого не блокуємо, лишається успадкований.
        own = rules_params(question)
        if not own["intent"] and (prev_state or {}).get("intent"):
            # Питання власного наміру не називає («а 22?») -- тримаємо
            # попередній, а не те, що вгадала модель. Вона мусить обрати
            # щось із enum навіть коли обирати нема з чого, і на коротких
            # уточненнях це «щось» стрибало: на «а 22?» -- «поза частиною»
            # після двох питань про повернення.
            params = dict(params, intent=prev_state["intent"])
        elif own["intent"] and own["intent"] != params.get("intent"):
            params = dict(params, intent=own["intent"])
            for slot in ("date", "subdivision", "name", "doc_number"):
                if slot not in INTENT_SLOTS[own["intent"]]:
                    params[slot] = None          # чужий наміру -- геть
                elif own[slot]:
                    params[slot] = own[slot]     # своє з питання -- головніше
                # інакше лишаємо успадковане: «а скільки у відпустці?» змінює
                # НАМІР, але дату бере з попереднього ходу. Раніше цей цикл
                # затирав її на None, і чат просив уточнити дату, яку щойно
                # сам же й назвав у попередній відповіді.

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

    out = _as_report(out)

    if fallback:
        if out.startswith(CLARIFY_MARK):
            out = CLARIFY_MARK + "\n" + WARN_FALLBACK + out[len(CLARIFY_MARK):]
        else:
            out = WARN_FALLBACK + "\n\n" + out
    if route == "підрахунок" and params is not None:
        # у кінці, щоб не зачіпати startswith-перевірки вище
        out += _state_marker(params)
    return out


# ── Вікно ────────────────────────────────────────────────────────────────────

# Шар представлення. Нічого з логіки вище він не змінює: answer() повертає той
# самий текст, що й раніше, а тут він лише розкладається по блоках і стилях.
#
# Стиль тримають дві речі й більше нічого:
#   1. gr.themes.Base(...) — токени, які Gradio вміє роздати компонентам;
#   2. theme.css — усе інше, через CSS-змінні на :root.
# Inline-стилів по компонентах немає навмисно: два місця для одного кольору
# розходяться на першій же правці.

ASSETS = os.path.join(HERE, "assets")
MARK = os.path.join(ASSETS, "mark.svg")
THEME_CSS = os.path.join(HERE, "theme.css")

TYPING_HTML = '<div class="typing"><i></i><i></i><i></i></div>'


def _note(kind, text):
    return f'<div class="note note--{kind}">{text}</div>'


def render_reply(text):
    """Той самий текст answer(), розкладений на блоки.

    Емодзі-маркери (⚠️ у рядку, попередження про правила у шапці) виводяться
    з тіла відповіді в окремі блоки: усередині абзацу вони читаються як шум,
    а окремим блоком із семантичним кольором — як стан системи. Сам текст
    не переписується, тому відповідь лишається тією самою.
    """
    notes = []
    if text.startswith(WARN_FALLBACK):
        text = text[len(WARN_FALLBACK):].lstrip("\n")
        notes.append(("info", "Локальна модель недоступна — маршрут обрано "
                              "правилами, дані з бази ті самі."))
    body = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("⚠️"):
            notes.append(("warn", stripped.lstrip("⚠️").strip()))
        else:
            body.append(line)
    out = "\n".join(body).strip()
    for kind, msg in notes:
        out += "\n\n" + _note(kind, msg)
    return out


def main():
    import gradio as gr

    theme = gr.themes.Base(
        font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"],
        font_mono=[gr.themes.GoogleFont("JetBrains Mono"), "ui-monospace",
                   "monospace"],
        radius_size=gr.themes.sizes.radius_sm,
        spacing_size=gr.themes.sizes.spacing_md,
    )

    EXAMPLES = [
        "Хто з 2-ї механізованої роти буде поза частиною 2026-05-15?",
        "Хто повертається 2026-05-24?",
        "Хто у відпустці за квитком №301?",
        "Скільки відсутніх по підрозділах на 2026-05-15?",
        "За скільки днів подавати рапорт на відпустку?",
        "Що робити, якщо захворів у відпустці?",
    ]

    def respond(message, history):
        """Генератор: спершу крапки на місці відповіді, потім сама відповідь.
        Дає чесний стан очікування там, де він буде видимий, — локальна модель
        на CPU думає десятки секунд."""
        message = (message or "").strip()
        history = history or []
        if not message:
            yield "", history, gr.update(), gr.update(visible=False), message
            return
        asked = history + [{"role": "user", "content": message}]
        yield ("", gr.update(value=asked + [{"role": "assistant",
                                             "content": TYPING_HTML}],
                             visible=True),
               gr.update(visible=False), gr.update(visible=False), message)
        try:
            reply = answer(message, history)
            # Попередження про правила — один раз за сесію: у лівій панелі воно
            # висить постійно, у кожній відповіді лише глушить текст. Шукаємо в
            # історії вже відрендерений блок, а не сирий маркер: після
            # render_reply() сирого тексту в історії не лишається.
            if reply.startswith(WARN_FALLBACK) and any(
                    "note--info" in _msg_text(m) for m in history):
                reply = reply[len(WARN_FALLBACK):].lstrip("\n")
            rendered, failed = render_reply(reply), False
        except Exception as exc:                       # noqa: BLE001
            rendered = _note("error", "Запит не вдалося виконати: "
                                      f"{type(exc).__name__}. Дані не змінено.")
            failed = True
        yield ("", gr.update(value=asked + [{"role": "assistant",
                                             "content": rendered}],
                             visible=True),
               gr.update(visible=False), gr.update(visible=failed), message)

    def _msg_text(m):
        if isinstance(m, dict):
            return str(m.get("content") or "")
        return str(getattr(m, "content", "") or "")

    def reset():
        """Порожній екран назад: стрічка ховається цілком, інакше під героєм
        лишається порожня сіра область, а поле вводу з'їжджає за екран."""
        return ("", gr.update(value=[], visible=False),
                gr.update(visible=True), gr.update(visible=False), "")

    # Gradio 6.23 скоупить усе, що прийшло через css_paths: `:root` переписується
    # в `.gradio-container… .contain :root` і не збігається ні з чим, тому токени
    # теми (і весь блок prefers-color-scheme) мовчки не застосовуються.
    # Той самий один файл підключаємо через head — там скоупінгу немає.
    with open(THEME_CSS, encoding="utf-8") as fh:
        css_head = f"<style>{fh.read()}</style>"

    with gr.Blocks(title="Чат обліку особового складу",
                   fill_height=True) as demo:
        last_q = gr.State("")

        with gr.Row(equal_height=False):
            with gr.Column(scale=0, min_width=272, elem_id="sidebar"):
                gr.HTML(
                    f'<div id="brand">'
                    f'<img src="/gradio_api/file={MARK}" alt="">'
                    f'<div><div class="brand-name">Облік особового складу</div>'
                    f'<div class="brand-sub">тестова версія</div></div></div>')
                new_chat = gr.Button("＋  Новий чат", elem_id="new-chat")
                gr.Markdown("Приклади питань", elem_classes="side-heading")
                with gr.Column(elem_id="examples"):
                    side_btns = [gr.Button(q) for q in EXAMPLES]
                dot, state_text = (("ok", f"модель {get_model()} — локально")
                                   if ollama_available()
                                   else ("rules", "модель недоступна — "
                                                  "працюють правила"))
                gr.HTML(f'<div id="side-status"><p>Дані вигадані, травень 2026.'
                        f'</p><p><span class="dot dot--{dot}"></span>'
                        f'{state_text}</p></div>', elem_id="side-status")

            with gr.Column(scale=1, elem_id="main-col"):
                with gr.Column(elem_id="topbar"):
                    gr.Markdown("Чат обліку особового складу")

                with gr.Column(elem_id="hero", visible=True) as hero:
                    gr.HTML(
                        '<div class="hero-title">Бажаю здоров\'я!</div>'
                        '<div class="hero-sub">Готовий доповідати за обліком '
                        'особового складу. Питання звичайними словами — '
                        'відповідь із документів, із джерелом.</div>')
                    with gr.Row(elem_id="hero-cards"):
                        hero_col_a = [gr.Button(q) for q in EXAMPLES[:3]]
                    with gr.Row(elem_id="hero-cards"):
                        hero_col_b = [gr.Button(q) for q in EXAMPLES[3:]]

                chat = gr.Chatbot(
                    value=[],
                    visible=False,
                    show_label=False,
                    elem_id="chat-area",
                    elem_classes="chatbot",
                    avatar_images=(None, MARK),
                    buttons=["copy"],
                    height="100%",
                )
                with gr.Row(elem_id="composer"):
                    box = gr.Textbox(
                        placeholder="Поставте питання про особовий склад…",
                        show_label=False, lines=1, max_lines=6,
                        container=False, scale=1, autofocus=True)
                    send = gr.Button("➤", elem_id="send-btn")
                retry = gr.Button("Спробувати ще", elem_id="retry-btn",
                                  visible=False)
                gr.HTML('<div id="hint">Відповідь формується з документів; '
                        'джерело — під кожною відповіддю.</div>')

        outs = [box, chat, hero, retry, last_q]
        box.submit(respond, [box, chat], outs)
        send.click(respond, [box, chat], outs)
        retry.click(respond, [last_q, chat], outs)
        new_chat.click(reset, None, outs)
        for btn, q in zip(side_btns + hero_col_a + hero_col_b, EXAMPLES * 3):
            btn.click(respond, [gr.State(q), chat], outs)

    # порт з env -- щоб прототип піднімався поруч із основним стендом
    demo.launch(server_name="127.0.0.1",
                server_port=int(os.environ.get("CHAT_PORT", "7861")),
                share=False,
                inbrowser=False, theme=theme, head=css_head,
                allowed_paths=[ASSETS])


if __name__ == "__main__":
    main()
