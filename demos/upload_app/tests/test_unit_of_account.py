# -*- coding: utf-8 -*-
"""Блок E: одиниця обліку, знаменник і перелік поруч із числом.

Шість пунктів звіту Дениса (11–14, 19, 21) — і всі про одне: **у відповіді не
сказано, ЩО саме порахували.** Кожна цифра окремо правдива, а на екрані вони
читаються як брехня:

* «9» рахує людей, «10» рахує рядки — та сама дата (п. 12);
* «12 у відпустці» і «15 поза частиною» — теж та сама дата (п. 13, 19);
* «303 в реєстрі» і «300 за штаткою» (п. 14);
* зайві пробіли міняли метрику й знаменник (п. 21).

## Що тут закріплюється

1. **люди, а не рядки** — і якщо документів більше за людей, це САМЕ сказано.
   Ховати різницю не можна: вона від анульованого квитка, тобто від дірки в
   даних (блок D), і мовчання про неї приховало б дірку;
2. **перелік поруч із числом** (вимога Ані 28.08) — з порогом, бо кількасот
   прізвищ це не відповідь;
3. **звірка переліку з числом** — перелік і число беруть дані РІЗНИМИ запитами,
   і саме там у п. 12 розійшлись цифри. Розбіжність кажеться вголос;
4. **нормалізація вводу** — невидимі пробіли не міняють дорогу.

Запуск:
    python -m pytest demos/upload_app/tests/test_unit_of_account.py -q
"""
import datetime

import pytest

from demos.upload_app.chat_gradio import app as chat_app

tiers = chat_app.tier_chat


@pytest.fixture(autouse=True)
def roster(monkeypatch):
    monkeypatch.setattr(tiers, "subdivisions", lambda: ["1-ша механізована рота"])


# ── Нормалізація вводу (п. 21) ─────────────────────────────────────────────

@pytest.mark.parametrize("odd", [
    "Скільком  осіб  у  відпустці  2026-10-10 ?",
    "Скільком осіб у відпустці 2026-10-10?",
    "  Скільком осіб у відпустці 2026-10-10?  ",
    "Скільком осіб у відпустці 2026-10-10 ?",
])
def test_invisible_whitespace_does_not_change_the_question(odd):
    """Для людини ці питання однакові -- значить і відповідь та сама."""
    canonical = "Скільком осіб у відпустці 2026-10-10?"
    assert chat_app.normalize_question(odd) == canonical, repr(odd)


def test_normalisation_does_not_rewrite_the_text():
    """Межа: прибираємо лише невидиме. Слова, орфографію й знаки не чіпаємо --
    інакше це вже виправлення вводу, про яке треба казати."""
    q = "Хто у відпусці в 3 ротті?"          # обидві одруківки лишаються
    assert chat_app.normalize_question(q) == q


def test_normalisation_makes_the_road_identical():
    """Головне: однакова дорога, а не лише однаковий рядок."""
    a = tiers.rules_route(chat_app.normalize_question(
        "Скільком  осіб  у  відпустці  2026-10-10 ?"))
    b = tiers.rules_route("Скільком осіб у відпустці 2026-10-10?")
    assert a == b, (a, b)


# ── Люди, а не рядки (п. 12) ──────────────────────────────────────────────

def _rows(names):
    """Рядки відсутності: одна людина може мати кілька документів."""
    return [{"person_name_raw": nm, "doc_number": f"№{100 + i}",
             "doc_type": "відпустка", "date_from": "2026-09-01",
             "date_to": "2026-09-09", "place": "", "reason": "щорічна",
             "status": "чинний", "fact_status": "confirmed",
             "actual_return": "", "leave_days": "", "unit_to_report": "",
             "deployment_org": "", "deployment_purpose": "",
             "deployment_days": "", "order_number": "", "order_date": "",
             "travel_document": "", "superseded_by": "", "service_id": "",
             "source_file": "запис у базі", "object_id": 1 + i}
            for i, nm in enumerate(names)]


def test_count_is_people_and_the_document_count_is_named(monkeypatch):
    """п. 12: Малишко з двома квитками не мусить ставати двома людьми.

    І друга половина, без якої перша неповна: різниця між людьми й документами
    мусить бути НАЗВАНА. Вона від анульованого квитка, тобто від дірки в даних
    -- сховати її означало б сховати дірку.
    """
    rows = _rows(["Малишко Камілла Омелянівна", "Малишко Камілла Омелянівна",
                  "Ґоляш Богодар Святославович"])
    monkeypatch.setattr(chat_app.db, "absences_on_date",
                        lambda *a, **kw: rows)
    monkeypatch.setattr(chat_app.db, "coverage_note", lambda date=None: "")
    monkeypatch.setattr(chat_app.db, "unconfirmed_absences_on_date",
                        lambda date: 0)
    monkeypatch.setattr(chat_app.db, "people_total", lambda: 303)

    out = chat_app.answer_absent("2026-09-02", None)

    # Розмітка навколо числа («**2 особи**») -- частина рендера, тому
    # перевіряємо частинами, а не одним рядком.
    assert "**2 особи**" in out and "поза частиною" in out, out
    assert "документів про це 3" in out, out
    # І метрика названа: «поза частиною» -- це не «у відпустці»
    assert "відпустка або відрядження" in out, out


