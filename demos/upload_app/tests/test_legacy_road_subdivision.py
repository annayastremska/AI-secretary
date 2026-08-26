# -*- coding: utf-8 -*-
"""Стара дорога не має заперечувати підрозділи, які в базі Є.

Знайдено глибоким аналізом 26.08. У `db.py` три функції повертали [] із
коментарем «зв'язку особа→підрозділ немає», а чат казав «База цього не знає: у
схемі немає зв'язку особа→підрозділ». Після заливки штатки (25.08, 300 осіб із
виміром `subdivision`) це стало **неправдою про власні дані**.

Шаблони каталогу такі питання зазвичай перехоплюють — але «зазвичай» не
гарантія: досить іншого формулювання, і людина почує заперечення того, що
система має. Тому обидві половини правди під тестом:
  * підрозділ, який у штатці Є -> фільтр працює, заперечення немає;
  * підрозділ, якого немає -> відмова, і причина саме та (немає підрозділу, а
    не «немає зв'язку в схемі»).

Запуск:
    python -m pytest demos/upload_app/tests/test_legacy_road_subdivision.py -q
"""
from demos.upload_app.chat_gradio import app

KNOWN = ["1-ша механізована рота", "2-га механізована рота",
         "3-тя механізована рота", "взвод забезпечення",
         "управління батальйону"]


def _known(monkeypatch, values=KNOWN):
    monkeypatch.setattr(app.db, "subdivision_values", lambda: values)


def test_existing_subdivision_is_recognised(monkeypatch):
    _known(monkeypatch)
    for asked in ("2 рота", "2-га механізована рота", "взвод забезпечення",
                  "1 рота"):
        assert app.subdivision_exists(asked), asked


def test_missing_subdivision_is_not_invented(monkeypatch):
    _known(monkeypatch)
    for asked in ("5 рота", "танковий батальйон", "7-ма рота"):
        assert not app.subdivision_exists(asked), asked


def test_refusal_names_the_real_reason(monkeypatch):
    _known(monkeypatch)
    text = app.no_such_subdivision("5 рота")
    assert "такого підрозділу в штатці немає" in text.lower()
    # саме те, чого тут бути НЕ мусить: стара причина була неправдою
    assert "зв'язку" not in text
    assert "взвод забезпечення" in text


def test_absent_answer_no_longer_denies_the_data(monkeypatch):
    """Питання «хто відсутній у 2 роті» мусить дійти до бази з фільтром, а не
    впертись у заперечення."""
    _known(monkeypatch)
    seen = {}

    def fake_absences(date, subdivision=None, **kw):
        seen["subdivision"] = subdivision
        return []

    monkeypatch.setattr(app.db, "absences_on_date", fake_absences)
    monkeypatch.setattr(app.db, "coverage_note", lambda *a, **k: "")
    monkeypatch.setattr(app.db, "unconfirmed_absences_on_date",
                        lambda *a, **k: 0)
    monkeypatch.setattr(app.db, "people_total", lambda: 303)
    out = app.answer_absent("2026-08-28", "2 рота")
    assert seen["subdivision"] == "2 рота", "фільтр не доїхав до бази"
    assert "у схемі немає" not in out


def test_absent_answer_refuses_unknown_subdivision(monkeypatch):
    _known(monkeypatch)
    out = app.answer_absent("2026-08-28", "5 рота")
    assert "штатці немає" in out
