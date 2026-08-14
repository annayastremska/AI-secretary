# Тести виправлень за рев'ю 14.08.2026 (docs/review-2026-08-14/b2-verdicts.md).
#
# Кожен тест ловить САМЕ ТИШУ, задокументовану у знахідці: не «правильний
# результат є», а «неправильний стан більше не проходить непоміченим».
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from eval.evaluate import evaluate_record


def _meta(status, fact_confirmed, unknown_critical=()):
    return {
        "status": status,
        "template": None,
        "facts": [{"confirmed": c, "additional_info": {}} for c in fact_confirmed],
        "subject": {},
        "unknown_critical_fields": list(unknown_critical),
    }


def _status_check(meta):
    row = evaluate_record(meta, {"id": "X-001"}, {}, None)
    checks = [c for c in row["checks"] if c["key"] == "чернетка_не_факт"]
    assert len(checks) == 1, "перевірка статусу мусить існувати в КОЖНОМУ записі"
    return checks[0]


# --- R-A2-02: прилад мусить міряти статуси, а не лише значення полів ------

def test_needs_review_with_confirmed_fact_is_a_failure():
    """Розрив «чернетка ≠ факт» (репро R-A1-01): needs_review-документ з
    confirmed=true у фактах раніше давав ті самі 176/176. Тепер це мінус у
    чисельнику."""
    check = _status_check(_meta("needs_review", [True]))
    assert check["ok"] is False


def test_confirmed_status_requires_all_facts_confirmed():
    check = _status_check(_meta("confirmed", [True, False]))
    assert check["ok"] is False


def test_confirmed_status_with_critical_gap_is_a_failure():
    check = _status_check(_meta("confirmed", [True], unknown_critical=["surname"]))
    assert check["ok"] is False


def test_consistent_statuses_pass():
    assert _status_check(_meta("confirmed", [True, True]))["ok"] is True
    assert _status_check(_meta("needs_review", [False, False]))["ok"] is True
    assert _status_check(_meta("unresolved", []))["ok"] is True


def test_status_check_enters_denominator():
    """Перевірка мусить входити у fields_total -- інакше вона знову
    декоративна."""
    row = evaluate_record(_meta("needs_review", [True]), {"id": "X-001"}, {}, None)
    assert row["fields_total"] >= 1
    assert row["fields_ok"] < row["fields_total"]


# --- R-A1-01 + R-A2-01: фінальний confirmed мусить їхати у facts -----------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LEAVE_DOCX = os.path.join(_PROJECT_ROOT, "data", "eval", "samples", "leave",
                           "synthetic-2026-05", "docx", "LEAVE-001.docx")


def _run_with_llm_template_source(monkeypatch):
    """Репро b2 (repro_01): справжній process_file з підміною РІВНО одного
    вердикту -- identify_template каже, що шаблон обрала МОДЕЛЬ."""
    import pipeline.run as run_mod
    from pipeline.config import load_config

    cfg = load_config(os.path.join(_PROJECT_ROOT, "config.yaml"))
    res = run_mod.build_resources(cfg, force_no_llm=True)
    res["store"] = None

    real_identify = run_mod.identify_template

    def fake_identify(*args, **kwargs):
        ident = real_identify(*args, **kwargs)
        ident["source"] = "llm"
        return ident

    monkeypatch.setattr(run_mod, "identify_template", fake_identify)
    return run_mod.process_file(_LEAVE_DOCX, res, cfg)


def test_needs_review_gate_writes_back_into_facts(monkeypatch):
    """Гейт (template_by_llm) гасив лише локальну змінну confirmed, а
    facts[*].confirmed лишалися True -- споживач, що фільтрує за
    facts.confirmed, брав needs_review-документ у підрахунки."""
    meta = _run_with_llm_template_source(monkeypatch)
    assert meta["status"] == "needs_review"
    assert meta["review_reason"] == "template_by_llm"
    assert meta["facts"], "факти мусять зберегтися (значення не губимо)"
    assert all(f["confirmed"] is False for f in meta["facts"]), \
        [f["confirmed"] for f in meta["facts"]]


# --- R-B1-02: 4-й токен ПІБ не сміє зникати мовчки -------------------------

def _rank_lookup():
    import yaml
    from pipeline.normalization.normalize import build_alias_lookup
    path = os.path.join(_PROJECT_ROOT, "pipeline", "dictionaries", "military_rank.yaml")
    with open(path, encoding="utf-8") as f:
        return build_alias_lookup(yaml.safe_load(f))


