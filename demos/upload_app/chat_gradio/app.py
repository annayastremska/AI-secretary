# джерело: answer/chat@andriy-followup-context (коміт 9b9ad91),
# адаптація під Postgres. Що змінено відносно оригіналу:
#   - db.py поруч -- ті самі сім функцій стику, але проти нашої РЕАЛЬНОЇ
#     Postgres (documents/objects/facts/dimensions), не SQLite-стенду;
#   - Ollama -> llama-cpp-python з локальними вагами
#     models/mamaylm-4b-q4_k_m.gguf; модель РЕЗИДЕНТНА (singleton у
#     tiers.py поруч, вантажиться один раз і гріється при старті);
#   - Google Fonts вирізано (усе локально, жодних зовнішніх запитів);
#   - вікно монтується в FastAPI апки (gr.mount_gradio_app, /chat), окремий
#     запуск лишився для дебагу (python demos/upload_app/chat_gradio/app.py);
#   - яруси живуть поруч у tiers.py (перенесено з demos/upload_app/chat.py,
#     задача 1.1): каталог SQL-шаблонів (query_catalog.yaml) із двоярусною
#     маршрутизацією (правила → модель-класифікатор у ЗАКРИТИЙ перелік
#     шаблонів) і вільний SELECT під рейками (read-only, валідатор, LIMIT,
#     таймаут, SQL у згортці) — усе пробується перед чесною відмовою.
#
# Схема роботи (оригінальна, без змін):
#   питання → маршрутизація (структурований виклик MamayLM або правила)
#           → дорога: підрахунок | довідник | цитата | діагностика | відмова
#   Порядок доріг у нас: правила → модель-маршрут → каталог/сім функцій →
#   ярус 2 → чесна відмова.
#
# Рішення з оригіналу, лишені без змін:
#   - Текст КОЖНОЇ відповіді формує код (шаблонні рядки), не модель.
#     Модель робить лише структуровані виклики: маршрут і параметри.
#     Так модель фізично не може вигадати цифру чи цитату.
#   - Механізм follow-up цілком: перенесення слотів кодом (INTENT_SLOTS,
#     _carry_over), стан у прихованому HTML-коментарі повідомлення,
#     словесні дати в extract_date.
#   - Якщо після уточнення дати все одно немає — чесна відмова, дефолтну
#     дату не підставляємо.
#   - Документи в нашій базі -- травень 2026: якщо рік не названо,
#     підставляється 2026 (правило стенду доречне й для нашої бази).

import contextvars
import datetime
import difflib
import logging
import os
import sys
import json
import re
import threading
import time
import uuid

import psycopg

os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import db  # noqa: E402  (сім функцій стику, docs/contracts/2026-08-14_chat-db-interface.md)

# Каталог шаблонів і ярус 2 -- tiers.py поруч (перенесено з chat.py,
# який видалено: одна кодова база чата). Звідти ж береться резидентна
# llama-cpp модель: один екземпляр на процес, спільний для маршрутизатора
# і ярусу 2.
import tiers as tier_chat  # noqa: E402

# Векторний ярус (етап 3 плану): питання -> найближчий шаблон каталогу за
# прикладами, мілісекунди на CPU, без виклику моделі. Живе окремим модулем
# поруч; без ваг encoder-а чат працює без нього (деградація, не падіння).
import vector_route as tier_vector  # noqa: E402

# Видимий стан обробки (задача B1, критерій Ш3): яруси кажуть, де вони
# зараз, а вікно показує це, поки answer() ще думає. Виклики progress.stage()
# нічого не роблять без активного трекера -- логіка доріг від них не
# залежить, тести й CLI працюють як раніше.
import progress  # noqa: E402

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

# ── MamayLM через llama-cpp-python (локально, без хмари і без Ollama) ────────
#
# Модель РЕЗИДЕНТНА: singleton у tiers.py поруч (_get_model),
# вантажиться з диска один раз на процес -- головна причина гальм старої
# версії була саме в перезавантаженні ваг. Прогрів робить warm_up() нижче
# (його кличе апка при старті у фоновому потоці).


def get_model():
    return os.path.basename(tier_chat.MODEL_PATH)


def model_available():
    """Швидка перевірка -- чи є ваги і чи не падало завантаження. Без ваг
    дорогі виклики навіть не пробуємо: чат живе на правилах і чесно каже це."""
    return (os.path.exists(tier_chat.MODEL_PATH)
            and not tier_chat._MODEL_FAILED)


def mamaylm_json(system, user, schema, schema_name):
    """Один структурований виклик до резидентної llama-cpp моделі: та сама
    ідея, що в оригіналі (system, user, schema) -> розпарсений dict, тільки
    JSON-схема йде в response_format (llama-cpp перетворює її на граматику --
    відповідь фізично не може вийти за схему). schema_name не
    використовується, лишений у сигнатурі для сумісності з оригіналом.
    None від _model_json означає «модель не відповіла» -- обробляється
    вище як fallback на правила."""
    return tier_chat._model_json(system, user, schema)


def warm_up():
    """Прогрів при старті апки: завантажити ваги і зробити крихітний виклик,
    щоб перший користувач не платив за перше завантаження (~30 с)."""
    try:
        if tier_chat._get_model() is not None:
            tier_chat._model_json(
                "Поверни JSON {\"ok\": true}.", "ок",
                {"type": "object", "properties": {"ok": {"type": "boolean"}},
                 "required": ["ok"], "additionalProperties": False})
    except Exception:
        pass
    try:
        # векторний ярус: збудувати маршрути з каталогу один раз при старті
        # (завантаження encoder-а -- секунди; перший користувач не чекає).
        # Немає ваг -> warm() поверне False і запише попередження в лог.
        tier_vector.warm()
    except Exception:
        pass


def warm_up_async():
    threading.Thread(target=warm_up, daemon=True).start()


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
    m = re.search(r"([123])\s*-?\s*[а-яіїєґ]*\s*механізован", low)
    if m:
        return SUBDIVISIONS[int(m.group(1)) - 1]
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


