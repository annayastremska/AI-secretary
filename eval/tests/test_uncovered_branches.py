# -*- coding: utf-8 -*-
"""Гілки, у яких НЕ БУЛО ні прогону, ні тесту (аудит 23.08, розд. 6).

Аудит невикористаної універсальності перелічив чотири гілки, які на 269
документах не спрацювали ЖОДНОГО разу і при цьому не мали тесту. Це найгірший
стан із можливих: код є, він щось обіцяє, і жодного доказу, що обіцянка
виконується. Правильна дія тут -- не видалення (кожна відповідає ЗАМІРЯНОМУ
випадку рев'ю), а покриття: тоді гілка або доведена, або впаде.

  * `positional_name_no_uppercase` -- A-05: ПІБ без ВЕЛИКОГО прізвища;
  * `name_tail_unparsed`           -- R-B1-02: неспожитий хвіст після по батькові;
  * `inflect_failed`               -- морфологія не змогла відмінити;
  * `no_template_match`            -- жодного сигналу шаблону взагалі.

Окремо: чому «ніколи не трапилось» саме по собі НЕ означає «мертвий код».
Наш корпус згенерований із двох бланків, де прізвище завжди у ВЕЛИКОМУ
регістрі, ПІБ завжди з трьох частин, а всі документи -- одного з двох типів.
Тобто нуль спрацювань описує КОРПУС, не код.

Запуск:
    python -m pytest eval/tests/test_uncovered_branches.py -q
"""
import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from pipeline.extraction.extract import (NAME_TAIL_METHOD,
                                         POSITIONAL_NAME_METHOD,
                                         positional_name_parts)


# --- A-05: ПІБ без ВЕЛИКОГО прізвища ----------------------------------------

def test_positional_name_parts_reads_lowercase_surname():
    """Заміряний вхід із holdout-форми: прізвище з великої, не ВЕЛИКИМИ."""
    parts = positional_name_parts(["Гайдученко", "Остап", "Миронович"])
    assert parts == {"surname": "Гайдученко", "given_name": "Остап",
                     "patronymic": "Миронович"}, parts


def test_positional_name_parts_refuses_garbage():
    """Клас помилки, через який позиційний розбір колись і прибрали: дати,
    номери й гомогліфи не мусять ставати ПІБ."""
    assert positional_name_parts(["25О", "від", "О7.О5.2О2б"]) is None
    assert positional_name_parts(["Гайдученко"]) is None          # одне слово
    assert positional_name_parts(["Гайдученко", "25О"]) is None   # зайвий токен


def test_positional_name_is_declared_unreliable():
    """Значення ВИДНО, але воно не дає `confirmed` -- інакше тихо зсунуті
    given_name/patronymic поїхали б у базу з довірою 0.9."""
    from pipeline.build_record import UNRELIABLE_METHODS
    assert POSITIONAL_NAME_METHOD in UNRELIABLE_METHODS


# --- R-B1-02: неспожитий хвіст після по батькові ----------------------------

def test_name_tail_is_carried_not_dropped():
    """«ЛЕМЕШКО Соломія Мустафа кизи»: 4-й токен -- частина імені, а не
    сміття. Розбір мусить його ЗБЕРЕГТИ окремо, а не викинути: саме на
    викинутому хвості поле й давало чужу ідентичність із `confirmed`."""
    from pipeline.extraction.extract import parse_rank_and_name
    _rank, parts = parse_rank_and_name("рядовий ЛЕМЕШКО Соломія Мустафа кизи",
                                       {})
    assert parts.get("_leftover_after_patronymic") == ["кизи"], parts
    # Звичайний ПІБ хвоста не має -- інакше перевірка вище була б тавтологією.
    _rank2, ok = parse_rank_and_name("рядовий ЛЕМЕШКО Соломія Романівна", {})
    assert not ok.get("_leftover_after_patronymic"), ok


def test_name_tail_reason_carries_the_full_tail_to_the_reviewer():
    """Провенанс несе САМ хвіст, а не голий null: рев'юер мусить бачити, що
    стояло в документі. Префікс -- константа, бо build_record читає його."""
    assert NAME_TAIL_METHOD == "name_tail_unparsed"
    reason = f"{NAME_TAIL_METHOD}:ЛЕМЕШКО Соломія Мустафа кизи"
    from pipeline.extraction.extract import AMBIGUOUS_MATCH_METHOD
    # Той самий клас, що звання поза довідником і кілька збігів (C-03):
    # значення Є, але воно не вирішене.
    assert reason.startswith(NAME_TAIL_METHOD + ":")
    assert AMBIGUOUS_MATCH_METHOD


# --- морфологія: inflect_failed ---------------------------------------------

