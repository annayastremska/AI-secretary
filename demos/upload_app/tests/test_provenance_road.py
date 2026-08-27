# -*- coding: utf-8 -*-
"""«Звідки ти знаєш?» -- питання про ДОКАЗ, не про людину.

Блок 4 перевірки (26.08). Такі питання їхали на стару дорогу й отримували
картку особи: перелік документів без способу отримання й без упевненості.
Формально не брехня — відповідь на інше питання. Шаблон `fact_provenance` у
каталозі БУВ і мав рівно ті дані, але свого складача тексту не мав, тобто
віддавав сиру таблицю з колонками.

Третя знахідка того ж блоку: таблиця `fact_sources` (2060 рядків, 68 фактів із
ДВОМА джерелами — файл і фото) не читалась ніде. На питання про доказ система
називала одне джерело з двох.
"""
import pytest

from demos.upload_app.chat_gradio import app as chat_app

tiers = chat_app.tier_chat


@pytest.fixture(autouse=True)
def known_name(monkeypatch):
    monkeypatch.setattr(tiers, "extract_name",
                        lambda q: "Гавриш" if "Гавриш" in q else None)


@pytest.mark.parametrize("question", [
    "Чому ти вважаєш, що Гавриш у відрядженні?",
    "Звідки ти знаєш про Гавриша?",
    "На підставі чого ця відповідь про Гавриша?",
    "Чим підтверджено, що Гавриш у відрядженні?",
])
def test_evidence_questions_go_to_provenance(question):
    """Дорога та сама; ФОРМАТ параметра імені змінився свідомо (28.08).

    Було `%Гавриш%` -- підрядок для `ILIKE`. Саме він давав п. 6-7 звіту
    Дениса: «Богодар» як підрядок ловив «Богодарович», тобто впевнену
    відповідь про іншу людину. Тепер `name_pattern` -- регулярка по межі
    слова, а шаблони порівнюють через `~*`.

    Тому тест перевіряє ПОВЕДІНКУ, а не літерал: шаблон мусить ловити цю
    людину й не мусить ловити довше слово. Так він лишається корисним і після
    наступної зміни формату.
    """
    import re

    tid, params = tiers.rules_route(question)
    assert tid == "fact_provenance", (question, tid)
    rx = params["name_pattern"]
    assert re.search(rx, "Гавриш Адам Станіславович", re.IGNORECASE), rx
    assert not re.search(rx, "Гавришенко Петро Іванович", re.IGNORECASE), (
        "шаблон імені ловить довше прізвище -- це і є дефект п. 7")


def test_normative_questions_still_work():
    """Гейт доказу стоїть ПЕРЕД нормативним -- нормативні не мусили зламатись."""
    assert tiers.rules_route("Які нормативні документи є в базі?")[0] == "normative_list"
    assert tiers.rules_route(
        "За скільки днів подавати рапорт на відпустку?")[0] == "normative_search"


ROW = {
    "name": "Гавриш Адам Станіславович", "dim_name": "Відрядження",
    "dim": "deployment_location", "value": "м. Кривоярськ",
    "valid_from": "2026-08-26", "valid_to": "2026-08-31",
    "status": "confirmed", "confidence": 0.9, "source_field": "place",
    "source_doc_id": 33, "source_kind": "electronic",
    "identification_method": "anchors", "identification_score": "15",
    "provenance": None, "document_number": "207",
    "document_date": "2026-08-23",
    "all_sources": "запис №18 (electronic), запис №33 (photo)",
}


def test_answer_says_how_and_from_where(monkeypatch):
    monkeypatch.setattr(tiers, "_run_template_sql", lambda sql, params: [ROW])
    monkeypatch.setattr(tiers, "_people_total", lambda: 303)
    text, _ = tiers.run_template("fact_provenance",
                                 {"name": "Гавриш",
                                  "name_pattern": "%Гавриш%"})
    assert "документ №207" in text
    assert "бланк упізнано якорями" in text        # спосіб отримання
    assert "впевненість" in text                   # оцінка
    assert "підтверджено людиною" in text          # статус
    assert "джерел у базі кілька" in text          # ОБА джерела, не одне
    assert "Зріз" in text


def test_single_source_does_not_claim_several(monkeypatch):
    monkeypatch.setattr(tiers, "_run_template_sql",
                        lambda sql, params: [dict(ROW, all_sources="запис №33 (electronic)")])
    monkeypatch.setattr(tiers, "_people_total", lambda: 303)
    text, _ = tiers.run_template("fact_provenance",
                                 {"name": "Гавриш", "name_pattern": "%Гавриш%"})
    assert "джерел у базі кілька" not in text


def test_no_facts_says_nothing_was_claimed(monkeypatch):
    monkeypatch.setattr(tiers, "_run_template_sql", lambda sql, params: [])
    monkeypatch.setattr(tiers, "_people_total", lambda: 303)
    text, _ = tiers.run_template("fact_provenance",
                                 {"name": "Ніхто", "name_pattern": "%Ніхто%"})
    assert "нічого не стверджувала" in text
