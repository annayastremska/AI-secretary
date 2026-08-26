# -*- coding: utf-8 -*-
"""Що є в базі — те має бути видно у відповіді. Аудит по всіх вимірах.

Питання Ані 26.08: «ти пропрацював це з усіма видами питань чи тільки для
цього?». Відповідь була «тільки для цього» — я виправила картку особи (посада й
підрозділ), а решту вимірів не перевірила. Аудит по живій базі знайшов дві речі:

1. **`leave_actual_return` існує (51 підтверджений факт), а чат казав, що
   «фактичне повернення в даних не фіксується».** Та сама неправда про власні
   дані, що й із підрозділами: система заперечувала те, що сама має.
2. **У переліках стояв номер ЗАПИСУ під виглядом номера документа:** «документ
   №127 у базі», хоч наказ має номер 1030. На «покажи документ №127» чат
   відповідав «такого немає» — тобто відповідь пропонувала питання, на яке
   потім відмовляла.

Плюс вісім вимірів не показувались ніде: тривалість, куди прибути, організація
й мета відрядження, наказ-підстава, проїзний документ.

Запуск:
    python -m pytest demos/upload_app/tests/test_all_dimensions_surfaced.py -q
"""
import datetime

from demos.upload_app.chat_gradio import app

tiers = app.tier_chat

#: Рядок відсутності, як його тепер віддає db._absence_row -- з усіма вимірами.
ROW = {
    "doc_number": "№1030", "doc_date": "2026-08-12", "doc_type": "відпустка",
    "service_id": "ID-48", "person_name_raw": "Влох Святослав Олесьович",
    "date_from": "2026-08-13", "date_to": "2026-09-01",
    "reason": "відпустка за сімейними обставинами", "place": "с. Соснова Гряда",
    "status": "чинний", "fact_status": "confirmed", "superseded_by": "",
    "source_file": "запис №127 у базі (docx)",
    "leave_days": "20", "unit_to_report": "в/ч А1234",
    "actual_return": "2026-08-25", "deployment_org": "", "deployment_purpose": "",
    "deployment_days": "", "order_number": "", "order_date": "",
    "travel_document": "",
}


def test_document_card_shows_what_the_base_knows():
    text = app.describe_doc(ROW)
    for expected in ("днів 20", "прибути до", "фактично повернувся 2026-08-25"):
        assert expected in text, f"{expected!r} немає у картці: {text}"


def test_document_card_stays_silent_about_empty_fields():
    """Порожнє поле не згадуємо: інакше відповідь роздувається прочерками."""
    text = app.describe_doc(dict(ROW, actual_return="", leave_days=""))
    assert "фактично повернувся" not in text
    assert "днів" not in text.replace("днів 20", "")


def test_absent_answer_no_longer_denies_actual_return(monkeypatch):
    """Раніше тут стояло «відмітки про фактичне повернення в даних немає»."""
    monkeypatch.setattr(app.db, "absences_on_date", lambda *a, **k: [ROW])
    monkeypatch.setattr(app.db, "coverage_note", lambda *a, **k: "")
    monkeypatch.setattr(app.db, "unconfirmed_absences_on_date", lambda *a, **k: 0)
    monkeypatch.setattr(app.db, "people_total", lambda: 303)
    out = app.answer_absent("2026-08-30", None, not_returned=True)
    assert "фактичне повернення в даних немає" not in out
    assert "1 із 1" in out


def test_list_shows_document_number_not_record_id(monkeypatch):
    """У переліку мусить бути номер документа, а номер запису — названий записом."""
    rows = [{"name": "Влох Святослав Олесьович", "dim": "leave",
             "valid_from": "2026-08-13", "valid_to": "2026-09-01",
             "source_doc_id": 127, "doc_number": "1030"}]
    monkeypatch.setattr(tiers, "_run_template_sql", lambda sql, params: rows)
    monkeypatch.setattr(tiers, "_people_total", lambda: 303)
    monkeypatch.setattr(tiers, "_unmatched_in_state", lambda params: 0)
    text, _ = tiers.run_template(
        "list_by_state", {"dims": ["leave"], "state": "leave",
                          "date_from": "2026-08-30", "date_to": "2026-08-30"})
    assert "документ №1030" in text
    assert "запис №127" in text
    assert "документ №127" not in text


