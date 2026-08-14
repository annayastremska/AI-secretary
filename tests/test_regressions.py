# -*- coding: utf-8 -*-
"""Регресійні тести на конкретні знайдені баги.

Кожен тест названий за класом проблеми й містить РЕАЛЬНИЙ вхід, який колись
давав неправильний результат. Мета не "покрити код", а не дати вже
виправленому багу повернутись безшумно -- це вже траплялось: фікс
дедуплікації клітинок docx через id() зламав розбір дат, і помітили це лише
випадковим прогоном.

Запуск (без обробки документів, без LLM, без моделі):
    python -m pytest tests/ -q
або без pytest:
    python tests/test_regressions.py
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from pipeline.build_record import build_record
from pipeline.classification.classify import classify_domain_rules, phrase_in_text
from pipeline.extraction.extract import (
    attested_numbers,
    compile_value_boundaries,
    extract_field_regex,
    find_block_before_label,
    ground_llm_value,
    group_blocks_into_lines,
    majority_vote,
    parse_rank_and_name,
    schema_label_heads,
    strip_literal_prefix,
    validate_block_value,
)
from pipeline.config import _merge
from pipeline.identification import load_schemas
from pipeline.normalization.normalize import (
    build_alias_lookup,
    detect_name_case,
    field_placeholder_tokens,
    is_placeholder,
    normalize_date,
    normalize_field,
    normalize_nominative_case,
    normalize_null_if_sentinel,
    parse_date_from_text,
)

_SCHEMAS = None


def _schema_by_template(template):
    """Справжня схема з schemas/ -- тести на витяг зв'язків і на regex-и
    підстави мусять читати ТОЙ САМИЙ YAML, що й пайплайн, інакше вони
    перевіряють свою копію правил, а не правила."""
    global _SCHEMAS
    if _SCHEMAS is None:
        _SCHEMAS = load_schemas(os.path.join(_PROJECT_ROOT, "schemas"))
    return next(s for s in _SCHEMAS if s["template"] == template)

RANK_LOOKUP = {"рядовий": ("soldier", "Солдат"), "підполковник": ("lt_colonel", "Підполковник")}


# --- ТИХО-НЕПРАВИЛЬНО ---

def test_female_surname_not_turned_male():
    """Жіноче прізвище на -ова НЕ перетворюється на чоловіче.
    Було: "ПЕТРОВА" -> "ПЕТРОВ" зі статусом normalized, тобто в базу йшла
    інша людина, а provenance показував успіх."""
    for surname in ("ПЕТРОВА", "ІВАНОВА", "КОВАЛЬОВА"):
        value, status = normalize_nominative_case(surname, role="surname")
        assert value == surname, (surname, value, status)
        assert status in ("already_nominative", "ambiguous_case"), status


def test_oblique_surname_still_normalized():
    """Справді непрямий відмінок далі приводиться до називного."""
    assert normalize_nominative_case("БЕВЗЕНКА", role="surname")[0] == "БЕВЗЕНКО"
    assert normalize_nominative_case("Іваненку", role="surname")[0] == "Іваненко"
    assert normalize_nominative_case("Юстима", role="given_name")[0] == "Юстим"
    assert normalize_nominative_case("Едуардовича", role="patronymic")[0] == "Едуардович"


def test_case_hint_resolves_ambiguity():
    """Підказка від по батькові дозволяє відмінити неоднозначне прізвище."""
    assert detect_name_case("Едуардовича", role="patronymic") == "oblique"
    assert detect_name_case("Едуардович", role="patronymic") == "nominative"
    value, status = normalize_nominative_case("ПЕТРОВА", role="surname", case_hint="oblique")
    assert (value, status) == ("ПЕТРОВ", "normalized")


def test_unknown_rank_does_not_shift_name():
    """Звання, відсутнє в довіднику, не зсуває ім'я та по батькові.
    Було: given_name='старшина', patronymic='Іван'."""
    _, parts = parse_rank_and_name("старшина ІВАНЕНКО Іван Іванович", RANK_LOOKUP)
    assert parts["surname"] == "ІВАНЕНКО"
    assert parts["given_name"] == "Іван"
    assert parts["patronymic"] == "Іванович"
    assert parts["_leftover_before_surname"] == ["старшина"]


def test_prefix_remnant_does_not_shift_name():
    """Залишок префікса ("Видано:") не потрапляє в ПІБ."""
    _, parts = parse_rank_and_name("Видано: рядовий БЕВЗЕНКО Іван Петрович", RANK_LOOKUP)
    assert parts["surname"] == "БЕВЗЕНКО"
    assert parts["given_name"] == "Іван"
    assert parts["patronymic"] == "Петрович"


def test_duplicate_ocr_token_does_not_shift_name():
    """Дубльований OCR-токен не прибирає обидва входження."""
    _, parts = parse_rank_and_name("рядовий ІВАНЕНКО Іваненко Петрович", RANK_LOOKUP)
    assert parts["given_name"] == "Іваненко"
    assert parts["patronymic"] == "Петрович"


def test_sentinel_semantics_not_inverted():
    """Реальне значення != "підтверджено порожнє", а сентинел доходить.
    Було інвертовано в обидва боки."""
    value, confirmed_empty = normalize_null_if_sentinel("ВПД № 123456", "не видавались")
    assert (value, confirmed_empty) == ("ВПД № 123456", False)
    value, confirmed_empty = normalize_null_if_sentinel("не видавались", "не видавались")
    assert (value, confirmed_empty) == (None, True)


def test_date_never_crashes_and_rejects_impossible():
    """Нецифровий вхід і неможливі дати -> None, без винятку."""
    assert normalize_date("невідомо", "квітня", "2025") is None
    assert normalize_date("15", "квітня", "дві тисячі") is None
    assert normalize_date("31", "02", "2025") is None      # 31 лютого
    assert normalize_date("5", "5", "202") is None          # рік поза межами
    assert normalize_date("15", "квітня", "2025") == "2025-04-15"
    assert normalize_date("15", " травня ", "2025") == "2025-05-15"   # пробіли


def test_split_vote_is_visible():
    """Нічия голосів позначається, а не виглядає як одноголосний результат."""
    assert majority_vote(["A", "B"]) == ("A", True)
    assert majority_vote(["A", "A", "B"]) == ("A", False)
    assert majority_vote([None, ""]) == (None, False)


def test_ambiguous_label_not_guessed():
    """Два однакові лейбли в документі -> ambiguous_label, не перше входження."""
    blocks = group_blocks_into_lines([
        "рядовий ІВАНЕНКО Петро\n(військове звання, прізвище)",
        "підполковник КОВАЛЬЧУК Дмитро\n(військове звання, прізвище)",
    ])
    assert find_block_before_label(blocks, "військове звання, прізвище") == (None, "ambiguous_label")


def test_denylist_catches_multiline_candidate():
    """Денай-лист перевіряє кожен рядок кандидата, не рівність усього рядка."""
    blocks = group_blocks_into_lines([
        "ПОСВІДЧЕННЯ ПРО ВІДРЯДЖЕННЯ\nДодаток 28",
        "(найменування військової частини)",
    ])
    result = find_block_before_label(blocks, "найменування військової частини",
                                      {"посвідчення про відрядження"})
    assert result == (None, "denylisted")


def test_value_split_across_blocks_by_wrapped_paren():
    """Значення, розірване PDF/OCR на межі блоку рівно всередині дужки
    ("...(в/ч" / "Т3011)"), збирається назад, а не обрізається до хвоста.
    Реальний випадок: TRIP-004.pdf, PyMuPDF розбив параграф саме тут."""
    blocks = group_blocks_into_lines([
        "(пункти призначень)\nЦентральна база зберігання майна (в/ч",
        "Т3011)\n(найменування військової частини, установи, організації)",
    ])
    result = find_block_before_label(
        blocks, "найменування військової частини, установи, організації")
    assert result == ("Центральна база зберігання майна (в/ч Т3011)", "matched")


def test_strip_prefix_knows_gender():
    """`strip_prefix` зі схеми -- ОДНА форма, а бланк друкує форму за статтю.
    Було: schemas/leave_ticket.yaml:84 оголошує "звільнений", LEAVE-001 (жінка)
    має надруковане "звільнена", і в значенні ОСНОВНОГО факту лишався рід:
    "звільнена щорічна основна відпустка за 2026 рік". Провенанс -- `matched`.

    Вхід -- справжні рядки блоку 3-4 з LEAVE-001.pdf (PyMuPDF складає п'ять
    полів бланка в один блок)."""
    blocks = group_blocks_into_lines([
        "№ 102    від 09.05.2026\n"
        "рядовий ЛЕМЕШКО Соломія Романівна\n"
        "(військове звання, прізвище, ім’я та по батькові)\n"
        "звільнена\n"
        "щорічна основна відпустка за 2026 рік\n"
        "(вид відпустки та найменування населеного пункту,",
    ])
    raw, reason = find_block_before_label(
        blocks, "вид відпустки та найменування населеного пункту",
        anchor="звільнений")
    assert reason == "matched", reason
    assert strip_literal_prefix(raw, "звільнений") == \
        "щорічна основна відпустка за 2026 рік", raw
    # Чоловіча форма (LEAVE-013) -- та сама поведінка, точний літерал.
    assert strip_literal_prefix("звільнений\nвідпустка для лікування",
                                "звільнений") == "відпустка для лікування"
    # Двослівний префікс за статтю: "відрядженому до" / "відрядженій до"
    # (schemas/deployment_certificate.yaml, destination_points).
    assert strip_literal_prefix("відрядженій до\nм. Вінниця",
                                "відрядженому до") == "м. Вінниця"
    # Слово, що НЕ є формою префікса, не зрізається.
    assert strip_literal_prefix("зобов’язаний прибути", "звільнений") == \
        "зобов’язаний прибути"


def test_destination_and_purpose_are_deterministic():
    """`destination_points` (основний факт!) і `purpose` мали `extraction: llm`,
    тобто без моделі не витягувались ЗА ПОБУДОВОЮ: 28 з 28 промахів корпусу
    deployment і `confirmed: 0 з 14`. На бланку це поля з лейблами.

    Вхід -- справжні блоки TRIP-001.docx (жіноча форма "відрядженій до") і
    справжній злитий блок TRIP-001.pdf, де PyMuPDF приклеїв рядок терміну
    відрядження до мети."""
    docx_blocks = group_blocks_into_lines([
        "відрядженій до", "м. Вінниця", "(пункти призначень)",
        "Центральна база зберігання майна (в/ч Т3011)",
        "(найменування військової частини, установи, організації)",
        "Термін відрядження “2” днів   з “21” травня 2026 р. по “22” травня 2026 р.",
        "отримання засобів індивідуального захисту", "(мета відрядження)",
    ])
    dest = find_block_before_label(docx_blocks, "пункти призначень",
                                   anchor="відрядженому до")
    assert dest[1] == "matched"
    assert strip_literal_prefix(dest[0], "відрядженому до") == "м. Вінниця"

    # PDF: термін відрядження і мета в ОДНОМУ блоці, дужкової примітки між
    # ними немає -- межу оголошує схема через value_starts_after.
    pdf_blocks = group_blocks_into_lines([
        "Термін відрядження “2” днів   з “21” травня 2026 р. по “22” травня 2026 р.\n"
        "отримання засобів індивідуального\nзахисту\n(мета відрядження)",
    ])
    boundaries = compile_value_boundaries({"value_starts_after": [r'Термін\s+відрядження']})
    assert find_block_before_label(pdf_blocks, "мета відрядження") == (
        "Термін відрядження “2” днів   з “21” травня 2026 р. по “22” травня 2026 р.\n"
        "отримання засобів індивідуального\nзахисту", "matched")
    assert find_block_before_label(pdf_blocks, "мета відрядження",
                                    boundaries=boundaries) == (
        "отримання засобів індивідуального\nзахисту", "matched")


def test_date_range_inconsistency_blocks_confirmed():
    """start > end не дає confirmed (і не пройшов би CHECK у БД-споживача)."""
    schema = {
        "template": "t", "fact_type": "leave",
        "fields": [
            {"name": "d1", "type": "date", "extraction": "regex", "db_target": "fact_date_start"},
            {"name": "d2", "type": "date", "extraction": "regex", "db_target": "fact_date_end"},
        ],
    }
    raw = {"d1": ({"day": "28", "month": "12", "year": "2026"}, "matched"),
           "d2": ({"day": "6", "month": "1", "year": "2026"}, "matched")}
    record = build_record(schema, raw, {})
    assert record["date_range_error"] is not None
    assert record["facts"][0]["confirmed"] is False


def test_unreliable_provenance_blocks_confirmed():
    """llm_split_vote і not_a_name не дають критичному полю статус resolved."""
    schema = {
        "template": "t", "fact_type": "leave",
        "fields": [{"name": "surname", "type": "text",
                    "extraction": "rank_and_name_tokenized",
                    "normalization": "nominative_case", "db_target": "person"}],
    }
    record = build_record(schema, {"surname": ("Таблиця", "llm")}, {})
    # ОНОВЛЕНО 13.08.2026: статус тепер `untagged_name`, не `not_a_name`.
    # Причина зміни: pymorphy3 ЗНАЄ слово "Таблиця" (як і "Володимир" чи
    # "Дергач") -- просто без граммеми імені. Старе очікування `not_a_name`
    # було неточним описом того, що сталося: словник знає слово, а не "не
    # знає". Суть тесту не змінилась і перевіряється двома рядками нижче:
    # значення від МОДЕЛІ, яке морфологія не підтвердила як ім'я, не дає
    # критичному полю `resolved`.
    assert record["field_provenance"]["surname"]["morphology"] == "untagged_name"
    assert record["field_provenance"]["surname"]["resolved"] is False
    assert "surname" in record["unknown_critical_fields"]

    # Друга половина того самого правила, і саме вона нова: те саме значення,
    # здобуте ДЕТЕРМІНОВАНО, підтвердження НЕ блокує. Морфологія "Володимира"
    # від "Таблиці" не відрізняє (обидва -- відоме слово в називному без
    # граммеми імені), тому розрізнювачем працює джерело: детермінований збіг
    # означає, що значення стояло в позиції ПІБ на бланку.
    # Без цього рядка TRIP-006 ("Дергач") і TRIP-010 ("Володимир") висіли в
    # черзі ручного рев'ю з ПРАВИЛЬНО витягнутим ПІБ -- через прогалину
    # розмітки словника VESUM, а не через сумнівне значення.
    matched_record = build_record(schema, {"surname": ("Володимир", "matched")}, {})
    assert matched_record["field_provenance"]["surname"]["morphology"] == "untagged_name"
    assert matched_record["field_provenance"]["surname"]["resolved"] is True
    assert "surname" not in matched_record["unknown_critical_fields"]


# --- КРАХ ---

def test_commented_config_section_does_not_crash():
    """Секція з повністю закомментованим вмістом дає None -> не має стирати дефолти."""
    base = {"llm": {"enabled": False, "n_ctx": 4096}}
    _merge(base, {"llm": None})
    assert base["llm"]["n_ctx"] == 4096


def test_malformed_dictionary_entry_does_not_crash():
    """Запис без aliases / alias-число / alias-null не валять довідник."""
    lookup = build_alias_lookup({"category": "x", "values": [
        {"code": "a", "label": "A"},                       # без aliases
        {"code": "b", "label": "B", "aliases": [30, None]},  # некоректні
        {"code": "c", "label": "C", "aliases": ["ц"]},
    ]})
    assert lookup["ц"] == ("c", "C")
    assert lookup["a"] == ("a", "A")   # код теж є шляхом до значення


def test_incomplete_domain_keyphrases_does_not_crash():
    """Неповний запис у domain_keyphrases не валить обробку всіх документів."""
    domain, scores = classify_domain_rules("текст", {"leave": {"title": ["х"]}, "broken": {}})
    assert scores == {"leave": 0, "broken": 0}


# --- ПРОГАЛИНА ---

def test_nbsp_and_double_spaces_still_match():
    """Нерозривний пробіл і подвійний пробіл не мають ламати збіг фрази.
    Було: усі три випадки давали False -> документ втрачав 5 балів -> unresolved."""
    assert phrase_in_text("додаток 30 до інструкції", "додаток 30")
    assert phrase_in_text("відпускний  квиток", "відпускний квиток")
    assert phrase_in_text("відпускний\nквиток", "відпускний квиток")


def test_stems_still_work_after_ws_normalization():
    """Свідомі стеми довідника (is_stem=True) досі ловлять словоформи; межа
    слова на початку працює однаково для стемів і звичайних фраз."""
    assert phrase_in_text("відрядження триває", "відрядж", is_stem=True)
    assert not phrase_in_text("пораду дали", "раду", is_stem=True)   # межа на початку


def test_non_stem_phrase_requires_end_boundary():
    """Без is_stem фраза НЕ має збігатись усередині довшого слова -- було:
    "додаток 28" збігався в "додаток 289"/"додаток 28а", "діб" -- у "дібрати"."""
    assert phrase_in_text("додаток 28 до інструкції", "додаток 28")
    assert not phrase_in_text("додаток 289 до інструкції", "додаток 28")
    assert not phrase_in_text("додаток 28а до інструкції", "додаток 28")
    assert phrase_in_text("10 діб відпустки", "діб")
    assert not phrase_in_text("ми маємо дібрати кандидатів", "діб")


def test_date_coverage():
    """Числові дати й дати без "р." розпізнаються."""
    assert parse_date_from_text("наказ від 15.05.2025 про") == {
        "day": "15", "month": "05", "year": "2025"}
    assert parse_date_from_text("з 15 травня 2025 до 20 травня")["month"] == "травня"
    assert parse_date_from_text("01/06/2025")["day"] == "01"


def test_dimension_field_becomes_separate_fact():
    """Поле з `dimension:` доходить до БД окремим фактом, а не лишається лише
    в additional_info (який завантажувач споживача не читає -- у таблиці facts
    немає JSON-колонки, тож посада/номер наказу мовчки губились)."""
    schema = {
        "template": "t", "fact_type": "deployment_location",
        "fields": [
            {"name": "dp", "type": "text", "extraction": "regex", "db_target": "fact_value"},
            {"name": "pos", "type": "text", "extraction": "regex",
             "db_target": "additional_info", "dimension": "position"},
        ],
    }
    record = build_record(schema, {"dp": ("Десна", "matched"),
                                   "pos": ("водій", "matched")}, {})
    # Основний факт ПЕРШИЙ: завантажувач бере facts[0] як джерело дати звання.
    assert record["facts"][0]["fact_type"] == "deployment_location"
    extra = [f for f in record["facts"] if f["fact_type"] == "position"]
    assert len(extra) == 1
    assert extra[0]["value_code"] == "водій"
    assert extra[0]["source_field"] == "pos"


def test_meta_shape_is_identical_for_every_status():
    """Дублікат і запис про збій мають ТІ САМІ ключі, що успішний запис.
    Було шість різних форм: у duplicate не було ні template, ні facts, і
    завантажувач падав KeyError на першому ж дублікаті в папці."""
    from pipeline.run import blank_meta
    required = {"id", "status", "file_hash", "source_file", "source_kind",
                "uploaded_at", "domain", "template", "identification",
                "storage_key", "reason", "review_reason", "review_queue",
                "subject", "facts", "field_provenance", "unknown_fields",
                "unknown_critical_fields", "confirmed_empty_fields",
                "not_implemented_fields", "date_range_error", "warnings"}
    assert required <= set(blank_meta())
    assert required <= set(blank_meta(status="duplicate"))
    assert blank_meta()["facts"] == [] and blank_meta()["subject"] == {}


def test_person_alias_and_completeness():
    """Аліас у тому самому порядку, що канонічне ім'я (інакше та сама людина
    створиться в базі двічі), і чесна позначка неповного ПІБ (у них
    people.last_name/first_name NOT NULL -- вставка впаде, а не пройде)."""
    from pipeline.run import _person_identity
    full = _person_identity({"surname": "БЕВЗЕНКО", "given_name": "Юстим",
                             "patronymic": "Едуардович"})
    assert full["person_alias"] == "БЕВЗЕНКО Юстим Едуардович"
    assert full["person_complete"] is True
    partial = _person_identity({"surname": "БЕВЗЕНКО", "given_name": None,
                                "patronymic": None})
    assert partial["person_alias"] == "БЕВЗЕНКО"
    assert partial["person_complete"] is False


def test_derived_facts_inherit_document_confirmation():
    """Похідний факт (поле з `dimension:`) не може бути confirmed, коли
    документ needs_review. Було: умова `criticality != "critical" or not
    unresolved` усередині гілки, що вже вимагає `not unresolved`, тобто
    тотожно True -- кожен похідний факт ішов у базу підтвердженим, а запити
    читають саме підтверджені факти."""
    schema = {
        "template": "t", "fact_type": "deployment_location",
        "fields": [
            # критичне поле лишається порожнім -> документ не підтверджений
            {"name": "dp", "type": "text", "extraction": "regex", "db_target": "fact_value"},
            {"name": "pos", "type": "text", "extraction": "regex",
             "db_target": "additional_info", "dimension": "position"},
        ],
    }
    record = build_record(schema, {"dp": (None, "no_value"),
                                   "pos": ("водій", "matched")}, {})
    assert record["facts"][0]["confirmed"] is False
    assert [f["confirmed"] for f in record["facts"][1:]] == [False]


def test_deferred_critical_field_blocks_confirmed():
    """`priority: deferred` на полі основного факту не робить його optional.
    Було: критичність писалась літералом "optional", поле не потрапляло в
    unknown_critical_fields, і документ виходив confirmed зі значенням null."""
    schema = {
        "template": "t", "fact_type": "equipment_status",
        "fields": [{"name": "row_value", "type": "text", "priority": "deferred",
                    "db_target": "fact_value"}],
    }
    record = build_record(schema, {}, {})
    assert record["field_provenance"]["row_value"]["criticality"] == "critical"
    assert "row_value" in record["unknown_critical_fields"]
    assert record["facts"][0]["confirmed"] is False


def test_unnormalized_name_blocks_confirmed():
    """Прізвище, що лишилось у відмінку джерела, не дає confirmed: споживач
    зіставляє людину за точним рядком імені, тож "БЕВЗЕНКА" створює другий
    об'єкт на ту саму людину й роздуває підрахунок."""
    from pipeline.build_record import UNRELIABLE_MORPHOLOGY
    assert "no_morphology" in UNRELIABLE_MORPHOLOGY
    assert "ambiguous_case" in UNRELIABLE_MORPHOLOGY


