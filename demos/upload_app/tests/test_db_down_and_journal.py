# -*- coding: utf-8 -*-
"""«Не знайшла» ≠ «база недоступна», і кожен хід має номер (етап 5).

Критерій приймання П3. Заміряно 25.08.2026: при впалій базі `answer()`
**падав з винятком** — тобто людина бачила технічну помилку, а не відповідь.
Це порушення не косметичне: «нічого не знайшлося» і «до даних не дістали» —
різні твердження, і плутати їх не можна. У першому випадку відповідь є (нуль);
у другому відповіді немає взагалі, а цифри в базі цілі.

Друга половина — номер звернення. На демо й у пілоті людина каже «чат відповів
дивно», і без номера це нерозв'язна задача. Номер видно в блоці «джерело», і
по ньому в журналі знаходиться саме той хід.

Запуск:
    python -m pytest demos/upload_app/tests/test_db_down_and_journal.py -q
"""
import logging

import psycopg
import pytest

from demos.upload_app.chat_gradio import app as chat_app


def test_db_down_gives_an_honest_answer_not_an_exception(monkeypatch):
    """ГОЛОВНЕ: виняток бази не доходить до людини."""
    def _boom(*a, **kw):
        raise psycopg.OperationalError("connection timeout expired")

    monkeypatch.setattr(chat_app, "_answer_inner", _boom)
    out = chat_app.answer("Скільком зараз у відпустці?")
    assert isinstance(out, str)
    assert "недоступна" in out


def test_db_down_text_differs_from_nothing_found(monkeypatch):
    """І це ДРУГИЙ текст, а не той самий: інакше людина не відрізнить збій від
    порожнього результату."""
    def _boom(*a, **kw):
        raise psycopg.OperationalError("connection timeout expired")

    monkeypatch.setattr(chat_app, "_answer_inner", _boom)
    db_down = chat_app.answer("Скільком зараз у відпустці?")
    assert db_down != chat_app.ANSWER_REFUSE
    assert "не лягає" not in db_down, "збій подано як «питання не лягає» — П3"
    # і навпаки: у тексті збою не має бути обіцянки, що даних немає
    assert "немає таких даних" not in db_down


def test_other_exceptions_are_not_masked(monkeypatch):
    """Запобіжник проти «щось пішло не так»: наші баги мусять лишатись
    видимими, інакше ми ховаємо їх від себе."""
    def _bug(*a, **kw):
        raise ValueError("це наш баг, а не база")

    monkeypatch.setattr(chat_app, "_answer_inner", _bug)
    with pytest.raises(ValueError):
        chat_app.answer("Скільком зараз у відпустці?")


def test_request_id_is_in_the_answer(monkeypatch):
    """Номер видно людині -- інакше його неможливо продиктувати."""
    monkeypatch.setattr(chat_app, "_answer_inner",
                        lambda q, h=None: "Відповідь." + chat_app.footer("тест"))
    out = chat_app.answer("будь-що")
    assert "звернення: " in out
    cid = out.split("звернення: ")[1][:6]
    assert len(cid) == 6 and cid.isalnum(), cid


def test_journal_line_carries_the_same_id(monkeypatch, caplog):
    """Номер у відповіді і номер у журналі -- один. Інакше він марний."""
    monkeypatch.setattr(chat_app, "_answer_inner",
                        lambda q, h=None: "Відповідь." + chat_app.footer("тест"))
    with caplog.at_level(logging.INFO, logger="chat.journal"):
        out = chat_app.answer("скільком зараз у відпустці?")
    cid = out.split("звернення: ")[1][:6]
    assert any(cid in rec.getMessage() for rec in caplog.records), caplog.text


def test_journal_does_not_leak_database_values(monkeypatch, caplog):
    """У журнал їде питання людини й службові цифри -- але не вміст відповіді
    (там ПІБ і дати з документів). Пишемо довжину, не текст."""
    secret = "ГАВРИШ Адам Станіславович"
    monkeypatch.setattr(chat_app, "_answer_inner",
                        lambda q, h=None: f"У відпустці: {secret}")
    with caplog.at_level(logging.INFO, logger="chat.journal"):
        chat_app.answer("хто у відпустці?")
    assert secret not in caplog.text


def test_id_is_different_per_request(monkeypatch):
    monkeypatch.setattr(chat_app, "_answer_inner",
                        lambda q, h=None: "Відповідь." + chat_app.footer("тест"))
    ids = {chat_app.answer("питання").split("звернення: ")[1][:6]
           for _ in range(5)}
    assert len(ids) == 5, ids


def test_refusals_also_carry_the_id(monkeypatch):
    """Знайдено verify_stack на сервері: відмови приходили БЕЗ номера, бо це
    готові константи -- їхній footer зібрався при імпорті модуля, коли номера
    ще не існувало. А скаржаться люди саме на відмови («чат відмовив, а
    чому?»), тобто номер потрібен там найбільше."""
    monkeypatch.setattr(chat_app, "_answer_inner",
                        lambda q, h=None: chat_app.ANSWER_REFUSE)
    out = chat_app.answer("яка завтра погода?")
    assert "звернення: " in out


def test_id_is_added_inside_the_source_block_not_after_state_marker(monkeypatch):
    """Номер дописується в блок «джерело», а не в кінець тексту: у кінці
    стоїть службовий маркер стану діалогу, і текст після нього зламав би його
    читання наступним ходом."""
    marker = ">>STATE"
    monkeypatch.setattr(
        chat_app, "_answer_inner",
        lambda q, h=None: "Цифра." + chat_app.footer("тест") + marker)
    out = chat_app.answer("питання")
    assert out.endswith(marker), out[-80:]
    assert "звернення: " in out.split("</details>")[0]


def test_id_not_duplicated_when_footer_already_has_it(monkeypatch):
    monkeypatch.setattr(chat_app, "_answer_inner",
                        lambda q, h=None: "Цифра." + chat_app.footer("тест"))
    out = chat_app.answer("питання")
    assert out.count("звернення: ") == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
