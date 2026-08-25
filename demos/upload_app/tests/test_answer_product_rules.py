# -*- coding: utf-8 -*-
"""Правила продукту мусять бути ВИДНІ у відповіді кожного шаблона (етап 5).

Два правила з CLAUDE.md, які легко зламати кодом:
  - **чернетка ≠ факт**: непідтверджене в підрахунок не входить, і про це має
    бути сказано, а не мовчки;
  - відповідь мусить мати **дату зрізу**: без неї цифру неможливо перевірити
    пізніше («скільком у відпустці» — станом на коли?).

Аудит 25.08 показав, що це виконувалось лише в частині шаблонів: 12 із 22
йшли генерик-гілкою й про чернетки не казали нічого, а `doc_by_number` не
казав дати. Тест перевіряє не «є такий рядок у коді», а РЕНДЕР кожного
шаблона на підставлених даних.

Запуск:
    python -m pytest demos/upload_app/tests/test_answer_product_rules.py -q
"""
import datetime

import pytest

from demos.upload_app.chat_gradio import app as chat_app

tiers = chat_app.tier_chat


#: Мінімальні рядки, які повертає БД, -- по одному на форму запиту в каталозі.
#: Ключі беруться з самого SQL шаблону, тому підставляємо узагальнено.
def _fake_rows_for(sql):
    row = {}
    for key, value in (("n", 3), ("docs", 2), ("name", "ІВАНОВ Іван"),
                       ("dim", "leave"), ("dim_name", "відпустка"),
                       ("value", "Київ"), ("status", "confirmed"),
                       ("valid_from", datetime.date(2026, 5, 1)),
                       ("valid_to", datetime.date(2026, 5, 10)),
                       ("source_doc_id", 7), ("doc_number", "118"),
                       ("domain", "leave"), ("queue_type", "new_person"),
                       ("source_file", "DEMO-01.docx"), ("id", 1),
                       ("title", "Наказ"), ("snippet", "текст"),
                       ("uploaded_at", datetime.date(2026, 5, 2))):
        if key in sql or True:
            row[key] = value
    return [row]


@pytest.fixture()
def rendered(monkeypatch):
    """Рендерить шаблон на підставлених рядках, без бази."""
    def _render(template_id):
        t = tiers._CATALOG[template_id]
        monkeypatch.setattr(tiers, "_run_template_sql",
                            lambda sql, params: _fake_rows_for(sql or ""))
        monkeypatch.setattr(tiers, "_people_total", lambda: 300)
        # Питання підібране так, щоб витяглись УСІ види обов'язкових параметрів:
        # стан, дата, ПІБ і номер документа. Інакше частина шаблонів піде
        # шляхом «обов'язкового параметра немає» і рендер не перевіриться.
        question = ("скільком зараз у відпустці 6 травня 2026, документ №118, "
                    "що відомо про Іванова")
        params = tiers.params_for_template(template_id, question) or {}
        text, _ = tiers.run_template(template_id, params)
        return text
    return _render


def _data_templates():
    """Шаблони з SQL (заблоковані -- окремий контракт: дослівна відмова)."""
    return [tid for tid, t in tiers._CATALOG.items()
            if not t.get("blocked") and t.get("sql")]


@pytest.mark.parametrize("template_id", _data_templates())
def test_every_answer_states_the_as_of_date(rendered, template_id):
    """Дата зрізу -- у КОЖНІЙ відповіді з даними."""
    text = rendered(template_id)
    assert "Зріз" in text or "зріз" in text, f"{template_id}: {text[:200]}"


@pytest.mark.parametrize("template_id", _data_templates())
def test_fact_templates_speak_about_drafts(rendered, template_id):
    """Шаблон, який рахує ФАКТИ, мусить сказати про чернетки -- або числом,
    або тим, що враховані лише підтверджені. Шаблони не про факти (документи,
    черга, нормативка) звільнені: порожня обіцянка гірша за її відсутність."""
    t = tiers._CATALOG[template_id]
    sql = t.get("sql") or ""
    if "facts" not in sql:
        pytest.skip("шаблон не про факти")
    text = rendered(template_id).lower()
    # Три законні способи сказати правду про чернетки:
    #   1. окреме число непідтверджених (шаблони-підрахунки);
    #   2. позначка статусу біля КОЖНОГО факту (person_status, doc_by_number --
    #      там перелік фактів, і статус видно по рядках);
    #   3. пряма фраза, що враховані лише підтверджені / що чернетки входять.
    assert ("непідтвердж" in text or "чернетк" in text
            or "лише підтверджені" in text
            or "[підтверджено" in text), f"{template_id}: {text[:300]}"


def test_blocked_template_answers_with_its_own_refusal(rendered):
    """Заблокований шаблон -- інший контракт: дослівна відмова каталогу, без
    цифр і без дати зрізу (нема чого датувати)."""
    text, source = tiers.run_template("subdivision_blocked", {})
    assert "підрозділ" in text.lower()
    assert any("заблоковано" in s for s in source)


def test_every_answer_carries_the_db_slice_date():
    """Зріз БАЗИ (момент читання) -- у кожній відповіді, бо footer() стоїть під
    кожною. Знайдено замірним прогоном: 69 відповідей із 124 не мали жодної
    дати -- шаблони каталогу її казали, старіші дороги (підрахунок, довідник,
    діагностика) ні."""
    import datetime
    today = datetime.date.today().isoformat()
    for route in ("відмова", "підрахунок", "довідник", "діагностика"):
        assert f"зріз бази: {today}" in chat_app.footer(route), route


def test_slice_date_does_not_contradict_the_data_slice():
    """Дві дати в одній відповіді -- це не суперечність: «Зріз: на 6 травня» про
    ДАНІ, «зріз бази» про момент читання. Перевірка, що обидві живуть разом і
    формулювання різні."""
    text = ("Трьом у відпустці.\n"
            "Зріз: на 2026-05-06 (за підтвердженими фактами).")
    out = text + chat_app.footer("каталог шаблонів (count_by_state_on_date)")
    assert "Зріз: на 2026-05-06" in out and "зріз бази:" in out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
