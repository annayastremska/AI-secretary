# -*- coding: utf-8 -*-
"""Створення документа в чаті: питання за питанням, без моделі.

Задача C1. Аня описала так: «чат питає який вид доку, після цього відкриває
потрібний шаблон і знаючи необхідні для нього поля питає ПІБ і тд».

## Звідки беруться питання, і чому не зі схеми читання

Спершу я написала, що перелік питань виводиться зі схеми
`pipeline/schemas/*.yaml`, якою система документ ЧИТАЄ. Замір це спростував:

    ключів, яких чекає заповнювач бланка (відпустка):  18
    імен полів у схемі:                                19
    СПІЛЬНИХ ІМЕН:                                      0

Схема описує читання, заповнювач -- письмо, і між ними не перейменування, а
переклад із похідними: ініціали (`PERSON_SHORT`), число словами
(`DAYS_WORDS`), дата частинами (`RET_D/M/Y`), формулювання, ЗАЛЕЖНІ ВІД
СТАТІ (`RELEASED`, `OBLIGED`), підписант -- поле, яке пайплайн узагалі не
витягує.

Тому джерело істини для «що питати» -- те, чого чекає ЗАПОВНЮВАЧ. Схема
лишається джерелом перевірок (типи, межі, довідники, узгодженість).

## Другої копії заповнювача немає

Бланки заповнює той самий код, яким згенеровано весь наш корпус:
`load_roster` → `person_slots` → `build_leave_values` /
`build_deployment_values` → `fill_leave_docx` / `fill_deployment_docx`.
Він лежить у генераторі демо-набору, і беремо ми його **лінивим імпортом за
шляхом** -- так уже робить `generate_one_live.py`.

Чому не своя копія: правило, що живе у двох місцях, розходиться, і за три дні
це вже сталося чотири рази (обрізання ПІБ, розпаковка історії, витяг номера
документа, гейт цитати). Ціна цього рішення названа вголос: продовий код
залежить від файла в `data/`, і важкі бібліотеки (PIL, numpy) тягнуться при
ПЕРШОМУ створенні документа, а не при старті процесу -- тому імпорт стоїть
усередині функції.

## Чого тут немає

* **моделі.** Питання ставить код, відповіді перевіряють нормалізатори. Тому
  режим працює за частки секунди й не має де вигадати значення;
* **запису в базу.** Створений документ -- чернетка: людина підписує його й
  завантажує звичайним шляхом. Якби він ішов у базу навпростець, там з'явився
  б факт, якого ніхто не читав із документа;
* **вигадування особи.** ПІБ, якого немає у штатці, не приймається: без
  штатки невідома навіть стать, а від неї залежить текст бланка.
"""
import datetime
import glob
import importlib.util
import io as _io
import json
import os
import re
import time
import uuid

try:
    from . import tiers as _t
except ImportError:                                  # pragma: no cover
    import tiers as _t

CHAT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(CHAT_DIR)
ROOT = os.path.dirname(os.path.dirname(APP_DIR))

#: Генератор демо-набору: у ньому бланки, штатка й заповнювачі.
_GEN_PATH = os.path.join(ROOT, "data", "eval", "samples", "demo-story",
                         "generate_demo_story.py")

#: Куди складаємо створені файли. Тимчасова тека процесу: ні `data/`, ні база,
#: ні git. Колекція документів із персональними даними -- це нове джерело
#: даних і окреме рішення, а не побічний ефект кнопки.
OUT_DIR = os.path.join(APP_DIR, "_docgen-out")

#: Скільком годин живе створений файл. Прибирається при кожному створенні.
KEEP_HOURS = 1

#: Скільком невдалих спроб на одне поле, після чого пропонуємо вихід.
MAX_TRIES = 3

#: СЛІД РЕЖИМУ -- ОКРЕМИЙ ФАЙЛ, а не спільний із чатом.
#:
#: Аня спитала, чи не змішувати. Не змішувати, і причина не смакова:
#: `trace_lookup --check` судить кожен запис правилом «у відповіді є джерело».
#: У режимі створення «відповідь» -- це ПИТАННЯ до людини, джерела в неї немає
#: за побудовою, тому в спільному файлі прилад почав би рахувати порушення
#: там, де їх нема. Ми вже наступали на це рівно раз, коли тести писали в
#: бойовий слід і прилад закричав «ЗЛАМАНО» про власний набір.
TRACE_PATH = os.path.join(ROOT, "logs", "docgen-trace.jsonl")

