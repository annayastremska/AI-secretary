"""Генеричні нормалізатори -- диспетчеризація за ТИПОМ поля (date/number/
category/text), не за назвою поля чи доменом. Один набір функцій для
будь-якої схеми.
"""
import datetime
import re

UKR_MONTHS = {
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5, "червня": 6,
    "липня": 7, "серпня": 8, "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12,
}

MIN_PLAUSIBLE_YEAR = 1900
MAX_PLAUSIBLE_YEAR = 2100

PLACEHOLDER_TOKENS = frozenset({
    "redacted", "???", "",
    "не заповнено", "не вказано", "не зазначено", "не видано", "не видавались",
    "відсутнє", "відсутній", "відсутня", "немає",
    # Стандартні канцелярські скорочення: "н/д" -- немає даних, "б/н" --
    # без номера (документ/наказ без номера). Раніше не входили в перелік --
    # такі значення проходили як реальний текст замість чесної прогалини.
    "н/д", "б/н",
})

# Перелік вище -- ДЕФОЛТ, не істина про всі бланки, і два його токени
# ("немає", "відсутній") є змістовними значеннями в іншому документі: у книзі
# обліку техніки «несправності: немає» означає "техніка справна", а ми
# перетворювали це на null (known-weak-spots.md, 2.4). Прибрати їх глобально
# НЕ МОЖНА -- на порожньому бланку відпускного квитка правило працює саме
# правильно, і саме на них воно й ловить незаповнене поле.
#
# Тому перелік налаштовується зі СХЕМИ, двома ключами (обидва -- і на рівні
# схеми, і на рівні окремого поля; поле важить більше):
#   placeholder_tokens:        повна ЗАМІНА дефолту;
#   placeholder_tokens_except: токени, ВИКЛЮЧЕНІ з дефолту, тобто такі, що в
#                              цьому бланку/полі є реальним значенням.
# Схемний рівень розкладається по полях один раз, при завантаженні схеми
# (identification.load_schemas), щоб normalize_field -- який бачить лише
# field_def -- не потребував доступу до всієї схеми.
PLACEHOLDER_TOKENS_KEY = "placeholder_tokens"
PLACEHOLDER_TOKENS_EXCEPT_KEY = "placeholder_tokens_except"


def resolve_placeholder_tokens(explicit=None, excluded=None) -> frozenset:
    """Ефективний перелік токенів-заповнювачів. Обидва аргументи None ->
    рівно PLACEHOLDER_TOKENS (побайтово попередня поведінка)."""
    base = frozenset(str(t).strip().lower() for t in explicit) \
        if explicit is not None else PLACEHOLDER_TOKENS
    if excluded:
        base = base - {str(t).strip().lower() for t in excluded}
    return base


def field_placeholder_tokens(field_def) -> frozenset:
    """Перелік для конкретного поля -- те, що в нього поклав
    identification.load_schemas (або сама схема, якщо поле оголосило ключі
    напряму).

    Власний `not_issued_sentinel` поля виключається АВТОМАТИЧНО. Інакше автор
    схеми мусив би написати ту саму фразу двічі (`not_issued_sentinel:` і
    `placeholder_tokens_except:`), а два переліки того самого рано чи пізно
    розійдуться. Семантично це тавтологія: фраза, оголошена як "документ
    прямо каже, що цього немає", за визначенням НЕ є заповнювачем для цього
    поля. Для `normalize_null_if_sentinel` це нічого не змінює -- вона й так
    перевіряє сентинел ПЕРШИМ, незалежно від переліку (див. її докстрінг),
    -- зате на етапі екстракції сентинел тепер доживає до нормалізації, а не
    гаситься як `blank_value`.
    """
    field_def = field_def or {}
    excluded = list(field_def.get(PLACEHOLDER_TOKENS_EXCEPT_KEY) or [])
    sentinel = field_def.get("not_issued_sentinel")
    if sentinel:
        excluded.append(sentinel)
    return resolve_placeholder_tokens(field_def.get(PLACEHOLDER_TOKENS_KEY),
                                      excluded)

