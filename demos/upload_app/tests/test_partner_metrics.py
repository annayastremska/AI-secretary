# -*- coding: utf-8 -*-
"""Пакет метрик Андрія на сторінці статистики.

Критерії приймання -- `docs/tasks/2026-08-27_acceptance-criteria.md`, розділ 11.

Домовленість про формат була одна й головна: у кожної метрики мусить бути
**`how`** -- чим саме її зміряно. Правило продукту: цифра без джерела не
показується. Тому метрика без `how` на сторінку не потрапляє, і про це
сказано числом -- тихо викинути замір партнера гірше, ніж показати, що з ним
не так.

Друге рішення, яке ці тести охороняють: метрики ОХОПЛЕННЯ згортаються.
Вони дублюють живі числа розділу «База зараз», і в пакеті одне з них уже
застаріле («41 нормативний акт» проти 44 у базі). Два числа про одне й те саме
на одному екрані -- це рівно той клас дефекту, який ми ловили весь тиждень.
"""
import io
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(APP_DIR))
for p in (APP_DIR, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from demos.upload_app import stats as stats_mod   # noqa: E402

PACKAGE = os.path.join(ROOT, "data", "eval", "andriy-metrics.json")
PAGE = os.path.join(APP_DIR, "static", "stats.html")


# ── Сам пакет ────────────────────────────────────────────────────────────────


def test_package_is_present_and_flat():
    """Записів 10 -- пакет обрізаний Андрієм 28.08 «під вітрину».

    Було 30. Він сам прибрав діагностичні лінії («без історії»), тавтологічні
    (recall і precision 12/12 при 27/27 на бінарній задачі), нерепрезентативні
    (8 питань), секундні (їх покриває наш живий лічильник латентності) і той,
    що ми самі дискредитували у звіті (зазор реранкера 1.82). Розклад під
    «правильних шаблонів обрано» переїхав у поле `how`.

    Число тут жорстке навмисно: пакет -- вітрина, і поповзти вона може лише
    свідомо."""
    data = json.load(io.open(PACKAGE, encoding="utf-8"))
    assert isinstance(data, list) and len(data) == 10, len(data)


def test_every_metric_in_the_package_says_how_it_was_measured():
    """Якби бракувало -- метрика просто не поїхала б на сторінку. Тест тут,
    щоб це було ВИДНО як вимога до пакета, а не як тихе відкидання."""
    data = json.load(io.open(PACKAGE, encoding="utf-8"))
    without = [m["name"] for m in data if not (m.get("how") or "").strip()]
    assert not without, without


# ── Читання пакета ───────────────────────────────────────────────────────────


def test_shown_and_folded_split():
    got = stats_mod.partner_metrics(PACKAGE)
    assert got["error"] is None
    assert got["dropped"] == 0
    assert len(got["shown"]) + len(got["folded"]) == 10
    # Чотири метрики охоплення дублюють живий розділ.
    assert len(got["folded"]) == 4, [m["name"] for m in got["folded"]]
    for m in got["folded"]:
        assert m["name"].lower().startswith("охоплення:")


def test_every_shown_metric_carries_how():
    for m in stats_mod.partner_metrics(PACKAGE)["shown"]:
        assert m["how"].strip(), m


def test_metric_without_how_is_dropped_and_counted(tmp_path):
    """К3: цифра без джерела не показується, і кількість відкинутого видна."""
    p = tmp_path / "m.json"
    p.write_text(json.dumps([
        {"name": "добра метрика", "value": 1, "how": "приладом X"},
        {"name": "без джерела", "value": 2},
        {"name": "порожнє джерело", "value": 3, "how": "   "},
        {"name": "без значення", "how": "приладом Y"},
    ], ensure_ascii=False), encoding="utf-8")
    got = stats_mod.partner_metrics(str(p))
    assert [m["name"] for m in got["shown"]] == ["добра метрика"]
    assert got["dropped"] == 3


def test_missing_file_does_not_break_the_page(tmp_path):
    """К4: відсутність пакета -- поле відповіді, а не падіння сторінки."""
    got = stats_mod.partner_metrics(str(tmp_path / "немає.json"))
    assert got["shown"] == [] and got["folded"] == []
    assert "пакета метрик немає" in (got["error"] or "")


def test_wrong_shape_is_reported_not_raised(tmp_path):
    p = tmp_path / "m.json"
    p.write_text('{"name": "не масив"}', encoding="utf-8")
    got = stats_mod.partner_metrics(str(p))
    assert "плоским масивом" in (got["error"] or "")


def test_collect_never_raises_and_includes_the_section():
    out = stats_mod.collect()
    assert "partner_metrics" in out
    assert isinstance(out["partner_metrics"], dict)


# ── Сторінка ─────────────────────────────────────────────────────────────────


def test_page_has_the_section_and_renders_it():
    html = io.open(PAGE, encoding="utf-8").read()
    assert 'id="partner-sec"' in html
    assert 'id="partner-tiles"' in html
    assert "renderPartner(data.partner_metrics)" in html


def test_page_shows_how_for_every_tile():
    """К2: `how` іде в розкривний блок, а не в атрибут title -- його не видно
    ні на проєкторі, ні на телефоні."""
    html = io.open(PAGE, encoding="utf-8").read()
    block = html.split("function metricTile", 1)[1].split("function renderPartner", 1)[0]
    assert "m.how" in block
    assert "чим зміряно" in block
    assert ".title =" not in block, "how у title -- його не видно"


def test_page_names_what_was_folded_and_dropped():
    html = io.open(PAGE, encoding="utf-8").read()
    block = html.split("function renderPartner", 1)[1]
    assert "не показано окремо" in block
    assert "відкинуто" in block


def test_page_makes_no_external_requests():
    """К5: правило проєкту -- нуль зовнішніх запитів зі сторінки."""
    html = io.open(PAGE, encoding="utf-8").read()
    for bad in ("http://", "https://", "//fonts.", "cdn."):
        assert bad not in html, bad


def test_who_measured_is_visible_per_number_not_as_a_lead():
    """К6 у новій редакції: підпис над розділом Аня прибрала 28.08, і тест
    мусив змінитись разом із критерієм.

    Стара версія перевіряла «Міряв Андрій» у html -- і після прибирання вона
    ПРОЙШЛА, бо знайшла ці слова в моєму ж комментарі про прибирання. Тобто
    міряла текст файла, а не те, що бачить людина. Третій такий випадок за
    день, тому тепер перевіряється саме зміст: хто і чим міряв, лишається
    видним у полі «чим зміряно» під КОЖНИМ числом.
    """
    html = io.open(PAGE, encoding="utf-8").read()
    body = "\n".join(ln for ln in html.splitlines()
                     if "/*" not in ln and "*" != ln.strip()[:1]
                     and "<!--" not in ln)
    assert "Міряв Андрій" not in body, "підпис над розділом мусив зникнути"
    # А джерело кожної цифри лишається: у пакеті `how` є в усіх записах, і
    # плитка його показує (тест `test_page_shows_how_for_every_tile`).
    for m in stats_mod.partner_metrics(PACKAGE)["shown"]:
        assert m["how"].strip(), m
