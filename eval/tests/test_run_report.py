# -*- coding: utf-8 -*-
"""Звіт прогону (pipeline/run_report.py): лічильники, які лишаються після
прогону. Причина появи -- відгуки «наче працює, але не вимірюється»
(24.08.2026): вимірювання жило в терміналі розробника, прогін не лишав
артефакту з цифрами.

Головне, що тут перевіряється, -- ЧЕСНІСТЬ класифікації:
  * «вирішено детерміновано» і «вирішено моделлю» не зливаються в одне
    «заповнено» (різниця між 0.9 і 0.6 -- сенс усього провенансу);
  * відкладені поля не входять у знаменник (не обіцяне -- не прогалина);
  * сам звіт каже, що правильності НЕ міряє.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from pipeline.run_report import build_report, _classify_method


def _meta(status="confirmed", prov=None, facts=None, critical=None, **kw):
    return dict({
        "status": status,
        "source_file": kw.pop("source_file", "X.docx"),
        "template": "leave_ticket", "domain": "leave",
        "field_provenance": prov or {},
        "facts": facts if facts is not None else [{"confirmed": True}],
        "unknown_critical_fields": critical or [],
    }, **kw)


def test_method_classes_do_not_merge_model_and_deterministic():
    assert _classify_method("matched") == "вирішено_детерміновано"
    assert _classify_method("derived") == "вирішено_детерміновано"
    assert _classify_method("llm") == "вирішено_моделлю"
    assert _classify_method("llm_split_vote") == "вирішено_моделлю"
    assert _classify_method("confirmed_empty_slot:blank_value") == "доведено_порожні"
    assert _classify_method("deferred") == "відкладені"
    # усе незнайоме -- чесна прогалина, не тихий нуль
    for m in ("no_value", "no_label", "ambiguous_label",
              "llm_error:RuntimeError", "unverified_foreign_edition", None):
        assert _classify_method(m) == "прогалини", m


def test_deferred_fields_are_not_promises():
    report = build_report([_meta(prov={
        "a": {"method": "matched"},
        "b": {"method": "deferred"},
        "c": {"method": "no_value"},
    })])
    p = report["поля"]
    assert p["обіцяних_схемами"] == 2, p   # deferred не рахується
    assert p["вирішено_детерміновано"] == 1
    assert p["прогалини"] == 1


def test_critical_gaps_and_draft_facts_are_visible():
    report = build_report([
        _meta(source_file="OK.docx"),
        _meta(status="needs_review", source_file="GAP.docx",
              critical=["surname"], facts=[{"confirmed": False}]),
    ])
    d, f = report["документів"], report["факти"]
    assert d["з_критичними_прогалинами"] == 1
    assert report["документи_з_критичними_прогалинами"] == ["GAP.docx"]
    assert f == {"усього": 2, "підтверджені": 1, "чернетки": 1}


def test_report_declares_what_it_does_not_measure():
    """Звіт без цього перетворюється на те, чим його читатимуть: на цифру
    правильності. Заява про межу -- частина контракту, тому під тестом."""
    report = build_report([])
    assert "правильності НЕ вимірює" in report["_що_це_міряє"]
