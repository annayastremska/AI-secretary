# -*- coding: utf-8 -*-
"""Бюджети виклику моделі — зафіксовані в коді, а не «за замовчуванням».

Задачі 4.3 і 4.6 плану. Сенс тесту не в самих числах, а в тому, що вони
СПРАВДІ доходять до виклику: константа з гарним коментарем, яку ніхто не
передає, гірша за її відсутність — вона створює враження, що межа є.

Окремо сторожиться рішення 4.6 «ретраїв немає»: один виклик на питання, а не
два. GBNF-граматика робить невалідний вивід неможливим на рівні генерації, тому
повторювати немає чого; повтор коштує ще один повний виклик моделі.

Запуск:
    python -m pytest demos/upload_app/tests/test_model_budgets.py -q
"""
import pytest

from demos.upload_app.chat_gradio import tiers


class _FakeModel:
    def __init__(self):
        self.calls = []

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        return {"choices": [{"message": {"content": '{"sql": "SELECT 1"}'}}]}


@pytest.fixture()
def fake_model(monkeypatch):
    model = _FakeModel()
    monkeypatch.setattr(tiers, "_get_model", lambda: model)
    return model


def test_max_tokens_reaches_the_call(fake_model):
    tiers._model_json("сис", "пит", {"type": "object"})
    assert fake_model.calls[0]["max_tokens"] == tiers.MODEL_MAX_TOKENS


def test_temperature_is_zero(fake_model):
    """Маршрутизація мусить бути відтворюваною: те саме питання -- той самий
    маршрут. Це не стиль, а умова того, що наші заміри щось означають."""
    tiers._model_json("сис", "пит", {"type": "object"})
    assert fake_model.calls[0]["temperature"] == 0


def test_one_call_per_question_no_retries(fake_model):
    """Рішення 4.6 у вигляді перевірки, а не коментаря."""
    assert tiers.MODEL_RETRIES == 0
    tiers._model_json("сис", "пит", {"type": "object"})
    assert len(fake_model.calls) == 1


def test_broken_model_answer_does_not_trigger_a_second_call(monkeypatch):
    """Навіть коли модель віддала сміття: жодного повтору, просто None. Так
    питання їде далі за ярусами, а не платить другий виклик."""
    calls = []

    class _Garbage:
        def create_chat_completion(self, **kwargs):
            calls.append(kwargs)
            return {"choices": [{"message": {"content": "не json"}}]}

    monkeypatch.setattr(tiers, "_get_model", lambda: _Garbage())
    assert tiers._model_json("сис", "пит", {"type": "object"}) is None
    assert len(calls) == 1


def test_history_is_not_sent_to_the_model(fake_model):
    """Контекст діалогу тримається СЛОТАМИ в коді, а не текстом у промпті
    (MODEL_HISTORY_TURNS = 0). Перевірка: у виклику рівно два повідомлення --
    системне й питання, без попередніх ходів."""
    assert tiers.MODEL_HISTORY_TURNS == 0
    tiers._model_json("сис", "пит", {"type": "object"})
    messages = fake_model.calls[0]["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]


def test_context_window_constant_is_the_one_used():
    """n_ctx у завантаженні моделі -- та сама константа, що задокументована."""
    import inspect
    src = inspect.getsource(tiers._get_model)
    assert "n_ctx=MODEL_N_CTX" in src, "вікно контексту знову літерал у виклику"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