def test_name_tail_token_is_not_silently_dropped():
    """«кизи» -- частина імені, не сміття. Раніше вихід для входу З хвостом і
    БЕЗ був побайтово однаковий (_leftover_before_surname=None, кінець)."""
    from pipeline.extraction.extract import parse_rank_and_name
    ranks = _rank_lookup()
    _, with_tail = parse_rank_and_name(
        "рядовий ЛЕМЕШКО Соломія Мустафа кизи", ranks)
    _, without_tail = parse_rank_and_name(
        "рядовий ЛЕМЕШКО Соломія Мустафа", ranks)
    assert with_tail != without_tail, \
        "входи з хвостом і без мусять давати РІЗНИЙ вихід"
    assert with_tail["_leftover_after_patronymic"] == ["кизи"]
    assert without_tail["_leftover_after_patronymic"] is None


def test_name_tail_blocks_confirmed_and_keeps_raw_text():
    """Поле з хвостом ПІБ мусить: не вирішитись, заблокувати confirmed
    (критичне) і донести рев'юерові повний хвіст, а не голий null."""
    from pipeline.extraction.extract import NAME_TAIL_METHOD
    from pipeline.identification import load_schemas
    from pipeline.build_record import build_record

    schema = next(s for s in load_schemas(os.path.join(_PROJECT_ROOT, "pipeline", "schemas"))
                  if s["template"] == "leave_ticket")
    raw = {f["name"]: (None, "no_value") for f in schema["fields"]}
    raw["patronymic"] = (None, f"{NAME_TAIL_METHOD}:Мустафа кизи")
    record = build_record(schema, raw, {"military_rank": _rank_lookup()})
    assert "patronymic" in record["unknown_critical_fields"]
    assert record["facts"][0]["confirmed"] is False
    assert record["unresolved_values"].get("patronymic") == "Мустафа кизи"
    assert record["field_provenance"]["patronymic"]["raw_text"] == "Мустафа кизи"


def test_three_token_names_unchanged():
    """Звичайний ПІБ з трьох токенів розбирається як раніше -- фікс не сміє
    зачепити основний шлях."""
    from pipeline.extraction.extract import parse_rank_and_name
    rank, parts = parse_rank_and_name("рядовий ЛЕМЕШКО Соломія Романівна", _rank_lookup())
    assert rank == {"code": "soldier", "label": "Солдат"}
    assert (parts["surname"], parts["given_name"], parts["patronymic"]) == \
        ("ЛЕМЕШКО", "Соломія", "Романівна")


# --- R-A2-03: зворотний прохід мапінгу -- неміряне поле не мовчить ----------

def _mapping():
    import io as _io
    import yaml as _yaml
    with _io.open(os.path.join(_PROJECT_ROOT, "eval", "field-mapping.yaml"),
                  encoding="utf-8") as f:
        return _yaml.safe_load(f)


def _schemas():
    from pipeline.identification import load_schemas
    return load_schemas(os.path.join(_PROJECT_ROOT, "pipeline", "schemas"))


def test_unmapped_schema_field_is_reported():
    """Поле схеми без ключа мапінгу й без запису в unmeasured -- помилка
    мапінгу, а не тиша (саме так 7+7 полів не мірялись непомітно)."""
    import copy
    from eval.evaluate import check_mapping
    mapping = copy.deepcopy(_mapping())
    del mapping["unmeasured"]["leave_ticket"]["leave_year"]
    problems = check_mapping(mapping, _schemas(), {})
    assert any("leave_year" in p and "не міряється" in p for p in problems), problems


def test_stale_unmeasured_entry_is_reported():
    """Запис unmeasured для поля, якого немає в схемі, -- застарілий і видимий."""
    import copy
    from eval.evaluate import check_mapping
    mapping = copy.deepcopy(_mapping())
    mapping["unmeasured"]["leave_ticket"]["ghost_field"] = "причина"
    problems = check_mapping(mapping, _schemas(), {})
    assert any("ghost_field" in p for p in problems), problems


def test_current_mapping_covers_all_active_fields():
    """Поточний мапінг + unmeasured разом покривають УСІ активні поля обох
    схем (з еталоном набору) -- нуль проблем зворотного проходу."""
    from eval.evaluate import check_mapping, load_ground_truth
    problems = check_mapping(_mapping(), _schemas(), load_ground_truth())
    assert problems == [], problems


# --- R-A1-09: інжест більше не викидає адресу клітинки таблиці ---------------

