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

PLACEHOLDER_TOKENS = {
    "redacted", "???", "",
    "не заповнено", "не вказано", "не зазначено", "не видано", "не видавались",
    "відсутнє", "відсутній", "відсутня", "немає",
}

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
_DATE_PATTERNS = (
    re.compile(r'(?<!\d)(?P<day>\d{1,2})[.\-/](?P<month>\d{1,2})[.\-/](?P<year>\d{4})(?!\d)'),
    re.compile(
        r'[«"“„]?(?P<day>\d{1,2})[»"”‟]?\s+(?P<month>[а-яіїє]+|\d{1,2})\s+'
        r'(?P<year>\d{4})\s*(?:р\.?|рок\w*)?'
    ),
)


def is_placeholder(raw_text) -> bool:
    """Surya позначає порожнє/нерозбірливе місце в джерелі як [REDACTED] --
    підтверджено повторюваним у попередніх тестах, не галюцинація. Також
    ловить типові "тут мало бути значення" маркери бланка: голі підкреслення/
    риски й українські фрази "не заповнено"/"не вказано" тощо."""
    if raw_text is None:
        return False
    cleaned = raw_text.strip().strip("[]")
    if cleaned.lower() in PLACEHOLDER_TOKENS:
        return True
    return bool(cleaned) and bool(_BLANK_FILL_RE.match(cleaned))


def month_to_num(month):
    return int(month) if str(month).isdigit() else UKR_MONTHS.get(str(month).lower())


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
    try:
        # datetime валідує діапазони й неможливі дати (31 лютого) замість
        # того, щоб зібрати формально коректний, але неіснуючий рядок.
        return datetime.date(year_num, month_num, day_num).isoformat()
    except ValueError:
        return None


def parse_date_from_text(raw_text):
    """Для полів, витягнутих не регексом-з-групами, а сирим блоком тексту
    (напр. block_before_label) -- шукає дату прямо в довільному рядку."""
    if not raw_text or is_placeholder(raw_text):
        return None
    low = raw_text.lower()
    for pattern in _DATE_PATTERNS:
        m = pattern.search(low)
        if m:
            return {"day": m.group("day"), "month": m.group("month"), "year": m.group("year")}
    return None


def to_int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def match_dictionary(raw_text, alias_lookup: dict):
    """Точний рядковий збіг після нормалізації. Незнайдений термін -> рядок-
    маркер, не None і не 0 (розділ 3.4 ТЗ: нуль не можна відрізнити від
    "записів немає"). Суфікс "за NNNN рік" відсікається окремим правилом,
    з допуском коми/крапки після нього."""
    if not raw_text or is_placeholder(raw_text):
        return None
    normalized = re.sub(r"\s*за\s*\d{4}\s*рік\s*[,.;]?\s*$", "", raw_text.strip().lower())
    hit = alias_lookup.get(normalized)
    return {"code": hit[0], "label": hit[1]} if hit else "термін не розпізнано"


def resolve_category(value, alias_lookup: dict):
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
    if value is None or is_placeholder(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    code_to_label = {code: label for code, label in alias_lookup.values()}
    if text in code_to_label:
        return {"code": text, "label": code_to_label[text]}
    return match_dictionary(text, alias_lookup)


def build_alias_lookup(dictionary: dict) -> dict:
    lookup = {}
    for value in dictionary["values"]:
        for alias in value["aliases"]:
            lookup[alias.strip().lower()] = (value["code"], value["label"])
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


def normalize_nominative_case(raw_name, role=None):
    """Приводить частину ПІБ до називного відмінка через pymorphy3 (uk).

    Повертає (значення, статус), де статус робить видимим те, що раніше було
    невидимим: до цього тут була заглушка, яка повертала текст без змін, і
    поле в родовому відмінку ("Іваненку Івану") виглядало в provenance як
    звичайний успіх нормалізації.

    Статуси:
      normalized          -- відмінок змінено
      already_nominative  -- уже називний, змін не потрібно
      no_morphology       -- pymorphy3 не встановлено
      not_a_name          -- морфологія не розпізнала слово як частину імені
      inflect_failed      -- розпізнала, але не змогла провідмінювати

    role ("surname"/"given_name"/"patronymic") звужує розбір до відповідної
    граммеми -- інакше прізвище могло б бути провідмінюване як звичайний
    іменник, що дало б тихо неправильний результат.
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

    required = _ROLE_GRAMMEME.get(role)
    parses = morph.parse(token)
    if required:
        candidates = [p for p in parses if required in p.tag]
    else:
        candidates = [p for p in parses if any(g in p.tag for g in _NAME_GRAMMEMES)]
    if not candidates:
        return raw_name, "not_a_name"

    best = candidates[0]
    if "nomn" in best.tag:
        return _restore_case(token, best.word), "already_nominative"
    inflected = best.inflect({"nomn"})
    if inflected is None:
        return raw_name, "inflect_failed"
    return _restore_case(token, inflected.word), "normalized"


def normalize_null_if_sentinel(raw_text, sentinel: str):
    """Повертає (значення, чи_підтверджено_порожнє). Розрізняє "документ
    прямо каже, що цього немає" від "не вдалося прочитати" -- обидва раніше
    давали однаковий None і губили цю різницю."""
    if not raw_text or is_placeholder(raw_text):
        return None, False
    if raw_text.strip().lower() == sentinel.lower():
        return None, True
    return raw_text.strip(), True


def normalize_field(field_def: dict, raw_value, dictionaries: dict):
    """Єдина точка диспетчеризації за field_def["type"]/["normalization"].
    dictionaries: {category_name: alias_lookup} для category-полів.
    Повертає (значення, чи_підтверджено_порожнє).
    """
    field_type = field_def.get("type")
    normalization = field_def.get("normalization")

    if raw_value is None:
        return None, False

    if field_type == "category":
        lookup = dictionaries.get(field_def["category"], {})
        return resolve_category(raw_value, lookup), False

    if field_type == "number":
        return to_int_or_none(raw_value), False

    if field_type == "date":
        if isinstance(raw_value, dict):
            return normalize_date(raw_value.get("day"), raw_value.get("month"), raw_value.get("year")), False
        if isinstance(raw_value, str):
            parsed = parse_date_from_text(raw_value)
            return (normalize_date(**parsed) if parsed else None), False
        return None, False

    if normalization == "nominative_case":
        if is_placeholder(raw_value):
            return None, False
        # Статус морфології тут відкидається: поля ПІБ ідуть через окрему
        # гілку в build_record, яка його зберігає в provenance. Ця гілка --
        # для схем, що оголосили nominative_case поза rank_and_name.
        value, _status = normalize_nominative_case(raw_value)
        return value, False

    if normalization == "null_if_not_issued":
        return normalize_null_if_sentinel(raw_value, field_def.get("not_issued_sentinel", ""))

    # text / object_ref за замовчуванням -- як є, лише відсіюючи placeholder
    return (None if is_placeholder(raw_value) else raw_value), False
