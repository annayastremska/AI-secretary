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
