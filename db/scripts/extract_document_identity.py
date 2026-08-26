"""Витягує реквізити нормативного документа з голови його ж тексту.

Запуск:
    python db/scripts/extract_document_identity.py            # лише показати
    python db/scripts/extract_document_identity.py --apply
    python db/scripts/extract_document_identity.py --doc 202  # розібрати один

## Навіщо

Жоден із 41 нормативного документа в базі не має назви: вони прийшли з
пайплайну, який залишив UUID замість імені файла (`pipeline_meta.title`
порожній у всіх, реквізитних фактів нуль). Без цього поля не працює нічого:
не процитувати джерело («назва, № від дата»), не поставити тест на «знайшли
правильний документ», не відповісти на запит за номером.

## Чому це НЕ пошук

Запит `НД ТЗІ 2.5-004-99` через FTS безнадійний: Postgres токенізує номер як
`'нд' 'тзі' '2.5' '-004' '-99'`, і AND-запит по ньому дає 85 фрагментів --
нуль розрізнювальної здатності. Гірше: текст цього номера є у 6 документах,
бо стандарти цитують один одного, а *є* цим номером лише один. Ідентифікатор
-- це ключ, а не запит, і його місце в атрибуті документа.

## Правила видобування, і чому саме такі

**1. Спершу викидаємо `{...}`.** Це службові примітки zakon.rada, і вони
ЗАВЖДИ про інші документи: `{Із змінами, внесеними згідно із Законами
№ 2171-III...}`, `{Наказ втратив чинність на підставі Наказу ... № 260}`.
Блок стоїть одразу після назви, тому наївне «перший номер у голові» брало б
номер чужого документа -- і не в одному крайньому випадку, а приблизно у 19 з
41 документа. Одне це правило прибирає весь клас помилок.

**2. Власний номер НД ТЗІ -- той, за яким БЕЗПОСЕРЕДНЬО йде видавець.** Через
глобальний порядок («останній у титулі») це не формулюється: титул у цих PDF
повторюється двічі, тому розгорнуте «Департамент спеціальних
телекомунікаційних...» трапляється рано, і 5 документів із 7 відпадали.

**2а. Але й сусідства мало -- потрібна граматика.** Документ 202 називається
«Методичні вказівки ... з вимогами НД ТЗІ 2.5-004-99», тобто цитований
стандарт стоїть безпосередньо перед видавцем, бо входить у власну назву.
Відкидає його лише те, що перед ним «з вимогами»: свій номер так не вводять.
Без цього 202 отримував номер документа 234 і ставав його близнюком.

**3. Розрідження літер і розриви рядків склеюємо.** В OCR це реально:
`М І Н І С Т Е Р С Т В О   О Б О Р О Н И` (документ 213), `З А К О Н`
(224), номер як `НД \nТЗІ \n2.5-004-99` (239).

**4. Де правило не вирішує -- не вгадуємо.** Документ іде в `review_queue` з
причиною. Це той самий принцип «витягуємо задеклароване, не домислюємо», що
вже прийнятий для чинності.

## Номер закону: виправлення попереднього висновку

Спершу я записав тут, що закон свого номера в тексті не несе і що він живе
лише в URL на zakon.rada. **Це неправильно.** Номер стоїть у ПІДПИСНОМУ блоці
(«м. Київ 24 березня 1999 року № 550-XIV»), а не в титулі -- тобто на позиції
до 640 тисяч символів від початку. Я його не бачив лише тому, що дивився
виключно на голову документа.

Знайшовся він так: на документі 224 модель відповіла `550-XIV`, і моя розмітка
зарахувала це як хибу. Помилялась розмітка.

Підписний блок знаходиться у 22 з 41 документа, і всі 22 номери збігаються зі
звіреним вручну списком корпусу. Тому саме він -- основний ідентифікатор
закону: це те, що людина набирає в запиті. Посилання на ВВР лишається
запасним, коли підпису немає.
"""
import argparse
import os
import re
import sys

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HEAD = 2600           # вікно титульного блоку
WIDE = 60000          # з якого вікна викидаємо примітки (див. head_of)