_CACHE = {}


def _gen():
    """Генератор демо-набору. Імпорт ЛІНИВИЙ і за шляхом.

    Лінивий -- бо він тягне PIL і numpy, а веб-процесу вони при старті не
    потрібні. За шляхом -- бо файл лежить у `data/`, тобто пакетом не є; так
    само його імпортує `generate_one_live.py`.
    """
    if "gen" not in _CACHE:
        spec = importlib.util.spec_from_file_location("gen_demo_story",
                                                      _GEN_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _CACHE["gen"] = mod
    return _CACHE["gen"]


# ── Види документів ─────────────────────────────────────────────────────────


def kinds():
    """-> [{key, title, blank}] -- два бланки, які система вміє читати."""
    g = _gen()
    return [
        {"key": "leave", "title": "відпускний квиток", "blank": g.LEAVE_BLANK},
        {"key": "deployment", "title": "посвідчення про відрядження",
         "blank": g.DEPL_BLANK},
    ]


def _kind_of(text):
    """Вид документа з відповіді людини. -> ключ або None."""
    low = (text or "").lower()
    if re.search(r"відпуст|квиток", low):
        return "leave"
    if re.search(r"відрядж|посвідч", low):
        return "deployment"
    return None


# ── План питань ─────────────────────────────────────────────────────────────
#
# Порядок такий, як його диктує сам документ: спершу про кого, потім про що,
# потім дати, і в кінці номер. Номер останній навмисно: перевірка перетину
# періодів робиться ДО нього, щоб людина не відповідала на всі питання й аж
# тоді дізнавалась, що документ не потрібен.

_PLAN = {
    "leave": [
        {"name": "person", "type": "person", "required": True,
         "ask": "Прізвище, ім'я, по батькові."},
        {"name": "leave_type", "type": "text", "required": True,
         "ask": "Вид відпустки так, як він названий у наказі — наприклад: "
                "щорічна основна відпустка за 2026 рік."},
        {"name": "place", "type": "text", "required": True,
         "ask": "Населений пункт, куди звільнено. З типом населеного пункту, як у документі — наприклад: м. Кривоярськ."},
        {"name": "start", "type": "date", "required": True,
         "ask": "Дата початку відпустки."},
        {"name": "end", "type": "date", "required": True,
         "ask": "Дата завершення відпустки."},
        {"name": "vpd", "type": "text", "required": False,
         "ask": "Номер військового перевізного документа, якщо видавався. Наприклад: 4480/26. Немає — скажіть «пропустити»."},
        {"name": "companions", "type": "text", "required": False,
         "ask": "Хто прямує разом, якщо є такі. Прізвище й ініціали — наприклад: Орлик Р.К. Немає — «пропустити»."},
        {"name": "number", "type": "number", "required": True,
         "ask": "Номер документа."},
        {"name": "issue", "type": "date", "required": True,
         "ask": "Дата видачі документа."},
    ],
    "deployment": [
        {"name": "person", "type": "person", "required": True,
         "ask": "Прізвище, ім'я, по батькові."},
        {"name": "dest", "type": "text", "required": True,
         "ask": "Населений пункт призначення. З типом населеного пункту — наприклад: м. Рівне."},
        {"name": "dest_org", "type": "text", "required": True,
         "ask": "Найменування частини або організації — наприклад: "
                "військова частина К2317."},
        {"name": "purpose", "type": "text", "required": True,
         "ask": "Мета відрядження. Одним рядком, як у наказі — наприклад: приймання матеріально-технічних засобів."},
        {"name": "start", "type": "date", "required": True,
         "ask": "Дата початку відрядження."},
        {"name": "end", "type": "date", "required": True,
         "ask": "Дата завершення відрядження."},
        {"name": "order_number", "type": "number", "required": True,
         "ask": "Номер наказу, на підставі якого відряджається."},
        {"name": "order", "type": "date", "required": True,
         "ask": "Дата того наказу."},
        {"name": "number", "type": "number", "required": True,
         "ask": "Номер посвідчення."},
        {"name": "issue", "type": "date", "required": True,
         "ask": "Дата видачі посвідчення."},
    ],
}


def plan(kind):
    return [dict(f) for f in _PLAN[kind]]


# ── К2: перевірка повноти мапінгу ───────────────────────────────────────────
#
# Найризикованіше місце режиму: людина відповіла на всі питання, а у файлі
# порожні місця. Тому перелік ключів беремо з САМОГО заповнювача -- розбором
# його коду, а не з уявлення про нього.

#: Зразковий набір відповідей: рівно ті поля, які питає режим. Потрібен, щоб
#: перевірити повноту мапінгу НЕ за моїм переліком, а за тим, що заповнювач
#: справді отримує.
_SAMPLE = {
    "leave": {"person": None, "leave_type": "щорічна основна відпустка",
              "place": "м. Кривоярськ", "start": "2026-11-03",
              "end": "2026-11-12", "number": "9001", "issue": "2026-11-01"},
    "deployment": {"person": None, "dest": "м. Заріччя",
                   "dest_org": "військова частина К2317",
                   "purpose": "приймання засобів", "start": "2026-11-03",
                   "end": "2026-11-07", "order_number": "447",
                   "order": "2026-11-01", "number": "9002",
                   "issue": "2026-11-02"},
}


def blank_keys(kind):
    """Ключі, яких чекає заповнювач бланка. -> множина.

    Читаються з КОДУ заповнювача. З'явиться там нове значення -- тест повноти
    впаде, і це єдиний спосіб дізнатись про нього вчасно, а не з порожнього
    місця у файлі.
    """
    src = open(_GEN_PATH, encoding="utf-8").read()
    fn = ("def fill_leave_docx" if kind == "leave"
          else "def fill_deployment_docx")
    body = src.split(fn, 1)[1].split("\ndef ", 1)[0]
    return set(re.findall(r"""v\[["']([A-Z_0-9]+)["']\]""", body))


def covered_keys(kind):
    """Ключі, які заповнювач СПРАВДІ отримує від наших відповідей.

    Спершу я перелічила їх руками -- і перелік одразу розійшовся: для
    відрядження бракувало DAYS, ORDER_BASIS, POSITION і SENT_TO. Ручний
    перелік і мусив розійтись: він описує не те, що відбувається, а те, що я
    про це думаю.

    Тому тут ми складаємо значення тим самим кодом, що при справжньому
    створенні, і дивимось, які ключі вийшли.
    """
    g = _gen()
    answers = dict(_SAMPLE[kind])
    answers["person"] = next(iter(g.load_roster()))
    values = _values_for(kind, answers)
    return set(values)


# ── Особа ───────────────────────────────────────────────────────────────────


def _roster():
    if "roster" not in _CACHE:
        _CACHE["roster"] = list(_gen().load_roster().values())
    return _CACHE["roster"]


def find_person(text):
    """Особа зі штатки за ПІБ. -> (рядки, помилка).

    Пошук по межі слова тією самою логікою, що в чаті: підрядок ловив би
    «Богодар» у «Богодарович» -- це вже давало впевнену відповідь про іншу
    людину (п. 6-7 звіту Дениса).
    """
    words = [w for w in re.findall(r"[А-ЯІЇЄҐа-яіїєґ'ʼ-]{3,}", text or "")]
    if not words:
        return [], "Не бачу прізвища. Назвіть ПІБ."

    # СПЕРШУ ТОЧНИЙ ЗБІГ СЛОВА, і лише потім допуск на відмінок.
    #
    # `name_pattern` навмисно терпимий: він мусить знаходити «Крижанівського»
    # як «Крижанівський», інакше чат заперечує власні дані. Але тут людина
    # НАЗИВАЄ особу, щоб на неї виписали документ, і терпимість грає проти:
    # «Гавриш» ловило чотирьох -- Гавриша, Гаврилева, Гаврилюка й другого
    # Гавриша. Тому точний збіг перший, а допуск -- лише коли точного немає.
    def _pick(strict):
        rows = _roster()
        for w in words:
            if strict:
                low = w.lower()
                rows = [r for r in rows
                        if low in r["full_name"].lower().split()]
            else:
                rx = re.compile(_t.name_pattern(w), re.IGNORECASE)
                rows = [r for r in rows if rx.search(r["full_name"])]
            if not rows:
                return []
        return rows

    rows = _pick(strict=True) or _pick(strict=False)
    if not rows:
        return [], ("Такої особи у штатці немає. Створити документ на людину, "
                    "якої немає в реєстрі, я не можу: без штатки невідомі "
                    "звання, підрозділ і навіть стать, а від них залежить "
                    "текст бланка.")
    if len(rows) > 1:
        names = "; ".join(r["full_name"] for r in rows[:5])
        more = f" і ще {len(rows) - 5}" if len(rows) > 5 else ""
        return rows, (f"Кого саме? Знайшла: {names}{more}. "
                      "Назвіть повніше.")
    return rows, None


# ── Перевірка відповідей ────────────────────────────────────────────────────


def validate_answer(field, text, answers):
    """-> (значення, помилка, примітка). Значення None означає «не прийнято»."""
    raw = (text or "").strip()
    if not raw:
        return None, "Порожня відповідь.", None

    if field["type"] == "person":
        rows, err = find_person(raw)
        if err:
            return None, err, None
        r = rows[0]
        note = (f"є у штатці: {r['rank']}, {r['subdivision']}")
        return r["service_id"], None, note

    if field["type"] == "date":
        # НЕДІЙСНА ДАТА -- окремий випадок, і сказати про неї треба ІНШЕ.
        #
        # Перша версія перевіряла це після розбору, і «31 лютого 2026»
        # отримувало «не зрозуміла дату»: розбір такої дати не дає нічого, бо
        # її не існує. Але людина написала зрозуміло -- неправильна сама дата,
        # і відповідь мусить казати саме це.
        if _looks_like_a_date(raw) and not _parse_date(raw):
            return None, ("Такої дати не існує. Перевірте число й місяць."), None
        iso = _parse_date(raw)
        if not iso:
            return None, ("Не зрозуміла дату. Напишіть у форматі "
                          "YYYY-MM-DD або «21 вересня 2026»."), None
        start = answers.get("start")
        if field["name"] == "end" and start and iso < start:
            return None, (f"Кінець ({iso}) раніше за початок ({start}). "
                          "Межі не міняю — назвіть іншу дату."), None
        note = None
        if field["name"] == "end" and start:
            days = (datetime.date.fromisoformat(iso)
                    - datetime.date.fromisoformat(start)).days + 1
            note = f"{iso}, тривалість {days} днів"
        return iso, None, (note or iso)

    if field["type"] == "number":
        num = raw.lstrip("№").strip()
        if not num.isdigit():
            return None, ("У номері мусять бути лише цифри. Якщо в документі "
                          "стоїть літера замість нуля — це помилка "
                          "документа, а не номер."), None
        return num, None, None

    return raw, None, None


_MONTHS = {"січ": 1, "лют": 2, "берез": 3, "квіт": 4, "трав": 5, "черв": 6,
           "лип": 7, "серп": 8, "верес": 9, "жовт": 10, "листоп": 11,
           "груд": 12}


def _looks_like_a_date(text):
    """Чи людина ВЗАГАЛІ називала дату (хай і неможливу).

    Потрібне, щоб відрізнити «31 лютого» (дата названа, але такої немає) від
    «щось незрозуміле» (дати немає взагалі). Дві різні відповіді людині.
    """
    low = (text or "").lower()
    if re.search(r"\b\d{4}-\d{1,2}-\d{1,2}\b", low):
        return True
    if re.search(r"\b\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4}\b", low):
        return True
    return bool(re.search(r"\b\d{1,2}\s+[а-яіїєґ]{3,}", low)
                and any(re.search(r"\b\d{1,2}\s+" + stem, low)
                        for stem in _MONTHS))


def _parse_date(text):
    """ISO-дата з тексту. -> «YYYY-MM-DD» або None."""
    low = (text or "").lower().strip()
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", low)
    if m:
        return _iso(m.group(1), m.group(2), m.group(3))
    m = re.search(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\b", low)
    if m:
        return _iso(m.group(3), m.group(2), m.group(1))
    m = re.search(r"\b(\d{1,2})\s+([а-яіїєґ]{3,})\w*\s*(\d{4})?", low)
    if m:
        for stem, num in _MONTHS.items():
            if m.group(2).startswith(stem):
                year = m.group(3) or str(datetime.date.today().year)
                return _iso(year, num, m.group(1))
    return None


def _iso(year, month, day):
    try:
        return datetime.date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


# ── Номери документів ───────────────────────────────────────────────────────


def taken_numbers():
    """Номери документів, які вже є в базі. -> множина рядків."""
    try:
        with _t._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT DISTINCT btrim(f.value) AS num
                         FROM facts f JOIN dimensions d ON d.id = f.dimension_id
                        WHERE d.code = 'document_number'""")
                return {r["num"] for r in cur.fetchall() if r["num"]}
    except Exception:
        # Не блокуємо режим: кажемо, що перевірити не вдалось.
        return set()


def next_free_number(taken):
    """Найближчий вільний номер після найбільшого зайнятого."""
    nums = sorted(int(n) for n in taken if str(n).isdigit())
    return str((nums[-1] + 1) if nums else 1)


# ── Перетин періодів у ТІЄЇ САМОЇ особи ─────────────────────────────────────

_DIM = {"leave": "leave", "deployment": "deployment_location"}


def find_conflicts(service_id, kind, start, end):
    """Чинні факти цієї особи того самого виміру, які перетинаються з періодом.

    Мова про ОДНУ людину: двоє різних одночасно у відпустці -- норма. Дотик
    день-у-день перетином не вважається (`<` і `>` на межах), бо документ, що
    починається наступного дня після завершення попереднього, суперечності не
    створює.
    """
    dim = _DIM[kind]
    try:
        with _t._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT f.valid_from, f.valid_to, f.source_doc_id,
                           (SELECT btrim(n.value) FROM facts n
                              JOIN dimensions nd ON nd.id = n.dimension_id
                             WHERE n.source_doc_id = f.source_doc_id
                               AND nd.code = 'document_number' LIMIT 1) AS number
                      FROM facts f
                      JOIN dimensions d ON d.id = f.dimension_id
                      JOIN people p ON p.object_id = f.object_id
                     WHERE p.service_id = %s AND d.code = %s
                       AND f.status = 'confirmed'
                       AND f.valid_from IS NOT NULL
                       AND f.valid_from <= %s
                       AND COALESCE(f.valid_to, f.valid_from) >= %s
                    """, (service_id, dim, end, start))
                return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


# ── Складання файла ─────────────────────────────────────────────────────────


def _values_for(kind, answers):
    """Значення для бланка з відповідей людини. -> словник.

    Винесено окремо навмисно: цим самим кодом і СТВОРЮЄТЬСЯ документ, і
    перевіряється повнота мапінгу. Дві копії тут означали б, що перевірка
    перевіряє не те, що працює.
    """
    g = _gen()
    roster = g.load_roster()
    person = roster[answers["person"]]
    who = g.person_slots(person, in_roster=True)
    today = datetime.date.today()

    def off(key):
        """Генератор рахує дати зсувом від «сьогодні» -- переводимо."""
        v = answers.get(key)
        return None if not v else (datetime.date.fromisoformat(v) - today).days

    if kind == "leave":
        spec = dict(id="CHAT", kind="leave", who=answers["person"],
                    number=answers["number"], issue=off("issue"),
                    start=off("start"), end=off("end"), ret=off("ret"),
                    leave_type=answers.get("leave_type", ""),
                    place=answers.get("place", ""),
                    vpd=answers.get("vpd") or "не видавались",
                    companions=answers.get("companions") or "—", fmt="docx")
    else:
        spec = dict(id="CHAT", kind="deployment", who=answers["person"],
                    number=answers["number"], issue=off("issue"),
                    order=off("order"),
                    order_number=answers.get("order_number", ""),
                    start=off("start"), end=off("end"),
                    dest=answers.get("dest", ""),
                    dest_org=answers.get("dest_org", ""),
                    purpose=answers.get("purpose", ""), fmt="docx")
    values, _ = g._build_one(spec, who, today)
    return values


def build(kind, answers, out_dir=None):
    """Заповнений бланк. -> шлях до файла.

    Значення складає той самий код, що весь наш корпус, тому документ
    упізнається тими самими схемами при завантаженні назад.
    """
    g = _gen()
    out = out_dir or OUT_DIR
    os.makedirs(out, exist_ok=True)
    _clean_old(out)
    values = _values_for(kind, answers)
    name = (("відпускний_квиток" if kind == "leave" else "посвідчення")
            + f"_{answers['number']}_{int(time.time())}.docx")
    path = os.path.join(out, name)
    (g.fill_leave_docx if kind == "leave" else g.fill_deployment_docx)(
        path, values)
    _stamp(path)
    return path


#: Позначка, яку прибрати не можна. Причина названа в пропозиції: гостям
#: створювати документи дозволено (рішення Ані), а файл виглядає як справжній
#: службовий документ. Ця позначка -- єдине, що відрізняє його в чужих руках.
STAMP = ("Сформовано автоматично системою «AI-секретар». "
         "Перед підписанням перевірити.")


def _stamp(path):
    from docx import Document
    doc = Document(path)
    doc.add_paragraph(STAMP)
    doc.save(path)


def _clean_old(out):
    """Тимчасові файли не накопичуються."""
    edge = time.time() - KEEP_HOURS * 3600
    for f in glob.glob(os.path.join(out, "*.docx")):
        try:
            if os.path.getmtime(f) < edge:
                os.remove(f)
        except OSError:
            pass


# ── Стан збирання: живе в СЕАНСІ, не в модулі ───────────────────────────────
#
# Модульна змінна дала б «один увімкнув -- у всіх увімкнулось»: це найтиповіша
# помилка такого перемикача, і на демо вона виглядала б як зламаний чат.

def start():
    """Новий сеанс збирання. Номер звернення -- одразу.

    Один номер на ВЕСЬ документ, а не на кожне питання: людина показує на
    подію («створювала документ, і ось що вийшло»), а подія тут одна. Десять
    номерів під десятьма питаннями були б шумом, у якому неможливо вказати на
    потрібний.
    """
    return {"id": uuid.uuid4().hex[:6],
            "kind": None, "idx": 0, "answers": {}, "tries": 0, "done": False,
            "confirm": False, "conflict": None}


def done_line(state):
    """Рядок із номером звернення для кінця розмови."""
    return f"звернення {(state or {}).get('id', '—')}"


def _log(state, event, **fields):
    """Слід режиму. ЖОДНОГО ЗНАЧЕННЯ полів -- лише що відбулось.

    Тут це строгіше, ніж у чаті: у режимі людина ДИКТУЄ ПІБ, дати й місце
    призначення. Правило проєкту -- у логи не їде персональна інформація, і
    писати відповіді означало б зібрати картотеку в лог-файлі.
    """
    rec = {"id": (state or {}).get("id"),
           "at": datetime.datetime.now().replace(microsecond=0).isoformat(),
           "kind": (state or {}).get("kind"),
           "event": event}
    rec.update(fields)
    try:
        os.makedirs(os.path.dirname(TRACE_PATH), exist_ok=True)
        with _io.open(TRACE_PATH, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        # Слід -- допоміжне знання. Не записався -- людина однаково отримує
        # документ.
        pass


def is_done(state):
    return bool((state or {}).get("done"))


#: Наполягання сформувати попри перетин. Окремим шаблоном, а не рядком у
#: переліку: воно перевіряється у двох місцях (у стані перетину й поза ним).
_OVERRIDE = re.compile(r"^\s*формуй\s*(усе\s*одно|все\s*одно)?\s*$",
                       re.IGNORECASE)


def _current_field_name(state):
    """Назва поля, на якому стоїмо. Для сліду: назва -- не персональні дані."""
    st = state or {}
    if not st.get("kind"):
        return "kind"
    fields = plan(st["kind"])
    idx = st.get("idx", 0)
    return fields[idx]["name"] if idx < len(fields) else "summary"


def _current_ask(state):
    """Питання, на якому стоїмо. Потрібне, щоб після керівної фрази людина
    бачила, чого від неї чекають, а не лишалась без підказки."""
    st = state or {}
    if not st.get("kind"):
        return ("Який документ створюємо? Скажіть «відпускний квиток» або "
                "«посвідчення про відрядження».")
    fields = plan(st["kind"])
    idx = st.get("idx", 0)
    return fields[idx]["ask"] if idx < len(fields) else None


#: Репліка, яка явно є питанням до бази, а не відповіддю на поле.
#:
#: Часточка на початку («а скільки…», «ну хто…») входить у шаблон навмисно:
#: перша версія починалась із самого слова-питання й не зловила «а скільки
#: зараз у відпустці?» -- тобто найприроднішу форму з усіх.
_QUESTIONISH = re.compile(
    r"^\s*(?:а|і|й|та|ну|ок|окей)?\s*"
    r"(скільк|хто\b|де\b|коли\b|яка\b|який\b|яке\b|які\b|чому\b|"
    r"покажи\b|дай\b|що\s)", re.IGNORECASE)


def step(state, text):
    """Один хід збирання. -> (стан, відповідь, шлях до файла або None)."""
    st = dict(state or start())
    raw = (text or "").strip()
    low = raw.lower()

    if low in ("скасувати", "відміна", "стоп", "вихід"):
        st["done"] = True
        _log(st, "cancelled", at_field=_current_field_name(st))
        return st, "Створення документа скасовано. Чат працює як звичайно.", None

    # КЕРІВНІ ФРАЗИ ПОЗА СВОЇМ МІСЦЕМ -- не дані.
    #
    # «формуй усе одно» і «замість №X» мають сенс лише тоді, коли система
    # сказала про перетин. Поза цим станом вони тихо ставали значенням поля:
    # у живому прогоні «формуй усе одно» записалось у номер перевізного
    # документа. Керівне слово, що стало даними, -- це найгірший вид тихої
    # помилки: людина думає, що керує, а вона диктує.
    if _OVERRIDE.match(low) and not st.get("conflict"):
        return st, ("Це відповідь на попередження про перетин, а зараз його "
                    "немає. " + (_current_ask(st) or "")), None

    if st["kind"] is None:
        kind = _kind_of(raw)
        if not kind:
            ask = ("Який документ створюємо? Скажіть «відпускний квиток» "
                   "або «посвідчення про відрядження».")
            # НОМЕР ЗВЕРНЕННЯ -- у першому повідомленні й у останньому.
            #
            # Один на весь документ: людина показує на ПОДІЮ («створювала
            # документ, і ось що вийшло»), а подія тут одна. Десять номерів під
            # десятьма питаннями були б шумом, у якому неможливо вказати на
            # потрібний -- рівно те, на що Аня скаржилась у чаті 27.08
            # («код звернення дублюється»).
            if not raw:
                _log(st, "started")
                return st, ask + "\n" + done_line(st), None
            return st, ask, None
        st["kind"] = kind
        st["idx"] = 0
        _log(st, "kind_chosen")
        return st, plan(kind)[0]["ask"], None

    fields = plan(st["kind"])

    # ПЕРЕТИН: або підстава, або наполягання, або вихід.
    if st.get("conflict"):
        m = re.match(r"замість\s*№?\s*([\w/\-]+)", low)
        if m:
            st["answers"]["supersedes"] = m.group(1)
            st["conflict"] = False
            return st, (f"Записала: видається замість документа №{m.group(1)}. "
                        + (_current_ask(st) or "")), None
        if _OVERRIDE.match(low):
            st["conflict"] = False
            st["answers"]["conflict_override"] = True
            return st, ("Гаразд, формую попри перетин. "
                        + (_current_ask(st) or "")), None
        st["done"] = True
        return st, ("Не формую. Якщо це заміна — почніть знову й скажіть "
                    "«замість №…» на цьому місці."), None

    if st.get("confirm"):
        if low in ("так", "формуй", "давай") or _OVERRIDE.match(low):
            st["done"] = True
            path = build(st["kind"], st["answers"])
            _log(st, "built", superseded=bool(st["answers"].get("supersedes")),
                 override=bool(st["answers"].get("conflict_override")))
            return st, "", path
        st["done"] = True
        return st, "Не формую. Створення скасовано.", None

    field = fields[st["idx"]]

    if low == "пропустити":
        if field["required"]:
            return st, (f"Це поле обов'язкове для цього бланка. {field['ask']}"), None
        st["answers"][field["name"]] = None
        return _advance(st, fields, "Пропущено.")

    # ГЕЙТ ПИТАНЬ -- НА ВСІ ПОЛЯ, включно з ПІБ.
    #
    # Спершу я виключила поле особи («там же ім'я, а не питання») -- і на
    # «а скільки зараз у відпустці?» режим пішов шукати таку особу в штатці й
    # видав довгу відмову про реєстр. Справжнє ПІБ не починається з «скільки»,
    # «хто» чи «покажи», тому виключати нічого не треба.
    if _QUESTIONISH.match(raw):
        return st, ("Зараз збирається документ, тому питання до бази я не "
                    f"беру. {field['ask']} Або скажіть «скасувати»."), None

    value, err, note = validate_answer(field, raw, st["answers"])
    if err:
        # У слід -- ЛИШЕ назва поля й те, що не прийнялось. Ні тексту людини,
        # ні тексту помилки: помилка може містити перелік знайдених ПІБ.
        _log(st, "rejected", field=field["name"])
        st["tries"] = st.get("tries", 0) + 1
        if st["tries"] >= MAX_TRIES:
            st["tries"] = 0
            tail = (" Можна сказати «пропустити»." if not field["required"]
                    else "")
            return st, (f"{err} Не виходить розібрати відповідь.{tail} "
                        "Або скажіть «скасувати»."), None
        return st, f"{err} {field['ask']}", None

    st["tries"] = 0
    st["answers"][field["name"]] = value
    _log(st, "accepted", field=field["name"])
    return _advance(st, fields, note)


def _advance(st, fields, note):
    """Наступне питання, перевірка перетину перед номером, або зведення."""
    st["idx"] += 1
    head = f"✓ {note}\n" if note else ""

    # ПЕРЕТИН ПЕРІОДІВ -- ЩОЙНО ВІДОМІ ОСОБА Й ОБИДВІ ДАТИ.
    #
    # Спершу я перевіряла «перед питанням про номер». Критерій це формально
    # виконував, а по суті ні: у квитку між датами й номером стоять ще ВПД і
    # супутники, тобто людина відповідала на два зайві питання, перш ніж
    # дізнатись, що документ узагалі не потрібен. Умова мусить залежати від
    # ДАНИХ, а не від позиції в переліку.
    a = st["answers"]
    if (not st.get("conflict_checked")
            and a.get("person") and a.get("start") and a.get("end")):
        st["conflict_checked"] = True
        clash = _conflict_note(st)
        if clash:
            st["conflict"] = True
            _log(st, "conflict_found")
            return st, head + clash, None
    nxt = fields[st["idx"]] if st["idx"] < len(fields) else None

    if nxt is None:
        st["confirm"] = True
        return st, head + _summary(st) , None

    if nxt["name"] == "number":
        taken = taken_numbers()
        if taken:
            free = next_free_number(taken)
            return st, (head + nxt["ask"]
                        + f" Найближчий вільний: {free}."), None
        return st, (head + nxt["ask"]
                    + " Перевірити зайнятість не вдалось: база недоступна."), None
    return st, head + nxt["ask"], None


def _conflict_note(st):
    """Текст про перетин, або None."""
    sid, start_, end_ = (st["answers"].get("person"),
                         st["answers"].get("start"), st["answers"].get("end"))
    if not (sid and start_ and end_):
        return None
    clash = find_conflicts(sid, st["kind"], start_, end_)
    if not clash:
        return None
    first = clash[0]
    num = first.get("number") or f"запис №{first.get('source_doc_id')}"
    return (f"У цієї особи вже є документ №{num} на період "
            f"{first.get('valid_from')} — {first.get('valid_to')}, і він "
            f"перетинається з {start_} — {end_}.\n"
            "Якщо новий документ видається ЗАМІСТЬ нього — скажіть «замість "
            f"№{num}». Якщо це інша підстава — скажіть «формуй усе одно». "
            "Або «скасувати».")


def _summary(st):
    """Зведення перед формуванням: людина мусить побачити все разом."""
    lines = ["Зібрано:"]
    for f in plan(st["kind"]):
        v = st["answers"].get(f["name"])
        if f["name"] == "person" and v:
            # У зведенні стояло «Прізвище, ім'я, по батькові: UNIT-0026».
            # Службовий код у полі, яке людина щойно назвала словами, -- це та
            # сама внутрішня кухня на екрані, на яку скаржився Денис (п. 25).
            rows = [r for r in _roster() if r["service_id"] == v]
            if rows:
                v = f"{rows[0]['rank']} {rows[0]['full_name']}"
        # Питання скорочуємо до суті: у зведенні «(наприклад: …)» -- шум.
        label = re.split(r"\s+[—(]|\.\s", f["ask"])[0].rstrip(".")
        lines.append(f"- {label}: " + ("—" if v in (None, "") else str(v)))
    lines.append("Сформувати документ? так / скасувати")
    return "\n".join(lines)
