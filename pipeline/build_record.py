"""Генерична збірка запису за db_target-тегами схеми. Не хардкодить назви
полів чи логіку конкретного домену -- усе визначає сама схема (db_target,
extraction, type, normalization, criticality).

Повертає record-словник під структуру БД, погоджену в
context/project-expectations.md розд. 4:
  subject           -> реєстр об'єктів (люди/техніка)
  facts             -> таблиця фактів (СПИСОК, бо один документ може дати
                       кілька фактів -- напр. кілька зупинок у посвідченні
                       про відрядження чи кілька рядків книги обліку)
  field_provenance  -> як саме отримано кожне поле (regex / LLM / збій), без
                       чого неможливо ні таргетувати 5%-аудит, ні перерахувати
                       документи після виправлення схеми
"""
import re

from pipeline.normalization.normalize import (
    normalize_field,
    normalize_nominative_case,
    is_placeholder,
    resolve_category,
)

_YEAR_SUFFIX = re.compile(r"за\s*(\d{4})\s*рік")

# db_target, що впливають на підрахунки (стан, дати, особа) -- саме для них
# architecture-proposal.md розд. 3 вимагає суворішого порога, ніж для
# довільного опису. additional_info за замовчуванням не критичний: інакше
# відсутній вільнотекстовий "purpose" блокував би підтвердження так само,
# як відсутня дата, і в черзі ручного рев'ю опинявся б кожен документ.
CRITICAL_DB_TARGETS = {"person", "fact_value", "fact_date_start", "fact_date_end"}


def extract_year_suffix(raw_text):
    if not raw_text:
        return None
    m = _YEAR_SUFFIX.search(str(raw_text).lower())
    return int(m.group(1)) if m else None


DERIVE_FUNCS = {
    "extract_year_suffix": extract_year_suffix,
}


def field_criticality(field: dict) -> str:
    """critical | optional. Явний criticality у схемі має приоритет над
    правилом за db_target -- щоб офіцери могли донастроїти окремі поля, не
    змінюючи код."""
    explicit = field.get("criticality")
    if explicit in ("critical", "optional"):
        return explicit
    return "critical" if field.get("db_target", "additional_info") in CRITICAL_DB_TARGETS else "optional"


def build_record(schema: dict, raw_extraction: dict, dictionaries: dict) -> dict:
    """raw_extraction: {field_name: (сире_значення, reason)} з extract_document()."""
    subject = {}
    fact_value_code = None
    fact_date_start = None
    fact_date_end = None
    additional_info = {}
    field_provenance = {}

    unknown_fields, unknown_critical_fields = [], []
    confirmed_empty_fields, not_implemented_fields = [], []

    for field in schema["fields"]:
        name = field["name"]
        target = field.get("db_target", "additional_info")

        if field.get("priority") == "deferred" or field.get("extraction") is None:
            not_implemented_fields.append(name)
            field_provenance[name] = {"method": "deferred", "criticality": "optional"}
            continue

        raw_value, reason = raw_extraction.get(name, (None, "no_value"))
        confirmed_empty = False

        if field.get("extraction") == "rank_and_name_tokenized":
            if field.get("type") == "category":
                # Значення може прийти або як {"code","label"} (детермінований
                # шлях через довідник), або як код-рядок (LLM під grammar-enum)
                # -- resolve_category приводить обидва до однієї форми.
                lookup = dictionaries.get(field["category"], {})
                normalized = resolve_category(raw_value, lookup)
            else:
                normalized = None if is_placeholder(raw_value) else normalize_nominative_case(raw_value)
        elif field.get("extraction") == "derived_from":
            source_raw, _ = raw_extraction.get(field["derived_from"], (None, None))
            normalized = DERIVE_FUNCS[field["derive"]](source_raw)
        else:
            normalized, confirmed_empty = normalize_field(field, raw_value, dictionaries)

        if target == "person":
            subject[name] = normalized
        elif target == "fact_value":
            fact_value_code = normalized.get("code") if isinstance(normalized, dict) else normalized
        elif target == "fact_date_start":
            fact_date_start = normalized
        elif target == "fact_date_end":
            fact_date_end = normalized
        else:
            additional_info[name] = normalized

        criticality = field_criticality(field)
        unresolved = normalized is None or normalized == "термін не розпізнано"
        field_provenance[name] = {
            "method": reason,
            "criticality": criticality,
            "resolved": not (unresolved or confirmed_empty),
        }

        if confirmed_empty:
            confirmed_empty_fields.append(name)
        elif unresolved:
            unknown_fields.append(name)
            if criticality == "critical":
                unknown_critical_fields.append(name)

    fact = {
        "fact_type": schema.get("fact_type"),
        "value_code": fact_value_code,
        "date_start": fact_date_start,
        "date_end": fact_date_end,
        # Підтверджено = усі КРИТИЧНІ поля на місці. Некритичні прогалини
        # видно в unknown_fields, але вони не блокують запис.
        "confirmed": len(unknown_critical_fields) == 0,
        "status": "current",
        "superseded_by_document_id": None,
        "additional_info": additional_info,
    }

    return {
        "subject": subject,
        # Список навмисно, хоч зараз завжди з одного елемента: таблиця фактів
        # приймає кілька рядків з одного документа (multiple: true поля --
        # stops, рядки книги обліку). Сама екстракція multiple-полів досі
        # deferred, але форма запису вже правильна й не вимагатиме переробки
        # виклику, коли її додамо.
        "facts": [fact],
        "field_provenance": field_provenance,
        "unknown_fields": unknown_fields,
        "unknown_critical_fields": unknown_critical_fields,
        "confirmed_empty_fields": confirmed_empty_fields,
        "not_implemented_fields": not_implemented_fields,
    }