# Видавці НД ТЗІ. Власний номер стоїть безпосередньо перед одним із них.
TZI_PUBLISHER = re.compile(
    r"(ДСТСЗІ|Департамент\s+спеціальних\s+телекомунікаційних"
    r"|Адміністрація\s+Держав\w*\s+служб)", re.I)
TZI_ID = re.compile(r"НД\s*ТЗІ\s*(\d\.\d\s*-\s*\d{3}\s*-\s*\d{2,4})", re.I)
TZI_MARK = re.compile(r"НОРМАТИВНИЙ\s+ДОКУМЕНТ", re.I)

# Граматична ознака ПОСИЛАННЯ, а не самоназви. Це те, що врешті розрізняє
# документ 202: його титул -- «Методичні вказівки ... з вимогами НД ТЗІ
# 2.5-004-99 | Адміністрація ...», тобто цитований стандарт стоїть
# безпосередньо перед видавцем і входить у власну назву. Ні «перший номер»,
# ні «сусідній з видавцем» його не відкидають -- відкидає лише те, що перед
# ним стоїть «з вимогами». Свій номер так не вводять.
CITATION_CUE = re.compile(
    r"(?:з\s+вимог\w*|вимог\w*|згідно\s+з|відповідно\s+до|див\.|порівн\w*"
    r"|з\s+урахуванням|на\s+підставі|визначен\w*)\s*$", re.I)

# Підписний блок. Шукається по ВСЬОМУ тексту, не по голові: у Кримінальному
# кодексі він на позиції 639479. Формати номера три -- римський (`550-XIV`),
# указний (`1153/2008`) і старий законодавчий (`80/94-ВР`).
SIGN_CITY = re.compile(
    r"м\.\s*Ки[її]в\s*(\d{1,2})\s+([а-яіїєґ]+)\s+(\d{4})\s*року\s*"
    r"№\s*(\d{2,5}\s*[-–]\s*[IVXLC]{1,6}|\d+/\d+\s*[-–]\s*ВР)", re.I)
SIGN_HEAD = re.compile(
    r"(?:Президент\s+України|Голова\s+Верховної\s+Ради)[^№]{0,160}?"
    r"№\s*(\d{2,5}\s*[-–]\s*[IVXLC]{1,6}|\d+/\d{4})", re.I | re.S)

MONTHS_GEN = {"січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5,
              "червня": 6, "липня": 7, "серпня": 8, "вересня": 9,
              "жовтня": 10, "листопада": 11, "грудня": 12}


def signature_number(text):
    """Номер із підписного блоку -> (номер, дата ISO або None)."""
    clean = strip_braces(text)
    m = SIGN_CITY.search(clean)
    if m:
        day, month, year = int(m.group(1)), MONTHS_GEN.get(m.group(2).lower()), m.group(3)
        iso = f"{year}-{month:02d}-{day:02d}" if month else None
        return re.sub(r"\s*", "", m.group(4)), iso
    m = SIGN_HEAD.search(clean)
    if m:
        return re.sub(r"\s*", "", m.group(1)), None
    return None, None


ORDER_HEAD = re.compile(
    r"НАКАЗ\s*(?:від\s*)?(\d{2}\.\d{2}\.\d{4})?\s*(?:м\.\s*КИ[ЇI]В)?\s*"
    r"(\d{2}\.\d{2}\.\d{4})?\s*№\s*(\d+)", re.I)
# `[^)]*?` тут був помилкою: у «Верховної Ради України (ВВР), 1999» є дужка
# всередині, і клас її не перетинав -- тому НІ ОДИН закон не розпізнавався.
VVR = re.compile(r"Відомост\w*\s+Верховн\w*\s+Рад\w*.{0,40}?"
                 r"(\d{4})\s*,\s*№\s*([\d\-]+)\s*,\s*ст\.?\s*(\d+)", re.I | re.S)
# `[^\n]` тут був помилкою: head_of склеює лише пробіли й табуляції, а переноси
# рядків лишаються -- і в документі 206 «ЗАТВЕРДЖЕНО Наказ Головнокомандувача
# Збройних Сил України» розбите на рядки, тому клас через них не проходив. Це
# був єдиний документ, якого регулярка не взяла, а модель узяла.
APPROVED_BY = re.compile(
    r"ЗАТВЕРДЖЕНО\s+Наказ\w*\s+(.{0,80}?)\s*(\d{1,2}\s+[а-яіїєґ]+\s+\d{4})\s*№\s*(\d+)",
    re.I | re.S)
