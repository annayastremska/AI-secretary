# -*- coding: utf-8 -*-
"""Блок «джерело» мусить називати ДОКУМЕНТИ, а не лише SQL.

Правило продукту, а не оформлення. Блок «джерело» існує, щоб цифру можна було
перевірити. Для інженера це робить SQL. Для офіцера, який приймає рішення, SQL
не означає нічого: перевірити цифру він може, лише піднявши сам документ — а
щоб його підняти, потрібні тип, номер і дата (запит Ані 27.08). Тобто блок був
адресований не тому, хто ним користується.

Тест стежить за трьома речами, кожна з яких легко відкочується випадково:
  1. документи названі — тип, номер, дата;
  2. SQL лишився (прозорість не зменшуємо, лише перестаємо ставити її першою);
  3. недоступний провенанс НЕ забирає з собою відповідь.
"""
import psycopg
import pytest

from demos.upload_app.chat_gradio import tiers


DOCS = [
    {"doc_id": 141, "doc_type": "Відпускний квиток", "doc_number": "124",
     "doc_date": "2026-08-23", "facts": 8},
    {"doc_id": 118, "doc_type": "Відпускний квиток", "doc_number": "118",
     "doc_date": "2026-08-21", "facts": 7},
]


def test_documents_are_named_with_type_number_date(monkeypatch):
    monkeypatch.setattr(tiers, "_run_template_sql", lambda sql, p: DOCS)
    lines = tiers._source_doc_lines({"dims": ["leave"], "on_date": "2026-08-27"})
    text = "\n".join(lines)
    assert "документів-джерел: 2" in text
    assert "Відпускний квиток №124 від 2026-08-23" in text
    assert "полів у відповіді: 8" in text
    # Номер ЗАПИСУ теж лишається: по ньому шукають у базі, і плутати його з
    # номером документа не можна (та сама причина, що в _doc_ref).
    assert "запис №141 у базі" in text


def test_long_list_is_folded(monkeypatch):
    many = [dict(DOCS[0], doc_id=i, doc_number=str(i)) for i in range(20)]
    monkeypatch.setattr(tiers, "_run_template_sql", lambda sql, p: many)
    lines = tiers._source_doc_lines({"dims": ["leave"], "on_date": "2026-08-27"})
    # Показуємо шість і кажемо, скільком лишилось: перелік на пів екрана в
    # блоці «джерело» ніхто не читає, отже він не допомагає перевірити цифру.
    assert sum(1 for ln in lines if ln.startswith("  · ")) == \
        tiers.SOURCE_DOCS_SHOWN + 1
    assert f"і ще {20 - tiers.SOURCE_DOCS_SHOWN} документів" in lines[-1]


def test_period_window_is_used(monkeypatch):
    seen = {}

    def fake(sql, params):
        seen["sql"] = sql
        seen["params"] = params
        return DOCS

    monkeypatch.setattr(tiers, "_run_template_sql", fake)
    tiers._source_doc_lines({"dims": ["leave"], "date_from": "2026-06-01",
                             "date_to": "2026-06-30"})
    assert "%(date_from)s" in seen["sql"] and "%(date_to)s" in seen["sql"]
    # Значення завжди йдуть ПАРАМЕТРАМИ: у рядок склеюється лише вибір між
    # двома готовими умовами вікна.
    assert seen["params"]["date_from"] == "2026-06-01"
    assert "2026-06-01" not in seen["sql"]


def test_no_dims_or_no_window_means_no_lines(monkeypatch):
    """Шаблон без виміру або без дати (документи, нормативка, довідка) — не
    родина «про стан», і провенанс до нього не застосовується."""
    monkeypatch.setattr(tiers, "_run_template_sql",
                        lambda sql, p: pytest.fail("не мусив питати базу"))
    assert tiers._source_doc_lines({}) == []
    assert tiers._source_doc_lines({"dims": ["leave"]}) == []


def test_unavailable_provenance_does_not_break_the_answer(monkeypatch):
    """Найважливіше. Провенанс — додаткове знання; якщо база його не дала,
    людина однаково мусить отримати відповідь на своє питання."""
    def boom(sql, params):
        raise psycopg.OperationalError("база недоступна")

    monkeypatch.setattr(tiers, "_run_template_sql", boom)
    assert tiers._source_doc_lines({"dims": ["leave"],
                                    "on_date": "2026-08-27"}) == []


def test_sql_stays_but_is_no_longer_first():
    """Прозорість не зменшується: запит лишається у блоці. Але він більше не
    перший рядок і має власний підпис «технічний»."""
    src = tiers.__file__
    text = open(src, encoding="utf-8").read()
    assert 'source.append(f"шаблон каталогу: {t[\'title\']} ({template_id})")' \
        in text
    assert '"технічний запит шаблону:", t["sql"].strip()' in text
