# -*- coding: utf-8 -*-
"""Фінальний вигляд прізвища: «Перша велика, решта малі» (завдання Анни,
15.08.2026).

Дві різні речі, які раніше робила одна функція, тепер розділені, і тести
охороняють САМЕ межу між ними:

1. _restore_case зберігає регістр ДЖЕРЕЛА -- він потрібен розпізнаванню
   (parse_rank_and_name відрізняє прізвище від імені за ВЕЛИКИМИ). Її
   поведінка НЕ змінилась, і тест на це тут є: якби хтось «полагодив»
   регістр усередині неї, розпізнавання б тихо поповзло.
2. surname_display_case форматує вже РОЗПІЗНАНЕ значення перед записом у
   subject -- незалежно від друку в документі.
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from pipeline.normalization.normalize import (  # noqa: E402
    surname_display_case, _restore_case)
from pipeline.extraction.extract import parse_rank_and_name  # noqa: E402


# ── 1. Сам форматер ─────────────────────────────────────────────────────────

def test_uppercase_source_becomes_title_case():
    assert surname_display_case("ОБЕРЕМКО") == "Оберемко"


def test_lowercase_source_becomes_title_case():
    assert surname_display_case("оберемко") == "Оберемко"


def test_already_title_case_unchanged():
    assert surname_display_case("Оберемко") == "Оберемко"


def test_hyphenated_surname_capitalizes_each_part():
    assert surname_display_case("ПЕТРЕНКО-ІВАНЕНКО") == "Петренко-Іваненко"
    assert surname_display_case("петренко-іваненко") == "Петренко-Іваненко"


def test_space_separated_prefix_capitalizes_each_word():
    assert surname_display_case("ДЕ ВІТТ") == "Де Вітт"


def test_apostrophe_is_not_a_boundary():
    # str.title() дав би «Дем'Янюк» -- саме тому форматер не на ньому.
    assert surname_display_case("ДЕМ'ЯНЮК") == "Дем'янюк"
    assert surname_display_case("дем’янюк") == "Дем’янюк"


def test_none_and_empty_pass_through():
    assert surname_display_case(None) is None
    assert surname_display_case("") == ""
    assert surname_display_case("   ") == "   "


# ── 2. Розпізнавання НЕ змінилось ───────────────────────────────────────────

def test_restore_case_still_preserves_source_register():
    """_restore_case мусить і далі відновлювати регістр ДЖЕРЕЛА: він -- сигнал
    для розпізнавання, а не косметика. Форматування виходу живе окремо."""
    assert _restore_case("ОБЕРЕМКО", "оберемко") == "ОБЕРЕМКО"
    assert _restore_case("Оберемко", "оберемко") == "Оберемко"
    assert _restore_case("оберемко", "оберемко") == "оберемко"


def test_recognition_by_source_register_still_works():
    """parse_rank_and_name і далі впізнає прізвище за ВЕЛИКИМИ в джерелі --
    форматування виходу відбувається пізніше (build_record) і на вхідні дані
    розпізнавання не впливає."""
    _, parsed = parse_rank_and_name(
        "рядовий ОБЕРЕМКО Соломія Романівна", {"рядовий": "soldier"})
    assert parsed["surname"] == "ОБЕРЕМКО"
    assert parsed["given_name"] == "Соломія"
    assert parsed["patronymic"] == "Романівна"