DECREE = re.compile(r"Указ\s+Президента", re.I)

# Латиниця, що видає себе за кириличну, -- для нормалізованого ключа.
CONFUSABLE = str.maketrans({
    "I": "І", "i": "і", "A": "А", "a": "а", "B": "В", "C": "С", "c": "с",
    "E": "Е", "e": "е", "H": "Н", "K": "К", "M": "М", "O": "О", "o": "о",
    "P": "Р", "p": "р", "T": "Т", "X": "Х", "x": "х", "y": "у", "Y": "У",
})


def unspace_letters(text):
    """`М І Н І С Т Е Р С Т В О` -> `МІНІСТЕРСТВО`.

    Береться лише за пробіги з 4+ одиночних літер, щоб не склеїти
    звичайні однолітерні слова («і», «з», «у») у сусідніх реченнях.
    """
    def fix(m):
        return m.group(0).replace(" ", "")
    return re.sub(r"(?:(?<=\s)|^)(?:[^\W\d_]\s){3,}[^\W\d_](?=\s|$)", fix, text)


def strip_braces(text):
    """Викидає `{...}` -- примітки zakon.rada про ІНШІ документи.

    Заміна на пробіл, а не на порожнє: інакше склеяться слова по обидва боки
    і зникне межа між назвою та наступним блоком.
    """
    return re.sub(r"\{[^{}]*\}", " ", text)


def normalize_key(s):
    """Ключ для порівняння ідентифікаторів: конфузабли, регістр, розділювачі."""
    s = s.translate(CONFUSABLE).lower()
    s = re.sub(r"[\s ]+", "", s)
    s = re.sub(r"[‐-―−]", "-", s)      # усі тире -> дефіс
    return s


def norm_soft(s):
    """Нормалізація, що ЗБЕРІГАЄ пробіли -- для РОЗБОРУ рядка.

    `normalize_key` зчищає всі пробіли (правильно для ключа), але для розбору
    це фатально: «№ 1934-XII від 06.12.1991» стає «№1934-хіівід06.12.1991», і
    після римських цифр іде «в», яке саме входить у клас кириличних римських
    літер -- межа слова не спрацьовує.
    """
    s = (s or "").translate(CONFUSABLE).lower()
    s = re.sub(r"[‐-―−]", "-", s)
    return re.sub(r"\s+", " ", s).strip()


# Кириличні двійники латинських римських цифр. Потрібні, бо після складання
# конфузаблів номер закону виходить у змішаному письмі.
ROMAN_FOLD = str.maketrans({"і": "i", "и": "i", "х": "x", "с": "c", "м": "m",
                            "д": "d", "л": "l", "в": "v"})


