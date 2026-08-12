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
    find_block_before_label,
    group_blocks_into_lines,
    majority_vote,
    parse_rank_and_name,
)
from pipeline.config import _merge
from pipeline.normalization.normalize import (
    build_alias_lookup,
    detect_name_case,
    is_placeholder,
    normalize_date,
    normalize_nominative_case,
    normalize_null_if_sentinel,
    parse_date_from_text,
)

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
    assert record["field_provenance"]["surname"]["morphology"] == "not_a_name"
    assert record["field_provenance"]["surname"]["resolved"] is False
    assert "surname" in record["unknown_critical_fields"]


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