def test_nominative_hint_is_evidence_not_absence():
    """Підказка "решта ПІБ у називному" -- це ДОКАЗ називного, не
    невизначеність. Інакше кожне жіноче прізвище на -ова блокувало б
    підтвердження документа."""
    value, status = normalize_nominative_case("ПЕТРОВА", role="surname",
                                              case_hint="nominative")
    assert (value, status) == ("ПЕТРОВА", "already_nominative")
    # без жодної підказки -- справді неоднозначно
    assert normalize_nominative_case("ПЕТРОВА", role="surname")[1] == "ambiguous_case"


def test_llm_not_asked_when_no_anchor_matched():
    """LLM не питають про шаблон, коли не збігся ЖОДЕН анкор. Було: документ з
    балом 0 (книга обліку техніки, рапорт -- будь-що без схеми) віддавався
    моделі, обмеженій переліком чужих шаблонів, і обраний нею шаблон приймався
    як остаточний -- чужа схема давала confirmed факт, якого в документі
    немає."""
    from pipeline.identification import identify_template
    schemas = [{"template": "leave_ticket", "fields": [],
                "identification": {"title": ["відпускний квиток"],
                                    "anchors": ["додаток 30"]}}]
    calls = []

    def fake_llm(prompt, choices):
        calls.append(choices)
        return "leave_ticket"

    result = identify_template("книга обліку техніки автомобільної",
                               schemas, llm_choose=fake_llm)
    assert calls == [], "LLM не має викликатись при нульовому балі"
    assert result["schema"] is None
    assert result["reason"] == "below_llm_floor"