def canonical(s):
    """Ідентифікатор -> (тип, розрізнювальні частини). None, якщо немає.

    Порівнювати ідентифікатори як РЯДКИ -- помилка. `НД ТЗІ 2.5-004-99` і
    `2.5-004-99` це той самий документ; `наказ № 402 від 14.08.2008` і
    «наказ 402» теж. Структурне порівняння знімає весь клас цих розбіжностей
    разом із питанням, знімати префікс чи ні.

    Перевірено на 41 документі (100% на незалежно звіреній підмножині) у
    `compare_identity_methods.py`, звідки й перенесено сюди -- щоб не тримати
    дві копії, які розійдуться.
    """
    if not s or str(s).strip().upper() == "NONE":
        return None
    t = norm_soft(str(s))
    m = re.search(r"(?:нд\s*тз[іi]\s*)?(\d\.\d\s*-\s*\d{3}\s*-\s*\d{2,4})", t)
    if m:
        return ("tzi", re.sub(r"\s+", "", m.group(1)))
    # ВВР перевіряємо ПЕРЕД наказом: у ньому теж є «№», але є й «ст.»
    m = re.search(r"(\d{4})\s*,?\s*№\s*([\d-]+)\s*,?\s*ст\.?\s*(\d+)", t)
    if m:
        return ("vvr", m.group(1), m.group(2), m.group(3))
    m = re.search(r"(\d+)\s*/\s*(\d+)\s*-\s*вр", t)
    if m:
        return ("law_vr", m.group(1), m.group(2))
    # Римські цифри доводиться зводити ОКРЕМО: CONFUSABLE складає X->Х та I->І,
    # але не V, тому «550-XIV» перетворюється на «550-хіv» -- кирилиця й
    # латиниця в одному слові, і жоден однорідний клас символів не збігається.
    # Тому беремо будь-які літери після номера і зводимо їх у латиницю.
    m = re.search(r"(\d{2,5})\s*-\s*([a-zа-яіїєґ]{1,7})(?![\w-])", t)
    if m:
        roman = m.group(2).translate(ROMAN_FOLD)
        if re.fullmatch(r"[ivxlcdm]+", roman):
            return ("law", m.group(1), roman)
    m = re.search(r"(\d{1,5})\s*/\s*(\d{4})", t)
    if m:
        return ("decree", m.group(1), m.group(2))
    m = re.search(r"№\s*(\d+)", t) or re.search(r"наказ\w*\s*(\d+)", t)
    if m:
        # Дату в порівнянні НЕ враховуємо: «наказ 402» і «наказ № 402 від
        # 14.08.2008» це один документ, і людина пише коротко.
        return ("order", m.group(1))
    return None


def head_of(text):
    """Голова документа: без приміток, без розрідження, з одним пробілом."""
    # Примітки викидаємо з ШИРШОГО вікна, ніж потім беремо. Інакше виходить
    # тихо й погано: `{...}` потребує закриваючої дужки В МЕЖАХ вікна, а блок
    # «Із змінами, внесеними згідно із Законами ...» у деяких законів довший
    # за 2600 символів. Тоді збігу немає, блок лишається цілим, і в голову
    # потрапляють десятки номерів ЧУЖИХ законів -- у документа 212 їх було 28.
    # Тобто правило, яке саме й мало відкидати чужі номери, у найважчих
    # випадках не працювало взагалі.
    raw = text[:WIDE]
    clean = unspace_letters(strip_braces(raw))
    return re.sub(r"[ \t ]+", " ", clean)[:HEAD]


# Де назва документа закінчується. `{` тут НЕ згадується навмисно: до цього
# місця примітки вже викинуті, і шукати їхню дужку -- суперечність, через яку
# перша версія не знаходила назв узагалі.
TITLE_END = re.compile(
    r"(?:Відповідно\s+до|З\s+метою|НАКАЗУЮ|ПОСТАНОВЛЯ|Кабінет\s+Міністрів"
    r"|Цей\s+Закон|\(Відомост|Зареєстровано|\d\.\s+[А-ЯІЇЄҐ])", re.I)


def cut_title(seg, limit=300):
    """Обрізає назву по першій службовій межі, а не по довжині."""
    seg = " ".join(seg.split())
    m = TITLE_END.search(seg)
    if m and m.start() > 10:
        seg = seg[:m.start()]
    return seg.strip(" .;:,()").strip() or None


