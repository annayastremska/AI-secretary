# -*- coding: utf-8 -*-
"""Блок C: ідентифікація особи. Найнебезпечніша група звіту Дениса 27.08.

Його ж слова, і вони визначають критерій: «неправильну цифру помітиш, а картка
з чужими документами виглядає як нормальна картка».

## Три причини, які тут закриваються

1. **пошук підрядком.** `canonical_name ILIKE '%Богодар%'` збігається і з
   «Ґоляш **Богодар** Святославович», і з «Дашкевич Едуард **Богодар**ович» —
   тому три різні людини склеювались в одну картку (п. 6);
2. **пошук за ПЕРШИМ словом.** На повне «Малишко Каміллі Омелянівні» шукали
   лише «Малишко», знаходили два збіги й просили «уточнити ПІБ повніше» — те,
   що людина вже написала повністю (п. 9);
3. **два незалежні пошуки.** Заголовок картки давав `find_people(рядок)`,
   документи — `absences_for_person(той самий рядок)`, і ніде не було сказано,
   що вони мусять зійтись на одній особі. Прилад показав, що навіть повне
   однозначне «Дашкевич Едуард Богодарович» тягло документ другого Дашкевича.

## Живий замір

`demos/upload_app/measure_identity.py` на `eval/chat/identity.tsv` (15 питань зі
звіту, ключі — `object_id`, не ПІБ). До правок: особа 14/15, чистота картки
13/15. Після: **15/15, 15/15, 15/15**.

Тут — те, що можна перевірити БЕЗ бази: сама логіка звуження й межа слова.
Числа з живої бази лежать у `data/eval/identity-report.json`.

Запуск:
    python -m pytest demos/upload_app/tests/test_identity.py -q
"""
import pytest

from demos.upload_app.chat_gradio import app as chat_app
from demos.upload_app.chat_gradio import db as chat_db

tiers = chat_app.tier_chat

#: Реєстр стенду в мініатюрі — рівно ті люди, на яких Денис зловив дефекти.
#: `object_id` справжні (знято з бази 27.08 читанням).
REGISTRY = [
    (48, "Дашкевич Едуард Богодарович"),
    (57, "Ґоляш Богодар Святославович"),
    (85, "Дашкевич Василь Захарович"),
    (100, "Онуфрієнко Богодар Мартинович"),
    (101, "Ващенко Богодар Максимович"),
    (62, "Малишко Камілла Омелянівна"),
    (301, "Малишко Леопольд Валентинович"),
    (126, "Крижанівський Тарас Богданович"),
    (254, "Гоголь-Яновський Арсен Віталійович"),
]


def _matches(pattern_word):
    """Відтворює пошук бази на списку вище — рівно тією регуляркою, якою шукає
    `db.find_people`. Так тест міряє ту саму умову, а не свою здогадку."""
    import re
    rx = chat_db.name_word_regex(pattern_word)
    if rx is None:
        return []
    return [(oid, name) for oid, name in REGISTRY
            if re.search(rx, name, re.IGNORECASE)]


@pytest.fixture(autouse=True)
def fake_registry(monkeypatch):
    def find_people(name=None, subdivision=None):
        return [{"object_id": oid, "full_name": nm, "service_id": f"UNIT-{oid:04d}",
                 "in_roster": True, "rank": "", "position_title": "",
                 "subdivision": "", "phone": ""}
                for oid, nm in _matches(name)]

    monkeypatch.setattr(chat_app.db, "find_people", find_people)
    monkeypatch.setattr(chat_app.db, "absences_for_person",
                        lambda *a, **kw: [])
    monkeypatch.setattr(chat_app.db, "absences_for_object",
                        lambda oid, only_active=True: [])


# ── Причина 1. Межа слова ─────────────────────────────────────────────────

def test_given_name_does_not_match_a_patronymic():
    """п. 6, ядро: «Богодар» НЕ мусить чіплятись до «Богодарович»."""
    got = {oid for oid, _ in _matches("Богодар")}
    assert 48 not in got, "«Богодар» зловив «Дашкевич Едуард Богодарович»"
    assert {57, 100, 101} <= got, got


def test_declension_still_matches():
    """ПЕРЕВІРКА НА ПОБІЧНУ ШКОДУ, і саме її найлегше зламати.

    Обрізання по три літери з'явилось не від нудьги: без нього «Що відомо про
    Крижанівського?» давало «у реєстрі людини немає» -- впевнене заперечення
    власних даних. Допуск на відмінок тепер робить сам пошук, і він мусить
    працювати в ОБИДВІ сторони: питання у відмінку, база в називному.
    """
    assert {oid for oid, _ in _matches("Крижанівського")} == {126}
    assert {oid for oid, _ in _matches("Ґоляша")} == {57}
    assert {oid for oid, _ in _matches("Каміла")} == {62}