def test_source_instruction_is_not_mistaken_for_a_blank():
    """Документ, що МІСТИТЬ бланки, не є жодним з них.

    Живе репро від команди БД: data/samples/normative/інструкція_діловодство.docx
    (402898 символів) чесно набирає бал 9 за deployment_certificate і 9 за
    leave_ticket -- бо містить і Додаток 28, і Додаток 30 разом із їхніми
    заголовками. Раніше нічия віддавалась LLM, і та впевнено називала це
    "посвідченням про відрядження". Причина НЕ в permissive-матчингу:
    phrase_in_text уже перевіряє межі слова, а збіглися повні точні
    заголовки. Причина в тому, що інструкція -- джерело всіх бланків.
    """
    from pipeline.identification import identify_template
    schemas = [
        {"template": "deployment_certificate", "fields": [],
         "identification": {"title": ["посвідчення про відрядження"],
                             "anchors": ["додаток 28", "мета відрядження"],
                             "min_score": 5}},
        {"template": "leave_ticket", "fields": [],
         "identification": {"title": ["відпускний квиток"],
                             "anchors": ["додаток 30", "дата повернення"],
                             "min_score": 5}},
    ]
    text = ("Інструкція з діловодства у Збройних Силах України. "
            "Додаток 28. ПОСВІДЧЕННЯ ПРО ВІДРЯДЖЕННЯ. Мета відрядження. "
            "Додаток 30. ВІДПУСКНИЙ КВИТОК. Дата повернення.")
    calls = []
    result = identify_template(text, schemas,
                               llm_choose=lambda p, c: calls.append(c) or "leave_ticket")
    assert calls == [], "нічию/контейнер не можна віддавати моделі"
    assert result["schema"] is None
    assert result["reason"].startswith("multiple_templates_matched")


