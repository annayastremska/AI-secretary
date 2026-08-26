# -*- coding: utf-8 -*-
"""Рік, названий людиною, не підмінюється нашою константою.

Знайдено фінальним адверсарним проходом 25.08: на питання «Скільком у
відпустці 1 січня 1990?» чат відповідав «0 — на **2026**-01-01 чинних
документів немає». Тобто тихо підміняв рік і відповідав на інше питання.

Це гірше за відмову: людина бачить упевнену відповідь про дату, якої не
називала, і не має жодного способу це помітити.

Підстановка `STAND_YEAR` лишається доречною РІВНО там, де рік не назвали
(«а 23 травня?») — і там вона озвучується датою зрізу у відповіді.

Запуск:
    python -m pytest demos/upload_app/tests/test_named_year.py -q
"""
import datetime

import pytest

from demos.upload_app.chat_gradio import app as chat_app


@pytest.mark.parametrize("question,expected", [
    ("1 січня 1990", "1990-01-01"),
    ("Скільком у відпустці 1 січня 1990?", "1990-01-01"),
    ("5 травня 2026 року", "2026-05-05"),
    ("10 вересня 2030", "2030-09-10"),
    ("23 грудня 1999 р.", "1999-12-23"),
])
def test_named_year_is_respected(question, expected):
    assert chat_app.extract_date(question) == expected


@pytest.mark.parametrize("question,expected", [
    ("а 23 травня?", "2026-05-23"),
    ("10 вересня", "2026-09-10"),
])
def test_year_not_named_still_defaults(question, expected):
    """Друга половина: без року поведінка та сама, що була. Інакше правка
    зламала б найчастіше уточнення в діалозі."""
    assert chat_app.extract_date(question) == expected


def test_iso_and_dotted_forms_unchanged():
    assert chat_app.extract_date("2026-08-28") == "2026-08-28"
    assert chat_app.extract_date("15.05.2026") == "2026-05-15"


def test_impossible_date_still_refused():
    """Запобіжник, який уже стояв: «31 лютого» не має ставати датою (інакше
    воно доїжджає до SQL і валить запит)."""
    assert chat_app.extract_date("31 лютого") is None
    assert chat_app.extract_date("31 лютого 1990") is None


def test_no_control_bytes_in_chat_sources():
    """Окремий сторож, і він тут не випадково: цю саму правку я двічі зіпсувала
    скриптом-патчем, який записав у файл керуючий байт (0x08) замість `\\b`.
    Регекс через це не міг спрацювати НІКОЛИ, а очима різниці не видно.
    Той самий клас поломки вже був у серпні з NUL-байтом у лоадері БД."""
    import glob
    import io
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bad = {}
    for path in glob.glob(os.path.join(here, "**", "*.py"), recursive=True):
        if "__pycache__" in path:
            continue
        data = io.open(path, "rb").read()
        found = [hex(b) for b in (0x00, 0x08, 0x0c, 0x1b) if bytes([b]) in data]
        if found:
            bad[os.path.relpath(path, here)] = found
    assert not bad, bad


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_day_is_taken_only_before_the_month_name():
    """«Хто відсутній у 2 роті 28 серпня 2026?» -- день 28, не 2.

    Знайдено живим прогоном 26.08. Регекс дня брав першу пару «цифра +
    слово» по всьому питанню, тому номер підрозділу ставав днем: чат упевнено
    відповідав про 2 серпня -- інша дата, інші люди, вигляд правильної
    відповіді. Тепер день читається лише безпосередньо перед назвою місяця.
    """
    cases = [
        ("Хто відсутній у 2 роті 28 серпня 2026?", datetime.date(2026, 8, 28)),
        ("Скільки у 3 роті у відпустці 6 травня 2026?", datetime.date(2026, 5, 6)),
        ("Скільком у відпустці 1 січня 1990?", datetime.date(1990, 1, 1)),
    ]
    for question, expected in cases:
        on_date, _, _ = chat_app.tier_chat.extract_dates(question)
        assert on_date == expected, f"{question}: {on_date}"