def test_list_says_plainly_when_document_number_is_missing(monkeypatch):
    rows = [{"name": "Хтось", "dim": "leave", "valid_from": "2026-08-13",
             "valid_to": "2026-09-01", "source_doc_id": 127, "doc_number": None}]
    monkeypatch.setattr(tiers, "_run_template_sql", lambda sql, params: rows)
    monkeypatch.setattr(tiers, "_people_total", lambda: 303)
    monkeypatch.setattr(tiers, "_unmatched_in_state", lambda params: 0)
    text, _ = tiers.run_template(
        "list_by_state", {"dims": ["leave"], "state": "leave",
                          "date_from": "2026-08-30", "date_to": "2026-08-30"})
    assert "номер документа не витягнуто" in text


def _row(**kw):
    return dict(ROW, **kw)


def test_current_absence_ignores_finished_documents():
    """«Поза частиною» -- це про сьогодні, а не про «є чинний документ».

    Живий прогін 26.08 на Гавришу: відповідь казала «Поза частиною —
    відпустка №101, до 2026-08-14», хоч та відпустка скінчилась 12 днів тому і
    в тій самій відповіді стояло «фактично повернувся 2026-08-15». Сьогодні він
    у відрядженні. `status == 'чинний'` -- про довіру до факту, не про те, чи
    людини немає ЗАРАЗ."""
    today = datetime.date(2026, 8, 26)
    finished = _row(doc_number="№101", date_from="2026-08-03",
                    date_to="2026-08-14", actual_return="2026-08-15")
    ongoing = _row(doc_number="№207", doc_type="відрядження",
                   date_from="2026-08-26", date_to="2026-08-31",
                   actual_return="")
    future = _row(doc_number="№300", date_from="2026-09-10",
                  date_to="2026-09-20", actual_return="")
    got = app.current_absences([finished, ongoing, future], today=today)
    assert [r["doc_number"] for r in got] == ["№207"]


def test_returned_person_is_told_they_are_in_the_unit(monkeypatch):
    """Чинний документ є, але він у минулому -> «У частині», не «Поза»."""
    today = datetime.date(2026, 8, 26)
    finished = _row(doc_number="№101", date_from="2026-08-03",
                    date_to="2026-08-14", actual_return="2026-08-15")
    monkeypatch.setattr(app.db, "find_people", lambda name=None, **k: [])
    monkeypatch.setattr(app.db, "absences_for_person", lambda *a, **k: [finished])
    monkeypatch.setattr(app.db, "coverage_note", lambda *a, **k: "")
    monkeypatch.setattr(app.datetime, "date",
                        type("D", (), {"today": staticmethod(lambda: today),
                                       "fromisoformat": datetime.date.fromisoformat}))
    out = app.answer_person("Гавриш")
    assert "**У частині**" in out
    assert "Поза частиною" not in out
    assert "фактично повернувся 2026-08-15" in out


def test_future_absence_is_not_called_the_last_one(monkeypatch):
    """«Остання» і «найближча» -- різні твердження.

    Живий прогін на Малишко 26.08: усі три її відпустки в майбутньому, а
    відповідь казала «Остання: відпустка №141, до 2026-09-20» -- бо бралось
    max(date_to). Це майбутнє під підписом минулого."""
    today = datetime.date(2026, 8, 26)
    soon = _row(doc_number="№118", date_from="2026-08-27",
                date_to="2026-09-09", actual_return="")
    later = _row(doc_number="№141", date_from="2026-09-14",
                 date_to="2026-09-20", actual_return="")
    monkeypatch.setattr(app.db, "find_people", lambda name=None, **k: [])
    monkeypatch.setattr(app.db, "absences_for_person", lambda *a, **k: [soon, later])
    monkeypatch.setattr(app.db, "coverage_note", lambda *a, **k: "")
    monkeypatch.setattr(app.datetime, "date",
                        type("D", (), {"today": staticmethod(lambda: today),
                                       "fromisoformat": datetime.date.fromisoformat}))
    out = app.answer_person("Малишко")
    assert "Найближча: відпустка №118, з 2026-08-27" in out
    assert "Остання" not in out
