# -*- coding: utf-8 -*-
"""«Звідки ти це знаєш?» -- дорога до сліду попереднього ходу. п. 18 звіту.

Денис: на це питання чат відповідав ДОСЛІВНО тим самим текстом, яким відмовляє
на питання про погоду в Києві. Він же назвав чому це погано: для обліку
особового складу «звідки цифра» -- нормальне робоче питання.

## Чому це не вимагало нових даних

Кожен хід уже пише машинний слід: дорога, шаблон, SQL шаблону, скільком рядків
повернула база, скільком тривало. Бракувало **дороги до нього** -- і саме її
просив Денис: «щоб по скріну можна було автоматично прогнати аналіз, як система
там давала інфо».

Ключ -- номер звернення з ПОПЕРЕДНЬОЇ відповіді бота: він у ній видимим рядком
(це теж його правка). Тобто відповідь на «звідки ти це знаєш» знаходиться в
розмові, а не в базі -- і тому дорога потребує ІСТОРІЇ, а не шаблона.

Запуск:
    python -m pytest demos/upload_app/tests/test_evidence_road.py -q
"""
import pytest

from demos.upload_app.chat_gradio import app as chat_app

tiers = chat_app.tier_chat

ANSWER_WITH_ID = ('Доповідаю: 1 особа у відпустці.\n\n'
                  '<div class="req-id">звернення b8ea2f</div>')
HISTORY = [{"role": "user", "content": "Скільком у відпустці 2026-10-10?"},
           {"role": "assistant", "content": ANSWER_WITH_ID}]

TRACE_ROW = {
    "id": "b8ea2f", "road": "каталог шаблонів (count_by_state_on_date)",
    "seconds": 0.16, "steps": [{
        "kind": "template", "template": "count_by_state_on_date",
        "title": "Скільки людей у стані на дату",
        "params": {"state": "leave", "on_date": "2026-10-10"}, "rows": 1}],
}


@pytest.fixture(autouse=True)
def no_db(monkeypatch):
    monkeypatch.setattr(chat_app.db, "find_people", lambda **kw: [])
    monkeypatch.setattr(chat_app.db, "absences_for_person",
                        lambda *a, **kw: [])


@pytest.mark.parametrize("question", [
    "Звідки ти це знаєш?",
    "звідки ти знаєш?",
    "На підставі чого ця відповідь?",
    "Чим підтверджено?",
])
def test_evidence_question_is_recognised(question):
    assert tiers.is_evidence_question(question), question


def test_answer_explains_the_previous_turn(monkeypatch):
    """ГОЛОВНЕ: відповідь -- дорога, шаблон, параметри й кількість рядків
    ПОПЕРЕДНЬОГО ходу, а не відмова."""
    monkeypatch.setattr(chat_app.trace, "find",
                        lambda cid, path=None: TRACE_ROW if cid == "b8ea2f"
                        else None)
    out = chat_app._extra_tiers("Звідки ти це знаєш?", HISTORY)
    assert out, "дорога не спрацювала -- лишилась відмова"
    assert "b8ea2f" in out
    assert "count_by_state_on_date" in out
    assert "2026-10-10" in out
    assert "Рядків із бази:** 1" in out
    # І шлях, яким це можна прогнати машинно -- прохання Дениса
    assert "trace_lookup.py b8ea2f" in out


def test_no_previous_answer_says_so(monkeypatch):
    """Порожня розмова -- кажемо прямо, а не вигадуємо походження."""
    out = chat_app._extra_tiers("Звідки ти це знаєш?", [])
    assert "немає жодної моєї відповіді з номером" in out


def test_missing_trace_says_so(monkeypatch):
    """Номер є, сліду немає (апку перезапускали) -- це теж треба сказати, а не
    промовчати: номер лишається дійсним для журналу."""
    monkeypatch.setattr(chat_app.trace, "find", lambda cid, path=None: None)
    out = chat_app._extra_tiers("Звідки ти це знаєш?", HISTORY)
    assert "Сліду для звернення b8ea2f немає" in out


def test_named_person_still_goes_to_provenance_template(monkeypatch):
    """ПЕРЕВІРКА НА ПОБІЧНУ ШКОДУ: якщо в питанні Є ПІБ, відповідати мусить
    шаблон походження факту, а не розбір попереднього ходу."""
    monkeypatch.setattr(tiers, "_run_template_sql",
                        lambda sql, p: [{"1": 1}])          # особа знайшлась
    tid, params = tiers.rules_route("Звідки ти знаєш про Гавриша?")
    assert tid == "fact_provenance", tid
    assert "name_pattern" in params
