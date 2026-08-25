# -*- coding: utf-8 -*-
"""Питання про те, чого в базі немає ЯК ПОНЯТТЯ, не має отримувати дані.

Знайдено фінальним адверсарним проходом 25.08:

  «Скільки поранених?»        → таблиця на 72 рядки «хто скільки днів у відпустці»
  «Скільки людей за штатом?»  → дамп ПІБ із таблиці people

Ярус вільного SQL складав ФОРМАЛЬНО безпечний запит, виконував його і віддавав
дані, які не мають до питання стосунку. Валідатор тут не допомагає: він
перевіряє безпеку запиту, а не його доречність.

Наша база тримає документи обліку відсутностей: відпустки, відрядження, звання,
посади, номери й дати документів, нормативні акти (перевірено запитом до
`dimensions` на живій базі). Поранень, втрат, зброї, техніки, штату в ній немає
не як «нуль», а як виду інформації — і з наших документів не з'явиться.

Запуск:
    python -m pytest demos/upload_app/tests/test_unknown_domain_gate.py -q
"""
import pytest

from demos.upload_app.chat_gradio import app as chat_app


UNKNOWN = [
    "Скільки поранених?",
    "Скільки людей за штатом?",
    "Які втрати за місяць?",
    "Скільки одиниць техніки в частині?",
    "Скільки зброї на складі?",
    "Хто в наряді сьогодні?",
    "Який некомплект особового складу?",
]


@pytest.mark.parametrize("question", UNKNOWN)
def test_unknown_domain_gets_an_honest_refusal(question):
    out = chat_app.answer(question)
    low = out.lower()
    assert "не знає" in low or "немає даних про" in low, out[:200]
    # відмова мусить НАЗВАТИ, що система знає -- інакше людина не зрозуміє,
    # чи питати інакше, чи не питати взагалі
    assert "відпустки" in low and "відрядження" in low, out[:300]


@pytest.mark.parametrize("question", UNKNOWN)
def test_unknown_domain_never_returns_a_data_dump(question):
    """Головне: жодних таблиць і жодних ПІБ у відповіді. Саме дамп і був
    дефектом -- він виглядав як відповідь."""
    out = chat_app.answer(question)
    body = out.split("<details")[0]
    assert " | " not in body, f"таблиця у відповіді: {body[:200]}"
    assert body.count("\n- ") == 0, f"перелік у відповіді: {body[:200]}"


#: Запобіжник проти перегину -- як і в інших гейтах. Ці питання база знає, і
#: гейт не має їх з'їдати.
KNOWN = [
    "Скільком зараз у відпустці?",
    "Хто зараз у відрядженні?",
    "Скільки документів у базі?",
    "Скільки непідтверджених фактів?",
]


@pytest.mark.parametrize("question", KNOWN)
def test_known_questions_are_not_eaten(question):
    out = chat_app.answer(question)
    low = out.lower()
    assert "не знає" not in low, out[:200]
    assert "відсутній вид" not in low, out[:200]


def test_gate_runs_before_the_model():
    """Відмова мусить бути дешевою: питання про поранених не має платити
    викликом моделі. Перевіряємо, що дорога -- саме guard-відмова."""
    out = chat_app.answer("Скільки поранених?")
    assert "виду даних немає в базі" in out, out[-300:]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