# Незаповнене поле бланка часто лишається як ряд підкреслень/рисок/лапок
# без жодного символу тексту (напр. "____________" чи "«____» ____ 20___ р.",
# де саму дату не вписано) -- перевірено на реальному порожньому шаблоні
# відпускного квитка. Такий рядок -- не текст, а графічний маркер "тут
# мало бути значення", і має оброблятися так само, як [REDACTED].
_BLANK_FILL_RE = re.compile(r'^[_\-–—.\s"\'«»“”„‟]*$')

# Гнучкі лапки (guillemets «», прямі "", фігурні "") -- той самий бланк може
# мати різні лапки для різних полів.
#
# Суфікс "р."/"року" тепер НЕОБОВ'ЯЗКОВИЙ, і додано числовий формат: раніше
# вимога суфікса означала, що "з 15 травня 2025 до 20 травня 2025 включно" і
# "15.05.2025" не розпізнавались узагалі -- систематична прогалина покриття,
# а не окремий випадок. Порядок важливий: числовий формат перший, бо
# "15.05.2025" інакше частково зматчився б словесним правилом.
# Гомогліфи: літери, візуально нерозрізненні з цифрами. Заміряно на
# TRIP-012 (`data/eval/synthetic-2026-05/.../TRIP-012.json`, вада "ocr_noise"): документ
# містить "25О", "О7.О5.2О2б", "ІО", "ІЗ", а еталон очікує "250",
# 2026-05-07, 10, 13. Важливо: перевірено, що ці літери сидять уже в
# ТЕКСТОВОМУ ШАРІ .docx, де розпізнавання не відбувається взагалі -- тобто
# в синтетичному наборі дефект вписаний генератором. Незалежно від цього,
# плутанина О/0, З/3, б/6, І/1 -- відомий клас справжніх OCR-помилок для
# кирилиці, тому правило корисне й поза синтетикою. Латиниця (O, l, S)
# додана як той самий візуальний клас.
#
# ВІДКРИТЕ ПИТАННЯ (docs/open-questions.md): чи мають справжні фото з
# частини цей дефект, чи це властивість лише синтетичного набору.
_NUMERIC_HOMOGLYPHS = str.maketrans({
    "О": "0", "о": "0", "O": "0", "o": "0",
    "З": "3", "з": "3",
    "б": "6",
    "І": "1", "і": "1", "l": "1", "I": "1",
    "S": "5",
})

# Клас "цифра АБО її гомогліф" -- лише для позицій, де формат уже гарантує
# число (день/місяць/рік між крапками, день у лапках). Поза такими
# позиціями заміна НЕБЕЗПЕЧНА: "з" -- найчастіший прийменник у датах
# ("з 10 травня"), і глобальна заміна перетворила б його на "3".
_D = r'[0-9OoОоЗзбІіIlS]'

# Ліва межа: не після літери. Без неї гомогліф ВСЕРЕДИНІ слова міг би
# почати збіг ("по 22 травня" -> день="о" з "по").
_NOT_AFTER_LETTER = r'(?<![^\W\d_])'

_DATE_PATTERNS = (
    re.compile(
        _NOT_AFTER_LETTER +
        rf'(?P<day>{_D}{{1,2}})[.\-/](?P<month>{_D}{{1,2}})[.\-/]'
        rf'(?P<year>{_D}{{4}})(?!\d)'
    ),
    re.compile(
        _NOT_AFTER_LETTER +
        rf'[«"“„]?(?P<day>{_D}{{1,2}})[»"”‟]?\s+(?P<month>[а-яіїє]+|{_D}{{1,2}})\s+'
        rf'(?P<year>{_D}{{4}})\s*(?:р\.?|рок\w*)?'
    ),
)


def fix_numeric_homoglyphs(text):
    """Замінює літери-гомогліфи на цифри ЛИШЕ в токенах, які вже містять
    хоч одну справжню цифру.

    Умова "хоч одна справжня цифра" -- це запобіжник, а не деталь: без неї
    правило з'їдало б звичайні слова ("з" -> "3", "об" -> "06"). З ним
    "2О2б" (є цифра 2) виправляється, а "з" і "після" лишаються цілими.
    Токени, що складаються ЛИШЕ з гомогліфів ("ІО" = 10), цим шляхом
    свідомо НЕ чіпаються -- для них єдиний безпечний контекст -- позиція
    всередині шаблону дати, де про це дбає _DATE_PATTERNS.
    """
    if not text:
        return text
    out = []
    for token in re.split(r'(\s+)', str(text)):
        if any(ch.isdigit() for ch in token):
            out.append(token.translate(_NUMERIC_HOMOGLYPHS))
        else:
            out.append(token)
    return "".join(out)