def test_one_letter_off_is_a_different_person():
    """п. 7: «Голяш» без Ґ -- це не Ґоляш і не Гоголь-Яновський."""
    assert _matches("Голяш") == []


def test_too_short_a_word_is_refused_not_guessed():
    """«Бог» не має ловити пів реєстру: порожньо честніше."""
    assert chat_db.name_word_regex("Бог") is None or _matches("Бог") == []


def test_regex_escapes_user_text():
    """Текст приходить від людини: метасимволи не мусять ставати регуляркою."""
    rx = chat_db.name_word_regex("Іван.*")
    assert rx is not None and r"\." in rx


# ── Причина 2. Усі слова ПІБ, а не перше ──────────────────────────────────

def test_full_name_narrows_instead_of_asking_to_clarify():
    """п. 9: за «Малишко» два збіги, за повним ПІБ -- один.

    Доти система шукала лише перше слово й просила уточнити те, що людина вже
    написала повністю.
    """
    res = chat_app.resolve_person("Покажи всі документи по Малишко Каміллі Омелянівні")
    assert [p["object_id"] for p in res["people"]] == [62]
    assert len(res["words"]) >= 2, res["words"]


def test_given_name_plus_patronymic_finds_the_right_person():
    """п. 6 наскрізь: «Богодар Святославович» -- це Ґоляш, а не Дашкевич."""
    res = chat_app.resolve_person("Богодар Святославович")
    assert [p["object_id"] for p in res["people"]] == [57]


def test_single_surname_still_reports_all_matches():
    """Одне прізвище -- і далі кілька збігів: це законне уточнення, а не
    дефект. Правка не має перетворювати неоднозначність у вибір за людину."""
    res = chat_app.resolve_person("Малишко")
    assert {p["object_id"] for p in res["people"]} == {62, 301}


def test_a_word_that_matches_nothing_does_not_empty_the_result():
    """Перетин застосовується лише поки не порожній.

    Інакше «Що відомо про Крижанівського, він з першої роти?» втратило б
    людину через слово, якого в ПІБ немає. Розширювати -- ні, обнуляти -- теж
    ні: слово, що нічого не дало, просто не звужує.
    """
    res = chat_app.resolve_person("Ґоляш Невідомевич")
    assert [p["object_id"] for p in res["people"]] == [57]


def test_stop_words_are_not_taken_for_names():
    """п. 30 щита: «А хто саме?» -- не прізвище."""
    for q in ("А хто саме?", "Покажи всіх", "Що відомо?"):
        assert chat_app.resolve_person(q)["people"] == [], q


# ── Причина 3. Документи -- за ключем особи ───────────────────────────────

def test_card_documents_are_taken_by_object_id(monkeypatch):
    """ГОЛОВНА правка блоку: картка бере документи ЗНАЙДЕНОЇ особи.

    Перевіряється не результат, а те, ЯК він отримується: чужий документ мусить
    стати неможливим за побудовою, а не менш імовірним.
    """
    calls = {"by_id": [], "by_name": []}
    monkeypatch.setattr(chat_app.db, "absences_for_object",
                        lambda oid, only_active=True:
                        calls["by_id"].append(oid) or [])
    monkeypatch.setattr(chat_app.db, "absences_for_person",
                        lambda name, only_active=True:
                        calls["by_name"].append(name) or [])
    monkeypatch.setattr(chat_app.db, "coverage_note", lambda date=None: "")

    chat_app.answer_person("Богодар Святославович")

    assert calls["by_id"] == [57], calls
    assert not calls["by_name"], "документи знову шукались по рядку"


def test_person_absent_from_registry_still_gets_documents(monkeypatch):
    """Єдиний випадок, коли ключа взяти нізвідки: особи в реєстрі немає, а
    документи з її ПІБ є. Тоді пошук по ПІБ лишається -- і картка ЧЕСНО каже,
    що реєстром вони не підтверджені (п. 8 звіту)."""
    monkeypatch.setattr(chat_app.db, "find_people", lambda **kw: [])
    seen = []
    monkeypatch.setattr(chat_app.db, "absences_for_person",
                        lambda name, only_active=True: seen.append(name) or [])
    monkeypatch.setattr(chat_app.db, "coverage_note", lambda date=None: "")

    out = chat_app.answer_person("Кривопишний")

    assert seen == ["Кривопишний"]
    assert "реєстрі частини людини" in out