def _real_date_or_none(year, month, day):
    """Складену дату перевіряємо конструктором date (знахідка верифікатора):
    «31 лютого» проходило регекс і їхало в SQL неіснуючою датою 2026-02-31 --
    жива база відповідала DataError і генерик-помилкою замість чесного
    уточнення. Краще порожній слот: чат сам перепитає дату. Той самий
    прийом, що в _refine_day (31 квітня -> None, не вигадуємо)."""
    try:
        return str(datetime.date(int(year), int(month), int(day)))
    except ValueError:
        return None


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
        return _real_date_or_none(*m.group(0).split("-"))
    m = re.search(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\b", text)   # 15.05.2026
    if m:
        return _real_date_or_none(m.group(3), m.group(2), m.group(1))
    low = text.lower()
    for word, shift in RELATIVE_DAYS.items():
        if word in low:
            return str(datetime.date.today() + datetime.timedelta(days=shift))
    m = re.search(r"(\d{1,2})\s*(?:-?[а-яіїєґ]{1,3})?\s+([а-яіїєґ]+)", text.lower())
    if m and 1 <= int(m.group(1)) <= 31:
        mon = _match_month(m.group(2))
        if mon:
            # РІК, НАЗВАНИЙ ЛЮДИНОЮ, важливіший за нашу константу.
            #
            # Знайдено адверсарним проходом 25.08: на «Скільком у відпустці
            # 1 січня 1990?» чат відповідав «0 -- на 2026-01-01…», тобто ТИХО
            # підмінював рік і відповідав на інше питання. Це гірше за
            # відмову: людина бачить впевнену відповідь про дату, якої не
            # називала.
            #
            # STAND_YEAR лишається дефолтом РІВНО для випадку «рік не
            # названий» («а 23 травня?») -- там підстановка доречна й
            # озвучується у відповіді датою зрізу.
            tail = text.lower()[m.end():]
            year = re.match(r"\s*(?:року|р\.?)?\s*(\d{4})", tail)
            return _real_date_or_none(year.group(1) if year else STAND_YEAR,
                                      mon, m.group(1))
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
        # Відмінок: «Цісика» -> пробуємо і обрізані форми («Цісик», «Ціси»).
        #
        # ТРИ символи, а не два: заміряно 26.08 на «Що відомо про
        # Крижанівського?» -- родовий відмінок довших прізвищ забирає ТРИ
        # літери («-ого»), і чат відповідав «у реєстрі людини немає», хоч
        # людина в базі є з вісьмома фактами. Найгірший вид відповіді:
        # впевнене заперечення власних даних.
        for stem in (tok, tok[:-1], tok[:-2], tok[:-3]):
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


# Екранування недовіреного тексту (задача 1.5, знахідка Андрія §8): усе, що
# прийшло з документа чи з бази (ПІБ, номери, назви файлів, текст розділів),
# перед вставкою в Markdown/HTML чата проходить _esc. Один хелпер на обидва
# модулі -- живе в tiers.py.
_esc = tier_chat._esc


#: Номер поточного звернення -- у ContextVar, а не параметром через двадцять
#: функцій: footer() кличеться з десятка місць, і протягувати id крізь усі
#: означало б переписати їх усі заради одного рядка.
_CURRENT_ID = contextvars.ContextVar("chat_request_id", default=None)

def footer(route, source="—", cut=None):
    """Джерело не зникає (правило ТЗ #52), але й не лізе поперед відповіді:
    перший екран — цифра й пояснення, посилання розгортається кліком.

    source і cut екрануються ТУТ: усі виклики передають сирі значення з бази
    (номери документів, назви файлів), і одна точка захисту надійніша за
    десять у викликах."""
    # Зріз бази -- у КОЖНІЙ відповіді, одним місцем.
    #
    # Замірний прогін 25.08 (measure_chat на 124 питаннях) показав: 69
    # відповідей із 124 не мали дати зрізу. Шаблони каталогу її кажуть (я
    # додала на етапі 5), а старіші дороги -- підрахунок, довідник,
    # діагностика -- ні. Дописувати рядок у кожну з них означало б двадцять
    # місць і двадцять шансів забути, тому він тут: footer() стоїть під
    # кожною відповіддю за визначенням.
    #
    # Формулювання точне: це не «дані станом на», а «зріз бази» -- момент,
    # коли ми базу прочитали. Якщо у відповіді є ще й свій зріз даних («на
    # 6 травня»), вони не суперечать: перший про дані, другий про читання.
    lines = [f"джерело: {_esc(source)}", f"дорога: {route}",
             f"зріз бази: {datetime.date.today().isoformat()}"]
    if cut:
        lines.insert(1, _esc(cut))
    # Номер звернення (етап 5): по ньому в журналі знаходиться саме цей хід.
    # Живе тут, а не окремим рядком у кінці відповіді, навмисно: у кінці вже
    # стоїть службовий маркер стану діалогу, і дописувати після нього означало
    # б ламати його читання наступним ходом.
    cid = _CURRENT_ID.get()
    if cid:
        lines.append(f"звернення: {cid}")
    return ("\n\n<details class=\"src\"><summary>джерело</summary>"
            + "<br>".join(lines) + "</details>")


def unconfirmed_note(date):
    """Склад відповіді формує код: зріз, знаменник, непідтверджені окремо.
    Непідтверджені (чернетки) у підрахунок НЕ входять -- правило продукту."""
    n = db.unconfirmed_absences_on_date(date)
    total = db.people_total()
    return (f"зріз: {date} · у реєстрі {total} осіб · непідтверджених записів "
            f"(чернетки, у підрахунок не входять): {n}")


def person_label(row):
    # ПІБ приходить із документа через OCR -- недовірений вхід, екрануємо
    name = (_esc(row["person_name_raw"]) if row["person_name_raw"]
            else "(ПІБ у документі порожній)")
    mark = "" if row["service_id"] else " — факт не підтверджено (чернетка)"
    return name + mark


def doc_source(rows):
    return ", ".join(f"{r['doc_number']} ({r['source_file']})" for r in rows) or "—"


# service_id зі списку критичних прибрано: у нашій схемі «непідтверджено» --
# це статус факту (показується позначкою біля статусу), а не порожнє поле.
CRITICAL_FIELDS = [("person_name_raw", "ПІБ"),
                   ("date_from", "дата початку"), ("date_to", "дата завершення")]


def empty_fields(r):
    return [label for key, label in CRITICAL_FIELDS if not r[key]]


def doc_lead(r):
    """Перше речення про документ: що з нього випливає, а не перелік полів.
    Дефект даних — це і є відповідь, тому він іде першим, не приміткою."""
    if empty_fields(r):
        return (f"**Установити не можна** — у {_esc(r['doc_number'])} не заповнені: "
                f"{', '.join(empty_fields(r))}. Нічого не підставляємо.")
    if r["date_to"] < r["date_from"]:
        return (f"**У {_esc(r['doc_number'])} суперечність** — завершення "
                f"({_esc(r['date_to'])}) раніше за початок ({_esc(r['date_from'])}). "
                "Показано як є, не виправляємо.")
    who = _esc(r["person_name_raw"]) if r["person_name_raw"] else "ПІБ не вказано"
    tail = "" if r["status"] == "чинний" else f", {_esc(r['status'])}"
    return (f"**{who}** — {_esc(r['doc_type'])} {_esc(r['doc_number'])}, "
            f"{_esc(r['date_from'])} – {_esc(r['date_to'])}{tail}.")


def describe_doc(r):
    """Картка документа списком — для випадків, коли документів кілька."""
    period = (f"{_esc(r['date_from'])} – {_esc(r['date_to'])}"
              if (r["date_from"] or r["date_to"]) else "період не вказано")
    out = [f"- **{_esc(r['doc_number'])}** · {_esc(r['doc_type'])} · {period} · "
           f"{_esc(r['status'])}"]
    detail = " · ".join(_esc(x) for x in (r["reason"], r["place"]) if x)
    if detail:
        out.append(f"  {detail}")
    # Решта відомого про документ. Аудит 26.08 (питання Ані «ти пропрацював це
    # з усіма видами питань?»): у базі 17 вимірів, а картка показувала чотири.
    # Показуємо ЛИШЕ те, що справді є: порожнє поле не згадуємо, інакше
    # відповідь роздується прочерками.
    extra = []
    for key, label in (("leave_days", "днів"),
                       ("deployment_days", "днів"),
                       ("deployment_org", "організація"),
                       ("deployment_purpose", "мета"),
                       ("unit_to_report", "прибути до"),
                       ("order_number", "наказ-підстава №"),
                       ("order_date", "від"),
                       ("travel_document", "проїзний документ")):
        value = (r.get(key) or "").strip()
        if value:
            extra.append(f"{label} {_esc(value)}")
    if extra:
        out.append("  " + " · ".join(extra))
    # Фактичне повернення -- окремим рядком, бо це найцінніша відмітка: вона
    # означає «людина вже в частині», хоч документ ще чинний.
    actual = (r.get("actual_return") or "").strip()
    if actual:
        out.append(f"  фактично повернувся {_esc(actual)}")
    if empty_fields(r):
        out.append(f"  ⚠️ порожні поля: {', '.join(empty_fields(r))} — з "
                   "документа їх встановити не можна")
    if r["date_from"] and r["date_to"] and r["date_to"] < r["date_from"]:
        out.append(f"  ⚠️ суперечність: завершення ({_esc(r['date_to'])}) раніше за "
                   f"початок ({_esc(r['date_from'])}). Показано як є")
    if r["status"] == "скасований":
        why = (f"скасований документом {_esc(r['superseded_by'])}"
               if r["superseded_by"] else "скасований")
        out.append(f"  документ не діє: {why}")
    return "\n".join(out)


# ── Дорога «підрахунок»: диспетчер intent → функція db.py ────────────────────


def plural_people(n):
    """«3 особи» / «5 осіб» — цифра має читатись як фраза, не як «3 людина»."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} особа"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} особи"
    return f"{n} осіб"


#: Відмова про підрозділ лишається -- але тепер лише там, де вона ПРАВДА:
#: коли названого підрозділу в штатці немає. Доти цей текст стояв на КОЖНЕ
#: питання про підрозділ і казав «у схемі немає зв'язку особа→підрозділ». Зі
#: штаткою (25.08) це стало неправдою про власні дані: зв'язок є, 300 рядків.
#: Знайдено глибоким аналізом 26.08.
def no_such_subdivision(asked):
    known = db.subdivision_values()
    tail = (" У базі є: " + ", ".join(_esc(v) for v in known) + "."
            if known else "")
    return (f"Такого підрозділу в штатці немає: «{_esc(asked)}». Нуль тут "
            f"означав би «нікого немає», а насправді немає самого "
            f"підрозділу.{tail}")


def subdivision_exists(asked):
    """-> чи є названий підрозділ у штатці.

    Людина каже «2 рота», а в штатці «2-га механізована рота» -- дослівного
    збігу не буде ніколи. Тому три спроби, від точнішої до грубшої: підрядок,
    номер підрозділу, початок назви словами («взвод забезпечення»)."""
    want = str(asked or "").strip().lower()
    if not want:
        return True
    known = [v.lower() for v in db.subdivision_values()]
    if not known:
        return False          # штатки в базі немає -- підтвердити нічим
    if any(want in v for v in known):
        return True
    digit = re.search(r"\d", want)
    if digit:
        return any(v.startswith(digit.group(0)) for v in known)
    return any(want.split()[0][:6] in v for v in known)


def answer_absent(date, subdivision, not_returned=False):
    if subdivision and not subdivision_exists(subdivision):
        return no_such_subdivision(subdivision) + footer("відмова")
    rows = db.absences_on_date(date, subdivision=subdivision)
    where = f" ({subdivision})" if subdivision else ""
    items = [f"- {person_label(r)} — {_esc(r['doc_type'])} {_esc(r['doc_number'])}, "
             f"до {_esc(r['date_to'])}" for r in rows]
    if not rows:
        body = f"0 — на {date}{where} чинних документів про відсутність немає."
        # Нуль поза покриттям -- це не «нікого немає», а «даних немає».
        note = db.coverage_note(date)
        if note:
            body += "\n" + note
    else:
        body = (f"**{plural_people(len(rows))}** поза частиною на {date}{where}.\n"
                + "\n".join(items))
    if not_returned:
        # питання №3 замовника: відмітки про фактичне повернення в даних немає
        # (контракт, «Що з цього НЕ покривається») — застереження одним рядком
        # ТУТ БУЛА НЕПРАВДА (аудит 26.08): «відмітки про фактичне повернення
        # в даних немає» -- при 51 підтвердженому факті `leave_actual_return`.
        # Тепер кажемо правду по цій вибірці: у скількох вона є, у скількох ні.
        with_ret = sum(1 for r in rows if (r.get("actual_return") or "").strip())
        if with_ret:
            body += (f"\n\nЦе ті, у кого відсутність триває за документами. "
                     f"У {with_ret} із {len(rows)} є відмітка про фактичне "
                     f"повернення — вона показана в картці документа.")
        else:
            body += ("\n\nЦе ті, у кого відсутність триває за документами; "
                     "відмітки про фактичне повернення ні в кого з них немає.")
    return body + footer("підрахунок", doc_source(rows), unconfirmed_note(date))


def answer_returning(date, subdivision):
    if subdivision and not subdivision_exists(subdivision):
        return no_such_subdivision(subdivision) + footer("відмова")
    rows = db.returning_on_date(date, subdivision=subdivision)
    where = f" ({subdivision})" if subdivision else ""
    if not rows:
        body = f"0 — на {date}{where} нічия відсутність не завершується."
        # Нуль поза покриттям -- це «даних немає», не «нікого немає». Тут
        # попередження бракувало (питання Ані 26.08: «чому на два шаблонних
        # питання немає відповіді?»): на «хто повертається 2026-05-09» чат
        # видавав чистий нуль, і з нього неможливо було зрозуміти, що травня
        # в базі немає взагалі. Сусідній шаблон це вже казав -- тут ні.
        note = db.coverage_note(date)
        if note:
            body += "\n" + note
    else:
        items = [f"- {person_label(r)} — {_esc(r['doc_type'])} {_esc(r['doc_number'])}"
                 for r in rows]
        body = (f"**{plural_people(len(rows))}** повертаються {date}{where}.\n"
                + "\n".join(items)
                + "\n\nЦе дата завершення за документом. Фактичне повернення "
                  "фіксується окремою відміткою — якщо вона є, її видно в "
                  "картці документа.")
    return body + footer("підрахунок", doc_source(rows), unconfirmed_note(date))


def answer_person(name):
    """Відповідь про людину: СПЕРШУ хто вона, потім документи.

    Питання Ані 26.08: на «що відомо про Усика?» відповідь починалася з
    «документів про відсутність немає» -- хоч про відсутність ніхто не питав.
    Питали «що відомо», і відомо в базі більше: ПІБ, звання, посада,
    підрозділ, номер за штаткою. Порядок відповіді має відповідати питанню:
    спочатку картка людини, а стан відсутності -- як одна з її властивостей,
    а не як заголовок."""
    people = db.find_people(name=name)
    rows = db.absences_for_person(name, only_active=False)
    active = [r for r in rows if r["status"] == "чинний"]
    parts = []
    # 1) ХТО ЦЕ -- перше, що людина читає
    if people:
        p = people[0]
        card = " · ".join(_esc(x) for x in (p["full_name"], p["rank"],
                                            p["position_title"],
                                            p["subdivision"]) if x)
        if p.get("in_roster"):
            card += f" · за штаткою {_esc(p['service_id'])}"
        parts.append("**" + card + "**")
        # Особа є в реєстрі, але відповідника у штатці немає -- рівно той
        # випадок, який пайплайн віддає людині на розгляд.
        if not p.get("in_roster", True):
            parts.append("⚠️ Відповідника у штатці немає — особу створено з "
                         "документа і вона чекає підтвердження людиною "
                         "(завдання в черзі рев'ю).")
        if len(people) > 1:
            parts.append(f"У реєстрі {len(people)} збіги за «{_esc(name)}» — "
                         "уточніть ПІБ повніше.")
    else:
        parts.append(f"У реєстрі частини людини за «{_esc(name)}» немає"
                     + (" — документи знайдено за ПІБ у самому документі, "
                        "реєстром вони не підтверджені." if rows else "."))
    # 2) ЩО ПРО НЕЇ Є В ДОКУМЕНТАХ
    if not rows:
        parts.append("**За документами у частині** — документів про "
                     "відсутність немає.")
    elif active:
        a = active[0]
        parts.append(f"**Поза частиною** — {_esc(a['doc_type'])} {_esc(a['doc_number'])}"
                     + (f", до {_esc(a['date_to'])}." if a["date_to"] else "."))
    else:
        # ТУТ БУЛО «всі документи скасовані» -- і це неправда для найчастішого
        # випадку. Заміряно 26.08 на «Що відомо про Дутку?»: у людини два
        # документи, обидва в стані ЧЕРНЕТКИ (unconfirmed), тобто вони не
        # скасовані, а чекають підтвердження людиною. Різниця принципова:
        # «скасовано» означає «більше не діє», «чернетка» -- «ще не враховано».
        # Тому формулювання виводиться зі СТАНІВ документів, а не одне на всі
        # випадки.
        drafts = [r for r in rows if r.get("fact_status") == "unconfirmed"]
        cancelled = [r for r in rows if r["status"] == "скасований"]
        if drafts and not cancelled:
            parts.append(
                f"**За документами у частині** — усі записи про відсутність "
                f"({len(drafts)}) у стані чернетки: вони чекають підтвердження "
                f"людиною і в підрахунки не входять.")
        elif drafts:
            parts.append(
                f"**За документами у частині** — {len(cancelled)} скасовано, "
                f"{len(drafts)} у стані чернетки (чекають підтвердження).")
        else:
            parts.append("**За документами у частині** — всі документи на цю "
                         "людину скасовані.")
    if rows:
        parts.append(f"\nДокументи ({len(rows)}):")
        parts += [describe_doc(r) for r in rows]
    return "\n".join(parts) + footer("підрахунок", doc_source(rows))


def doc_number_warning(doc_number):
    """№3О4 — кирилична «О» серед цифр: шум OCR. Назвати, не виправляти."""
    tail = (doc_number or "").lstrip("№").strip()
    if tail and not tail.isdigit():
        return (f"⚠️ у номері документа нецифровий символ (можливий шум "
                f"розпізнавання): №{_esc(tail)} — місце, де система не впевнена")
    return None


def answer_doc(doc_number):
    rows = db.document_by_number(doc_number)
    warn = doc_number_warning(doc_number)
    if not rows:
        body = (f"Документа {_esc(doc_number)} у базі стенду немає. Номер не "
                "виправляємо і схожих не підставляємо.")
        if warn:
            body += "\n" + warn
        return body + footer("підрахунок", "—")
    parts = []
    if len(rows) > 1:
        active = [r for r in rows if r["status"] == "чинний"]
        latest = max(active or rows, key=lambda r: r["doc_date"])
        parts.append(f"**Два документи з номером {_esc(doc_number)}** — діє "
                     f"пізніший, від {_esc(latest['doc_date'])}.")
        parts += [describe_doc(r) for r in rows]
    else:
        r = rows[0]
        parts.append(doc_lead(r))
        detail = " · ".join(_esc(x) for x in (r["doc_type"], r["reason"],
                                              r["place"], r["status"]) if x)
        parts.append(detail)
        if r["status"] == "скасований" and r["superseded_by"]:
            parts.append(f"Не діє: скасований документом {_esc(r['superseded_by'])}.")
    if warn:
        parts.append(warn)
    return "\n".join(parts) + footer("підрахунок", doc_source(rows))


def answer_summary(date):
    rows = db.count_absent_by_subdivision(date)
    if not rows:
        # Рядків немає -- значить у базі немає самого виміру `subdivision`
        # (до заливки штатки так і було). Тоді замість розбивки -- загальне
        # число і пряма причина, чому розбивки немає.
        n = len(db.absences_on_date(date))
        body = ("Розбивки по підрозділах зараз дати не можу: у базі немає "
                "жодного запису про підрозділи.\n"
                f"По всій частині на {date}: {plural_people(n)} поза "
                "частиною (за підтвердженими фактами).")
        return body + footer("підрахунок (без розбивки)", "facts + dimensions",
                             unconfirmed_note(date))
    total_abs = sum(r["absent"] for r in rows)
    total = sum(r["total"] for r in rows)
    items = [f"- {_esc(r['subdivision'])} — {r['absent']} з {r['total']}"
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
                # текст уточнення пише модель -- екрануємо, як і решту
                # недовіреного (сама вона бачила питання користувача)
                # Покриття беремо З БАЗИ. Раніше тут стояв літерал «дані
                # стенду -- травень 2026», і 25.08 він відправив замовницю
                # питати про травень, якого в базі немає взагалі: підказка
                # системи виявилась єдиним неправдивим рядком у діалозі.
                cover = db.coverage_note()
                q = (_esc(clarify_hint) if clarify_hint else
                     "За яку дату рахувати? Назвіть у форматі YYYY-MM-DD."
                     + (f" {cover}" if cover else ""))
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


ANSWER_QUOTE = ("Дослівно цитувати накази система не береться: у базі є лише "
                "розпізнаний OCR-текст документів і облікові поля (номер, "
                "дати, тип, статус, місце). Цитата з OCR-тексту без звірки з "
                "оригіналом була б видана за дослівну — цього не робимо."
                + footer("цитата"))

ANSWER_DIAG = ("Такого типу даних у базі немає: телеметрії й журналів стану "
               "техніки система не зберігає. Є реєстр осіб, документи про "
               "відпустки й відрядження та черга перевірки. Бракує: дані про "
               "фактичний стан техніки." + footer("діагностика"))

ANSWER_REFUSE = ("На це система відповісти не може, бо питання не лягає на "
                 "жодну з її доріг: підрахунок відсутностей за документами, "
                 "довідник, цитати чи діагностика."
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
        return ("У базі поки немає нормативних документів (domain = "
                "'normative'), тож довідкову відповідь дати нема з чого. "
                "Коли пайплайн завантажить нормативку — пошук запрацює "
                "через Ukrainian FTS без змін у чаті."
                + footer("довідник"))
    best = hits[0]
    src = (f"{best['doc_title']}, розділ {best['section_number']} "
           f"({best['source_note']})")
    # Конкретика першою: перші два речення розділу — це, як правило, сама
    # норма (строк, хто дозволяє), решта — умови й винятки. Текст не
    # переписуємо й не переказуємо: ріжемо по крапці, обидві частини — дослівні.
    # Текст розділу -- вміст документа, тобто недовірений вхід (знахідка
    # Андрія §8: оригінал вставляв його в <details> без екранування, і
    # <script> з документа виконався б у браузері). Екрануємо ДО вставки:
    # і перше речення в Markdown, і повний текст у згортці.
    text = " ".join(best["text"].split())
    parts_sent = re.split(r"(?<=[.!?])\s+", text)
    lead, rest = _esc(parts_sent[0]), _esc(" ".join(parts_sent[1:]))
    body = (f"**{lead}**\n\n_{_esc(best['section_title'])}, "
            f"розділ {_esc(best['section_number'])}_")
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
    # «-->» усередині значення (могло прийти з питання через модельний слот)
    # закрило б HTML-коментар достроково і вивалило решту в розмітку.
    # Заміна на > ЛИШЕ всередині JSON-рядка: json.loads поверне той
    # самий текст, а в коментарі послідовності «-->» більше немає.
    payload = json.dumps(keep, ensure_ascii=False).replace("-->", "--\\u003e")
    return f"\n<!--slots:{payload}-->"


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


def _drop_invented_name(params, question):
    """Модель бачили на тому, що підставляє «Петренко» з прикладу в
    системному промпті (з датами та сама історія -- тому дата тепер береться
    ЛИШЕ з правил, див. answer()). Ім'я, перших
    чотирьох літер якого немає в питанні, -- вигадане (перевірка префіксом,
    бо ПІБ у питанні стоїть у відмінку: «про Усика» -> «Усик»)."""
    n = (params.get("name") or "").strip()
    if n and n[:4].lower() not in question.lower():
        params = dict(params)
        params["name"] = None
    return params


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


# ── Додаткові дороги нашої апки: каталог SQL-шаблонів і ярус 2 ──────────────
#
# Викликаються, коли сім функцій не впорались (dispatch повернув None) або
# маршрут -- «відмова». Порядок -- маршрутизація з tiers.py + vector_route.py:
# спершу каталог query_catalog.yaml правилами (SQL написаний людиною,
# звірений з базою), потім ВЕКТОРНИЙ ярус (найближчий шаблон за прикладами
# каталогу, мілісекунди, без моделі -- етап 3 плану), потім
# модель-класифікатор у ЗАКРИТИЙ перелік шаблонів того ж каталогу, потім
# вільний SELECT під рейками (read-only сесія, валідатор, LIMIT 200,
# таймаут 5 с, SQL у згортці).
# Не склалось -- None, і вище лишається чесна відмова.


def _fmt_source_block(source_lines, route_label):
    # html.escape замість самодільного replace("<", "&lt;"): & без
    # екранування дозволяє протягнути &lt;script&gt; як текст, який
    # наступний innerHTML-прохід перетворить назад на тег
    body = "<br>".join(_esc(s) for s in source_lines)
    return ("\n\n<details class=\"src\"><summary>джерело</summary>"
            + body + f"<br>дорога: {route_label}</details>")


def _catalog_tier(question):
    """Каталог шаблонів (правила з tiers.py: регекси, звірений SQL) -> текст
    або None. Детерміновано, без моделі."""
    progress.stage("rules")
    try:
        routed = tier_chat.rules_route(question)
    except Exception:
        routed = None
    if not routed:
        return None
    tid, params = routed
    try:
        text, source = tier_chat.run_template(tid, params)
    except Exception:
        return None
    return text + _fmt_source_block(source, f"каталог шаблонів ({tid})")


def _vector_tier(question):
    """Векторний ярус (chat_gradio/vector_route.py, задача 3.3): питання ->
    найближчий шаблон каталогу за прикладами, мілісекунди, без моделі.
    Вектори дають лише id шаблону -- параметри витягуються правилами
    (tiers.params_for_template). None -> питання їде далі (модель -> ярус
    2): нижче порога, ярус вимкнено (немає ваг) або обов'язковий параметр
    не витягся."""
    progress.stage("vector")
    # агрегати (середнє, мін/макс...) каталог не вміє -- те саме правило,
    # що в rules_route: пропустити в ярус 2, а не тягнути в схожий шаблон
    if tier_chat._AGGREGATE.search(question.lower()):
        return None
    try:
        tid, score = tier_vector.vector_route(question)
    except Exception:
        return None
    if tid is None or tid not in tier_chat._CATALOG:
        return None
    t = tier_chat._CATALOG[tid]
    if tid == "smalltalk":
        # розмовний маршрут (задача 3.5): фіксована відповідь кодом --
        # без SQL, без бази і без моделі («дякую» не їде в 27B на 43 с)
        return (_esc(t["refusal"].strip())
                + _fmt_source_block(
                    [f"шаблон каталогу: smalltalk ({t['title']})",
                     "SQL немає: розмовна фраза, відповідь фіксована кодом"],
                    f"розмовний маршрут (вектори, бал {score:.2f})"))
    try:
        params = tier_chat.params_for_template(tid, question)
    except Exception:
        return None
    if params is None:
        return None
    try:
        text, source = tier_chat.run_template(tid, params)
    except Exception:
        return None
    return text + _fmt_source_block(
        source, f"каталог шаблонів, обрано векторним ярусом "
                f"({tid}, бал {score:.2f})")


# Позначка «модельна відмова»: питання не про дані бази -- ярус 2 після
# цього не пробуємо (нема сенсу платити ще один виклик моделі за SELECT,
# якому нема з чого вийти).
_MODEL_REFUSED = object()


def _model_catalog_tier(question):
    """Друга спроба ярусу 1: модель-класифікатор у ЗАКРИТИЙ перелік шаблонів
    каталогу (tiers.model_route). Модель лише обирає шаблон і параметри --
    SQL і текст відповіді лишаються кодом. -> текст, _MODEL_REFUSED або None
    (None = далі пробуємо ярус 2)."""
    if not (model_available() and tier_chat._get_model() is not None):
        return None
    progress.stage("model_catalog")
    try:
        routed = tier_chat.model_route(question)
    except Exception:
        return None
    if not routed:
        return None
    tid, params = routed
    if tid == "відмова":
        return _MODEL_REFUSED
    if tid == "вільний_sql":
        return None
    try:
        text, source = tier_chat.run_template(tid, params)
    except Exception:
        return None
    return text + _fmt_source_block(
        source, f"каталог шаблонів, обрано моделлю-класифікатором ({tid})")


def _tier2_tier(question):
    """Ярус 2: вільний SELECT під рейками (read-only, валідатор, LIMIT 200,
    таймаут 5 с, SQL у згортці) -> текст або None.

    Ярус УВІМКНЕНИЙ за домовленістю із замовницею: немає підходящого шаблона --
    модель складає запит сама, під рейками. Вимикається лише явно
    (`CHAT_FREE_SQL=0`), якщо на конкретному показі потрібні лише перевірені
    шаблони. Рейки й причини -- біля прапорця `FREE_SQL_ENABLED` у tiers.py."""
    if not tier_chat.FREE_SQL_ENABLED:
        return None
    if not (model_available() and tier_chat._get_model() is not None):
        return None
    progress.stage("tier2")
    try:
        text, source = tier_chat.tier2_answer(question)
    except Exception:
        return None
    if text.startswith("Відповідь на нешаблонний запит"):
        return ("⚠️ нешаблонний запит: SQL склала модель, виконано "
                "read-only з валідатором\n" + text
                + _fmt_source_block(source, "ярус 2 (вільний SELECT)"))
    return None


# Ярус 2 при відмові вмикається лише для питань, які взагалі схожі на
# питання про дані бази: «Яка погода завтра?» не має платити хвилину
# модельного часу за спробу скласти SELECT, якому нема з чого вийти.
_DBISH = re.compile(
    r"відпуст|відрядж|документ|наказ|факт|званн|особ|людей|осіб|черг|"
    r"переві|підрозділ|баз[аіи]|скільки|хто\b|реєстр", re.I)


def _extra_tiers(question):
    """Порядок доріг (етап 3 плану): каталог правилами → ВЕКТОРИ (найближчий
    шаблон за прикладами, мс) → модель-класифікатор у закритий перелік →
    вільний SELECT (ярус 2). Векторний ярус стоїть ДО гейта _DBISH: він
    безкоштовний, а розмовні фрази («дякую») інакше не дійшли б до свого
    маршруту. Модельна «відмова» зупиняє перебір: питання не про дані бази,
    ярус 2 не смикаємо."""
    out = _catalog_tier(question)
    if out is not None:
        return out
    out = _vector_tier(question)
    if out is not None:
        return out
    if not _DBISH.search(question):
        return None
    out = _model_catalog_tier(question)
    if out is _MODEL_REFUSED:
        return None
    if out is not None:
        return out
    return _tier2_tier(question)


#: Шаблони каталогу, які ВІДБИРАЮТЬ питання в старої дороги (див. розбір у
#: answer()). Рівно ті, де стара дорога помиляється: підрахунки й переліки за
#: станом. Перелік явний, а не «усе, що впізнав каталог»: решту доріг ми не
#: міряли на цьому корпусі, і тихо переводити їх перед демо не будемо.
_STATE_TEMPLATES = {
    "count_by_state_on_date",
    "count_by_state_period",
    "list_by_state",
    "absent_breakdown_on_date",
    "returning_on_date",
    # Нормативні -- сюда ж, і з тієї самої причини: стара дорога «довідник»
    # на нашій базі МЕРТВА (`db.search_reference` завжди повертає []), і на
    # «які накази зберігаються» вона казала «у базі немає нормативних
    # документів» -- при 41 такому документі. Шаблони каталогу ті самі
    # питання виконують і звірені (verify_catalog).
    "normative_list",
    "normative_search",
    # Підрозділи -- сюди ж від 25.08: штатка в базі, тому питання відповідне, а
    # стара дорога про підрозділи не знає нічого (у `people` такої колонки
    # немає -- зв'язок лежить окремим виміром `subdivision`).
    "count_by_state_in_subdivision",
    "list_by_state_in_subdivision",
    "subdivision_breakdown",
    "subdivision_unknown",
}


def _answer_inner(question, history=None):
    question = (question or "").strip()
    if not question:
        return "Поставте питання." + footer("відмова")
    if len(question) > 500:
        # гард із tiers-версії: дуже довгий текст -- не питання до бази
        return ("Занадто довге питання (понад 500 символів)."
                + footer("відмова"))
    merged, clarified = _merge_clarification(question, history)
    if is_quota_question(merged.lower()):
        # детерміновано, без моделі: причина відмови відома наперед
        return ANSWER_NO_QUOTA
    if tier_chat._DESTRUCTIVE.search(merged):
        # явно деструктивне прохання ріжеться правилами ще ДО бази й моделі:
        # чат ходить у базу read-only користувачем і нічого не змінює
        return ("Відхилено: чат працює з базою лише в режимі читання і не "
                "змінює та не видаляє дані. Зміни в базі — через сторінку "
                "завантаження і перевірку людиною."
                + footer("відмова (guard до бази й моделі)"))

    # ── Гейт «такого в базі немає взагалі» ──────────────────────────────────
    #
    # Знайдено фінальним адверсарним проходом 25.08: «Скільки поранених?» →
    # таблиця на 72 рядки «хто скільки днів у відпустці»; «Скільки людей за
    # штатом?» → дамп ПІБ. Ярус вільного SQL складав ФОРМАЛЬНО безпечний
    # запит, виконував його і віддавав дані, які не мають до питання
    # стосунку. Валідатор тут ні при чому: він перевіряє безпеку, не
    # доречність.
    #
    # Причина глибша за один ярус: наша база тримає ДОКУМЕНТИ обліку
    # відсутностей. Поранень, втрат, зброї, техніки, штату в ній немає як
    # понять -- не «немає даних», а немає такого виміру. На такі питання
    # єдина правдива відповідь -- сказати, чого саме бракує, і назвати, що
    # система справді знає.
    unknown = tier_chat.UNKNOWN_DOMAIN.search(merged.lower())
    if unknown:
        return ("Такого система не знає: у базі немає даних про "
                f"«{_esc(unknown.group(0))}» — це не «нуль», а відсутній вид "
                "інформації.\n"
                "Система працює з документами обліку відсутностей: відпустки "
                "й відрядження (дати, місце, тривалість, фактичне "
                "повернення), звання й посади з тих самих документів, номери "
                "та дати документів, нормативні акти.\n"
                "Якщо потрібне саме те, чого немає, — це нове джерело даних, "
                "не питання до цієї бази."
                + footer("відмова: виду даних немає в базі"))

    model_ok = model_available()  # без ваг модель навіть не пробуємо
    route, clarify_hint, params, fallback = None, None, None, not model_ok

    # ── Питання про СТАН -- звіреним каталогом, а не старою дорогою ──────────
    #
    # Знайдено фінальним адверсарним проходом 25.08, і це був провал усього
    # демо-сценарію. Дорога «сім функцій» (db.py, порт зі стенду) на питання
    # «хто зараз у відпустці»:
    #   - рахує РЯДКИ, а не людей: Малишко з двома квитками (№118 і його
    #     заміна №131) давала 11 замість 9;
    #   - НЕ фільтрує стан: «хто у відпустці» і «хто у відрядженні» віддавали
    #     ІДЕНТИЧНИЙ перелік;
    #   - вимагає назвати дату на слово «зараз».
    # Шаблони каталогу того самого питання звірені з незалежним підрахунком по
    # 199 записах (verify_catalog, усі шаблони) -- тобто одна дорога перевірена,
    # друга ні, і саме неперевірена стояла першою.
    #
    # Тому: якщо правила каталогу впізнали ШАБЛОН ПРО СТАН -- відповідає
    # каталог. Решта питань (особа, документ, довідник, діагностика) лишається
    # на своїх дорогах: там стара дорога дає більше, ніж каталог, і ламати її
    # за три дні до демо немає причини.
    try:
        _state_route = tier_chat.rules_route(merged)
    except Exception:
        _state_route = None
    if _state_route and _state_route[0] in _STATE_TEMPLATES:
        out = _catalog_tier(merged)
        if out is not None:
            return _as_report(out)

    # Швидкий шлях ПЕРЕД моделлю (скарга замовниці на латентність): якщо
    # правила впевнено впізнали підрахунок із власним наміром -- модель не
    # потрібна, відповідь за частки секунди. Питання без власного наміру
    # («а 4 травня?») сюди не потрапляє -- ним займеться модель із
    # позначеним контекстом або перенесення слотів нижче.
    rp = rules_params(merged)
    # «а 22?» -- день без місяця: сутність підрахунку, яку extract_date не
    # бачить; добираємо її з дати попереднього ходу (_refine_day), інакше
    # найкоротше з допитувань не потрапляє ні в швидкий шлях, ні у
    # відновлення маршруту без моделі.
    _prev_slots = _read_state(history) or {}
    _bare_day = _refine_day(merged, _prev_slots.get("date"))
    if rp["intent"] and rules_route(merged) == "підрахунок":
        # fallback лишається True, лише якщо моделі взагалі немає (чесна
        # позначка «працюють правила»); з моделлю це не деградація, а
        # свідомий швидкий шлях
        route, params = "підрахунок", rp
    elif (route is None and not rp["intent"] and _prev_slots
            and (rp["date"] or rp["subdivision"] or rp["doc_number"]
                 or rp["name"] or _bare_day)):
        # Коротке допитування («а 4 травня?», «а по 2 роті?»): є сутність
        # підрахунку, немає власного наміру, а в історії лежать слоти
        # попереднього ходу -- намір добере _carry_over детерміновано, без
        # моделі. Це головний удар по латентності: друге питання відповідає
        # за частки секунди, а не за два виклики моделі. Модельний шлях із
        # позначеним «Попереднє:/Поточне:» лишається для формулювань, які
        # правила не розібрали.
        route, params = "підрахунок", rp

    # Правила перед моделлю, крок 2: каталог SQL-шаблонів (query_catalog.yaml,
    # регекси з chat.py). Ловить те, чого сім функцій не мають: черга
    # перевірки, непідтверджені факти, кількість документів, «зараз/сьогодні».
    # Без цього модель тягла такі питання в найближчий знайомий шаблон і
    # відповідала уточненням не по темі (бачили на «Скільки непідтверджених
    # фактів?»).
    if route is None:
        cat = _catalog_tier(merged)
        if cat is not None:
            return _as_report(cat)

    # Агрегати (середнє, мін/макс, суми) — шаблонів для них немає ні тут, ні
    # в каталозі: одразу ярус 2 (порядок із tiers.py), інакше маленька модель
    # тягне їх у випадкову дорогу (бачили «цитату» на «в середньому діб»).
    if route is None and tier_chat._AGGREGATE.search(merged.lower()):
        t2 = _tier2_tier(merged)
        if t2 is not None:
            return _as_report(t2)
        # Ярус 2 не впорався або моделі немає -- чесна відмова ТУТ, не далі:
        # інакше «скільки в середньому...» падає в підрахунок і успадковує
        # слоти минулого ходу (бачили відповідь про 4 травня на питання про
        # середню тривалість у режимі без моделі).
        if model_ok:
            return _as_report(
                "Не знайшла: скласти безпечний запит для цього агрегатного "
                "питання не вдалося. Спробуйте перефразувати."
                + footer("ярус 2 (не склалось)"))
        return _as_report(
            "Не знайшла шаблону для цього агрегатного питання, а локальна "
            "модель недоступна — відповідаю лише за шаблонними питаннями."
            + footer("відмова (без моделі)"))

    if route is None and model_ok:
        progress.stage("model_route")
        try:
            r = mamaylm_json(ROUTE_SYSTEM, merged, ROUTE_SCHEMA, "route") or {}
            route = r.get("route") if r.get("route") in ROUTES else None
            clarify_hint = r.get("clarify_question") or None
            if route == "підрахунок":
                # НЕ merged: параметри витягуються з позначеного
                # «Попереднє:/Поточне:», щоб сутності бралися з поточного
                # питання, а намір міг успадкуватись
                p = mamaylm_json(PARAMS_SYSTEM, _params_input(question, history),
                                 PARAMS_SCHEMA, "params") or {}
                # None теж приймаємо: це «наміру в питанні немає», його
                # добере _carry_over. Відкидаємо лише сміття поза enum.
                if p and (p.get("intent") in INTENTS or p.get("intent") is None):
                    params = p
        except Exception:
            route = None
        if route is None:
            fallback = True
    if fallback or route is None:
        route = rules_route(merged)
        # Без моделі коротке допитування («а 4 травня?») правила відкинули б:
        # у ньому є сутність підрахунку, але немає наміру. Якщо в історії є
        # збережені слоти -- перенесення (_carry_over нижче) добере намір
        # детерміновано, тож маршрут чесно лишається підрахунком.
        if (route == "відмова" and _prev_slots
                and (extract_date(merged) or extract_doc_number(merged)
                     or normalize_subdivision(merged) or _bare_day)):
            route = "підрахунок"
    elif route == "відмова" and extract_doc_number(merged):
        # явна сутність підрахунку (№ документа) перемагає — див. шапку.
        # Модель інколи відмовляє на номер із шумом OCR (№3О4)
        route = "підрахунок"
    if route == "підрахунок" and params is None:
        params = rules_params(merged)

    # Слоти, яких у питанні не було, добираємо з попереднього ходу. Робиться
    # ПІСЛЯ моделі й правил: спершу чесно дивимось, що є в самому питанні.
    if route == "підрахунок" and params is not None:
        # Дата -- ЛИШЕ з правил. Модель тут не авторитет: вона підставляла
        # то дату зі стенду (на «скільки сьогодні повертаються?» відповідала
        # за 2026-05-24), то дату з прикладу у власному промпті. Правила
        # читають буквальний текст питання й покривають ISO, «23 травня»,
        # «15.05.2026» і «сьогодні/вчора/завтра»; чого вони не впізнали --
        # того в питанні й немає, слот лишається порожнім і його добере
        # перенесення з попереднього ходу. merged, не question: склейка
        # «бот спитав -- людина відповіла» не має губити дату з питання,
        # до якого було уточнення.
        params = dict(params, date=extract_date(merged))
        params = _drop_invented_name(params, merged)
        prev_state = _read_state(history)
        if not params["date"]:
            params["date"] = _refine_day(question, (prev_state or {}).get("date"))
        params = _carry_over(params, prev_state)
        own = rules_params(question)
        if not own["intent"] and (prev_state or {}).get("intent"):
            # Питання власного наміру не називає («а 22?») -- тримаємо
            # попередній, а не те, що вгадала модель. Вона мусить обрати
            # щось із enum навіть коли обирати нема з чого, і на коротких
            # уточненнях це «щось» стрибало: на «а 22?» -- «поза частиною»
            # після двох питань про повернення.
            params = dict(params, intent=prev_state["intent"])
        elif own["intent"] and own["intent"] != params.get("intent"):
            # Запобіжник проти зайвого успадкування: якщо ПОТОЧНЕ питання
            # само однозначно називає намір (№документа, ПІБ, «хто
            # повертається»...), він головніший за успадкований. Без цього
            # «Хто у відпустці за квитком №301?» після питання з датою
            # відповідало старим наміром і старою датою -- розмітка
            # «Попереднє:» робить модель надто охочою тягнути контекст.
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
        progress.stage("db")
        result = dispatch_count(params, clarified, clarify_hint, merged)
        if isinstance(result, tuple) and result[0] == "clarify":
            out = f"{CLARIFY_MARK}\n{result[1]}" + footer("підрахунок (потрібне уточнення)")
        elif result is None:
            # сім функцій не підійшли → каталог шаблонів → ярус 2 → чесна
            # відмова (порядок доріг з шапки файлу)
            out = _extra_tiers(merged) or (
                "Порахувати це за наявними полями не можна (у документах "
                "немає таких даних).\n" + ANSWER_REFUSE)
        else:
            out = result
    elif route == "довідник":
        progress.stage("db")
        out = answer_reference(merged)
    elif route == "цитата":
        out = ANSWER_QUOTE
    elif route == "діагностика":
        out = ANSWER_DIAG
    else:
        # перед чесною відмовою -- каталог шаблонів і ярус 2 (вільний SELECT
        # під рейками); якщо і вони не впорались, відмова лишається відмовою
        out = _extra_tiers(merged) or ANSWER_REFUSE

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


# ── Журнал звернень і номер звернення (етап 5) ───────────────────────────────
#
# Навіщо номер. На демо й у пілоті людина каже «чат відповів дивно», і без
# номера це нерозв'язна задача: у логах десятки питань, а яке саме -- невідомо.
# Номер видно у блоці «джерело» кожної відповіді, і по ньому в журналі
# знаходиться рівно той хід: питання, дорога, час, чим закінчилось.
#
# Навіщо журнал саме такий. Він пише РЯДОК НА ХІД, а не дамп: питання (обрізане),
# дорога, тривалість, довжина відповіді. Персональних даних із бази в журнал не
# їде -- лише те, що людина сама написала, і те, як система це обробила.
_journal = logging.getLogger("chat.journal")

ANSWER_DB_DOWN = (
    "База зараз недоступна, тому відповісти не можу. Це не «нічого не "
    "знайшлося» — це збій доступу до даних: цифри в базі на місці, але чат до "
    "них не дістає. Спробуйте ще раз за хвилину; якщо повториться — скажіть "
    "адміністратору."
    + footer("збій доступу до бази"))


def _request_id():
    """Короткий номер: шість символів достатньо, щоб знайти хід у журналі за
    день роботи, і достатньо коротко, щоб людина продиктувала його голосом."""
    return uuid.uuid4().hex[:6]


def _with_request_id(text, cid):
    """Дописати номер звернення у відповідь, якщо його там ще немає.

    Навіщо, якщо footer() і так його ставить. Частина відповідей -- ГОТОВІ
    КОНСТАНТИ (ANSWER_REFUSE, відмова про підрозділи, «немає норми»): їхній
    footer зібрався при ІМПОРТІ модуля, коли номера ще не існувало. Заміряно
    verify_stack на сервері 25.08: саме відмови приходили без номера -- тобто
    рівно ті відповіді, на які людина і скаржиться («чат відмовив, а чому?»).

    Дописуємо в кінець блоку «джерело», а не окремим рядком у кінці тексту:
    у кінці стоїть службовий маркер стану діалогу, і текст після нього зламав
    би його читання наступним ходом.
    """
    if not text or "звернення: " in text:
        return text
    tail = "</details>"
    if tail in text:
        cut = text.rindex(tail)
        return text[:cut] + f"<br>звернення: {cid}" + text[cut:]
    return text + f"\n\nзвернення: {cid}"


def answer(question, history=None):
    """Обгортка над `_answer_inner`: номер звернення, журнал і ОДНА чесна
    відмова замість технічного винятку, коли база недоступна.

    Чому саме недоступність бази ловиться окремо, а решта винятків -- ні.
    «Не знайшла» і «база недоступна» -- різні твердження (критерій приймання
    П3), і плутати їх не можна: у першому випадку даних немає, у другому вони
    є, але ми до них не дістали. Перевірено 25.08: без цієї обгортки впала
    база давала користувачу технічну помилку, а не відповідь. Решта винятків
    навмисно летять далі: це наші баги, і глушити їх «щось пішло не так»
    означає ховати їх від себе."""
    cid = _request_id()
    token = _CURRENT_ID.set(cid)
    started = time.monotonic()
    try:
        out = _answer_inner(question, history)
    except psycopg.OperationalError as exc:
        _journal.warning("[%s] база недоступна за %.1f с: %s | питання: %.80s",
                         cid, time.monotonic() - started,
                         type(exc).__name__, question or "")
        return _with_request_id(ANSWER_DB_DOWN, cid)
    finally:
        _CURRENT_ID.reset(token)
    out = _with_request_id(out, cid)
    _journal.info("[%s] %.1f с | відповідь %d символів | питання: %.80s",
                  cid, time.monotonic() - started, len(out or ""),
                  question or "")
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
# Спільні токени обличчя апки (задача B3): один файл на чат і на дві звичайні
# сторінки (/ і /stats). Лежить у static/, бо звідти його <link>-ом беруть
# сторінки; чат підклеює його вмістом (див. make_head_css).
TOKENS_CSS = os.path.join(os.path.dirname(HERE), "static", "theme-tokens.css")

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


def make_theme():
    """Google Fonts вирізано свідомо: gr.themes.GoogleFont тягне css/шрифти з
    fonts.googleapis.com -- зовнішній запит, порушення «все локально».
    Системні шрифти, жодного звернення назовні. У Gradio 6 theme/head
    передаються в mount_gradio_app / launch, не в Blocks -- тому окремі
    функції, які кличе і апка, і standalone-запуск."""
    import gradio as gr
    return gr.themes.Base(
        font=["system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        font_mono=["ui-monospace", "Consolas", "monospace"],
        radius_size=gr.themes.sizes.radius_sm,
        spacing_size=gr.themes.sizes.spacing_md,
    )


def make_head_css():
    # Gradio 6 скоупить усе, що прийшло через css/css_paths (`:root`
    # переписується і не збігається ні з чим) -- той самий файл через head
    # працює без скоупінгу (рішення з оригіналу, лишено).
    #
    # Два файли в один <style> і саме в такому порядку: спершу спільні
    # токени (:root), потім правила чата, які на них спираються. <link> на
    # /static/theme-tokens.css тут не годиться -- head Gradio віддається до
    # монтування, і відносний шлях під root_path=/chat не резолвиться;
    # вміст файлу від місця монтування не залежить.
    parts = []
    for path in (TOKENS_CSS, THEME_CSS):
        with open(path, encoding="utf-8") as fh:
            parts.append(fh.read())
    return "<style>" + "\n".join(parts) + "</style>"


def build_blocks():
    import gradio as gr

    # приклади -- під нашу реальну базу (3 демо-документи, травень 2026)
    EXAMPLES = [
        "Хто був у відпустці 5 травня?",
        "Хто повертається 2026-05-09?",
        "Що відомо про Усика?",
        "Покажи документ №109",
        "Скільки непідтверджених фактів?",
        "Скільки документів чекають перевірки?",
    ]

    def respond(message, history):
        """Генератор: кадри ВИДИМОГО СТАНУ на місці відповіді, потім сама
        відповідь.

        Було: одні крапки на весь час обробки. Стало (задача B1, критерій
        Ш3): назва ярусу, який зараз працює, і секундомір, який рухається.
        Причина -- 43-47 с на виклик локальної моделі: три нерухомі крапки
        на такому часі люди читають як «зависло», а назва ярусу («модель
        складає SQL-запит») ще й розповідає, як продукт улаштований.

        Механіка -- progress.stream(): answer() їде в окремому потоці, а цей
        генератор тікає таймером і віддає кадри. Сам answer() не змінився:
        яруси лише позначають, де вони (progress.stage), нічого не
        повертаючи інакше.
        """
        message = (message or "").strip()
        history = history or []
        if not message:
            yield "", history, gr.update(), gr.update(visible=False), message
            return
        asked = history + [{"role": "user", "content": message}]

        def frame(content, failed=False):
            return ("", gr.update(value=asked + [{"role": "assistant",
                                                  "content": content}],
                                  visible=True),
                    gr.update(visible=False), gr.update(visible=failed),
                    message)

        rendered, failed = None, False
        for kind, payload in progress.stream(lambda: answer(message, history)):
            if kind == "stage":
                yield frame(payload)
            elif kind == "error":
                rendered = _note("error", "Запит не вдалося виконати: "
                                          f"{type(payload).__name__}. "
                                          "Дані не змінено.")
                failed = True
            else:
                reply = payload
                # Попередження про правила — один раз за сесію: у лівій панелі
                # воно висить постійно, у кожній відповіді лише глушить текст.
                # Шукаємо в історії вже відрендерений блок, а не сирий маркер:
                # після render_reply() сирого тексту в історії не лишається.
                if reply.startswith(WARN_FALLBACK) and any(
                        "note--info" in _msg_text(m) for m in history):
                    reply = reply[len(WARN_FALLBACK):].lstrip("\n")
                rendered = render_reply(reply)
        yield frame(rendered if rendered is not None else TYPING_HTML, failed)

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
    # знак системи -- інлайном: шлях /gradio_api/file=... під монтуванням у
    # FastAPI (root_path=/chat) не резолвиться, а інлайн-SVG не залежить від
    # того, де живе апка
    with open(MARK, encoding="utf-8") as fh:
        mark_svg = fh.read()

    with gr.Blocks(title="AI-секретар", fill_height=True) as demo:
        last_q = gr.State("")

        with gr.Row(equal_height=False):
            with gr.Column(scale=0, min_width=272, elem_id="sidebar"):
                gr.HTML(
                    f'<div id="brand">'
                    f'{mark_svg}'
                    f'<div><div class="brand-name">AI-секретар</div>'
                    f'<div class="brand-sub">облік особового складу</div>'
                    f'</div></div>')
                new_chat = gr.Button("＋  Новий чат", elem_id="new-chat")
                # Переходи між сторінками апки -- ті самі три, що в шапці
                # звичайних сторінок (/ і /stats): один продукт означає, що
                # набір переходів не змінюється від екрана до екрана.
                gr.HTML('<a href="/" class="page-link">⬆&nbsp; Завантажити '
                        'документ</a>'
                        '<a href="/stats" class="page-link">▤&nbsp; '
                        'Статистика</a>', elem_id="page-links")
                gr.Markdown("Приклади питань", elem_classes="side-heading")
                with gr.Column(elem_id="examples"):
                    side_btns = [gr.Button(q) for q in EXAMPLES]
                dot, state_text = (("ok", f"модель {get_model()} — локально")
                                   if model_available()
                                   else ("rules", "модель недоступна — "
                                                  "працюють правила"))
                gr.HTML(f'<div id="side-status"><p>У підрахунках — лише '
                        f'підтверджені факти; чернетки окремим числом.'
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

    # черга: виклик моделі не блокує інші запити (сам виклик серіалізований
    # локом у tiers.py, але шаблонні відповіді йдуть паралельно)
    demo.queue(default_concurrency_limit=4)
    return demo


def main():
    """Окремий запуск для дебагу; бойовий шлях -- монтування в FastAPI апки
    (demos/upload_app/app.py, gr.mount_gradio_app(..., path='/chat'))."""
    demo = build_blocks()
    warm_up_async()
    demo.launch(server_name="127.0.0.1",
                server_port=int(os.environ.get("CHAT_PORT", "7861")),
                share=False, inbrowser=False,
                theme=make_theme(), head=make_head_css())


if __name__ == "__main__":
    main()