def homoglyph_tolerant_pattern(pattern: str):
    """Розширює `\\d` у схемному регексі до "цифра АБО її гомогліф".

    Навіщо окрема функція, а не правка схем: `\\d` у патерні -- це вже
    декларація автора схеми "тут стоїть число". Розширювати саме її
    безпечно й самодокументовано, і схеми лишаються читабельними для
    людини. Правити ж кожен патерн вручну означало б дублювати список
    гомогліфів у десятку місць.

    Повертає `(розширений_патерн, was_expanded)`. Прапорець потрібен
    викликачу: значення, захоплене РОЗШИРЕНИМ патерном, може містити
    літери-гомогліфи, і його треба пропустити через
    `fix_numeric_homoglyphs` перед використанням. Якщо патерн не містив
    `\\d`, автор не обіцяв число -- і чіпати захоплене значення не можна.

    `\\d` усередині символьного класу (`[\\d\\w]`) НЕ розширюється: це
    зламало б клас вкладеними дужками. Перевірено 13.08.2026, що в наших
    схемах таких випадків немає, але правило лишається на майбутнє.
    """
    out = []
    i, depth, expanded = 0, 0, False
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            pair = pattern[i:i + 2]
            if pair == r"\d" and depth == 0:
                out.append(_D)
                expanded = True
            else:
                out.append(pair)
            i += 2
            continue
        if ch == "[":
            depth += 1
        elif ch == "]" and depth:
            depth -= 1
        out.append(ch)
        i += 1
    return "".join(out), expanded


def _fix_date_part(value, allow_month_name=False):
    """Гомогліфи -> цифри для однієї частини дати.

    `allow_month_name` рятує назви місяців: "жовтня" містить "о", і сліпа
    заміна дала б "ж0втня". Тому відомі назви місяців повертаються як є.
    """
    if value is None:
        return None
    if allow_month_name and str(value).lower() in UKR_MONTHS:
        return value
    return str(value).translate(_NUMERIC_HOMOGLYPHS)


def fix_declared_numeric(value):
    """Гомогліфи -> цифри для значення, захопленого слотом, який схема
    ОГОЛОСИЛА числовим (`\\d`, розширений `homoglyph_tolerant_pattern`).

    Відрізняється від `fix_numeric_homoglyphs` тим, що НЕ вимагає цифри в
    токені: тут гарантію дає сам патерн, тому "ІО" -> "10" (випадок, який
    токенне правило свідомо пропускає). Назви місяців усе одно захищені --
    слот `(?P<month>\\d{1,2}|[а-яіїє]+)` дозволяє і слово, і число, тому
    "жовтня" не має перетворитись на "ж0втня".

    Застосовувати ЛИШЕ до значень із розширених слотів. На довільному
    тексті це правило небезпечне.
    """
    return _fix_date_part(value, allow_month_name=True)


def is_placeholder(raw_text, tokens=None) -> bool:
    """Surya позначає порожнє/нерозбірливе місце в джерелі як [REDACTED] --
    підтверджено повторюваним у попередніх тестах, не галюцинація. Також
    ловить типові "тут мало бути значення" маркери бланка: голі підкреслення/
    риски й українські фрази "не заповнено"/"не вказано" тощо.

    tokens -- перелік токенів-заповнювачів для ЦЬОГО поля
    (field_placeholder_tokens); None -> дефолтний PLACEHOLDER_TOKENS.
    Графічний маркер (_BLANK_FILL_RE: голі підкреслення/риски/лапки) НЕ
    налаштовується й перевіряється завжди: це не слово документа, а порожнє
    місце на бланку, і жодне поле не може мати його реальним значенням."""
    if raw_text is None:
        return False
    cleaned = raw_text.strip().strip("[]")
    if cleaned.lower() in (PLACEHOLDER_TOKENS if tokens is None else tokens):
        return True
    return bool(cleaned) and bool(_BLANK_FILL_RE.match(cleaned))