def test_no_document_note_when_counts_agree(monkeypatch):
    """Побічна шкода: рядок про документи не має з'являтись, коли різниці
    немає -- інакше він стане шумом у кожній відповіді."""
    rows = _rows(["Ґоляш Богодар Святославович", "Малишко Камілла Омелянівна"])
    monkeypatch.setattr(chat_app.db, "absences_on_date", lambda *a, **kw: rows)
    monkeypatch.setattr(chat_app.db, "coverage_note", lambda date=None: "")
    monkeypatch.setattr(chat_app.db, "unconfirmed_absences_on_date",
                        lambda date: 0)
    monkeypatch.setattr(chat_app.db, "people_total", lambda: 303)

    out = chat_app.answer_absent("2026-09-02", None)
    assert "документів про це" not in out, out


# ── Перелік поруч із числом (вимога Ані 28.08) ────────────────────────────

def test_count_answer_lists_the_people(monkeypatch):
    """«1 особа у відпустці» без прізвища перевірити неможливо."""
    listed = [{"name": "Ґоляш Богодар Святославович", "dim": "leave",
               "value": "щорічна", "valid_from": "2026-09-21",
               "valid_to": "2026-10-10", "source_doc_id": 64,
               "doc_number": "1077"}]
    monkeypatch.setattr(tiers, "_run_template_sql", lambda sql, p: listed)

    lines = tiers._names_with_count(
        {"dims": ["leave"], "on_date": datetime.date(2026, 10, 10)}, 1)

    assert lines and lines[0] == "Поіменно:"
    assert any("Ґоляш" in ln for ln in lines), lines


def test_long_list_is_refused_with_a_way_out(monkeypatch):
    """Поріг: кількасот прізвищ -- не відповідь. Але людині сказано, як їх
    отримати, інакше відмова стає тупиком."""
    lines = tiers._names_with_count(
        {"dims": ["leave"], "on_date": datetime.date(2026, 10, 10)}, 300)
    assert len(lines) == 1
    assert "покажи поіменно" in lines[0], lines


def test_mismatch_between_list_and_number_is_said_out_loud(monkeypatch):
    """ГОЛОВНА перевірка цього блоку.

    Перелік і число беруть дані РІЗНИМИ запитами -- рівно там у п. 12 звіту
    розійшлись «9 людей» і «10 рядків». Якщо вони не збігаються, відповідь
    мусить це сказати, а не покласти суперечність на один екран.
    """
    listed = [{"name": f"Особа {i}", "dim": "leave", "value": "щорічна",
               "valid_from": "2026-09-01", "valid_to": "2026-09-09",
               "source_doc_id": i, "doc_number": str(i)} for i in range(3)]
    monkeypatch.setattr(tiers, "_run_template_sql", lambda sql, p: listed)

    lines = tiers._names_with_count(
        {"dims": ["leave"], "on_date": datetime.date(2026, 10, 10)}, 2)

    assert any("⚠️" in ln and "число вище" in ln for ln in lines), lines


def test_zero_gets_no_list(monkeypatch):
    """Нуль -- це вже повна відповідь; «поіменно: нікого» було б шумом."""
    assert tiers._names_with_count({"dims": ["leave"]}, 0) == []


def test_list_failure_does_not_break_the_number(monkeypatch):
    """Перелік -- додаткове знання. Немає його -- число лишається числом."""
    import psycopg

    def boom(sql, p):
        raise psycopg.OperationalError("база недоступна")

    monkeypatch.setattr(tiers, "_run_template_sql", boom)
    assert tiers._names_with_count(
        {"dims": ["leave"], "on_date": datetime.date(2026, 10, 10)}, 2) == []


# ── Названий стан рахується РІВНО як названий (рішення Ані 28.08) ──────────

def _absent_call(monkeypatch, state):
    """-> (текст відповіді, вимір, який поїхав у базу)."""
    seen = {}

    def fake(date, subdivision=None, doc_type=None, confirmed=True, dim=None):
        seen["dim"] = dim
        return _rows(["Ґоляш Богодар Святославович"])

    monkeypatch.setattr(chat_app.db, "absences_on_date", fake)
    monkeypatch.setattr(chat_app.db, "coverage_note", lambda date=None: "")
    monkeypatch.setattr(chat_app.db, "unconfirmed_absences_on_date",
                        lambda date: 0)
    monkeypatch.setattr(chat_app.db, "people_total", lambda: 303)
    out = chat_app.answer_absent("2026-09-02", None, state=state)
    return out, seen.get("dim")


def test_named_leave_counts_only_leave(monkeypatch):
    """п. 13 і 19: на питання ПРО ВІДПУСТКУ чат відповідав числом «поза
    частиною» -- відпустка ПЛЮС відрядження. Звідси 12 і 15 на одну дату."""
    out, dim = _absent_call(monkeypatch, "leave")
    assert dim == "leave", "у базу поїхали обидва виміри"
    assert "у відпустці" in out, out
    assert "поза частиною" not in out, out


def test_named_deployment_counts_only_deployment(monkeypatch):
    out, dim = _absent_call(monkeypatch, "deployment")
    assert dim == "deployment_location"
    assert "у відрядженні" in out, out


def test_unnamed_state_still_says_what_it_counted(monkeypatch):
    """Людина спитала «скільком відсутніх» -- метрика ширша, і саме тому її
    треба назвати: «поза частиною» це не самоочевидне слово."""
    out, dim = _absent_call(monkeypatch, None)
    assert dim is None, "фільтр поставлено там, де про нього не просили"
    assert "поза частиною (відпустка або відрядження)" in out, out


def test_state_comes_from_the_question(monkeypatch):
    """Наскрізь: стан мусить доїхати з тексту питання, а не задаватись рукою."""
    assert tiers.extract_state("Скільком осіб у відпустці 2026-09-01?") == "leave"
    assert tiers.extract_state("Хто у відрядженні 2026-09-01?") == "deployment"
    assert tiers.extract_state("Скільком відсутніх 2026-09-01?") == "absent"
