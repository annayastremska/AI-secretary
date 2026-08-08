"""Генеричні нормалізатори -- диспетчеризація за ТИПОМ поля (date/number/
category/text), не за назвою поля чи доменом. Один набір функцій для
будь-якої схеми.
"""
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
# мати різні лапки для різних полів; і суфікс "р."/"року" в одному правилі.
_DATE_IN_TEXT = re.compile(
    r'[«"“„]?(?P<day>\d{1,2})[»"”‟]?\s+(?P<month>[а-яіїє]+|\d{1,2})\s+(?P<year>\d{4})\s*(?:р\.?|рок\w*)'
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


def normalize_date(day, month, year):
    if day is None or month is None or year is None:
        return None
    month_num = month_to_num(month)
    if month_num is None:
        return None
    year_num = int(year) if len(str(year)) == 4 else 2000 + int(year)
    return f"{year_num:04d}-{month_num:02d}-{int(day):02d}"


def parse_date_from_text(raw_text):
    """Для полів, витягнутих не регексом-з-групами, а сирим блоком тексту
    (напр. block_before_label) -- шукає дату прямо в довільному рядку."""
    if not raw_text or is_placeholder(raw_text):
        return None
    m = _DATE_IN_TEXT.search(raw_text.lower())
    if not m:
        return None
    return {"day": m.group("day"), "month": m.group("month"), "year": m.group("year")}


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


def normalize_nominative_case(raw_name):
    """TODO: називний відмінок (pymorphy2 uk чи аналог) -- не реалізовано.
    Заглушка повертає текст без змін."""
    return raw_name


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
        return (None if is_placeholder(raw_value) else normalize_nominative_case(raw_value)), False

    if normalization == "null_if_not_issued":
        return normalize_null_if_sentinel(raw_value, field_def.get("not_issued_sentinel", ""))

    # text / object_ref за замовчуванням -- як є, лише відсіюючи placeholder
    return (None if is_placeholder(raw_value) else raw_value), False