def month_to_num(month):
    # .strip() обов'язковий: grammar LLM-виводу не обмежує форму рядка, тож
    # " травня " з одним зайвим пробілом знищувало дату цілком.
    text = str(month).strip()
    return int(text) if text.isdigit() else UKR_MONTHS.get(text.lower())


def _digits_to_int(value):
    """None, якщо значення не суто цифрове. Потрібно, бо grammar LLM-виводу
    не обмежує day/month/year шаблоном цифр: модель може повернути
    {"day": "невідомо"}, і прямий int() кидав ValueError, який ішов угору й
    валив ВЕСЬ пакетний прогін, а не лише це поле."""
    text = str(value).strip()
    return int(text) if text.isdigit() else None


def normalize_date(day, month, year):
    """ISO-дата або None. Ніколи не кидає виняток на будь-якому вході."""
    if day is None or month is None or year is None:
        return None
    day_num = _digits_to_int(day)
    month_num = month_to_num(month)   # None для невідомої назви місяця
    year_raw = _digits_to_int(year)
    if day_num is None or month_num is None or year_raw is None:
        return None
    year_num = year_raw if len(str(year).strip()) == 4 else 2000 + year_raw
    # Діапазон року: LLM під grammar може віддати "202" -> 2202, і datetime
    # прийме таку дату як цілком валідну. Документообіг частини не містить
    # дат поза цими межами, тож усе інше -- помилка розпізнавання, не дата.
    if not (MIN_PLAUSIBLE_YEAR <= year_num <= MAX_PLAUSIBLE_YEAR):
        return None
    try:
        # datetime валідує діапазони й неможливі дати (31 лютого) замість
        # того, щоб зібрати формально коректний, але неіснуючий рядок.
        # OverflowError -- окремо від ValueError: LLM під grammar не обмежує
        # ДОВЖИНУ цифрового рядка day/month (лише те, що це цифри), тому
        # аномально довгий рядок ("99999999999999999999") проходить
        # _digits_to_int (Python int довільної точності) і падає лише тут,
        # коли datetime намагається звести його до C long. Без цього виняток
        # ішов угору й губив ВЕСЬ документ замість чесного None для одного
        # поля -- відтворено напряму.
        return datetime.date(year_num, month_num, day_num).isoformat()
    except (ValueError, OverflowError):
        return None


def parse_date_from_text(raw_text, tokens=None):
    """Для полів, витягнутих не регексом-з-групами, а сирим блоком тексту
    (напр. block_before_label) -- шукає дату прямо в довільному рядку."""
    if not raw_text or is_placeholder(raw_text, tokens):
        return None
    low = raw_text.lower()
    for pattern in _DATE_PATTERNS:
        m = pattern.search(low)
        if m:
            # Гомогліфи виправляються ЛИШЕ тут, у вже впізнаній позиції
            # дати: сам шаблон (крапки або лапки навколо дня) гарантує, що
            # на цьому місці стоїть число, а не слово.
            return {
                "day": _fix_date_part(m.group("day")),
                "month": _fix_date_part(m.group("month"), allow_month_name=True),
                "year": _fix_date_part(m.group("year")),
            }
    return None


def to_int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# Числа прописом. Потрібні не для повноти, а тому що на реальному бланку
# кількість днів надрукована ЛИШЕ словом: рядок "терміном на" / "тринадцять" /
# "(кількість днів прописом)" -- цифри в документі немає взагалі. Regex на
# цифри через це давав None у 9 з 16 відпускних квитків синтетичного набору.
# Діапазон свідомо обмежений 1..31 (найбільше комбіноване значення --
# "тридцять один"): це кількість днів/діб, не довільне число.

# Верхня межа для ЦИФРОВОГО шляху number_from_words (словесний і так
# обмежений структурою UKR_NUMBER_WORDS вище). Без неї захоплений OCR-шум
# (номер документа, рік тощо) на regex-провалі для day-полів (duration_days/
# deployment_days) міг дати правдоподібне, але довільне число днів без
# жодної помилки. 366 -- з запасом (повний рік): цифрою можуть записати
# довший сумарний період, ніж є у словесному переліку.
MAX_PLAUSIBLE_DAYS = 366

