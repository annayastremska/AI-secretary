"""Генерична збірка запису за db_target-тегами схеми. Не хардкодить назви
полів чи логіку конкретного домену -- усе визначає сама схема (db_target,
extraction, type, normalization, criticality).

Повертає record-словник під структуру БД, погоджену в
docs/spec/project-expectations.md розд. 4:
  subject           -> реєстр об'єктів (люди/техніка)
  facts             -> таблиця фактів (СПИСОК, бо один документ може дати
                       кілька фактів -- напр. кілька зупинок у посвідченні
                       про відрядження чи кілька рядків книги обліку)
  field_provenance  -> як саме отримано кожне поле (regex / LLM / збій), без
                       чого неможливо ні таргетувати 5%-аудит, ні перерахувати
                       документи після виправлення схеми
"""
import datetime
import re

from pipeline.extraction.extract import (
    AMBIGUOUS_MATCH_METHOD,
    NAME_PART_ROLES,
    POSITIONAL_NAME_METHOD,
    NAME_TAIL_METHOD,
    UNVERIFIED_METHOD,
    field_part,
    name_group_key,
    primary_name_group,
)
from pipeline.normalization.normalize import (
    UNRESOLVED_TERM_MARKER,
    detect_name_case,
    field_placeholder_tokens,
    normalize_field,
    normalize_nominative_case,
    is_placeholder,
    resolve_category,
    surname_display_case,
)

# Провенанс, який НЕ вважається надійним підтвердженням значення. Значення
# зберігається (дані не губимо), але поле не рахується вирішеним, тому
# критичне поле з таким провенансом не дає документу статус confirmed.
# Без цього LLM могла віддати "рядовий" у поле given_name, морфологія
# відповідала not_a_name, значення лишалось -- і документ виходив confirmed.
# POSITIONAL_NAME_METHOD -- імпортом, не літералом, з тієї самої причини, що
# UNVERIFIED_METHOD: рядок провенансу мусить мати одне джерело, інакше
# перейменування тихо вимкнуло б блокер (A-05).
UNRELIABLE_METHODS = ("llm_split_vote", UNVERIFIED_METHOD, POSITIONAL_NAME_METHOD)
# UNVERIFIED_METHOD ("unverified_foreign_edition") доданий 14.08.2026,
# known-weak-spots.md розд. 8.6. Він означає рівно те, чого цей перелік і
# стосується: детермінований шлях значення знайшов, але бланк не впізнаний,
# тому підтвердити його НІЧИМ. Поки build_record цього методу не знав, поле
# отримувало resolved: true, і facts[0]["confirmed"] міг лишитись true, тоді як
# meta["status"] уже був needs_review (статус ставить run.py). Тобто провенанс
# суперечив сам собі: поле, назване непідтвердженим, вважалось вирішеним, і
# споживач, що фільтрує за facts.confirmed, а не за meta.status, узяв би його
# в підрахунок. Імпорт із extract, а не літерал: рядок мусить мати одне
# джерело -- інакше перейменування провенансу тихо вимкнуло б цей блокер.
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
# untagged_name блокує ЛИШЕ значення від LLM, і це не компроміс, а єдиний
# доступний розрізнювач. Морфологія "Володимира" (справжнє ім'я, не розмічене
# у VESUM) від "Таблиці" (не ім'я взагалі) НЕ відрізняє: обидва -- відоме
# слово в називному без граммеми імені. Відрізняє їх ДЖЕРЕЛО: детермінований
# збіг означає, що значення стояло в позиції ПІБ на бланку, а токен від
# моделі такої гарантії не має. Тому untagged_name від `matched` не блокує, а
# від `llm` -- блокує (регресійний тест на "Таблиця" саме про це).
# Далі -- решта міркування про untagged_name (рішення Анни 13.08.2026): це
# випадок "словник знає слово, але не розмітив його як імя" -- напр.
# "Володимир" і "Дергач" у VESUM. Значення при цьому правильне й уже в
# називному, тому блокувати підтвердження немає підстав. А
# untagged_oblique блокує: там слово відоме, стоїть у непрямому
# відмінку, і без граммеми імені привести його до називного безпечно
# неможливо -- це той самий ризик "інша людина в базі".
UNRELIABLE_MORPHOLOGY = ("not_a_name", "inflect_failed",
                         "no_morphology", "ambiguous_case",
                         "untagged_oblique")

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
    # Значення правильне й у називному, лише словник не розмітив його
    # як імя -- нижче за matched, але не блокує підтвердження.
    "untagged_name": 0.8,
    "untagged_oblique": 0.4,
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