def test_schema_validation_catches_yaml_mistakes():
    """Описка в новому YAML -- явна помилка до обробки документів, а не тихий
    no_value і не KeyError посеред process_file (там _persist ще не викликався,
    тому документ не отримував ні запису у сховище, ні рядка в індексі)."""
    from pipeline.identification import validate_schema
    problems = validate_schema({
        "template": "broken", "fact_type": "leave",
        "fields": [
            {"name": "a", "extraction": "bloc_before_label", "label_before": "x"},
            {"name": "b", "extraction": "regex", "db_target": "fact_value",
             "regex_variants": [{"pattern": "(?P<value>["}]},
            {"name": "c", "extraction": "llm", "db_target": "equipment_object"},
        ],
    }, known_fact_types={"leave"})
    messages = " | ".join(m for _, m in problems)
    assert "невідомий режим extraction" in messages
    assert "невалідний regex" in messages
    assert "невідомий db_target" in messages
    # робочі схеми проєкту помилок не мають
    from pipeline.identification import load_schemas
    from pipeline.run import load_fact_types
    fact_types = load_fact_types(os.path.join(_PROJECT_ROOT, "dictionaries"))
    for schema in load_schemas(os.path.join(_PROJECT_ROOT, "schemas")):
        errors = [m for sev, m in validate_schema(schema, fact_types) if sev == "error"]
        assert errors == [], errors


def test_field_names_are_free_roles_are_declared():
    """Схема може називати поля як завгодно -- роль оголошується через `part:`.
    Було: код шукав літеральні імена `rank`/`surname`/`given_name`/`patronymic`,
    тому схема з `applicant_surname` не отримувала ні значення, ні правильної
    причини, а морфологія втрачала граммему-обмежувач."""
    from pipeline.extraction.extract import field_part
    schema = {
        "template": "free", "fact_type": "leave",
        "fields": [
            {"name": "prizvyshche", "part": "surname", "type": "text",
             "extraction": "rank_and_name_tokenized",
             "label_before": "звання", "normalization": "nominative_case",
             "db_target": "person"},
        ],
    }
    assert field_part(schema["fields"][0]) == "surname"
    record = build_record(schema, {"prizvyshche": ("БЕВЗЕНКА", "matched")}, {})
    # ключ у subject -- РОЛЬ, бо контракт з БД мусить лишатись стабільним
    assert record["subject"]["surname"] == "БЕВЗЕНКО"
    assert record["field_provenance"]["prizvyshche"]["morphology"] == "normalized"


def test_second_person_does_not_steal_first_persons_name():
    """Дві особи в документі -> два незалежні розбори. Було: єдиний кеш на
    весь документ, не ключований лейблом, тому поля другої особи отримували
    ПІБ першої -- не null, а тихо чуже значення з провенансом matched."""
    from pipeline.extraction.extract import resolve_name_groups, group_blocks_into_lines
    schema = {"fields": [
        {"name": "a_rank", "part": "rank", "group": "applicant", "type": "category",
         "category": "military_rank", "extraction": "rank_and_name_tokenized",
         "label_before": "звання заявника"},
        {"name": "a_surname", "part": "surname", "group": "applicant",
         "extraction": "rank_and_name_tokenized"},
        {"name": "c_rank", "part": "rank", "group": "commander", "type": "category",
         "category": "military_rank", "extraction": "rank_and_name_tokenized",
         "label_before": "звання командира"},
        {"name": "c_surname", "part": "surname", "group": "commander",
         "extraction": "rank_and_name_tokenized"},
    ]}
    blocks = group_blocks_into_lines([
        "рядовий ІВАНЕНКО Петро\n(звання заявника)",
        "підполковник КОВАЛЬЧУК Дмитро\n(звання командира)",
    ])
    groups = resolve_name_groups(schema, blocks, set(),
                                 {"military_rank": RANK_LOOKUP})
    assert groups["applicant"][1]["surname"] == "ІВАНЕНКО"
    assert groups["commander"][1]["surname"] == "КОВАЛЬЧУК"
    assert groups["commander"][0]["code"] == "lt_colonel"


