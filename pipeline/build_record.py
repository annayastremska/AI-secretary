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

from pipeline.extraction.extract import (
    NAME_PART_ROLES,
    field_part,
    name_group_key,
    primary_name_group,
)
from pipeline.normalization.normalize import (
    detect_name_case,
    normalize_field,
    normalize_nominative_case,
    is_placeholder,
    resolve_category,
)

# Провенанс, який НЕ вважається надійним підтвердженням значення. Значення
# зберігається (дані не губимо), але поле не рахується вирішеним, тому
# критичне поле з таким провенансом не дає документу статус confirmed.
# Без цього LLM могла віддати "рядовий" у поле given_name, морфологія
# відповідала not_a_name, значення лишалось -- і документ виходив confirmed.
UNRELIABLE_METHODS = ("llm_split_vote",)
# no_morphology і ambiguous_case додані після верифікації проти БД-споживача.
# Причина конкретна: обидва означають, що прізвище лишилось у відмінку
# ДЖЕРЕЛА, а завантажувач зіставляє людину за точним рядком canonical_name --
# отже "БЕВЗЕНКА" створює другий об'єкт на ту саму людину, і при статусі
# confirmed підрахунок "скільком людям зараз у відпустці" роздувається.
# no_morphology = pymorphy3 недоступний, тобто НЕ нормалізовано жодного ПІБ:
# це найгірший випадок, а не найлегший, і замовчувати його не можна.
# ambiguous_case тут означає саме "жодного свідчення про відмінок" --
# випадок "решта ПІБ каже називний" тепер дає already_nominative
# (normalize.normalize_nominative_case), тож жіночі прізвища на -ова з
# називним по батькові в цей блокер не потрапляють.
UNRELIABLE_MORPHOLOGY = ("not_a_name", "inflect_failed",
                         "no_morphology", "ambiguous_case")

# Числова впевненість для facts.confidence у БД-споживача (колонка існує й
# досі лишалась NULL). Не ймовірність, а порядок довіри до СПОСОБУ отримання:
# детермінований збіг за версткою бланка надійніший за генерацію моделі, а
# розкол голосів -- найненадійніший з тих, що взагалі дають значення.
CONFIDENCE_BY_METHOD = {
    "matched": 0.9,
    "derived": 0.8,
    "llm": 0.6,
    "llm_split_vote": 0.3,
}
# Морфологія не спрацювала -> значення лишилось у відмінку джерела, тобто
# ідентичність особи може не зійтися з наявною в базі. Стеля впевненості.
CONFIDENCE_CAP_BY_MORPHOLOGY = {
    "not_a_name": 0.3,
    "inflect_failed": 0.4,
    "no_morphology": 0.5,
    "ambiguous_case": 0.7,
}


