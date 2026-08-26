"""Особа без відповідника у штатці має бути видна у відповіді.

Нащо: пайплайн такі випадки віддає людині на розгляд (завдання `new_person`),
але факти по них лягають ПІДТВЕРДЖЕНИМИ, тому людина входить у цифру як
звичайна. Заміряно 26.08 на живій базі: у «9 у відпустці на 28.08» один із
дев'яти -- Крижанівський, якого у штатці немає. Статус фактів -- зона
завантажувача (Андрій), а наша частина правди -- сказати про це вголос.

Тест ловить саме зникнення цих двох рядків: цифра лишається тією самою, а
попередження про неї -- ні.
"""
from demos.upload_app.chat_gradio import app

# та сама копія tiers, яку бачить апка (у процесі їх може виявитися дві --
# див. test_single_module_instance.py), інакше патчі підуть у чужу копію
tiers = app.tier_chat


def test_card_says_person_is_not_in_roster(monkeypatch):
    monkeypatch.setattr(app.db, "find_people", lambda name=None, **k: [
        {"service_id": "ID-77", "full_name": "Крижанівський Тарас Богданович",
         "rank": "солдат", "position_title": "", "subdivision": "",
         "in_roster": False}])
    monkeypatch.setattr(app.db, "absences_for_person", lambda *a, **k: [])
    out = app.answer_person("Крижанівський")
    assert "у штатці немає" in out
    assert "чекає підтвердження людиною" in out


def test_card_silent_when_person_is_in_roster(monkeypatch):
    monkeypatch.setattr(app.db, "find_people", lambda name=None, **k: [
        {"service_id": "UNIT-0269", "full_name": "Швайка Давид Борисович",
         "rank": "старший сержант", "position_title": "", "subdivision": "",
         "in_roster": True}])
    monkeypatch.setattr(app.db, "absences_for_person", lambda *a, **k: [])
    out = app.answer_person("Швайка")
    assert "у штатці немає" not in out


def _count_answer(monkeypatch, unmatched):
    """Відповідь шаблону count_by_state_on_date на підставних цифрах."""
    monkeypatch.setattr(tiers, "_unmatched_in_state", lambda params: unmatched)
    monkeypatch.setattr(tiers, "_people_total", lambda: 303)
    monkeypatch.setattr(tiers, "_run_template_sql",
                        lambda sql, params: [{"n": 9}])
    text, _ = tiers.run_template("count_by_state_on_date",
                                 {"dims": ["leave"], "state": "leave",
                                  "on_date": "2026-08-28"})
    return text


def test_count_answer_names_unmatched_people(monkeypatch):
    out = _count_answer(monkeypatch, 1)
    assert "9" in out
    assert "З них 1 — особи, яких немає у штатці" in out


def test_count_answer_silent_when_all_matched(monkeypatch):
    assert "у штатці" not in _count_answer(monkeypatch, 0)


def test_note_reaches_period_and_list_templates(monkeypatch):
    """Один текст на три шаблони підрахунку -- інакше правда буде лише в
    одному з трьох питань («скільком зараз», «скільком за період», «хто»)."""
    monkeypatch.setattr(tiers, "_unmatched_in_state", lambda params: 2)
    monkeypatch.setattr(tiers, "_people_total", lambda: 303)
    monkeypatch.setattr(tiers, "_run_template_sql",
                        lambda sql, params: [{"n": 5}])
    period, _ = tiers.run_template(
        "count_by_state_period",
        {"dims": ["leave"], "state": "leave",
         "date_from": "2026-08-01", "date_to": "2026-08-31"})
    assert "З них 2 — особи, яких немає у штатці" in period

    monkeypatch.setattr(tiers, "_run_template_sql", lambda sql, params: [])
    listing, _ = tiers.run_template(
        "list_by_state",
        {"dims": ["leave"], "state": "leave",
         "date_from": "2026-08-01", "date_to": "2026-08-31"})
    assert "З них 2 — особи, яких немає у штатці" in listing


def test_unmatched_helper_returns_zero_without_dims():
    # немає з чим питати базу -> 0, а не виняток і не вигадана цифра
    assert tiers._unmatched_in_state({}) == 0
    assert tiers._unmatched_in_state({"dims": ["leave"]}) == 0


def test_person_status_marks_the_name(monkeypatch):
    """Другий шлях відповіді про людину -- шаблон каталогу person_status.
    Там цифри немає, є ім'я, тож позначка мусить стояти біля імені."""
    facts = [{"name": "Крижанівський Тарас Богданович",
              "dim_name": "відпустка", "dim": "leave", "value": "сімейні",
              "valid_from": "2026-08-26", "valid_to": "2026-09-01",
              "status": "confirmed", "confidence": 0.9, "source_doc_id": 124}]

    def fake_sql(sql, params):
        if "service_id IS NULL" in sql:
            return [{"name": "Крижанівський Тарас Богданович"}]
        return facts

    monkeypatch.setattr(tiers, "_run_template_sql", fake_sql)
    monkeypatch.setattr(tiers, "_people_total", lambda: 303)
    text, _ = tiers.run_template("person_status",
                                 {"name_pattern": "%Крижанівськ%"})
    assert "відповідника у штатці немає" in text