def test_docx_table_cells_carry_their_address():
    """(таблиця, рядок, стовпець) були фізично в руках walk_tables і
    відкидались blocks.append(text) -- багаторядкова таблиця (книга обліку)
    ставала нерозбірною за побудовою."""
    from pipeline.ingestion.ingest import extract_docx_blocks
    text, blocks = extract_docx_blocks(_LEAVE_DOCX)
    cells = [b for b in blocks if isinstance(b, dict) and "table" in b]
    assert cells, "клітинки таблиць мусять нести адресу"
    for cell in cells:
        assert isinstance(cell["table"], int)
        assert isinstance(cell["row"], int)
        assert isinstance(cell["col"], int)
        assert cell["text"].strip()
    # текст документа не змінився: join віддає ті самі рядки
    assert all((b if isinstance(b, str) else b["text"]) in text for b in blocks)


# --- R-A1-08: константи родини бланків перевизначаються схемою --------------

def test_extraction_limits_are_schema_tunable():
    """Заміряний наслідок дефолтів: 6-символьна шапка «Звання» не входить у
    label_heads за побудовою (MIN_LABEL_HEAD_CHARS=16), тож
    validate_block_value приймав її значенням surname. Схема з іншою
    типографікою тепер знижує поріг у СВОЄМУ YAML, не в коді."""
    import copy
    from pipeline.extraction.extract import schema_label_heads, validate_block_value
    schema = copy.deepcopy(_leave_schema())
    schema["fields"].append({"name": "x", "label_before": "Звання",
                             "extraction": "block_before_label"})
    # дефолти: коротка голова відсікається -> «Звання» приймається значенням
    heads_default = schema_label_heads(schema)
    assert all("звання" != h for h in heads_default)
    # перевизначення схемою: голова з'являється, значення-лейбл відхиляється
    schema["extraction_limits"] = {"label_head_tokens": 1,
                                   "min_label_head_chars": 6}
    heads_tuned = schema_label_heads(schema)
    assert "звання" in heads_tuned
    surname_field = next(f for f in schema["fields"] if f["name"] == "surname")
    value, reason = validate_block_value(surname_field, "Звання", heads_tuned)
    assert reason != "matched", (value, reason)


def test_unknown_extraction_limit_key_is_loud():
    import copy
    from pipeline.identification import validate_schema
    schema = copy.deepcopy(_leave_schema())
    schema["extraction_limits"] = {"oversized_candidate_charz": 300}
    problems = validate_schema(schema)
    assert any(sev == "error" and "oversized_candidate_charz" in msg
               for sev, msg in problems), problems


# --- R-A1-10 + R-A2-12: «handwritten» вимагає доказу читання з пікселів -----

def test_handwritten_queue_requires_ocr_evidence():
    """Раніше вистачало source_kind=photo без перевірки вмісту: born-digital
    docx під іменем .pdf потрапляв би в чергу «рукописне»."""
    from pipeline.run import _review_queue_type
    # вміст прийшов з текстового шару -- рукописним його ніхто не читав
    assert _review_queue_type("needs_review", "photo", False,
                              ocr_used=False) == "unconfirmed_fact"
    # вміст справді читався OCR-ом з зображення
    assert _review_queue_type("needs_review", "photo", False,
                              ocr_used=True) == "handwritten"
    assert _review_queue_type("needs_review", "electronic", False,
                              ocr_used=False) == "unconfirmed_fact"


# --- R-A2-04: document_links міряються, а не «пайплайн не знає» -------------

def test_replacing_document_must_carry_supersedes_link():
    """Документ-замінник без зв'язку supersedes -- провал перевірки, а не
    невидимість (раніше звіт стверджував, що міряти нічого)."""
    meta = _meta("confirmed", [True])
    meta["document_links"] = []
    truth = {"id": "X-001", "пара": {"replaces": "X-000"}}
    row = evaluate_record(meta, truth, {}, None)
    link = next(c for c in row["checks"] if c["key"] == "зв'язок_скасування")
    assert link["ok"] is False


def test_invented_supersedes_link_is_a_failure():
    """Вигаданий зв'язок на документі без пари не кращий за пропущений."""
    meta = _meta("confirmed", [True])
    meta["document_links"] = [{"link_type": "supersedes",
                               "target_document_number": "157"}]
    row = evaluate_record(meta, {"id": "X-001", "пара": None}, {}, None)
    link = next(c for c in row["checks"] if c["key"] == "зв'язок_скасування")
    assert link["ok"] is False