def field_confidence(reason, morph_status=None):
    """None, якщо значення немає взагалі."""
    base = CONFIDENCE_BY_METHOD.get((reason or "").split(":", 1)[0])
    if base is None:
        return None
    cap = CONFIDENCE_CAP_BY_MORPHOLOGY.get(morph_status)
    return min(base, cap) if cap is not None else base

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
    extra_facts = []
    # Додаткові особи документа (група != основної). Поки не йдуть у БД --
    # у таблиці people одна особа на документ -- але видимі в записі, щоб
    # рев'юер бачив, що документ описує не одного суб'єкта.
    extra_subjects = {}
    # Сирий текст полів, які не вирішились: значення в документі є, але
    # не зіставилось із контрольованим словником. Див. коментар нижче.
    unresolved_values = {}
    # Основна особа -- та, чиє поле стоїть у схемі першим (див.
    # extract.primary_name_group). Не спеціальна назва групи.
    primary_group = primary_name_group(schema)

    unknown_fields, unknown_critical_fields = [], []
    confirmed_empty_fields, not_implemented_fields = [], []

    # Відмінок ПІБ визначається ОДИН раз на документ, за по батькові: його
    # форми найхарактерніші ("Едуардович" проти "Едуардовича"), і без цієї
    # підказки неоднозначні прізвища ("ПЕТРОВА" -- і називний жіночий, і
    # родовий чоловічий) неможливо розвести без ризику зіпсувати рід.
    # Пошук іде за РОЛЛЮ поля (`part:` у схемі), а не за його іменем: схема
    # може називати поля applicant_patronymic чи commander_name, і читання
    # літеральних імен просто не знаходило б підказку.
    by_part = {}
    for field in schema["fields"]:
        part = field_part(field)
        if part in NAME_PART_ROLES:
            by_part.setdefault(part, []).append(field["name"])

    case_hint = None
    for hint_part in ("patronymic", "given_name"):
        for hint_field in by_part.get(hint_part, []):
            hint_value, _ = raw_extraction.get(hint_field, (None, None))
            if isinstance(hint_value, str):
                case_hint = detect_name_case(hint_value, role=hint_part)
                if case_hint:
                    break
        if case_hint:
            break

    for field in schema["fields"]:
        name = field["name"]
        target = field.get("db_target", "additional_info")

        if field.get("priority") == "deferred" or field.get("extraction") is None:
            # Критичність рахується ЧЕСНО, а не примусово optional. Раніше тут
            # стояв літерал "optional", тому схема, написана поетапно (поле
            # основного факту поки deferred), віддавала confirmed факт зі
            # значенням null: поле не потрапляло ні в unknown_fields, ні в
            # unknown_critical_fields, і перевірка на порожню критику
            # проходила. У наявних двох схемах усі deferred-поля -- це
            # additional_info, тому зміна нічого зараз не ламає, але для
            # будь-якої нової схеми вона обов'язкова.
            criticality = field_criticality(field)
            not_implemented_fields.append(name)
            field_provenance[name] = {"method": "deferred", "criticality": criticality,
                                      "resolved": False}
            unknown_fields.append(name)
            if criticality == "critical":
                unknown_critical_fields.append(name)
            continue

        raw_value, reason = raw_extraction.get(name, (None, "no_value"))
        confirmed_empty = False
        morph_status = None

        if field.get("extraction") == "rank_and_name_tokenized":
            if field.get("type") == "category":
                # Значення може прийти або як {"code","label"} (детермінований
                # шлях через довідник), або як код-рядок (LLM під grammar-enum)
                # -- resolve_category приводить обидва до однієї форми.
                lookup = dictionaries.get(field["category"], {})
                normalized = resolve_category(raw_value, lookup)
            elif is_placeholder(raw_value):
                normalized = None
            else:
                # role=name звужує морфологічний розбір до потрібної частини
                # імені (Surn/Name/Patr), а статус іде в provenance -- щоб
                # "не нормалізовано" не виглядало як успішна нормалізація.
                normalized, morph_status = normalize_nominative_case(
                    raw_value, role=field_part(field), case_hint=case_hint)
        elif field.get("extraction") == "derived_from":
            source_raw, _ = raw_extraction.get(field["derived_from"], (None, None))
            normalized = DERIVE_FUNCS[field["derive"]](source_raw)
        else:
            normalized, confirmed_empty = normalize_field(field, raw_value, dictionaries)

        if normalized == "термін не розпізнано":
            # match_dictionary свідомо повертає цей рядок-маркер, не None, щоб
            # відрізнити "довідник не впізнав аліас" від "None" (розд. 3.4 ТЗ).
            # Але це сигнал ДЛЯ ПРОВЕНАНСУ (raw_text/unresolved_values нижче),
            # не значення для запису -- без цієї конверсії маркер писався
            # буквальним українським рядком у subject/fact_value_code/
            # additional_info замість null. Дормантний баг: єдине зараз
            # category-поле (rank) сюди не доходить (парситься окремим шляхом
            # ще ДО resolve_category), але коментар schemas/leave_ticket.yaml
            # прямо каже, що category-поле з db_target: fact_value РАНІШЕ
            # існувало (leave_type) -- форма, що вмикає баг, уже траплялась.
            normalized = None

        if target == "person":
            # Ключ у subject -- РОЛЬ, не ім'я поля: споживач читає
            # subject["surname"] / ["given_name"] / ["patronymic"], тож контракт
            # з БД мусить лишатись стабільним, поки схема вільна називати поля
            # applicant_surname чи commander_rank.
            part = field_part(field)
            subject_key = part if part in NAME_PART_ROLES else name
            group = name_group_key(field)
            if group != primary_group:
                # Другу особу документа поки нікуди покласти: subject плаский,
                # а таблиця people в БД-споживача приймає одну особу на
                # документ. Значення НЕ губимо і НЕ перетираємо першу особу
                # (це було б тихо чуже значення в полі) -- воно йде окремо і
                # видно, що суб'єкт не один.
                additional_info[name] = normalized
                extra_subjects.setdefault(group, {})[subject_key] = normalized
            elif subject_key in subject and subject[subject_key] is not None:
                additional_info[name] = normalized
            else:
                subject[subject_key] = normalized
        elif target == "fact_value":
            fact_value_code = normalized.get("code") if isinstance(normalized, dict) else normalized
        elif target == "fact_date_start":
            fact_date_start = normalized
        elif target == "fact_date_end":
            fact_date_end = normalized
        else:
            additional_info[name] = normalized

        criticality = field_criticality(field)
        unreliable = (reason in UNRELIABLE_METHODS
                      or morph_status in UNRELIABLE_MORPHOLOGY)
        unresolved = normalized is None or unreliable
        field_provenance[name] = {
            "method": reason,
            "criticality": criticality,
            "resolved": not (unresolved or confirmed_empty),
        }
        if morph_status is not None:
            # Видимий статус морфології: normalized / already_nominative /
            # ambiguous_case / no_morphology / not_a_name / inflect_failed.
            # Усі, крім перших двох, означають, що значення лишилось у
            # відмінку джерела.
            field_provenance[name]["morphology"] = morph_status
        # normalized is None, НЕ unresolved: стеля впевненості за морфологією
        # (CONFIDENCE_CAP_BY_MORPHOLOGY, узгоджена з командою БД --
        # db-handoff-notes.md) мала застосовуватись САМЕ для морфологічно
        # ненадійних значень (not_a_name/inflect_failed/no_morphology/
        # ambiguous_case). Але ці самі 4 статуси примушують unresolved=True
        # (щоб не дати confirmed) -- через `unresolved` тут стеля НІКОЛИ не
        # спрацьовувала: до field_confidence доходили лише значення, для
        # яких morph_status уже НЕ один із чотирьох, тобто cap завжди
        # промахувався повз словник. Наслідок: facts.confidence лишався NULL
        # саме там, де команда БД просила НИЗЬКЕ, а не відсутнє число --
        # чергу рев'ю неможливо відсортувати за найгіршими записами
        # (db-handoff-notes.md, п.4). Значення при цьому не губиться:
        # unresolved і далі блокує confirmed незалежно від цієї зміни.
        confidence = None if normalized is None else field_confidence(reason, morph_status)
        if confidence is not None:
            field_provenance[name]["confidence"] = confidence

        # Поле, що оголосило `dimension:`, стає ОКРЕМИМ фактом. Без цього все
        # з db_target: additional_info не доходить до БД узагалі: у таблиці
        # facts немає JSON-колонки, а завантажувач споживача вставляє лише
        # fact.value_code -- тобто посада, мета, номер наказу мовчки губились.
        if field.get("dimension") and normalized is not None and not unresolved:
            extra_facts.append({
                "fact_type": field["dimension"],
                "value_code": (normalized.get("code") if isinstance(normalized, dict)
                               else str(normalized)),
                "date_start": None,
                "date_end": None,
                # confirmed виставляється ПІСЛЯ циклу, разом з основним фактом.
                # Тут стояв вираз `criticality != "critical" or not unresolved`,
                # який усередині цієї гілки тотожно True (гілка вже вимагає
                # `not unresolved`) -- тобто КОЖЕН похідний факт ішов у базу як
                # підтверджений, навіть з документа зі статусом needs_review,
                # якого ніхто не переглядав. А запити читають саме підтверджені
                # факти.
                "confirmed": None,
                "confidence": confidence,
                "status": "current",
                "superseded_by_document_id": None,
                "additional_info": {},
                "source_field": name,
            })

        # Сире значення з документа для НЕВИРІШЕНОГО поля. Причина конкретна:
        # звання, якого немає в довіднику ("гвардії підпоручик"), у документі
        # НАПИСАНЕ, а в записі ставало просто null -- тобто ми віддавали
        # "звання невідоме" там, де воно відоме, лише не входить у наш
        # контрольований словник. Тепер текст доходить і до рев'юера (він
        # бачить, який саме аліас дописати), і до БД як опційне значення.
        # {code,label}-інваріант при цьому не порушується: сам field лишається
        # невирішеним, сирий текст живе окремо.
        # Умова саме "значення НЕ вийшло", а не "поле невирішене": поле може
        # бути невирішеним і при наявному значенні (морфологія не спрацювала) --
        # це вже видно через morphology + resolved, і дублювати його тут
        # означало б наповнити unresolved_values успішно витягнутими
        # прізвищами й розмити зміст цього ключа.
        value_missing = normalized is None
        if value_missing and not confirmed_empty:
            raw_text = None
            if isinstance(raw_value, str) and raw_value.strip():
                raw_text = raw_value.strip()
            elif (reason or "").startswith("rank_not_in_dictionary:"):
                raw_text = reason.split(":", 1)[1].strip() or None
            if raw_text and not is_placeholder(raw_text):
                unresolved_values[name] = raw_text
                field_provenance[name]["raw_text"] = raw_text

        if confirmed_empty:
            confirmed_empty_fields.append(name)
        elif unresolved:
            unknown_fields.append(name)
            if criticality == "critical":
                unknown_critical_fields.append(name)

    # Узгодженість діапазону. Реальний тригер: "з 28 грудня по 6 січня 2026 р."
    # -- рік із кінця діапазону приписувався початку, і виходило
    # date_start=2026-12-28 > date_end=2026-01-06, тобто діапазон на -356
    # днів, при статусі confirmed. Жодної перевірки не було ніде.
    # Додатковий аргумент: у БД-споживача на facts стоїть
    # CHECK (valid_to >= valid_from), тож такий запис узагалі не вставився б.
    date_range_error = None
    if fact_date_start and fact_date_end and fact_date_start > fact_date_end:
        date_range_error = f"date_start ({fact_date_start}) > date_end ({fact_date_end})"
        for date_field, date_target in (("fact_date_start", fact_date_start),
                                        ("fact_date_end", fact_date_end)):
            for fname, fdef in ((f["name"], f) for f in schema["fields"]):
                if fdef.get("db_target") == date_field:
                    field_provenance.setdefault(fname, {})["date_range_error"] = True
                    if fname not in unknown_fields:
                        unknown_fields.append(fname)
                    if field_criticality(fdef) == "critical" and fname not in unknown_critical_fields:
                        unknown_critical_fields.append(fname)
        # Дати лишаються у виході (їх видно людині), але діапазон
        # позначений як недостовірний і не дає confirmed.

    # Впевненість основного факту -- найслабша ланка серед полів, що його
    # утворюють (значення + дати): факт не може бути надійнішим за найгірше
    # зі своїх складових.
    primary_sources = [f["name"] for f in schema["fields"]
                       if f.get("db_target") in ("fact_value", "fact_date_start", "fact_date_end")]
    primary_confidences = [field_provenance[n]["confidence"] for n in primary_sources
                           if n in field_provenance and "confidence" in field_provenance[n]]

    fact = {
        "fact_type": schema.get("fact_type"),
        "value_code": fact_value_code,
        "date_start": fact_date_start,
        "date_end": fact_date_end,
        # Підтверджено = усі КРИТИЧНІ поля на місці. Некритичні прогалини
        # видно в unknown_fields, але вони не блокують запис.
        "confirmed": len(unknown_critical_fields) == 0,
        "confidence": min(primary_confidences) if primary_confidences else None,
        "status": "current",
        "superseded_by_document_id": None,
        "additional_info": additional_info,
    }

    # Похідний факт не може бути надійнішим за документ, з якого взятий:
    # особа в ньому та сама, і якщо ПІБ або дати документа під питанням, то
    # "посада = водій" прив'язана до непевної особи. Успадкування, а не
    # власний розрахунок по полю.
    for extra in extra_facts:
        extra["confirmed"] = fact["confirmed"]

    # Порядок важливий: основний факт першим, бо завантажувач споживача бере
    # facts[0] як джерело дати для факту звання.
    return {
        "subject": subject,
        # Кілька фактів з одного документа -- нормальна ситуація: основний
        # факт шаблону + по одному на кожне поле, що оголосило `dimension:`.
        # Один рядок facts = (об'єкт, вимір, значення) у БД-споживача.
        "facts": [fact] + extra_facts,
        "field_provenance": field_provenance,
        "unknown_fields": unknown_fields,
        "unknown_critical_fields": unknown_critical_fields,
        "confirmed_empty_fields": confirmed_empty_fields,
        "not_implemented_fields": not_implemented_fields,
        "date_range_error": date_range_error,
        "extra_subjects": extra_subjects,
        "unresolved_values": unresolved_values,
    }
