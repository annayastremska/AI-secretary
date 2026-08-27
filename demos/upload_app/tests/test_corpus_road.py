# -*- coding: utf-8 -*-
"""Блок 7: питання ПРО КОРПУС — окрема дорога, і жодної неправди про склад бази.

п. 15 звіту Дениса 27.08: на «Який період покривають документи в базі?» чат
казав «у базі поки немає нормативних документів», а на «Що взагалі є в базі?» —
«normative: 44». Одне з двох мусило бути хибним.

Хибним був перший текст. І насправді чисел було **три**, не два:

| число | звідки |
|---|---|
| 0 | `db.search_reference` -> `answer_reference` (текст із часів, коли нормативки не було) |
| 41 | ЛІТЕРАЛ у тексті відмови нормативного ланцюга |
| 44 | живий `COUNT(*)` по `documents` — правда |

## Що зроблено і чого НЕ зроблено

**Не оживлено** `db.search_reference`. Це головне рішення блоку: умова, яка її
кличе, зараз ніколи не виконується — і саме тому питання про НОРМУ доїжджають
до ланцюга Андрія, який дає адресу пункту й дослівну цитату. Оживити пошук
означало б перехопити їх гіршою дорогою (перелік розділів без адреси).
Розбір — `docs/research/2026-08-27_normative-two-roads.md`.

**Зроблено:** питання про корпус маршрутизується в `documents_count` — окрема
дорога, як і сказав Андрій 28.08: ланцюг шукає одиницю ТЕКСТУ, а кількості
документів у тексті документів немає. Плюс літерал «41» став живим лічильником,
а коди доменів (`leave`, `normative`) — людськими назвами (п. 25 звіту).

Запуск:
    python -m pytest demos/upload_app/tests/test_corpus_road.py -q
"""
import pytest

from demos.upload_app.chat_gradio import app as chat_app

tiers = chat_app.tier_chat


@pytest.fixture(autouse=True)
def roster(monkeypatch):
    monkeypatch.setattr(tiers, "subdivisions", lambda: ["1-ша механізована рота"])


# ── Маршрутизація ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "Який період покривають документи в базі?",
    "Що взагалі є в базі?",
    "Скільки документів у базі?",
    "Який період покриття документів?",
])
def test_corpus_questions_have_their_own_road(question):
    tid, _ = tiers.rules_route(question)
    assert tid == "documents_count", (question, tid)


@pytest.mark.parametrize("question", [
    "Який період відпустки у Ґоляша?",
    "За скільки днів подавати рапорт?",
    "Скільком осіб у відпустці 2026-10-10?",
])
def test_gate_is_narrow_and_does_not_eat_other_questions(question):
    """ПЕРЕВІРКА НА ПОБІЧНУ ШКОДУ. Гейт корпусу міг би проглинути все, де є
    слово «період» або «база» -- зокрема питання про ОСОБУ й про НОРМУ."""
    tid = tiers.rules_route(question)
    assert not tid or tid[0] != "documents_count", (question, tid)


# ── Жодної неправди про склад бази ────────────────────────────────────────

def test_reference_road_no_longer_claims_the_base_has_no_normative_docs(
        monkeypatch):
    """ГОЛОВНЕ твердження блоку: текст, який Денис спіймав, мусить зникнути."""
    monkeypatch.setattr(chat_app.db, "search_reference", lambda q, limit=3: [])
    monkeypatch.setattr(tiers, "run_template",
                        lambda tid, params: ("Документів у базі: 204.\n"
                                             "- нормативні акти (normative): 44",
                                             ["шаблон"]))
    monkeypatch.setattr(chat_app.db, "coverage_note",
                        lambda date=None: "Покриття даних у базі: A — B.")

    out = chat_app.answer_reference("Який період покривають документи в базі?")

    assert "немає нормативних документів" not in out, out
    assert "44" in out and "204" in out, out


def test_corpus_answer_names_what_the_coverage_is_about(monkeypatch):
    """Межа, без якої відповідь стає новою неправдою: покриття вважається по
    відпустках і відрядженнях, а не по нормативці -- у норм-актів дати інші."""
    monkeypatch.setattr(tiers, "run_template",
                        lambda tid, params: ("Документів у базі: 204.", ["ш"]))
    monkeypatch.setattr(chat_app.db, "coverage_note",
                        lambda date=None: "Покриття даних у базі: A — B.")
    out = chat_app._corpus_answer()
    assert "нормативні акти датуються окремо" in out, out


def test_corpus_answer_says_it_is_a_failure_not_emptiness(monkeypatch):
    """Запит не виконався -- це збій доступу, а не «даних немає». Плутати ці
    два стани -- рівно те, від чого страхує правило «нуль означає що?»."""
    def boom(tid, params):
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(tiers, "run_template", boom)
    out = chat_app._corpus_answer()
    assert "збій доступу" in out, out


def test_search_reference_is_deliberately_left_dead():
    """Це не забутий рефакторинг, а рішення -- і тест його фіксує.

    `db.search_reference` шукає FTS по `documents.text_content`, де нормативки
    немає (вона в `document_units`). Умова, яка кличе цю функцію у виборі
    дороги, тому ніколи не виконується -- і саме через це питання про норму
    доїжджають до ланцюга з адресою пункту й цитатою. Якщо хтось «полагодить»
    пошук, цей тест впаде і змусить прочитати розбір.
    """
    import inspect
    src = inspect.getsource(chat_app.rules_route)
    assert "db.search_reference(question)" in src, (
        "умову прибрали або змінили -- перечитайте "
        "docs/research/2026-08-27_normative-two-roads.md")


# ── Коди доменів людською мовою (п. 25) ───────────────────────────────────

def test_domain_codes_are_translated_but_still_traceable():
    assert tiers.domain_label("leave") == "відпустки"
    assert tiers.domain_label("normative") == "нормативні акти"
    assert tiers.domain_label("staffing") == "штатна книжка"


def test_unknown_domain_is_shown_as_is_not_hidden():
    """Невідомий домен -- це НОВІ дані, і людина мусить побачити, що вони є."""
    assert tiers.domain_label("weapons") == "weapons"
    assert tiers.domain_label("") == "без типу"


def test_chat_and_stats_page_agree_on_domain_names():
    """Два словники в різних процесах -- свідома копія; розходження ловиться
    тут, а не оком на демо."""
    from demos.upload_app import stats
    for code, label in stats.DOMAIN_LABELS.items():
        assert tiers.DOMAIN_LABEL.get(code) == label, code