UKR_NUMBER_WORDS = {
    "один": 1, "одна": 1, "одну": 1, "два": 2, "дві": 2, "три": 3, "чотири": 4,
    "п'ять": 5, "шість": 6, "сім": 7, "вісім": 8, "дев'ять": 9, "десять": 10,
    "одинадцять": 11, "дванадцять": 12, "тринадцять": 13, "чотирнадцять": 14,
    "п'ятнадцять": 15, "шістнадцять": 16, "сімнадцять": 17, "вісімнадцять": 18,
    "дев'ятнадцять": 19, "двадцять": 20, "тридцять": 30,
}
_APOSTROPHES = "’‘`´"


def number_word_value(token):
    """Значення ОДНОГО числівника прописом, або None. Апострофи зводяться до
    одного, бо в документах вони бувають різні (’ ‘ ` ´) -- та сама
    нормалізація, що й у number_from_words, винесена окремо, щоб перевірка
    "чи це число взагалі є в документі" (extract.attested_numbers) не
    дублювала перелік слів."""
    if not isinstance(token, str):
        return None
    text = token.lower().strip()
    for ch in _APOSTROPHES:
        text = text.replace(ch, "'")
    return UKR_NUMBER_WORDS.get(text)


def number_from_words(raw_value):
    """'тринадцять' -> 13; 'двадцять один' -> 21; '13' -> 13; інше -> None.

    Складені числа ("двадцять сім") -- сума двох частин, бо українською
    записуються окремими словами. Апострофи в документах бувають різні
    (’ ‘ ` ´), тому зводяться до одного.
    """
    if raw_value is None:
        return None
    direct = to_int_or_none(str(raw_value).strip())
    if direct is not None:
        return direct if 1 <= direct <= MAX_PLAUSIBLE_DAYS else None
    text = str(raw_value).lower().strip()
    for ch in _APOSTROPHES:
        text = text.replace(ch, "'")
    tokens = [t for t in re.split(r"[\s\-]+", text) if t]
    total, matched = 0, 0
    for token in tokens[:2]:      # максимум "двадцять сім"
        value = UKR_NUMBER_WORDS.get(token)
        if value is None:
            break
        total += value
        matched += 1
    return total if matched else None


def lemmatize_phrase(text):
    """'старшого сержанта' -> 'старший сержант'. None, якщо морфології немає.

    Потрібно тому, що в документах звання й категорії стоять у тому відмінку,
    якого вимагає речення бланка ("Видано старшому сержанту ..."), а довідник
    містить називний. Точний рядковий збіг через це давав "термін не
    розпізнано" на цілком звичайних формах -- перевірено: "підполковника",
    "рядового", "старшого сержанта" не знаходились жодна.

    Аліаси-стеми довідника ("відрядж") лематизація не псує: вона лише ДОДАЄ
    ключі через setdefault, ніколи не замінюючи точні.
    """
    morph = _get_morph()
    if morph is None or not isinstance(text, str) or not text.strip():
        return None
    lemmas = []
    for token in text.split():
        parses = morph.parse(token)
        lemmas.append(parses[0].normal_form if parses else token.lower())
    result = " ".join(lemmas)
    return result if result != text.strip().lower() else None


def lookup_alias(candidate, alias_lookup: dict):
    """Точний збіг, інакше -- збіг за лемою. Єдина точка, щоб обидва шляхи
    (match_dictionary і токенізація звання+ПІБ) поводились однаково."""
    if not candidate:
        return None
    key = str(candidate).strip().lower()
    hit = alias_lookup.get(key)
    if hit is not None:
        return hit
    lemma = lemmatize_phrase(key)
    return alias_lookup.get(lemma) if lemma else None


def match_dictionary(raw_text, alias_lookup: dict, tokens=None):
    """Точний рядковий збіг після нормалізації. Незнайдений термін -> рядок-
    маркер, не None і не 0 (розділ 3.4 ТЗ: нуль не можна відрізнити від
    "записів немає"). Суфікс "за NNNN рік" відсікається окремим правилом,
    з допуском коми/крапки після нього."""
    if not raw_text or is_placeholder(raw_text, tokens):
        return None
    normalized = re.sub(r"\s*за\s*\d{4}\s*рік\s*[,.;]?\s*$", "", raw_text.strip().lower())
    hit = lookup_alias(normalized, alias_lookup)
    return {"code": hit[0], "label": hit[1]} if hit else "термін не розпізнано"


