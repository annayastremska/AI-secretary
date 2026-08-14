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


def test_instrument_catches_the_writeback_break(monkeypatch):
    """Зв'язка з R-A2-02: якби write-back знову зник, прилад мусить це
    покарати. Імітуємо регресію вручну й перевіряємо, що чисельник падає."""
    meta = _run_with_llm_template_source(monkeypatch)
    meta["facts"][0]["confirmed"] = True  # регресія: чернетка знову «факт»
    row = evaluate_record(meta, {"id": "X-001"}, {}, None)
    bad = [c for c in row["checks"] if c["key"] == "чернетка_не_факт"]
    assert bad and bad[0]["ok"] is False