def test_primary_subject_is_first_group_in_schema():
    """Основна особа -- та, чиє поле в схемі перше. Було: перевірка на
    літеральну назву групи, тому схема з явними групами лишала subject
    порожнім і основна особа не доходила до БД взагалі."""
    schema = {
        "template": "two", "fact_type": "leave",
        "fields": [
            {"name": "a_surname", "part": "surname", "group": "applicant",
             "extraction": "rank_and_name_tokenized", "label_before": "l1",
             "db_target": "person"},
            {"name": "c_surname", "part": "surname", "group": "commander",
             "extraction": "rank_and_name_tokenized", "label_before": "l2",
             "db_target": "person"},
        ],
    }
    record = build_record(schema, {"a_surname": ("ІВАНЕНКО", "matched"),
                                   "c_surname": ("КОВАЛЬЧУК", "matched")}, {})
    assert record["subject"]["surname"] == "ІВАНЕНКО"
    assert record["extra_subjects"]["commander"]["surname"] == "КОВАЛЬЧУК"
    # друга особа не перетирає першу й не губиться
    assert record["subject"].get("surname") != "КОВАЛЬЧУК"


def test_validator_rejects_unknown_part_and_duplicate_role():
    """Невідома роль і дві однакові ролі в одній групі -- явні помилки."""
    from pipeline.identification import validate_schema
    problems = validate_schema({
        "template": "bad", "fact_type": "leave",
        "fields": [
            {"name": "x", "part": "nickname", "extraction": "rank_and_name_tokenized",
             "label_before": "l"},
            {"name": "s1", "part": "surname", "extraction": "rank_and_name_tokenized"},
            {"name": "s2", "part": "surname", "extraction": "rank_and_name_tokenized"},
        ],
    }, known_fact_types={"leave"})
    messages = " | ".join(m for _, m in problems)
    assert "part 'nickname' невідома" in messages
    assert "оголошені двічі в одній групі" in messages


def test_placeholder_detection():
    assert is_placeholder("________________")
    assert is_placeholder("не заповнено")
    assert is_placeholder("[REDACTED]")
    assert not is_placeholder("БЕВЗЕНКО")


def test_homoglyph_dates_recovered():
    """Клас `ocr_noise` -- не шум розпізнавання, а гомогліфи, вписані в сам
    документ (заміряно: ті самі літери є в текстовому шарі .docx, де OCR не
    відбувається). TRIP-012 давав 0 з трьох дат."""
    from pipeline.normalization.normalize import parse_date_from_text
    assert parse_date_from_text("О7.О5.2О2б") == {
        "day": "07", "month": "05", "year": "2026"}
    # День лише з літер ("ІО" = 10) -- ловиться тільки завдяки лапкам, які
    # шаблон вимагає навколо дня.
    assert parse_date_from_text('з "ІО" травня 202б р.') == {
        "day": "10", "month": "травня", "year": "2026"}


def test_homoglyph_fix_does_not_eat_ordinary_words():
    """Найнебезпечніший бік правила: "з" -- найчастіший прийменник у датах,
    і глобальна заміна перетворила б його на "3", а "жовтня" на "ж0втня"."""
    from pipeline.normalization.normalize import (
        parse_date_from_text, fix_numeric_homoglyphs)
    assert parse_date_from_text('"01" жовтня 2026 р.') == {
        "day": "01", "month": "жовтня", "year": "2026"}
    assert parse_date_from_text("з 15 травня 2025 до 20 травня 2025") == {
        "day": "15", "month": "травня", "year": "2025"}
    assert parse_date_from_text("зобов'язаний прибути") is None
    assert parse_date_from_text("після закінчення строку") is None
    # Токенне правило вимагає СПРАВЖНЬОЇ цифри в токені.
    assert fix_numeric_homoglyphs("з") == "з"
    assert fix_numeric_homoglyphs("об") == "об"
    assert fix_numeric_homoglyphs("2О2б") == "2026"


def test_homoglyph_tolerant_pattern_expansion():
    """`\\d` у схемному регексі -- це декларація "тут число", тому лише її
    безпечно розширювати. Усередині символьного класу -- не можна: вкладені
    дужки зламали б клас."""
    import re
    from pipeline.normalization.normalize import (
        homoglyph_tolerant_pattern, fix_declared_numeric)
    pattern = r'№\s*(?P<value>[\w/\-]+)\s+від\s+\d{1,2}\.\d{1,2}\.\d{4}'
    expanded, was_expanded = homoglyph_tolerant_pattern(pattern)
    assert was_expanded
    m = re.search(expanded, "№ 25О    від О7.О5.2О2б".lower())
    assert m and fix_declared_numeric(m.group("value")) == "250"
    # Патерн без \d не оголошував числа -- захоплене чіпати не можна.
    assert homoglyph_tolerant_pattern(r'abc(?P<v>[а-я]+)')[1] is False
    # \d усередині [...] лишається як є.
    assert homoglyph_tolerant_pattern(r'[\d\w]+')[0] == r'[\d\w]+'


def test_matched_from_layout_is_validated():
    """`matched` від block_before_label приймався БЕЗУМОВНО і не потрапляв у
    global_gaps, тобто LLM-фолбек до нього не доходив ніколи.

    Вхід -- справжні блоки LEAVE-011.pdf, де поле `actual_return_date`
    (порожнє на бланку) отримувало кандидатом НАЗВУ ЧАСТИНИ з друкованою
    приміткою під нею, і лише випадкова невдача нормалізації дати рятувала
    запис від назви частини в полі дати."""
    schema = {
        "template": "leave_ticket", "fact_type": "leave",
        "identification": {"title": ["відпускний квиток"], "anchors": ["додаток 30"]},
        "fields": [
            {"name": "unit_to_report", "type": "object_ref",
             "extraction": "block_before_label",
             "label_before": "найменування військової частини або населеного пункту"},
            {"name": "actual_return_date", "type": "date",
             "extraction": "block_before_label", "label_before": "дата повернення",
             "normalization": "iso_date"},
        ],
    }
    heads = schema_label_heads(schema)
    date_field = schema["fields"][1]
    # 1. Кандидат -- назва частини з приміткою: у ньому стоїть лейбл цього ж
    #    бланка, тобто взято верстку, не значення.
    assert validate_block_value(
        date_field,
        "військова частина А0000\n(найменування військової частини або населеного пункту)",
        heads) == (None, "printed_label_in_value")
    # 2. Порожній слот дати на бланку -- не дата.
    assert validate_block_value(date_field, "“”  20 р.", heads) == (None, "type_mismatch")
    # 3. Справжня дата (LEAVE-013) проходить без змін.
    assert validate_block_value(date_field, "“22” травня 2026 р.", heads) == (
        "“22” травня 2026 р.", "matched")
    # 4. Справжнє значення текстового поля з дужками НЕ відхиляється
    #    (TRIP-004: "Центральна база зберігання майна (в/ч Т3011)").
    text_field = {"name": "destination_org", "type": "object_ref"}
    assert validate_block_value(
        text_field, "Центральна база зберігання майна (в/ч Т3011)", heads)[1] == "matched"