def test_correct_supersedes_link_passes():
    meta = _meta("confirmed", [True])
    meta["document_links"] = [{"link_type": "supersedes",
                               "target_document_number": "157"}]
    truth = {"id": "X-001", "пара": {"replaces": "X-000"}}
    row = evaluate_record(meta, truth, {}, None)
    link = next(c for c in row["checks"] if c["key"] == "зв'язок_скасування")
    assert link["ok"] is True


def test_wrapped_sentinel_is_confirmed_empty_not_a_value():
    """Знахідка нової перевірки «впд»: текстовий шар PDF розриває сентинел
    («не \\nвидавались»), однорядковий патерн ловив лише «не», і воно їхало в
    БД реальним значенням ВПД. Тест тримає обидва шари фіксу: багаторядковий
    варіант патерна і пробіло-стійке порівняння сентинела."""
    from pipeline.extraction.extract import extract_field_regex
    from pipeline.normalization.normalize import normalize_null_if_sentinel

    field = next(f for f in _leave_schema()["fields"]
                 if f["name"] == "travel_document_number")
    text = ("Для проїзду видано військові перевізні документи за №\n"
            "не \nвидавались\n"
            "Дійсний у разі пред’явлення документа, який засвідчує особу.\n")
    value, reason = extract_field_regex(field, text)
    assert reason == "matched"
    assert normalize_null_if_sentinel(value, "не видавались") == (None, True), value


# --- R-A1-04: --reprocess не лишає два живі записи на той самий вміст -------

def test_reprocess_retires_previous_record(tmp_path):
    """Раніше після --reprocess на диску лежали ДВА .md з одним file_hash у
    робочих теках -- споживач documents/** порахував би вміст двічі."""
    import glob as _glob
    import pipeline.run as run_mod
    from pipeline.config import load_config

    cfg = load_config(os.path.join(_PROJECT_ROOT, "config.yaml"))
    cfg["storage"] = dict(cfg["storage"], local_root=str(tmp_path / "output"))
    res = run_mod.build_resources(cfg, force_no_llm=True)

    first = run_mod.process_file(_LEAVE_DOCX, res, cfg)
    assert first["status"] == "confirmed"

    dup = run_mod.process_file(_LEAVE_DOCX, res, cfg)
    assert dup["status"] == "duplicate"

    second = run_mod.process_file(_LEAVE_DOCX, res, cfg, reprocess=True)
    assert second["status"] == "confirmed"
    assert second["supersedes_storage_key"] == first["storage_key"]

    live = _glob.glob(str(tmp_path / "output" / "documents" / "**" / "*.md"),
                      recursive=True)
    retired = _glob.glob(str(tmp_path / "output" / "superseded" / "**" / "*.md"),
                         recursive=True)
    assert len(live) == 1, f"живим має лишитись рівно один запис: {live}"
    assert len(retired) == 1, "старий запис не губиться, а їде в superseded/"
    # дедуплікація віддає НОВИЙ ключ
    assert res["store"].find_by_hash(second["file_hash"]) == second["storage_key"]


# --- R-B1-01: тематичний домен не дається за ОДИН збіг фрази ----------------

def _domains():
    from pipeline.classification.classify import load_domain_keyphrases
    return load_domain_keyphrases(os.path.join(
        _PROJECT_ROOT, "pipeline", "dictionaries", "domain_keyphrases.yaml"))


def test_single_body_phrase_hit_does_not_assign_domain():
    """Медична довідка з одним «у відпустку» отримувала домен leave з балом 1,
    а через мапінг ще й subject_kind=person і create_subject_object=True."""
    from pipeline.classification.classify import classify_domain_rules
    text = "Довідка про лікування. Призначено стаціонарне лікування терміном на десять днів."
    domain, scores = classify_domain_rules(text, _domains())
    assert scores.get("leave") == 1, scores
    assert domain is None, "один збіг однієї фрази -- не свідчення"


def test_title_hit_still_assigns_domain():
    from pipeline.classification.classify import classify_domain_rules
    domain, _ = classify_domain_rules("Відпускний квиток № 102", _domains())
    assert domain == "leave"


def test_no_domain_means_no_subject_object():
    """Без домену вид суб'єкта -- unknown, об'єкт у чужому реєстрі не
    створюється."""
    from pipeline.subject_kind import creates_object, resolve_subject_kind
    kind_info = resolve_subject_kind(schema=None, domain=None, domains=_domains())
    assert kind_info["kind"] == "unknown"
    assert creates_object(kind_info["kind"]) is False


