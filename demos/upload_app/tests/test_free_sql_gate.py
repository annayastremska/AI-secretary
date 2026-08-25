# -*- coding: utf-8 -*-
"""Ярус, де SQL пише модель: увімкнений за домовленістю, вимикається явно.

Домовленість із замовницею: **немає підходящого шаблона -- модель має право
скласти запит сама.** Інакше система вміє відповідати лише на заздалегідь
передбачені питання, а сенс мовного інтерфейсу саме в непередбачених.

25.08 я цей ярус була вимкнула, вирішивши за замовницю — помилка, виправлена
того ж дня. Тест тепер сторожить обидві половини домовленості:
  1. дефолт — УВІМКНЕНО (щоб ніхто знову не вирішив мовчки);
  2. вимикач працює і не «з'їдає» питання: вимкнений ярус доводить питання до
     чесної відмови, а не до порожнечі.

Запуск:
    python -m pytest demos/upload_app/tests/test_free_sql_gate.py -q
"""
import pytest

from demos.upload_app.chat_gradio import app as chat_app

#: ВАЖЛИВО: беремо саме той модуль, який використовує апка.
#:
#: Один і той самий файл `tiers.py` живе в sys.modules ДВІЧІ: як `tiers`
#: (chat_gradio кладе свою теку в sys.path і імпортує сусідів коротко) і як
#: `demos.upload_app.chat_gradio.tiers`. Це два РІЗНІ обʼєкти зі своїм станом.
#: Тест, який патчить пакетну копію, нічого не змінює для апки -- і проходить
#: вхолосту. Саме так у мене й вийшло 25.08: перевірка «ярус не викликається»
#: була зеленою, бо ярус і так не викликався (моделі немає), а не через патч.
tiers = chat_app.tier_chat


def test_enabled_by_default():
    """Дефолт — УВІМКНЕНО, бо так домовлено. Якщо хтось (у т.ч. я) знову
    вирішить вимкнути мовчки, це впаде тут, а не з'ясується на демо."""
    assert tiers.FREE_SQL_ENABLED is True


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("no", False), ("off", False),
])
def test_flag_reading(monkeypatch, value, expected):
    """Прапорець читається однаково для звичних написань: «CHAT_FREE_SQL=true»
    не має мовчки означати «вимкнено»."""
    monkeypatch.setenv("CHAT_FREE_SQL", value)
    import importlib
    reloaded = importlib.reload(tiers)
    try:
        assert reloaded.FREE_SQL_ENABLED is expected
    finally:
        monkeypatch.delenv("CHAT_FREE_SQL", raising=False)
        importlib.reload(tiers)


def test_tier2_not_called_when_disabled(monkeypatch):
    """Коли ярус вимкнули явно — модель справді не отримує завдання «склади
    SQL». Перевіряємо не прапорець, а що функція не викликається."""
    called = []
    monkeypatch.setattr(tiers, "tier2_answer",
                        lambda q: called.append(q) or ("щось", []))
    monkeypatch.setattr(tiers, "FREE_SQL_ENABLED", False)
    assert chat_app._tier2_tier("скільки в середньому днів відпустки?") is None
    assert called == [], "ярус вимкнений, а tier2_answer усе одно викликали"


def test_question_still_gets_an_honest_refusal(monkeypatch):
    """Вимкнений ярус не має ковтати питання: людина мусить побачити відмову,
    а не порожнечу. Модель і база тут недоступні (як у тестовому оточенні),
    тобто це шлях «нічого не склалось»."""
    monkeypatch.setattr(tiers, "FREE_SQL_ENABLED", False)
    out = chat_app.answer("скільки в середньому днів відпустки на людину?")
    assert isinstance(out, str) and out.strip(), "відповідь не може бути порожньою"
    assert "не" in out.lower(), out[:200]


def test_enabled_tier_is_called_and_its_answer_is_marked(monkeypatch):
    """Друга половина домовленості: увімкнений ярус працює, і його відповідь
    ПОЗНАЧЕНА. Позначка з видимим SQL -- те, що робить цифру перевірною:
    валідатор гарантує безпеку запиту, а не те, що він відповідає на
    поставлене питання."""
    monkeypatch.setattr(tiers, "FREE_SQL_ENABLED", True)
    monkeypatch.setattr(chat_app, "model_available", lambda: True)
    monkeypatch.setattr(tiers, "_get_model", lambda: object())
    monkeypatch.setattr(
        tiers, "tier2_answer",
        lambda q: ("Відповідь на нешаблонний запит: 7", ["SQL:", "SELECT 1"]))
    out = chat_app._tier2_tier("скільки в середньому днів відпустки?")
    assert out is not None
    assert "нешаблонний запит" in out
    assert "SELECT 1" in out, "SQL мусить бути видно у відповіді"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