# --- Узгодженість залежних полів -----------------------------------------
#
# Клас, а не окремий випадок. Заміряний тригер (LEAVE-011): модель віддала
# `днів = 17` при порожньому полі на бланку; `початок`/`кінець`/`повернення`
# чесно лишились None і пішли в прогалини, а `днів` -- ні. У БД пішов би
# внутрішньо суперечливий запис -- ТРИВАЛІСТЬ БЕЗ ДАТ -- без жодного маркера,
# і саме відсутність маркера дорожча за саму галюцинацію.
#
# Які ще поля логічно залежать від інших (перевірено по обох схемах і по
# всьому еталонному набору, 30 документів):
#   duration_days   = leave_end_date_planned - leave_start_date + 1  (16/16)
#   deployment_days = deployment_end_date - deployment_start_date + 1 (14/14)
#   actual_return_date >= leave_end_date_planned                      (16/16)
#   leave_start_date   >= document_date                               (16/16)
#   deployment_start_date >= document_date                            (14/14)
#   basis_order_date   <= document_date                               (14/14)
# Реалізовані два правила, яких досить, щоб покрити всі перелічені:
#   days_span_inclusive -- РІВНІСТЬ (кількість днів включно з обома кінцями);
#   not_before          -- ПОРЯДОК двох дат.
# Перелік закритий навмисно, як NAME_PART_ROLES: правило -- це код, а не
# вільний вираз у YAML, інакше опечатка в схемі дала б тихо вимкнену перевірку.
#
# Два різні результати, і різниця принципова:
#   consistency_error       -- обидва боки відомі й СУПЕРЕЧАТЬ один одному;
#   unverifiable_dependency -- бік, від якого значення залежить, відсутній,
#                              тобто значення НЕМА ЧИМ підтвердити. Саме цей
#                              випадок і був німим.
# В обох поле стає невирішеним (значення зберігається, не губиться), тобто
# критичне поле не дасть confirmed, а некритичне буде видно в unknown_fields
# і в record["consistency_problems"].

def _days_span_inclusive(values):
    """Скільки днів у діапазоні, включно з обома кінцями (як рахує бланк:
    "з 10 по 22 травня" -- терміном на 13 днів, не 12)."""
    start, end = values.get("start"), values.get("end")
    if not start or not end:
        return None
    d0, d1 = datetime.date.fromisoformat(start), datetime.date.fromisoformat(end)
    return abs((d1 - d0).days) + 1


CONSISTENCY_RULES = {
    # rule -> (обов'язкові посилання, функція очікуваного значення | None)
    "days_span_inclusive": (("start", "end"), _days_span_inclusive),
    "not_before": (("not_before",), None),
}


def check_consistency(rule: dict, own_value, resolved_values):
    """(проблема_або_None, деталі). rule -- блок `consistency:` поля схеми."""
    name = (rule or {}).get("rule")
    spec = CONSISTENCY_RULES.get(name)
    if spec is None:
        # Невідоме правило -- це ПОМИЛКА СХЕМИ, і вона мусить бути видна, а не
        # означати "перевірки немає" (валідатор схем ловить це раніше).
        return "unknown_consistency_rule", name
    refs, expected_fn = spec
    if own_value is None:
        # Немає ЧОГО перевіряти: поле й так уже прогалина й уже в
        # unknown_fields. Перевірка ДО читання посилань навмисно -- інакше
        # порожнє поле отримувало б ще й `unverifiable_dependency` і
        # дублювало сигнал, який уже є.
        return None, None
    values = {}
    for ref in refs:
        target_field = rule.get(ref)
        if not target_field:
            return "unknown_consistency_rule", f"{name}: немає посилання '{ref}'"
        values[ref] = resolved_values.get(target_field)
        if values[ref] is None:
            return "unverifiable_dependency", target_field
    if name == "not_before":
        other = values["not_before"]
        try:
            if datetime.date.fromisoformat(str(own_value)) < datetime.date.fromisoformat(str(other)):
                return "consistency_error", f"{own_value} < {rule['not_before']}={other}"
        except ValueError:
            return None, None
        return None, None
    try:
        expected = expected_fn(values)
    except (TypeError, ValueError):
        return None, None
    if expected is None or int(own_value) == expected:
        return None, None
    return "consistency_error", f"{own_value} != {expected}"


