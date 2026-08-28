# -*- coding: utf-8 -*-
"""Тест-сет маршрутизатора мусить збігатися з каталогом.

Знайдено живою поломкою 27.08. Тест-сет писався, коли питання про підрозділи
система відмовляла (шаблон `subdivision_blocked`). Потім підрозділи зробили,
шаблон-відмову розібрали на чотири робочі, а тест-сет лишився з іменем, якого
в каталозі більше немає. Прилад `measure_router` упав на `ValueError: ... is
not in list` -- ЩЕ ДО першої цифри, тобто метрика «правильно розпізнаних
питань» просто не існувала, і про це ніхто не знав.

Два інваріанти, які тут стережуться:

  1. кожен `expected` у тест-сеті -- це шаблон, який справді є в каталозі.
     Інакше прилад міряє проти імені, якого немає;
  2. кожен запис групи `example` -- ДОСЛІВНО один із прикладів каталогу.
     Прилад для цієї групи робить leave-one-out (виймає приклад з індексу,
     щоб він не відповідав сам собі) і шукає рядок за точним збігом. Немає
     збігу -- падіння, а не гірша цифра.

Тест дешевий (два yaml, без моделі) і стоїть у звичайному прогоні: саме тому,
що поломка була МОВЧАЗНОЮ і жила до наступного ручного запуску приладу.
"""
import io
import os

import yaml

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(APP_DIR, "query_catalog.yaml")
TESTSET = os.path.join(APP_DIR, "router_testset.yaml")


def _load():
    catalog = yaml.safe_load(io.open(CATALOG, encoding="utf-8"))["templates"]
    items = yaml.safe_load(io.open(TESTSET, encoding="utf-8"))
    if isinstance(items, dict):
        items = items.get("questions") or items.get("items")
    return catalog, items


#: Єдине значення `expected`, яке НЕ є id шаблону: «правильна відповідь тут --
#: відмова». Перелічене тут дослівно, а не пропущене шаблоном на два підкреслення:
#: сентинел мусить бути один і названий, інакше цей контракт перестане ловити
#: одруківки в `expected` -- а він для цього й стоїть.
REFUSAL = "__refusal__"


def test_every_expected_template_exists():
    catalog, items = _load()
    ids = {t["id"] for t in catalog} | {REFUSAL}
    unknown = sorted({q["expected"] for q in items} - ids)
    assert not unknown, (
        "тест-сет чекає шаблонів, яких немає в каталозі: " + str(unknown)
        + ". Так було з subdivision_blocked: шаблон розібрали, тест-сет "
          "лишили -- і прилад падав замість того, щоб дати цифру.")


def test_the_refusal_sentinel_is_not_a_catalog_template():
    """Сентинел мусить лишатись ПОЗА каталогом.

    Якщо колись у каталозі з'явиться шаблон із таким id, замір відмов тихо
    перетвориться на звичайне «влучив у шаблон» -- тобто прилад показував би
    цифру, яка нічого не міряє. Тест тримає саме цю межу."""
    catalog, _ = _load()
    assert REFUSAL not in {t["id"] for t in catalog}


def test_the_refusal_group_is_measured():
    """Група існує й непорожня.

    Без цього тесту сентинел можна прибрати з набору, не зламавши нічого
    видимого: прилад просто перестав би міряти відмови й показував би ті самі
    числа -- рівно та дірка, задля закриття якої він і з'явився."""
    _, items = _load()
    refusals = [q for q in items if q["expected"] == REFUSAL]
    assert len(refusals) >= 3, refusals
    for q in refusals:
        assert q.get("group") == "refusal", q


def test_example_group_is_verbatim_from_catalog():
    catalog, items = _load()
    examples = {e.strip()
                for t in catalog for e in (t.get("examples") or [])}
    missing = [q["q"] for q in items
               if q.get("group") == "example" and q["q"].strip() not in examples]
    assert not missing, (
        "у групі example є питання, яких немає в прикладах каталогу: "
        + str(missing) + ". Прилад робить для цієї групи leave-one-out за "
                         "точним збігом рядка і на такому записі падає. Якщо "
                         "питання -- перефраз, група мусить бути paraphrase.")


def test_example_group_points_at_the_template_it_came_from():
    """Приклад мусить чекати ТОГО шаблону, у прикладах якого він стоїть.
    Інакше leave-one-out виймає рядок з одного шаблону, а правильною
    вважається відповідь іншого -- і цифра неправильна, хоч нічого не падає."""
    catalog, items = _load()
    owner = {}
    for t in catalog:
        for e in (t.get("examples") or []):
            owner.setdefault(e.strip(), t["id"])
    wrong = [(q["q"], q["expected"], owner[q["q"].strip()])
             for q in items
             if q.get("group") == "example" and q["q"].strip() in owner
             and owner[q["q"].strip()] != q["expected"]]
    assert not wrong, wrong
