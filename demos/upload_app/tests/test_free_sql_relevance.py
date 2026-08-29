# -*- coding: utf-8 -*-
"""Ярус вільного SQL не відповідає на питання, яких база не знає.

Дефект, знайдений Анею 29.08 живцем: на «скільки набоїв на складі» ярус склав
запит по виміру `document_number` і віддав 129 номерів документів як
відповідь. Валідатор пропустив -- він міряє безпеку, не доречність.

Це найгірший клас відмови: впевнена відповідь не про те. Тому дві перевірки, і
обидві механічні -- моделью модель ми не судимо.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CHAT = os.path.join(HERE, "..", "chat_gradio")
sys.path.insert(0, os.path.abspath(CHAT))

import tiers                                        # noqa: E402


# ── Перевірка 1: предмет питання ─────────────────────────────────────────────


def _db_knows(words):
    """Заглушка бази: знає рівно перелічені корені."""
    known = {w[:tiers.SUBJECT_MIN_ROOT] for w in words}
    return lambda root: root in known


def test_ammunition_question_is_refused():
    """Той самий випадок, що зловила Аня."""
    got = tiers.unknown_subject("скільки набоїв на складі",
                                probe=_db_knows(["відпустка", "житомир"]))
    assert got == "набоїв", got


def test_known_subject_passes():
    """Питання про відпустки далі йде: слово база знає."""
    assert tiers.unknown_subject(
        "яка середня тривалість відпустки",
        probe=_db_knows(["відпустка", "тривалість"])) is None


def test_one_known_word_is_enough():
    """Досить одного впізнаного слова: ми відсікаємо предмет, якого немає
    взагалі, а не вивіряємо кожне слово питання."""
    assert tiers.unknown_subject(
        "скільки людей поїхало у Житомир на тому тижні",
        probe=_db_knows(["житомир"])) is None


def test_service_words_alone_do_not_refuse():
    """Питання з одних службових слів предмета не має -- відмовляти нема за
    що, хай іде далі й ловиться іншими рейками."""
    assert tiers.unknown_subject("скільки їх зараз",
                                 probe=_db_knows([])) is None


def test_short_words_are_not_judged():
    """Корені коротші за SUBJECT_MIN_ROOT дають хибні збіги в українській --
    та сама причина, що й у PLACE_MIN_ROOT для місць."""
    assert tiers.unknown_subject("хто де був", probe=_db_knows([])) is None


def test_db_failure_does_not_become_a_lie():
    """База недоступна -> НЕ «у базі такого немає». Це брехня іншого роду."""
    def broken(root):
        raise RuntimeError("база лежить")
    monkey = tiers._run_template_sql
    try:
        tiers._run_template_sql = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("база лежить"))
        assert tiers._subject_known_to_db("набої") is True
    finally:
        tiers._run_template_sql = monkey


# ── Перевірка 2: «скільки» вимагає підрахунку ────────────────────────────────


@pytest.mark.parametrize("q", [
    "скільки набоїв на складі",
    "скільки людей у відпустці",
    "яка кількість документів",
])
def test_quantity_questions_are_detected(q):
    assert tiers._QUANTITY_ASK.search(q), q


@pytest.mark.parametrize("q", [
    "хто у Житомирі",
    "яка середня тривалість відпустки",
])
def test_non_quantity_questions_are_not_detected(q):
    assert not tiers._QUANTITY_ASK.search(q), q


def test_plain_select_is_not_an_answer_to_how_many():
    """Саме цей SQL і був у зловленому випадку."""
    sql = ("SELECT f.value::int FROM facts f JOIN dimensions d "
           "ON f.dimension_id = d.id WHERE d.code = 'document_number' "
           "AND f.status = 'confirmed' LIMIT 200")
    assert not tiers._SQL_AGGREGATE.search(sql)


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM facts LIMIT 200",
    "SELECT count(DISTINCT f.object_id) FROM facts f LIMIT 200",
    "SELECT AVG(f.value::int) FROM facts f LIMIT 200",
    "SELECT SUM(f.valid_to - f.valid_from + 1) FROM facts f LIMIT 200",
])
def test_aggregates_are_recognised(sql):
    assert tiers._SQL_AGGREGATE.search(sql), sql


def test_min_max_are_not_counted_as_counting():
    """MIN/MAX -- це не підрахунок, а вибір одного значення. На питання
    «скільки» вони не відповідають, тому в агрегати не входять."""
    assert not tiers._SQL_AGGREGATE.search("SELECT MAX(f.value) FROM facts f")


# ── Обидві перевірки разом, наскрізь ─────────────────────────────────────────


def test_tier2_refuses_before_calling_the_model(monkeypatch):
    """Відмова стається ДО моделі: інакше гість чекає 30 с на завідомо
    непотрібний запит."""
    called = []
    monkeypatch.setattr(tiers, "unknown_subject", lambda q, **k: "набоїв")
    monkeypatch.setattr(tiers, "_model_json",
                        lambda *a, **k: called.append(1) or None)
    text, src = tiers.tier2_answer("скільки набоїв на складі")
    assert not called, "модель не мала викликатись"
    assert "немає даних про" in text
    assert "набоїв" in text
    assert any("предмет питання відсутній" in s for s in src)


def test_tier2_refuses_list_for_how_many(monkeypatch):
    """Предмет у базі є, але запит склався на перелік -- відповіді немає."""
    sql = ("SELECT f.value::int FROM facts f JOIN dimensions d "
           "ON f.dimension_id = d.id WHERE d.code = 'document_number' "
           "AND f.status = 'confirmed' LIMIT 200")
    monkeypatch.setattr(tiers, "unknown_subject", lambda q, **k: None)
    monkeypatch.setattr(tiers, "_model_json", lambda *a, **k: {"sql": sql})
    monkeypatch.setattr(tiers, "validate_sql", lambda s: (sql, None))
    monkeypatch.setattr(tiers, "_connect",
                        lambda *a, **k: pytest.fail("до бази не мало дійти"))
    text, src = tiers.tier2_answer("скільки документів у базі")
    assert "питання про кількість" in text
    assert any("немає підрахунку" in s for s in src)
