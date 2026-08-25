# -*- coding: utf-8 -*-
"""Ярус, де SQL пише модель, на демо вимкнений — і це перевіряється.

Рішення 25.08.2026 (критерій приймання П7). Причина не в безпеці: рівно один
SELECT, без DML/DDL, read-only і в DSN, і в сесії, примусовий LIMIT 200,
таймаут 5 с — усе це є. Причина в тому, що валідатор перевіряє, чи запит
БЕЗПЕЧНИЙ, і не перевіряє, чи він відповідає на ПОСТАВЛЕНЕ питання. Отже ярус
може віддати впевнену цифру не на те питання — прямо проти правила «відмова
краща за вигадку».

Тест сторожить дві речі, і друга не менш важлива за першу:
  1. за замовчуванням ярус не викликається взагалі;
  2. вимкнення не «з'їдає» питання: воно доходить до чесної відмови.

Запуск:
    python -m pytest demos/upload_app/tests/test_free_sql_gate.py -q
"""
import pytest

from demos.upload_app.chat_gradio import app as chat_app
from demos.upload_app.chat_gradio import tiers


def test_disabled_by_default():
    """Дефолт — вимкнено. Якщо колись стане навпаки, це має впасти тут, а не
    з'ясуватись на демо."""
    assert tiers.FREE_SQL_ENABLED is False


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("no", False), ("off", False),
    ("", False),
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
    """ГОЛОВНЕ: модель не отримує завдання «склади SQL». Перевіряємо не
    прапорець, а те, що функція справді не викликається."""
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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