def test_llm_value_absent_from_document_is_a_gap():
    """LEAVE-011: модель віддала `днів = 17`, підрядка "17" у документі немає
    взагалі, поле на бланку порожнє -- і це НЕ потрапляло в прогалини, на
    відміну від дат, які модель чесно лишила None.

    Текст -- справжній фрагмент LEAVE-011 (кількість днів на бланку не
    надрукована ні цифрою, ні прописом)."""
    document = ("Відпускний квиток\n№ 143    від 07.05.2026\n"
                "звільнений\nвідпустка у зв’язку з навчанням\nм. Хмельницький\n"
                "терміном на\n(кількість днів прописом)\nз “”  20 р.  по “”  20 р.\n"
                "Для проїзду видано військові перевізні документи за №\n6114/26")
    days = {"name": "duration_days", "type": "number"}
    assert ground_llm_value(days, 17, document) == (None, "ungrounded_llm_value")
    # Число, надруковане в документі ЦИФРОЮ, проходить.
    assert ground_llm_value(days, 143, document) == (143, None)
    # Число, надруковане ЛИШЕ ПРОПИСОМ (реальний випадок бланка: "тринадцять"),
    # теж мусить проходити -- інакше правило відхиляло б правильні значення.
    assert 13 in attested_numbers("терміном на\nтринадцять\n(кількість днів прописом)")
    assert ground_llm_value(days, 13, "терміном на тринадцять днів") == (13, None)
    # Текстове поле: значення мусить бути підрядком документа.
    place = {"name": "destination_place", "type": "text"}
    assert ground_llm_value(place, "м. Хмельницький", document) == ("м. Хмельницький", None)
    assert ground_llm_value(place, "м. Одеса", document) == (None, "ungrounded_llm_value")


def test_duration_without_dates_is_marked():
    """Найдорожче в LEAVE-011 не сама галюцинація, а те, що запис "тривалість
    без дат" ішов у БД без ЖОДНОГО маркера. Значення з документа тут узяті
    справжні (номер 143, дата видачі 07.05.2026), дати відпустки на бланку
    порожні."""
    schema = {
        "template": "leave_ticket", "fact_type": "leave",
        "fields": [
            {"name": "leave_type_and_destination", "type": "text",
             "extraction": "block_before_label", "label_before": "x",
             "db_target": "fact_value"},
            {"name": "leave_start_date", "type": "date", "extraction": "regex",
             "db_target": "fact_date_start"},
            {"name": "leave_end_date_planned", "type": "date", "extraction": "regex",
             "db_target": "fact_date_end"},
            {"name": "duration_days", "type": "number", "extraction": "regex",
             "dimension": "leave_days", "db_target": "additional_info",
             "consistency": {"rule": "days_span_inclusive",
                             "start": "leave_start_date",
                             "end": "leave_end_date_planned"}},
        ],
    }
    no_dates = {
        "leave_type_and_destination": ("відпустка у зв’язку з навчанням", "matched"),
        "leave_start_date": (None, "no_value"),
        "leave_end_date_planned": (None, "no_value"),
        "duration_days": (11, "llm"),
    }
    record = build_record(schema, no_dates, {})
    assert record["consistency_problems"]["duration_days"] == \
        "unverifiable_dependency: leave_start_date"
    assert record["field_provenance"]["duration_days"]["resolved"] is False
    assert "duration_days" in record["unknown_fields"]
    # Похідний факт з непідтвердженого значення в базу не йде.
    assert [f for f in record["facts"] if f["fact_type"] == "leave_days"] == []

    # Дати є, але кількість днів їм суперечить -> consistency_error.
    wrong = dict(no_dates,
                 leave_start_date=({"day": "10", "month": "05", "year": "2026"}, "matched"),
                 leave_end_date_planned=({"day": "22", "month": "05", "year": "2026"}, "matched"),
                 duration_days=(17, "llm"))
    record = build_record(schema, wrong, {})
    assert record["consistency_problems"]["duration_days"] == "consistency_error: 17 != 13"

    # Правильне значення (LEAVE-001: з 10 по 22 травня = 13 днів) проходить,
    # і факт leave_days доходить до БД.
    ok = dict(wrong, duration_days=(13, "matched"))
    record = build_record(schema, ok, {})
    assert record["consistency_problems"] == {}
    assert [f["value_code"] for f in record["facts"] if f["fact_type"] == "leave_days"] == ["13"]


def test_cancelling_document_is_extractable_but_pair_is_not():
    """Три пари еталона: ЧИННИЙ документ пари справді несе ознаку скасування,
    а СКАСОВАНИЙ -- ні (він не може знати, що його скасують). Тому наш бік може
    віддати лише ознаку, а закрити старий факт без таблиці зв'язків
    документ->документ (architecture-proposal.md розд. 2 п.4) неможливо.

    Рядки -- справжні (`надруковано.LEAVE_TYPE` / `PURPOSE` еталона)."""
    leave = _schema_by_template("leave_ticket")
    trip = _schema_by_template("deployment_certificate")

    def links(schema, text):
        raw = {f["name"]: extract_field_regex(f, text) for f in schema["fields"]
               if f.get("extraction") == "regex"}
        return build_record(schema, raw, {})["document_links"]

    # LEAVE-016 -- є НОМЕР скасованого квитка.
    got = links(leave, "відпустка за сімейними обставинами "
                       "(виданий замість анульованого квитка № 157)")
    assert "157" in [x["target_document_number"] for x in got], got
    # LEAVE-014 -- позначка є, номера НЕМА: пару шукають за особою й датами.
    got = links(leave, "відпустка для лікування після хвороби згідно з висновком "
                       "ВЛК (перервана, відкликаний з відпустки)")
    assert got and all(x["target_document_number"] is None for x in got), got
    # TRIP-014 -- є НОМЕР.
    got = links(trip, "проходження курсу підвищення кваліфікації "
                      "(переоформлено замість посвідчення № 254)")
    assert "254" in [x["target_document_number"] for x in got], got
    # СКАСОВАНИЙ документ пари (LEAVE-013 / TRIP-013) не має жодної ознаки.
    assert links(leave, "відпустка для лікування після хвороби згідно з "
                        "висновком ВЛК") == []
    assert links(trip, "проходження курсу підвищення кваліфікації") == []


def test_basis_order_regex_matches_real_blank():
    """`basis_order_date`/`basis_order_number` вимагали літерально "наказ від",
    а бланк друкує "наказ КОМАНДИРА ВІЙСЬКОВОЇ ЧАСТИНИ А0000 від ...". Обидва
    поля мають `dimension:`, тобто йдуть у БД окремими фактами, і обидва давали
    0/14 -- невидимо, бо їх немає в data/eval/field-mapping.yaml.

    Рядки -- справжні `надруковано.ORDER_BASIS` (TRIP-001 і TRIP-012 з
    гомоглифами)."""
    trip = _schema_by_template("deployment_certificate")
    by_name = {f["name"]: f for f in trip["fields"]}
    line = ("Підстава відрядження: наказ командира військової частини А0000 "
            "від 19.05.2026 № 345")
    assert extract_field_regex(by_name["basis_order_date"], line)[0] == \
        {"day": "19", "month": "05", "year": "2026"}
    assert extract_field_regex(by_name["basis_order_number"], line)[0] == "345"
    noisy = ("Підстава відрядження: наказ командира військової частини АОООО "
             "від Об.О5.2О2б № З9б")
    assert extract_field_regex(by_name["basis_order_date"], noisy)[0] == \
        {"day": "06", "month": "05", "year": "2026"}


def test_placeholder_tokens_are_configurable_from_schema():
    """`немає`/`відсутній` -- змістовні значення в книзі обліку техніки
    ("несправності: немає" = техніка справна), і ми робили з них null. Прибрати
    глобально не можна: на ПОРОЖНЬОМУ бланку відпускного це правило працює
    правильно."""
    # Дефолт незмінний.
    assert is_placeholder("немає") and is_placeholder("відсутній")
    equipment_field = {"name": "faults", "type": "text",
                       "placeholder_tokens_except": ["немає", "відсутній"]}
    tokens = field_placeholder_tokens(equipment_field)
    assert not is_placeholder("немає", tokens)
    assert normalize_field(equipment_field, "немає", {}) == ("немає", False)
    # Решта переліку для цього ж поля лишається чинною...
    assert is_placeholder("не заповнено", tokens)
    # ...і графічний маркер порожнього бланка теж (він не налаштовується).
    assert is_placeholder("____________", tokens)
    # Повна заміна переліку.
    only = {"name": "x", "type": "text", "placeholder_tokens": ["н/д"]}
    assert is_placeholder("н/д", field_placeholder_tokens(only))
    assert not is_placeholder("немає", field_placeholder_tokens(only))