# --- R-A2-06: документ, що впав необробленою помилкою, лишає слід -----------

def test_crashed_document_is_persisted_and_archived(tmp_path, monkeypatch):
    """Раніше виняток у process_target давав запис ЛИШЕ в консолі: id=None,
    нуль нових файлів у сховищі, нуль рядків індексу, а continue минав
    архівацію -- файл лишався в папці-приймачі й падав на кожному запуску."""
    import shutil
    import pipeline.run as run_mod
    from pipeline.config import load_config

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    shutil.copy(_LEAVE_DOCX, inbox / "LEAVE-001.docx")

    cfg = load_config(os.path.join(_PROJECT_ROOT, "config.yaml"))
    cfg["paths"] = dict(cfg["paths"], input_dir=str(inbox))
    cfg["storage"] = dict(cfg["storage"], local_root=str(tmp_path / "output"))
    cfg["intake"] = dict(cfg["intake"], archive=True,
                         processed_dir=str(tmp_path / "processed"),
                         failed_dir=str(tmp_path / "failed"))

    res = run_mod.build_resources(cfg, force_no_llm=True)

    def boom(*args, **kwargs):
        raise RuntimeError("штучний збій")

    monkeypatch.setattr(run_mod, "process_file", boom)
    results, _skipped = run_mod.process_target(str(inbox), res, cfg)

    assert len(results) == 1
    meta = results[0]
    assert meta["status"] == "unresolved"
    assert meta["id"] is not None, "запис мусить мати id"
    assert meta["file_hash"] is not None
    assert meta["storage_key"] is not None
    # слід у сховищі: файл запису існує
    stored = tmp_path / "output" / meta["storage_key"].replace("/", os.sep)
    assert stored.exists(), "запис про збій мусить лежати у сховищі"
    # рядок в індексі: наступний прогін побачить документ як оброблений
    index = tmp_path / "output" / "index" / "processed.jsonl"
    assert index.exists() and meta["file_hash"] in index.read_text(encoding="utf-8")
    # файл пішов з папки-приймача у failed (вічний цикл розірвано)
    assert not (inbox / "LEAVE-001.docx").exists()
    assert meta.get("archived_to")


# --- R-A1-02: відсутній blank_template не сміє давати recognized:True -------

def test_missing_blank_template_is_not_recognized():
    """_read_lines неіснуючого шляху -> [], і вердикт давав recognized:True
    при total:0 -- три захисти вимикались мовчки."""
    import copy
    from pipeline.identification import blank_edition_verdict
    schema = copy.deepcopy(_leave_schema())
    schema["blank_template"] = "data/eval/samples/leave/НЕМА.docx"
    verdict = blank_edition_verdict("будь-який текст", schema)
    assert verdict["recognized"] is False
    assert verdict["reason"] == "blank_template_missing_or_empty"


def test_missing_blank_template_is_a_schema_error():
    """Валідатор мусить ловити битий шлях на завантаженні -- раніше різниця
    з базовим прогоном була порожня (жодного error чи warning)."""
    import copy
    from pipeline.identification import validate_schema
    schema = copy.deepcopy(_leave_schema())
    schema["blank_template"] = "data/eval/samples/leave/НЕМА.docx"
    problems = validate_schema(schema)
    assert any(sev == "error" and "blank_template" in msg
               for sev, msg in problems), problems


def test_schema_without_blank_template_stays_inert():
    """Схема БЕЗ ключа перевірки не отримує -- це оголошена межа, не дефект."""
    import copy
    from pipeline.identification import blank_edition_verdict, validate_schema
    schema = copy.deepcopy(_leave_schema())
    del schema["blank_template"]
    verdict = blank_edition_verdict("текст", schema)
    assert verdict["recognized"] is True and verdict["total"] == 0
    assert not any("blank_template" in msg for _sev, msg in validate_schema(schema))


# --- R-A1-06 + R-A2-05: значення `normalization:` валідуються ---------------

def _leave_schema():
    from pipeline.identification import load_schemas
    return next(s for s in load_schemas(os.path.join(_PROJECT_ROOT, "pipeline", "schemas"))
                if s["template"] == "leave_ticket")


