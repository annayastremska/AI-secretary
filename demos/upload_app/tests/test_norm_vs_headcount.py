# -*- coding: utf-8 -*-
"""Питання про НОРМУ не має отримувати підрахунок ЛЮДЕЙ. Два дефекти з живої розмови.

Обидва знайшла Аня 28.08 у чаті, і обидва — «впевнена відповідь на інше
питання», тобто найгірший вид помилки: перевірити її людина не може.

## Дефект 1: «скільки максмум термін щорічної відпустки?»

Отримала: «8 осіб у відпустці на 2026-08-28» плюс перелік восьми прізвищ.

Питання про ОДИНИЦЮ ЧАСУ («скільком днів»), відповідь — про ОСІБ. У коді вже
стояли ДВА гейти нормативки, і питання пройшло крізь обидва: у першому немає
слова «термін», у другому було лише «тривалість». Плюс одруківка «максмум» —
гейт, який ловить тільке правильне написання, не гейт.

## Дефект 2: «як отримати пароль для Wifi»

Отримала перелік документів по домейнах — тобто відповідь про СКЛАД БАЗИ на
питання про ЗМІСТ норми. І це зробила моя ж правка блоку 7: раніше там була
неправда («у базі немає нормативних документів»), стала відповідь не на те
питання. Обидві однаково безкорисні, друга ще й виглядає впевненою.

Причина: гейт «питання про корпус» жив ЛИШЕ в правилах, а до цієї гілки питання
доходить від МОДЕЛІ, яка про той гейт не знає. Тепер гейт — одна функція, і її
питають з обох місць.

І головне: у корпусі Є документ «Підключення до службової мережі військової
частини», тобто на це питання відповідь існує — її мусить дати ланцюг цитатою.

Запуск:
    python -m pytest demos/upload_app/tests/test_norm_vs_headcount.py -q
"""
import pytest

from demos.upload_app.chat_gradio import app as chat_app

tiers = chat_app.tier_chat


@pytest.fixture(autouse=True)
def no_db(monkeypatch):
    monkeypatch.setattr(tiers, "subdivisions",
                        lambda: ["1-ша механізована рота"])
    monkeypatch.setattr(tiers, "_run_template_sql", lambda sql, p: [])


# ── Дефект 1: норма проти обліку ───────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "скільки максмум термін щорічної відпустки?",      # дослівно з розмови
    "скільки максимум термін щорічної відпустки?",
    "Скільком днів щорічної відпустки покладено?",
    "яка тривалість щорічної відпустки",
    "не більше скількох днів відпустки можна взяти",
])
def test_norm_questions_do_not_get_a_headcount(question):
    tid, _ = tiers.rules_route(question)
    assert tid == "normative_search", (question, tid)


@pytest.mark.parametrize("question", [
    "скільком осіб у відпустці 2026-10-10?",
    "Хто у відпустці 2026-10-10?",
    "Скільком відсутніх 2026-09-02?",
])
def test_headcount_questions_still_get_a_headcount(question):
    """ПЕРЕВІРКА НА ПОБІЧНУ ШКОДУ. Гейт норми міг би проглинути облік --
    тоді на «скільком осіб у відпустці» прилітала б цитата з наказу."""
    tid, _ = tiers.rules_route(question)
    assert tid in ("count_by_state_on_date", "list_by_state"), (question, tid)


@pytest.mark.xfail(strict=True, reason=(
    "ЩЕ ОДНА ЖАДІБНА РЕГУЛЯРКА, знайдена цим же тестом. «який максимальний "
    "термін відпустки» до гейта норми НЕ доходить: раніше за нього стоїть гейт "
    "агрегатів (`мін/макс/середнє`), який віддає питання в ярус вільного SQL. "
    "Тобто це не дірка в моєму гейті, а ПОРЯДОК доріг -- рівно та системна "
    "причина, що описана в docs/research/2026-08-28_why-the-chat-answers-wrong.md. "
    "Переставляти дороги перед демо не буду: гейт агрегатів існує, щоб "
    "«середня тривалість відпустки» йшла в дані, і його зсув треба міряти "
    "окремо. strict=True: коли порядок полагодять, тест стане червоним."))
def test_aggregate_gate_takes_the_norm_question_first():
    tid = tiers.rules_route("який максимальний термін відпустки")
    assert tid and tid[0] == "normative_search"


def test_days_of_a_named_person_is_accounting_not_a_norm(monkeypatch):
    """Межа гейта: «скільки днів у відпустці Ґоляша» -- це ОБЛІК. У питанні є
    особа, і відповідь у базі, а не в нормативному акті."""
    monkeypatch.setattr(tiers, "_run_template_sql",
                        lambda sql, p: [{"1": 1}])          # особа знайшлась
    tid, _ = tiers.rules_route("скільки днів у відпустці Ґоляша")
    assert tid != "normative_search", tid


# ── Дефект 2: «довідник» -> ланцюг, а не склад бази ───────────────────────

def _reference(monkeypatch, question):
    """-> який шаблон покликали з дороги «довідник»."""
    calls = []
    monkeypatch.setattr(chat_app.db, "search_reference", lambda q, limit=3: [])
    monkeypatch.setattr(chat_app.db, "coverage_note",
                        lambda date=None: "Покриття: A — B.")
    monkeypatch.setattr(tiers, "run_template",
                        lambda tid, p: (calls.append(tid)
                                        or ("текст", ["джерело"])))
    chat_app.answer_reference(question)
    return calls


def test_content_question_goes_to_the_normative_chain(monkeypatch):
    """Дослівне питання Ані. Відповідь на нього в корпусі Є."""
    assert _reference(monkeypatch, "як отримати пароль для Wifi") == \
        ["normative_search"]


@pytest.mark.parametrize("question", [
    "Що взагалі є в базі?",
    "Який період покривають документи в базі?",
    "Скільком документів у базі?",
])
def test_corpus_question_still_answers_about_the_corpus(monkeypatch, question):
    """І навпаки: питання ПРО КОРПУС не має йти в ланцюг -- він шукає одиницю
    тексту, а кількості документів у тексті документів немає."""
    assert _reference(monkeypatch, question) == ["documents_count"]


def test_corpus_gate_lives_in_one_place():
    """Причина дефекту 2 -- гейт у одному місці й перевірка у двох. Тест
    фіксує, що функція одна й доступна обом дорогам."""
    assert callable(tiers.is_corpus_question)
    assert tiers.is_corpus_question("Що взагалі є в базі?")
    assert not tiers.is_corpus_question("як отримати пароль для Wifi")
    import inspect
    src = inspect.getsource(chat_app.answer_reference)
    assert "is_corpus_question" in src