def test_empty_blank_is_still_empty_after_token_change():
    """Порожній бланк відпускного мусить лишатись порожнім: саме на ньому
    перелік токенів працює правильно. Рядки -- справжні з
    data/samples/leave/відпускний_шаблон.docx і з LEAVE-011 (вада
    empty_fields)."""
    leave = _schema_by_template("leave_ticket")
    by_name = {f["name"]: f for f in leave["fields"]}
    for blank in ("____________", "—", "«»", "  --  "):
        assert is_placeholder(blank), blank
    # НОВЕ (13.08.2026): коментар до _BLANK_FILL_RE (normalize.py) обіцяє, що
    # правило ловить і «____» ____ 20___ р.» -- не ловить: у рядку лишаються
    # надруковані "20" і "р.", а клас символів патерна цифр і літер не містить.
    # Тобто НЕ ЗАПОВНЕНИЙ слот дати на цьому бланку placeholder-ом не
    # вважається взагалі, і від хибного значення його рятує лише те, що дата
    # з нього не парситься. Фіксуємо ФАКТИЧНУ поведінку, щоб розбіжність не
    # читалась як працююча перевірка.
    for not_caught in ("«____» ____ 20___ р.", "“”  20 р."):
        assert not is_placeholder(not_caught), not_caught
        assert normalize_field({"name": "d", "type": "date"}, not_caught, {}) == (None, False)
    # Порожній слот дати на бланку -> None, не дата.
    assert normalize_field(by_name["actual_return_date"], "“”  20 р.", {}) == (None, False)
    # Сентинел лишається сентинелом (не плутається з placeholder) -- LEAVE-015.
    assert normalize_field(by_name["travel_document_number"], "не видавались", {}) == (None, True)
    # ...а реальний номер лишається значенням -- LEAVE-016.
    assert normalize_field(by_name["travel_document_number"], "7367/26", {}) == ("7367/26", False)


def test_procedural_domain_wins_over_topical_score():
    """Документ ПРАВИЛ не класифікується як документ про тему, яку він описує.

    Заміряно на data/samples/normative/інструкція_діловодство.docx (402898
    символів): бали leave 8, equipment 8, staffing 9, deployment 7 --
    інструкція МІСТИТЬ усі бланки й згадує всі теми, тому тематичний
    переможець визначався шумом (до появи домену normative вона була
    `equipment`, після -- `staffing`, і жоден не є правдою). Тепер `normative`
    оголошений як `kind: procedural` і перевіряється окремо: це інша ВІСЬ
    (запис проти правил), а не сильніший конкурент за балами.
    """
    domains = {
        "normative": {"kind": "procedural", "title": ["інструкція з діловодства"]},
        # тематичний домен зі свідомо ВИЩИМ балом, щоб перевірялась саме
        # окремість перевірки, а не те, чия вага більша
        "staffing": {"title": ["обліку особового складу"],
                     "body": ["особовий склад", "штат", "посада"]},
    }
    text = ("Інструкція з діловодства у Збройних Силах України. "
            "Обліку особового складу. Особовий склад, штат, посада.")
    assert classify_domain_rules(text, domains)[0] == "normative"
    # і навпаки: бланк, що лише ПОСИЛАЄТЬСЯ на інструкцію в родовому відмінку
    # ("до Інструкції з діловодства"), процедурним не стає
    blank = "Додаток 30 до Інструкції з діловодства у Збройних Силах України"
    assert classify_domain_rules(blank, domains)[0] != "normative"


# --- ВИД СУБ'ЄКТА документа (subject_kind) --------------------------------
# Порядок рівнів і кожна межа окремо. Це не "покриття нового модуля": кожен
# тест нижче фіксує рішення, яке легко зняти рефакторингом і не помітити --
# наслідок побачили б лише в чужій базі (фантомний об'єкт у `objects`, звідки
# завантажувач не має шляху видалення).


def test_subject_kind_schema_beats_domain_map():
    """Схема СТАРША за мапінг домену: вона прямо оголошує, що описує, а домен
    -- це підрахунок ключових фраз, здатний із оголошенням не збігтися.
    Найлегше зламати саме цей порядок (порахувати домен «про всяк випадок»),
    тому мапінг тут навмисно суперечить схемі."""
    from pipeline.subject_kind import resolve_subject_kind
    got = resolve_subject_kind(
        schema={"template": "t", "domain": "leave", "subject_kind": "person"},
        domain="leave", domains={"leave": {"subject_kind": "equipment"}})
    assert got == {"kind": "person", "source": "schema", "reason": None}


def test_subject_kind_from_domain_map_when_no_schema():
    from pipeline.subject_kind import resolve_subject_kind
    got = resolve_subject_kind(schema=None, domain="equipment",
                               domains={"equipment": {"subject_kind": "equipment"}})
    assert got["kind"] == "equipment" and got["source"] == "domain_map"


def test_normative_domain_has_no_subject_and_creates_no_object():
    """Причина, чому мапінгу потрібне значення "none". Реальний документ:
    Інструкція з діловодства -- домен `normative`, суб'єкта немає взагалі.
    Без "none" мапінг вигадав би їй вид, і в реєстрі назавжди осів би
    фантомний об'єкт (шляху видалення в завантажувачі БД немає).
    Мапінг читається з YAML, не з коду -- тому тест читає САМ ФАЙЛ."""
    from pipeline.classification.classify import load_domain_keyphrases
    from pipeline.subject_kind import creates_object, resolve_subject_kind
    domains = load_domain_keyphrases(
        os.path.join(_PROJECT_ROOT, "dictionaries", "domain_keyphrases.yaml"))
    got = resolve_subject_kind(schema=None, domain="normative", domains=domains)
    assert got["kind"] == "none"
    assert creates_object(got["kind"]) is False
    # І навпаки: домен з реальним видом об'єкт створює.
    assert creates_object(resolve_subject_kind(
        schema=None, domain="leave", domains=domains)["kind"]) is True


def test_every_domain_and_schema_declares_subject_kind():
    """Мапінг мусить покривати ВСІ домени, а обидві робочі схеми -- оголошувати
    вид явно. Інакше документ отримує 'unknown', об'єкт не створюється, і це
    видно лише в черзі рев'ю через тиждень."""
    from pipeline.classification.classify import load_domain_keyphrases
    from pipeline.identification import load_schemas
    from pipeline.subject_kind import (
        DECLARABLE_SUBJECT_KINDS, domain_subject_kind_problems)
    domains = load_domain_keyphrases(
        os.path.join(_PROJECT_ROOT, "dictionaries", "domain_keyphrases.yaml"))
    assert domain_subject_kind_problems(domains) == []
    for schema in load_schemas(os.path.join(_PROJECT_ROOT, "schemas")):
        assert schema.get("subject_kind") in DECLARABLE_SUBJECT_KINDS, schema["template"]


def test_subject_kind_unknown_when_domain_has_no_mapping():
    """Домен визначено, мапінгу для нього НЕМА -> 'unknown' з причиною, і LLM
    НЕ питають: на це питання мусить відповідати рядок у YAML, а відповідь
    моделі лише замаскувала б прогалину в довіднику -- причому тихо, бо вид
    виглядав би визначеним."""
    from pipeline.subject_kind import resolve_subject_kind
    calls = []

    def never(prompt, choices):
        calls.append(prompt)
        return "person"

    got = resolve_subject_kind(schema=None, domain="staffing",
                               domains={"staffing": {"title": []}},
                               llm_choose=never, text="текст")
    assert got["kind"] == "unknown"
    assert got["reason"] == "domain_without_subject_kind:staffing"
    assert calls == []