def resolve_category(value, alias_lookup: dict, tokens=None):
    """Приводить категоріальне значення до єдиної форми {"code","label"},
    звідки б воно не прийшло. Три можливі входи:
    - вже {"code","label"} -- детермінований шлях через довідник;
    - КОД ("soldier") -- так віддає LLM, бо grammar обмежує вивід enum-ом
      кодів довідника;
    - сирий текст-алиас із бланка ("рядовий") -- regex/label-пошук.
    Без цього приведення те саме поле потрапляло в БД то як dict, то як
    рядок, залежно від того, який шлях спрацював (підтверджено тестом)."""
    if isinstance(value, dict) and "code" in value:
        return value
    if value is None or is_placeholder(value, tokens):
        return None
    text = str(value).strip()
    if not text:
        return None
    code_to_label = {code: label for code, label in alias_lookup.values()}
    if text in code_to_label:
        return {"code": text, "label": code_to_label[text]}
    return match_dictionary(text, alias_lookup, tokens)


def build_alias_lookup(dictionary: dict) -> dict:
    """Стійкий до неповних записів довідника: запис без `aliases`, alias-число
    чи alias-null раніше валили ВЕСЬ прогін (KeyError/AttributeError із
    load_dictionaries), хоч правка YAML -- це заявлений штатний спосіб
    розширення системи. Некоректний запис тихо пропускається на рівні alias,
    а не забирає з собою решту довідника."""
    lookup = {}
    for value in (dictionary or {}).get("values") or []:
        if not isinstance(value, dict) or not value.get("code"):
            continue
        code, label = value["code"], value.get("label", value["code"])
        for alias in value.get("aliases") or []:
            if isinstance(alias, str) and alias.strip():
                lookup[alias.strip().lower()] = (code, label)
        # Сам код теж має бути шляхом до значення: LLM під grammar-enum
        # віддає саме код, і без цього він залежав від resolve_category.
        lookup.setdefault(str(code).strip().lower(), (code, label))
    return lookup


_MORPH = None
_MORPH_LOADED = False
# Граммеми частин імені в pymorphy3: прізвище / ім'я / по батькові.
_ROLE_GRAMMEME = {"surname": "Surn", "given_name": "Name", "patronymic": "Patr"}
_NAME_GRAMMEMES = ("Surn", "Name", "Patr")


def _get_morph():
    """pymorphy3 імпортується ліниво й лише раз: аналізатор важкий, а пайплайн
    мусить працювати й без нього (тоді поле лишається як у документі, але
    статус це явно показує)."""
    global _MORPH, _MORPH_LOADED
    if not _MORPH_LOADED:
        _MORPH_LOADED = True
        try:
            import pymorphy3
            _MORPH = pymorphy3.MorphAnalyzer(lang="uk")
        except Exception:
            _MORPH = None
    return _MORPH


def _restore_case(original: str, value: str) -> str:
    """pymorphy3 завжди віддає слово в нижньому регістрі, а в бланках прізвище
    друкують ВЕЛИКИМИ -- регістр джерела несе інформацію (саме за ним
    parse_rank_and_name відрізняє прізвище від імені), тому його треба
    відновити, а не втратити при нормалізації."""
    if original.isupper():
        return value.upper()
    if original[:1].isupper():
        return value[:1].upper() + value[1:]
    return value


def _name_parses(token, role=None):
    """Розбори pymorphy3, обмежені граммемою частини імені (Surn/Name/Patr)."""
    morph = _get_morph()
    if morph is None or not isinstance(token, str) or not token.strip():
        return []
    text = token.strip()
    if " " in text:
        return []
    required = _ROLE_GRAMMEME.get(role)
    parses = morph.parse(text)
    if required:
        return [p for p in parses if required in p.tag]
    return [p for p in parses if any(g in p.tag for g in _NAME_GRAMMEMES)]


def detect_name_case(token, role=None):
    """nominative | oblique | None -- у якому відмінку стоїть частина ПІБ.

    Використовується як ПІДКАЗКА для інших частин того самого ПІБ: по батькові
    має найхарактерніші форми ("Едуардович" проти "Едуардовича"), тому саме за
    ним найнадійніше видно, чи все ПІБ у називному, чи в непрямому відмінку.
    None -- сигнал невідомості (немає розбору або він неоднозначний)."""
    candidates = _name_parses(token, role)
    if not candidates:
        return None
    has_nominative = any("nomn" in p.tag for p in candidates)
    has_oblique = any("nomn" not in p.tag for p in candidates)
    if has_nominative and not has_oblique:
        return "nominative"
    if has_oblique and not has_nominative:
        return "oblique"
    return None