def test_normalization_typo_is_loud():
    """Одруківка `null_if_not_isued` не давала ЖОДНОГО повідомлення валідатора,
    а «не видавались» ставало реальним значенням поля в БД."""
    import copy
    from pipeline.identification import validate_schema
    schema = copy.deepcopy(_leave_schema())
    field = next(f for f in schema["fields"]
                 if f.get("normalization") == "null_if_not_issued")
    field["normalization"] = "null_if_not_isued"
    problems = validate_schema(schema)
    assert any(sev == "error" and "normalization" in msg and "null_if_not_isued" in msg
               for sev, msg in problems), problems


def test_sentinel_without_normalization_is_loud():
    """not_issued_sentinel без null_if_not_issued -- сентинел їде в БД як
    реальне значення; це і є заміряний наслідок одруківки."""
    import copy
    from pipeline.identification import validate_schema
    schema = copy.deepcopy(_leave_schema())
    field = next(f for f in schema["fields"] if f.get("not_issued_sentinel"))
    del field["normalization"]
    problems = validate_schema(schema)
    assert any(sev == "error" and "not_issued_sentinel" in msg
               for sev, msg in problems), problems


def test_dead_normalization_on_dispatched_type_is_loud():
    """`normalization:` на type date/number/category мертвий за побудовою --
    саме так 8 рядків iso_date прожили в схемах, не читаючись ніде."""
    import copy
    from pipeline.identification import validate_schema
    schema = copy.deepcopy(_leave_schema())
    field = next(f for f in schema["fields"] if f.get("type") == "date")
    field["normalization"] = "nominative_case"
    problems = validate_schema(schema)
    assert any(sev == "error" and "не читається для type" in msg
               for sev, msg in problems), problems


def test_current_schemas_validate_clean_of_errors():
    """Обидві робочі схеми (після зняття мертвого iso_date) не мають ЖОДНОЇ
    помилки валідатора -- інакше run.py виключив би їх із набору."""
    from pipeline.identification import load_schemas, validate_schema
    for schema in load_schemas(os.path.join(_PROJECT_ROOT, "pipeline", "schemas")):
        errors = [m for sev, m in validate_schema(schema) if sev == "error"]
        assert not errors, errors


# --- R-B1-04: неможлива дата мусить лишати сирий збіг -----------------------

def test_impossible_date_keeps_raw_match_visible():
    """«31 лютого» гасило поле БЕЗ raw_text і з порожнім unresolved_values:
    механізм вимагав isinstance(raw_value, str), а date-поле приходить
    regex-групами (dict). Рев'юер бачив resolved:false без жодної причини."""
    from pipeline.identification import load_schemas
    from pipeline.build_record import build_record

    schema = next(s for s in load_schemas(os.path.join(_PROJECT_ROOT, "pipeline", "schemas"))
                  if s["template"] == "leave_ticket")
    raw = {f["name"]: (None, "no_value") for f in schema["fields"]}
    raw["leave_start_date"] = ({"day": "31", "month": "лютого", "year": "2026"},
                               "matched")
    record = build_record(schema, raw, {})
    assert "leave_start_date" in record["unknown_critical_fields"]
    assert record["unresolved_values"].get("leave_start_date") == "31 лютого 2026"
    assert record["field_provenance"]["leave_start_date"]["raw_text"] == "31 лютого 2026"


# --- R-B1-03: часткова сума числівника не повертається ---------------------

def test_partial_number_word_sum_is_refused():
    """'двадцять одін' давав 20 з провенансом matched/0.9 -- значення,
    складене з ПОЛОВИНИ входу, виглядало як повністю прочитане."""
    from pipeline.normalization.normalize import number_from_words
    assert number_from_words("двадцять одін") is None
    assert number_from_words("двадцять фыва") is None
    assert number_from_words("тринадцять днів") is None


def test_full_number_words_still_parse():
    from pipeline.normalization.normalize import number_from_words
    assert number_from_words("тринадцять") == 13
    assert number_from_words("двадцять одна") == 21
    assert number_from_words("двадцять") == 20
    assert number_from_words("13") == 13


def test_instrument_catches_the_writeback_break(monkeypatch):
    """Зв'язка з R-A2-02: якби write-back знову зник, прилад мусить це
    покарати. Імітуємо регресію вручну й перевіряємо, що чисельник падає."""
    meta = _run_with_llm_template_source(monkeypatch)
    meta["facts"][0]["confirmed"] = True  # регресія: чернетка знову «факт»
    row = evaluate_record(meta, {"id": "X-001"}, {}, None)
    bad = [c for c in row["checks"] if c["key"] == "чернетка_не_факт"]
    assert bad and bad[0]["ok"] is False