def test_subject_kind_llm_branch_is_closed_enum_with_unknown():
    """Рівень 3 (домену немає): вибір ЗАКРИТИЙ і містить 'unknown'. Вільна
    відповідь створила б вид, якого в `object_kinds` немає, і смітила б у
    `objects.kind_id` (NOT NULL); без 'unknown' модель, обмежена лише
    реальними видами, змушена вибрати щось навіть коли суб'єкта немає."""
    from pipeline.subject_kind import (
        KNOWN_SUBJECT_KINDS, LLM_SUBJECT_CHOICES, resolve_subject_kind)
    seen = {}

    def fake_choose(prompt, choices):
        seen["choices"] = choices
        return "equipment"

    got = resolve_subject_kind(schema=None, domain=None, domains={},
                              llm_choose=fake_choose, text="книга обліку техніки")
    assert got == {"kind": "equipment", "source": "llm", "reason": None}
    assert seen["choices"] == list(LLM_SUBJECT_CHOICES)
    assert "unknown" in seen["choices"] and "none" in seen["choices"]
    assert set(KNOWN_SUBJECT_KINDS) <= set(seen["choices"])
    # Модель поза переліком (grammar збоїв не виключає) -> не пускаємо у вихід.
    assert resolve_subject_kind(schema=None, domain=None, domains={},
                                llm_choose=lambda p, c: "будинок",
                                text="x")["kind"] == "unknown"


def test_subject_kind_no_llm_no_domain_gives_unknown_not_crash():
    """Модель не підключена (за замовчуванням) -- вихід існує завжди."""
    from pipeline.subject_kind import resolve_subject_kind
    got = resolve_subject_kind(schema=None, domain=None, domains={})
    assert got["kind"] == "unknown" and got["reason"] == "no_schema_no_domain"


def test_validator_rejects_unknown_subject_kind():
    """Так само, як невідомі part / db_target / type / dimension: значення
    відповідає рядку чужої таблиці `object_kinds`, а `objects.kind_id` --
    NOT NULL, тож опечатка інакше не проявилась би НІДЕ на нашому боці.
    'unknown' оголосити НЕ можна: це не оголошення, а його відсутність."""
    from pipeline.identification import validate_schema
    base = {"template": "bad", "fact_type": "leave",
            "fields": [{"name": "v", "type": "text", "db_target": "fact_value",
                        "extraction": "regex", "regex_variants": [{"pattern": "x"}]}]}
    for bad in ("persons", "unknown", "human"):
        problems = validate_schema(dict(base, subject_kind=bad),
                                   known_fact_types={"leave"})
        assert any(sev == "error" and f"subject_kind '{bad}'" in msg
                   for sev, msg in problems), bad
    # Відсутність -- ПОПЕРЕДЖЕННЯ, не помилка: помилка виключила б схему з
    # набору (run.py:build_resources), тобто всі документи цього шаблону пішли
    # б в unresolved через один відсутній рядок YAML, тоді як робочий фолбек
    # (мапінг домену) є.
    problems = validate_schema(base, known_fact_types={"leave"})
    assert any(sev == "warning" and "немає subject_kind" in msg
               for sev, msg in problems)
    assert not any(sev == "error" and "subject_kind" in msg for sev, msg in problems)


def test_dictionary_validator_rejects_unknown_subject_kind():
    """Те саме для мапінгу домену: сміттєве значення в YAML інакше пройшло б
    у вихід рядком, бо на нашому боці воно не ламає нічого."""
    from pipeline.subject_kind import domain_subject_kind_problems
    problems = domain_subject_kind_problems({"d": {"subject_kind": "людина"}})
    assert any(sev == "error" and "людина" in msg for sev, msg in problems)
    # ...і воно НЕ доходить до виходу навіть якщо валідатор проігнорували.
    from pipeline.subject_kind import resolve_subject_kind
    got = resolve_subject_kind(schema=None, domain="d",
                               domains={"d": {"subject_kind": "людина"}})
    assert got["kind"] == "unknown"
    assert got["reason"] == "invalid_subject_kind_in_dictionary:людина"


def test_subject_kind_gate_is_separate_axis_from_person_complete():
    """`creates_object` і `person_complete` -- РІЗНІ питання, і кон'юнкцією їх
    робити не можна: у техніки прізвища немає за визначенням, тому
    `person_complete: false` для неї норма, а не перешкода створенню об'єкта.
    Завантажувач БД мусить читати обидва ключі окремо."""
    from pipeline.run import _person_identity
    from pipeline.subject_kind import creates_object
    equipment_subject = _person_identity({})
    assert equipment_subject["person_complete"] is False
    assert creates_object("equipment") is True
    assert creates_object(None) is False


def test_blank_meta_always_carries_subject_kind_keys():
    """Форма шапки однакова для ВСІХ статусів: у дублікаті й у записі про
    нечитабельний файл ключі мусять бути, інакше завантажувач падає KeyError
    (це вже траплялось -- саме тому blank_meta існує). null тут означає "до
    питання не дійшли", і це НЕ те саме, що 'unknown' ("питали, відповіді
    немає")."""
    from pipeline.run import blank_meta
    meta = blank_meta(status="duplicate")
    for key in ("subject_kind", "subject_kind_source", "subject_kind_reason",
                "create_subject_object"):
        assert key in meta, key
    assert meta["subject_kind"] is None
    assert meta["create_subject_object"] is False


def test_procedural_fallback_needs_both_length_and_phrases():
    """Нормативний документ, чийого ЗАГОЛОВКА ми не знаємо, ловиться другим
    шляхом -- але лише коли довжина Й кілька різних фраз разом.

    Довжина сама по собі хибна як ознака: книга штатно-посадового обліку теж
    довга й НЕ нормативна. Тому тест перевіряє обидві межі -- і що довгий
    документ без процедурних фраз процедурним НЕ стає.
    """
    domains = {
        "normative": {"kind": "procedural", "title": ["інструкція з діловодства"],
                      "body": ["ці правила", "набирає чинності", "визначає порядок"],
                      "procedural_fallback": {"min_body_hits": 2, "min_chars": 20000}},
        "staffing": {"title": ["обліку особового складу"], "body": ["особовий склад"]},
    }
    padding = "текст " * 5000            # ~30000 символів
    # дві РІЗНІ процедурні фрази + довжина -> процедурний
    assert classify_domain_rules(
        "Ці правила набирає чинності. " + padding, domains)[0] == "normative"
    # ОДНА фраза при тій самій довжині -> ні (випадковий збіг не дає вироку)
    assert classify_domain_rules("Ці правила. " + padding, domains)[0] != "normative"
    # довгий документ БЕЗ процедурних фраз -> ні (це і є книга обліку)
    assert classify_domain_rules(
        "Обліку особового складу. Особовий склад. " + padding, domains)[0] == "staffing"
    # ті самі дві фрази, але документ КОРОТКИЙ -> ні (витяг, не звід правил)
    assert classify_domain_rules("Ці правила набирає чинності.", domains)[0] != "normative"


def _run_all():
    failures = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  OK   {name}")
            except AssertionError as exc:
                failures.append((name, exc))
                print(f"  FAIL {name}: {exc}")
            except Exception as exc:
                failures.append((name, exc))
                print(f"  ERR  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{'ПРОВАЛЕНО: ' + str(len(failures)) if failures else 'усі тести пройшли'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