def normalize_nominative_case(raw_name, role=None, case_hint=None):
    """Приводить частину ПІБ до називного відмінка через pymorphy3 (uk).
    Повертає (значення, статус).

    Статуси: normalized | already_nominative | no_morphology | not_a_name |
    inflect_failed | ambiguous_case.

    ГОЛОВНЕ ПРАВИЛО (виправлення підтвердженого тестом руйнівного бага):
    якщо серед розборів є хоч один у називному відмінку -- слово НЕ
    відмінюється. Раніше брався просто перший розбір, і для жіночих прізвищ
    на -ова/-ева перший розбір -- це родовий відмінок ЧОЛОВІЧОГО прізвища:
    "ПЕТРОВА" ставало "ПЕТРОВ", "КОВАЛЬОВА" -> "КОВАЛЬОВ". У базу йшла інша
    людина, а provenance показував `normalized`, тобто успіх. Скор pymorphy3
    тут не допомагає -- він 1.0 в усіх варіантів (перевірено).

    case_hint ("oblique"/"nominative") -- підказка від іншої частини того
    самого ПІБ. Потрібна для справді неоднозначних слів: "ПЕТРОВА" може бути
    і називним жіночим, і родовим чоловічим, і без контексту вибір
    неможливий. Якщо по батькові стоїть у непрямому відмінку
    ("Едуардовича"), то й прізвище непряме -- тоді відмінюємо.

    role звужує розбір до граммеми частини імені (Surn/Name/Patr), інакше
    прізвище могло б відмінюватись як звичайний іменник.
    """
    if not isinstance(raw_name, str) or not raw_name.strip():
        return raw_name, "skipped"
    token = raw_name.strip()
    if " " in token:
        # Очікується один токен; складене значення не наша задача -- краще
        # лишити як є, ніж провідмінювати щось несподіване.
        return raw_name, "skipped"

    morph = _get_morph()
    if morph is None:
        return raw_name, "no_morphology"

    candidates = _name_parses(token, role)
    if not candidates:
        # "Словник не знає як ІМ'Я" і "словник не знає слова взагалі" -- різні
        # речі, і раніше вони давали однаковий статус not_a_name, який блокує
        # confirmed. Заміряно на еталоні: pymorphy3 знає "Володимир" (3
        # розбори) і "Дергач" (1 розбір), але БЕЗ граммем Name/Surn -- VESUM
        # просто не розмітив частину власних імен. Тобто документи з цілком
        # правильно витягнутим ПІБ висіли в черзі через прогалину РОЗМІТКИ
        # словника, а не через сумнівне значення (TRIP-006, TRIP-010).
        #
        # Рішення Анни 13.08.2026: не блокувати підтвердження, коли словник
        # просто не знає слова. Поправка, яку додаю: відмінювати такий токен
        # усе одно НЕ можна -- без граммеми імені прізвище провідмінювалось би
        # як звичайний іменник, а це той самий клас руйнування, що
        # "ПЕТРОВА -> ПЕТРОВ". Тому розводимо три випадки:
        untagged = morph.parse(token)
        if not untagged:
            # Слова немає в словнику взагалі -- підозріло, лишаємо блокування.
            return raw_name, "not_a_name"
        if any("nomn" in p.tag for p in untagged):
            # Слово відоме, стоїть у називному -- відмінювати нічого, значення
            # вже правильне. Не блокуємо.
            return raw_name, "untagged_name"
        # Слово відоме, але в НЕПРЯМОМУ відмінку й без граммеми імені:
        # безпечно привести до називного неможливо (саме тут живе ризик
        # "інша людина в базі"), тому блокування лишається.
        return raw_name, "untagged_oblique"

    nominative = [p for p in candidates if "nomn" in p.tag]
    oblique = [p for p in candidates if "nomn" not in p.tag]

    if nominative and not oblique:
        return _restore_case(token, nominative[0].word), "already_nominative"

    if nominative and oblique:
        # Неоднозначно. За замовчуванням НЕ чіпаємо: лишити слово як у
        # документі -- це прогалина нормалізації, а зіпсувати рід -- це тихо
        # інша людина. Відмінюємо лише коли решта ПІБ прямо каже "непрямий".
        if case_hint == "nominative":
            # Решта ПІБ прямо каже "називний" -- це ДОКАЗ, а не відсутність
            # доказу. Розділено навмисно: раніше і "по батькові каже називний",
            # і "підказки немає" давали однаковий ambiguous_case, тому кожне
            # жіноче прізвище на -ова виглядало так само непевно, як
            # прізвище без жодного свідчення про відмінок.
            return _restore_case(token, nominative[0].word), "already_nominative"
        if case_hint != "oblique":
            return _restore_case(token, nominative[0].word), "ambiguous_case"

    inflected = (oblique or candidates)[0].inflect({"nomn"})
    if inflected is None:
        return raw_name, "inflect_failed"
    return _restore_case(token, inflected.word), "normalized"


