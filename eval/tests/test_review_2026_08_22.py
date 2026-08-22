# Тести виправлень за рев'ю 22.08.2026 (docs/review-2026-08-22/verdicts.md).
#
# Як і в test_review_fixes.py, кожен тест ловить САМЕ ТИШУ зі знахідки:
# не «правильний результат є», а «зіпсований стан більше не дає 100%».
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from eval.evaluate import evaluate_record, main

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LEAVE_DOCX = os.path.join(_PROJECT_ROOT, "data", "eval", "samples", "leave",
                           "synthetic-2026-05", "docx")


def _meta(status="confirmed", confirmed=(True,), **extra):
    """Запис, у якому НЕМА жодної підстави для сумніву: шаблон визначено,
    факти є, прогалин немає, попереджень немає."""
    meta = {
        "status": status,
        "template": "leave_ticket",
        "facts": [{"confirmed": c, "additional_info": {}} for c in confirmed],
        "subject": {},
        "unknown_critical_fields": [],
        "warnings": [],
    }
    meta.update(extra)
    return meta


def _check(meta, key):
    row = evaluate_record(meta, {"id": "X-001"}, {}, None)
    hits = [c for c in row["checks"] if c["key"] == key]
    assert len(hits) == 1, f"перевірка '{key}' мусить бути в КОЖНОМУ записі"
    return hits[0]


# --- A-01: прилад мусить карати НЕОБҐРУНТОВАНУ ВІДМОВУ ---------------------
#
# Репро знахідки: пайплайн, у якого КОЖЕН запис -- needs_review з
# facts[*].confirmed=False, отримував ті самі 224/224 = 100.0%. Тобто цифра,
# якою міряють якість, не падала від того, що система перестала підтверджувати
# будь-що.

def test_mass_refusal_without_grounds_is_a_failure():
    check = _check(_meta("needs_review", (False,)), "відмова_обґрунтована")
    assert check["ok"] is False
    assert check["refusal_grounds"] == []


def test_refusal_with_a_critical_gap_is_fine():
    meta = _meta("needs_review", (False,), unknown_critical_fields=["surname"])
    assert _check(meta, "відмова_обґрунтована")["ok"] is True


@pytest.mark.parametrize("extra", [
    {"unresolved_values": {"days": "13"}},
    {"consistency_problems": ["кінець раніше початку"]},
    {"date_range_error": True},
    {"warnings": ["OCR: сторінка 2 порожня"]},
    {"template": None},
    {"facts": []},
])
def test_every_objective_ground_justifies_refusal(extra):
    """Підстави читаються з ВИХОДУ пайплайна, не зі сценарію еталона."""
    assert _check(_meta("needs_review", (False,), **extra),
                  "відмова_обґрунтована")["ok"] is True


def test_confirmed_document_does_not_get_punished_by_the_new_check():
    """Інваріант карає лише відмову. Брехню в бік довіри карає
    `чернетка_не_факт` -- перевірки не мусять дублювати одна одну."""
    check = _check(_meta("confirmed", (True,)), "відмова_обґрунтована")
    assert check["ok"] is True and check["trivial"] is True


def test_both_directions_are_now_measured():
    """Симетрія: обидва напрямки брехні мусять давати мінус у знаменнику."""
    lying_up = evaluate_record(_meta("needs_review", (True,)), {"id": "X"}, {}, None)
    refusing = evaluate_record(_meta("needs_review", (False,)), {"id": "X"}, {}, None)
    healthy = evaluate_record(_meta("confirmed", (True,)), {"id": "X"}, {}, None)
    assert lying_up["fields_ok"] < lying_up["fields_total"]
    assert refusing["fields_ok"] < refusing["fields_total"]
    assert healthy["fields_ok"] == healthy["fields_total"]


def test_refusal_check_enters_the_denominator():
    row = evaluate_record(_meta("needs_review", (False,)), {"id": "X-001"}, {}, None)
    assert any(c["key"] == "відмова_обґрунтована" for c in row["checks"])
    assert row["fields_total"] == len(row["checks"])


# --- A-09: три цифри окремо, тривіальні перевірки видно --------------------

def test_checks_are_split_into_three_groups():
    row = evaluate_record(_meta(), {"id": "X-001"}, {}, None)
    groups = row["by_group"]
    assert set(groups) == {"field", "invariant", "blank"}
    assert sum(g["total"] for g in groups.values()) == row["fields_total"]
    assert sum(g["ok"] for g in groups.values()) == row["fields_ok"]
    # Статусні перевірки -- інваріанти, а не «поля»: саме через цю склейку
    # «224/224» читалось як «224 витягнутих поля».
    assert groups["invariant"]["total"] >= 3


def test_link_check_on_a_document_without_a_pair_is_marked_trivial():
    """Документ без пари: правильна відповідь -- «зв'язків немає», і її
    отримує задарма пайплайн, який зв'язків не витягує взагалі. Заміряно:
    14 із 16 у корпусі leave."""
    check = _check(_meta(), "зв'язок_скасування")
    assert check["ok"] is True and check["trivial"] is True


def test_link_check_on_a_real_pair_is_not_trivial():
    meta = _meta(document_links=[{"link_type": "supersedes",
                                  "target_document_number": "157"}])
    check = evaluate_record(meta, {"id": "X-001", "пара": {"replaces": "157"}},
                            {}, None)
    hit = next(c for c in check["checks"] if c["key"] == "зв'язок_скасування")
    assert hit["ok"] is True and hit["trivial"] is False


# --- A-15 + C-11: провал вимірювання мусить давати ненульовий код ----------

def test_exit_code_is_nonzero_below_the_fail_under_threshold():
    """Прилад без цього не можна поставити в CI: до 22.08.2026 `main`
    беззастережно повертав 0, тобто провал вимірювання не відрізнявся від
    успіху."""
    code = main(["--no-llm", "--input", _LEAVE_DOCX, "--fail-under", "100.5"])
    assert code == 1


def test_exit_code_is_zero_on_a_clean_run():
    code = main(["--no-llm", "--input", _LEAVE_DOCX, "--fail-under", "99.0"])
    assert code == 0