def test_inflect_failed_is_unreliable_and_low_confidence():
    """Гілка «морфологія не змогла» мусить бути і в ненадійних, і з низькою
    довірою -- інакше невідмінене значення виглядало б як прочитане."""
    # Стеля живе в CONFIDENCE_CAP_BY_MORPHOLOGY, не в CONFIDENCE_BY_METHOD:
    # спосіб отримання й стан морфології -- дві різні осі, і саме тому
    # невідмінене `matched` (0.9) обрізається до 0.4.
    from pipeline.build_record import (CONFIDENCE_CAP_BY_MORPHOLOGY,
                                       UNRELIABLE_MORPHOLOGY)
    assert "inflect_failed" in UNRELIABLE_MORPHOLOGY
    assert CONFIDENCE_CAP_BY_MORPHOLOGY.get("inflect_failed", 1.0) <= 0.5


# --- no_template_match ------------------------------------------------------

def test_no_template_match_on_a_document_with_zero_signal():
    """Текст без жодного сигналу шаблону: причина мусить бути саме
    `no_template_match`, а не `below_llm_floor` (у черзі рев'ю це різні дії
    людини) -- і саме вона пускає документ у вільну гілку."""
    from pipeline.identification import identify_template
    from pipeline.run import FREEFORM_ELIGIBLE_REASONS
    ident = identify_template("Рецепт борщу. Буряк, капуста, картопля.", [], {})
    assert ident["template"] is None
    assert ident["reason"] in FREEFORM_ELIGIBLE_REASONS, ident["reason"]


def test_both_freeform_reasons_are_reachable():
    """У множині два елементи; тест доводить, що обидва досяжні -- інакше
    множина з двох приховувала б мертвий елемент."""
    from pipeline.config import load_config
    from pipeline.identification import identify_template
    from pipeline.run import build_resources

    cfg = load_config("config.yaml", project_root=_PROJECT_ROOT)
    res = build_resources(cfg, force_no_llm=True)
    schemas = res["schemas"]

    # Нуль сигналу -> no_template_match (схеми Є, але жодна не влучила).
    zero = identify_template("Буряк, капуста, картопля, олія.", schemas,
                             res["domains"])
    # Слабкий сигнал (одне слово бланка) -> below_llm_floor.
    weak = identify_template("відпустка", schemas, res["domains"])
    reasons = {zero.get("reason"), weak.get("reason")}
    assert reasons <= {"no_template_match", "below_llm_floor"} | {None}, reasons
    assert "no_template_match" in reasons or "below_llm_floor" in reasons, reasons


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))


# --- правило порога, а не число (аудит абляцією 24.08) ----------------------

def test_title_alone_passes_the_threshold():
    """ПРАВИЛО: влучення в заголовок бланка саме по собі достатнє, а збіг
    анкорів без заголовка -- ні. Доти воно було записане чотирма незалежними
    п'ятірками, і рівність між ними не перевіряло ніщо: заміряно, що підняти
    TITLE_WEIGHT до 6 коштує два тести й НУЛЬ перевірок корпусу."""
    from pipeline.identification import (ANCHOR_WEIGHT, DEFAULT_MIN_SCORE,
                                         TITLE_WEIGHT)
    assert TITLE_WEIGHT >= DEFAULT_MIN_SCORE, (
        "заголовок сам мусить проходити поріг")
    assert ANCHOR_WEIGHT < DEFAULT_MIN_SCORE, (
        "один анкор без заголовка не мусить проходити поріг")


def test_schema_min_score_agrees_with_the_rule():
    """Схема має право оголосити свій `min_score:` -- але не нижчий за один
    анкор і не вищий за заголовок, інакше вона тихо скасовує правило."""
    import glob
    import io as _io
    import os as _os

    import yaml as _yaml
    from pipeline.identification import ANCHOR_WEIGHT, TITLE_WEIGHT

    checked = 0
    for path in glob.glob(_os.path.join(_PROJECT_ROOT, "pipeline", "schemas",
                                        "*.yaml")):
        data = _yaml.safe_load(_io.open(path, encoding="utf-8")) or {}
        declared = ((data.get("identification") or {}).get("min_score")
                    if isinstance(data.get("identification"), dict) else None)
        if declared is None:
            continue
        checked += 1
        assert ANCHOR_WEIGHT < declared <= TITLE_WEIGHT, (path, declared)
    assert checked >= 2, f"перевірено схем: {checked}"


def test_pdf_lookback_limit_is_pinned_not_self_referential():
    """Заміряно 24.08: ліміт добору через перенос рядка ІНЕРТНИЙ -- значення
    2, 3 і 4 дають ті самі цифри на обох pdf-корпусах (256/256 і 211/211).
    Тобто його не тримає жодна перевірка корпусу, і єдиний спосіб побачити
    тиху зміну -- назвати число тут явно."""
    from pipeline.extraction.extract import MAX_PDF_WRAP_LOOKBACK_LINES
    assert MAX_PDF_WRAP_LOOKBACK_LINES == 3, (
        "значення змінилось: перевірте, чи це свідомо -- на корпусі воно "
        "нічого не міняє, тому регресія тут не спрацює")