_DOC_NUMBER_RE = re.compile(r'^[\w/\-]+$')


def _looks_like_document_number(value) -> bool:
    """Чи витягнуте значення -- НОМЕР документа, а не словесна позначка.
    Розділяє два різні за дією випадки: "знайди документ № 157" проти
    "знайди попередній квиток цієї людини"."""
    text = str(value).strip()
    return bool(text) and bool(_DOC_NUMBER_RE.match(text)) and any(c.isdigit() for c in text)


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
    # Значення полів, які ВИРІШИЛИСЬ -- єдине, чим можна підтверджувати
    # залежне поле. Невирішене значення нічого не підтверджує за визначенням.
    resolved_values = {}
    consistency_problems = {}
    document_links = []

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

        if normalized == UNRESOLVED_TERM_MARKER:
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
            if part == "surname" and isinstance(normalized, str):
                # Фінальний вигляд прізвища -- «Перша велика, решта малі»,
                # незалежно від друку в документі. Саме тут, а не в
                # _restore_case: регістр джерела ще потрібен розпізнаванню
                # (яке слово -- прізвище), а це -- лише представлення на
                # виході. Формат до розгалуження, щоб друга особа документа
                # (extra_subjects) і додаткові поля отримали той самий вигляд;
                # person_alias складається з subject пізніше (run.py), тому
                # успадковує його автоматично.
                normalized = surname_display_case(normalized)
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
                      or morph_status in UNRELIABLE_MORPHOLOGY
                      or (morph_status == "untagged_name"
                          and (reason or "").startswith("llm")))
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

        # Поле, що оголосило `dimension:`, стає ОКРЕМИМ фактом -- значенням,
        # за яким можна ЗАПИТАТИ, а не лише прочитати.
        # ВИПРАВЛЕНО 22.08.2026 (A-03): тут стояло «у таблиці facts немає
        # JSON-колонки», що вже неправда (`facts.additional_info JSONB`,
        # міграція 8a667569ba4d, і завантажувач її пише). Наслідок сталого
        # обґрунтування -- ДУБЛЮВАННЯ: реквізити документа їдуть і в
        # additional_info основного факту, і окремими рядками facts. Чи
        # прибирати `dimension:` з реквізитів -- питання до контракту з
        # командою БД (docs/review-2026-08-22/fixes-pipeline.md, «потребує
        # рішення»), а не тиха правка.
        if field.get("dimension") and normalized is not None and not unresolved:
            extra_facts.append({
                "fact_type": field["dimension"],
                "is_primary": False,
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
            elif isinstance(raw_value, dict):
                # Date-поле приходить regex-ГРУПАМИ ({day, month, year}), а не
                # рядком -- через це сирий збіг неможливої дати («31 лютого»)
                # не зберігався НІДЕ: unresolved_values порожній, warnings
                # порожні, рев'юер бачив голе resolved:false без причини
                # (R-B1-04). Групи склеюються в читабельний рядок у порядку
                # оголошення -- «31 лютого 2026».
                joined = " ".join(str(v).strip() for v in raw_value.values()
                                  if v is not None and str(v).strip())
                raw_text = joined or None
            elif (reason or "").startswith(("rank_not_in_dictionary:",
                                            NAME_TAIL_METHOD + ":",
                                            AMBIGUOUS_MATCH_METHOD + ":")):
                # Значення в документі Є (звання поза довідником / хвіст ПІБ
                # після по батькові, R-B1-02; кілька різних збігів одного
                # патерна, C-03) -- рев'юер мусить бачити, що саме там
                # стояло, а не голий null.
                raw_text = reason.split(":", 1)[1].strip() or None
            if raw_text and not is_placeholder(raw_text, field_placeholder_tokens(field)):
                unresolved_values[name] = raw_text
                field_provenance[name]["raw_text"] = raw_text

        if confirmed_empty:
            confirmed_empty_fields.append(name)
        elif unresolved:
            unknown_fields.append(name)
            if criticality == "critical":
                unknown_critical_fields.append(name)
        else:
            resolved_values[name] = normalized

        # Зв'язок документ -> документ. Збирається окремим ключем, а не
        # ховається в additional_info: таблиці зв'язків, до якої це належить,
        # у схемі споживача ще немає, тож значення мусить бути на видноті --
        # інакше погоджене рішення (architecture-proposal.md, розд. 2 п.4)
        # втратиться другий раз.
        # ВИПРАВЛЕНО 22.08.2026 (A-03): тут ще стояло «завантажувач
        # additional_info не читає взагалі (у facts немає JSON-колонки)» --
        # стале: колонка є (міграція 8a667569ba4d) і завантажувач її пише.
        # Аргумент за окремий ключ від цього не слабший: JSON-поле факту --
        # не місце для вказівки закрити ІНШИЙ документ.
        if field.get("link_type") and normalized is not None:
            document_links.append({
                "link_type": field["link_type"],
                # Номер скасованого документа, якщо він надрукований. None ->
                # позначка є, номера немає (LEAVE-014: "перервана, відкликаний
                # з відпустки"), тобто пару шукають за особою й датами.
                "target_document_number": (str(normalized)
                                           if field.get("type") != "text"
                                           or _looks_like_document_number(normalized)
                                           else None),
                "source_field": name,
                "evidence": str(normalized),
                "method": reason,
            })

    # Узгодженість ЗАЛЕЖНИХ полів -- окремим проходом, бо перевірка потребує
    # значень ІНШИХ полів, а вони готові лише після циклу.
    for field in schema["fields"]:
        rule = field.get("consistency")
        if not rule:
            continue
        name = field["name"]
        if name in confirmed_empty_fields:
            # Документ ПРЯМО каже, що значення немає -- перевіряти нічого.
            continue
        problem, detail = check_consistency(rule, resolved_values.get(name),
                                            resolved_values)
        if problem is None:
            continue
        consistency_problems[name] = f"{problem}: {detail}"
        field_provenance.setdefault(name, {})["consistency"] = problem
        field_provenance[name]["consistency_detail"] = detail
        # Значення лишається у виході (його видно людині), але поле більше не
        # вважається вирішеним -- саме цього маркера й не було.
        if field_provenance[name].get("resolved"):
            field_provenance[name]["resolved"] = False
        resolved_values.pop(name, None)
        if name not in unknown_fields:
            unknown_fields.append(name)
        if field_criticality(field) == "critical" and name not in unknown_critical_fields:
            unknown_critical_fields.append(name)
        # Похідний факт (`dimension:`) з непідтвердженого значення не йде в
        # базу -- та сама умова, що в циклі вище (`not unresolved`), лише
        # застосована після того, як стала відома залежність.
        extra_facts = [f for f in extra_facts if f.get("source_field") != name]

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
        # ОСНОВНИЙ факт документа -- позначкою, а не позицією в списку
        # (рев'ю 22.08.2026, C-08). Доти статус документа рахувався як
        # `record["facts"][0].get("confirmed")` у run.py, а завантажувач
        # споживача бере `facts[0]` як джерело дати для факту звання -- тобто
        # три модулі були зв'язані мовчазною угодою про ІНДЕКС, якої не
        # перевіряв ніхто (валідатор порядку полів не бачить). Порядок
        # лишається тим самим (основний першим), але тепер він не єдиний
        # носій цього знання.
        "is_primary": True,
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
        # {поле: "consistency_error: ..." | "unverifiable_dependency: ..."} --
        # значення, яке суперечить іншим полям документа або не має чим
        # підтвердитись (напр. тривалість відпустки без дат початку й кінця).
        "consistency_problems": consistency_problems,
        # [{link_type, target_document_number, source_field, evidence, method}]
        # -- ознака скасування/зміни ІНШОГО документа, витягнута з тексту
        # цього. Порожній список = ознаки в документі немає (норма).
        # Зіставити пару звідси НЕМОЖЛИВО: скасований документ жодної ознаки
        # не містить, тож закриття старого факту -- це запит по всіх
        # документах, тобто таблиця зв'язків на боці БД.
        "document_links": document_links,
        "extra_subjects": extra_subjects,
        "unresolved_values": unresolved_values,
    }