def extract(text):
    """-> dict(kind, identifier, title, issue_date, confidence, reason)."""
    head = head_of(text)

    # --- НД ТЗІ -------------------------------------------------------------
    if TZI_MARK.search(head):
        pub = TZI_PUBLISHER.search(head)
        ids = list(TZI_ID.finditer(head))
        if not ids:
            return dict(kind="tzi", identifier=None, title=None, issue_date=None,
                        confidence=0.0,
                        reason="НД ТЗІ без номера в голові")
        # Власний номер -- той, за яким БЕЗПОСЕРЕДНЬО йде видавець. Через
        # глобальний порядок («останній перед видавцем») це не формулюється:
        # титул у цих PDF повторюється двічі, тому розгорнуте «Департамент
        # спеціальних телекомунікаційних...» трапляється рано, а номер стоїть
        # біля скороченого «ДСТСЗІ» пізніше -- і 5 документів із 7 відпадали.
        pubs = [m.start() for m in TZI_PUBLISHER.finditer(head)]
        cited = {m.start() for m in ids
                 if CITATION_CUE.search(head[max(0, m.start() - 30):m.start()])}
        own, best = None, None
        for m in ids:
            if m.start() in cited:
                continue                  # це посилання на інший стандарт
            after = [p - m.end() for p in pubs if p >= m.end()]
            if after and (best is None or min(after) < best):
                best, own = min(after), m
        if own is not None and best <= 80:
            conf, why = 0.95, None
        else:
            rest = [m for m in ids if m.start() not in cited]
            if not rest:
                return dict(kind="tzi", identifier=None, title=None, issue_date=None,
                            confidence=0.0,
                            reason="усі номери в голові -- посилання на інші стандарти")
            own, conf, why = rest[0], 0.5, "видавець не стоїть поруч із номером -- узято перший"

        num = re.sub(r"\s*", "", own.group(1))
        ident = f"НД ТЗІ {num}"
        # Назва -- між преамбулою «НОРМАТИВНИЙ ДОКУМЕНТ ... ІНФОРМАЦІЇ» і номером.
        pre = TZI_MARK.search(head)
        seg = head[pre.end():own.start()]
        seg = re.sub(r"^\s*СИСТЕМИ\s+ТЕХНІЧНОГО\s+ЗАХИСТУ\s+ІНФОРМАЦІЇ", " ", seg, flags=re.I)
        title = " ".join(seg.split()).strip(" .;")
        year = re.search(r"(?:Київ|КИЇВ)\s*(\d{4})", head)
        return dict(kind="tzi", identifier=ident, title=title or None,
                    issue_date=(year.group(1) + "-01-01") if year else None,
                    confidence=conf, reason=why)

    # --- Наказ із шапкою ----------------------------------------------------
    m = ORDER_HEAD.search(head)
    if m and m.start() < 400:
        date = m.group(1) or m.group(2)
        num = m.group(3)
        iso = None
        if date:
            d, mo, y = date.split(".")
            iso = f"{y}-{mo}-{d}"
        tail = head[m.end():]
        # `[^.]*?` тут не працював: у «13 грудня 2017 р. за № 1502/31370» є
        # точка в «р.», тому блок не відрізався і в назву потрапляв хвіст
        # реєстрації.
        parts = re.split(r"за\s*№\s*\d+/\d+", tail, maxsplit=1)
        tail = parts[1] if len(parts) > 1 else tail
        return dict(kind="order", identifier=f"наказ № {num}" + (f" від {date}" if date else ""),
                    title=cut_title(tail), issue_date=iso, confidence=0.9, reason=None)

    # --- Закон / постанова із посиланням на ВВР -----------------------------
    v = VVR.search(head)
    if v:
        sig, sig_date = signature_number(text)
        title = " ".join(head[:v.start()].split()).strip(" .;(")
        title = re.sub(r"^(?:ЗАКОН|КОДЕКС)\s*У\s*К\s*Р\s*А\s*[ЇI]\s*Н\s*И\s*",
                       "", title, flags=re.I)
        title = cut_title(title) or ""
        # Підписний номер кращий за посилання на ВВР: саме його набирає людина.
        ident = f"№ {sig}" if sig else f"ВВР {v.group(1)} № {v.group(2)} ст.{v.group(3)}"
        if not title:
            # Кримінальний кодекс: назва втрачена при вивантаженні, але номер
            # у підписі є -- документ усе одно ідентифікований.
            return dict(kind="law", identifier=ident, title=None,
                        issue_date=sig_date,
                        confidence=0.75 if sig else 0.3,
                        reason="назви в тексті немає -- титул втрачено при вивантаженні")
        return dict(kind="law", identifier=ident, title=title,
                    issue_date=sig_date, confidence=0.9 if sig else 0.85, reason=None)

    # --- Указ Президента ----------------------------------------------------
    if DECREE.search(head[:200]):
        sig, sig_date = signature_number(text)
        tm = re.search(r"(Про\s+.{10,400})", head, re.S)
        return dict(kind="decree", identifier=f"№ {sig}" if sig else None,
                    title=cut_title(tm.group(1)) if tm else None,
                    issue_date=sig_date, confidence=0.8 if sig else 0.4,
                    reason=None if sig else "указ без номера в тексті")

    # --- «ЗАТВЕРДЖЕНО наказом ... № N» --------------------------------------
    a = APPROVED_BY.search(head)
    if a:
        tm = re.search(r"((?:ІНСТРУКЦІЯ|ПОЛОЖЕННЯ|ПОРЯДОК|ПРАВИЛА)\s+.{10,250})",
                       head[a.end():], re.S | re.I)
        # Дата словами -> ISO, щоб порівнювалась із рештою.
        dm = re.match(r"(\d{1,2})\s+([а-яіїєґ]+)\s+(\d{4})", a.group(2), re.I)
        iso = None
        if dm and MONTHS_GEN.get(dm.group(2).lower()):
            iso = f"{dm.group(3)}-{MONTHS_GEN[dm.group(2).lower()]:02d}-{int(dm.group(1)):02d}"
        return dict(kind="approved_doc",
                    identifier=f"наказ № {a.group(3)}" + (f" від {iso}" if iso else ""),
                    title=cut_title(tm.group(1)) if tm else None,
                    issue_date=iso, confidence=0.8, reason=None)

    # --- Лише назва ---------------------------------------------------------
    tm = re.search(r"^\s*((?:Про|Деякі|Щодо)\s+.{10,400})", head, re.S)
    if tm:
        sig, sig_date = signature_number(text)
        return dict(kind="untitled_order", identifier=f"№ {sig}" if sig else None,
                    title=cut_title(tm.group(1)), issue_date=sig_date,
                    confidence=0.75 if sig else 0.45,
                    reason=None if sig else "є назва, але немає номера й дати в тексті")

    return dict(kind=None, identifier=None, title=None, issue_date=None,
                confidence=0.0, reason="голову не розпізнано жодним правилом")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--doc", type=int, help="розібрати один документ докладно")
    ap.add_argument("--min-confidence", type=float, default=0.6,
                    help="нижче цього -- у review_queue, а не в поле")
    args = ap.parse_args(argv)

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        if args.doc:
            cur.execute("SELECT id, text_content FROM documents WHERE id = %s", (args.doc,))
        else:
            cur.execute("""SELECT id, text_content FROM documents
                            WHERE domain='normative' AND text_content IS NOT NULL
                            ORDER BY id""")
        rows = cur.fetchall()

    if args.doc and rows:
        print("── ГОЛОВА ПІСЛЯ ОЧИЩЕННЯ " + "─" * 50)
        print(head_of(rows[0][1])[:1200])
        print("\n── ВИТЯГНУТО " + "─" * 62)
        for k, v in extract(rows[0][1]).items():
            print(f"  {k:12} {v}")
        return 0

    good, weak = [], []
    for doc_id, text in rows:
        r = extract(text)
        r["id"] = doc_id
        (good if r["confidence"] >= args.min_confidence else weak).append(r)

    print(f"Розібрано: {len(rows)}   впевнено: {len(good)}   на перевірку: {len(weak)}\n")
    print("── ВПЕВНЕНО " + "─" * 64)
    for r in sorted(good, key=lambda x: (x["kind"] or "", x["id"])):
        print(f"  {r['id']:>4} [{r['kind']:<14}] {str(r['identifier'] or '—'):<34} "
              f"{(r['title'] or '')[:60]}")
    print("\n── НА ПЕРЕВІРКУ ЛЮДИНОЮ " + "─" * 52)
    for r in sorted(weak, key=lambda x: x["id"]):
        print(f"  {r['id']:>4} [{str(r['kind']):<14}] conf={r['confidence']:.2f}  {r['reason']}")
        print(f"        назва: {(r['title'] or '—')[:88]}")

    dup = {}
    for r in good:
        if r["identifier"]:
            dup.setdefault(normalize_key(r["identifier"]), []).append(r["id"])
    coll = {k: v for k, v in dup.items() if len(v) > 1}
    if coll:
        print("\n── ОДИН ІДЕНТИФІКАТОР У КІЛЬКОХ ДОКУМЕНТІВ " + "─" * 33)
        for k, v in coll.items():
            print(f"  {k}  ->  {v}")

    print("\n" + ("ЗАСТОСОВАНО" if args.apply else
                  "DRY-RUN: нічого не змінено"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