def normalize_null_if_sentinel(raw_text, sentinel: str, tokens=None):
    """Повертає (значення, чи_підтверджено_порожнє). Розрізняє "документ
    прямо каже, що цього немає" від "не вдалося прочитати" -- обидва інакше
    дали б однаковий None і згубили цю різницю.

    Семантика раніше була ІНВЕРТОВАНА в обидва боки (підтверджено тестом):
    реальне значення повертало True (тобто "підтверджено порожнє"), а сам
    сентинел до цієї функції взагалі не доходив, бо його першим перехоплював
    is_placeholder -- ті самі фрази є в PLACEHOLDER_TOKENS. Тому сентинел
    тепер перевіряється ПЕРШИМ, до перевірки на placeholder.

    Ця залежність від переліку лишається й після того, як перелік став
    налаштовуваним (`placeholder_tokens_except` у схемі): схема МОЖЕ вийняти
    "не видавались" з переліку, і тоді порядок перестане мати значення, але
    покладатися на це не можна -- порядок тут і є гарантією, незалежною від
    налаштування."""
    if raw_text is None:
        return None, False
    text = str(raw_text).strip()
    if sentinel and text.lower() == sentinel.strip().lower():
        return None, True          # документ прямо каже "не видавались"
    if not text or is_placeholder(text, tokens):
        return None, False         # не вдалося прочитати
    return text, False             # реальне значення


def normalize_field(field_def: dict, raw_value, dictionaries: dict):
    """Єдина точка диспетчеризації за field_def["type"]/["normalization"].
    dictionaries: {category_name: alias_lookup} для category-полів.
    Повертає (значення, чи_підтверджено_порожнє).
    """
    field_type = field_def.get("type")
    normalization = field_def.get("normalization")
    tokens = field_placeholder_tokens(field_def)

    if raw_value is None:
        return None, False

    if field_type == "category":
        lookup = dictionaries.get(field_def["category"], {})
        return resolve_category(raw_value, lookup, tokens), False

    if field_type == "number":
        # number_from_words, а не to_int_or_none: приймає і цифру, і пропис.
        # Порядок саме такий -- цифра перевіряється першою всередині.
        return number_from_words(raw_value), False

    if field_type == "date":
        if isinstance(raw_value, dict):
            return normalize_date(raw_value.get("day"), raw_value.get("month"), raw_value.get("year")), False
        if isinstance(raw_value, str):
            parsed = parse_date_from_text(raw_value, tokens)
            return (normalize_date(**parsed) if parsed else None), False
        return None, False

    if normalization == "nominative_case":
        if is_placeholder(raw_value, tokens):
            return None, False
        # Статус морфології тут відкидається: поля ПІБ ідуть через окрему
        # гілку в build_record, яка його зберігає в provenance. Ця гілка --
        # для схем, що оголосили nominative_case поза rank_and_name.
        value, _status = normalize_nominative_case(raw_value)
        return value, False

    if normalization == "null_if_not_issued":
        return normalize_null_if_sentinel(raw_value, field_def.get("not_issued_sentinel", ""), tokens)

    # text / object_ref за замовчуванням -- як є, лише відсіюючи placeholder
    return (None if is_placeholder(raw_value, tokens) else raw_value), False
