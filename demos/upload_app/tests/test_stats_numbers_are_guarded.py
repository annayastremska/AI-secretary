# -*- coding: utf-8 -*-
"""Кожне число, яке сторінка «Статистика» ПОКАЗУЄ, стережеться точним значенням.

Нащо цей файл існує окремо. Аудит чесності тестів 27.08 зламав по черзі кожне
головне число сторінки — і жоден тест не впав:

    знаменник звірки каталогу × 2      100% -> 50%      24 passed
    знаменник маршрутизації підмінено  100% -> 11.1%    24 passed
    fields_pct × 0.9                   100% -> 90%      17 passed
    tests_total = лише passed          299/300 -> 299/299  17 passed
    normative_ok -> None               41/42 -> null/42  17 passed
    фільтр черги навпаки               28 -> 133        17 passed
    service_id IS NULL -> IS NOT NULL  3 -> ~297        17 passed

Причина була одна: наявні тести перевіряли ДІАПАЗОН («0 <= pct <= 100»,
«fields_total > 0»), а діапазон витримує майже будь-яку поломку. Тест на
діапазон — це не рейка, а її подоба: він падає лише тоді, коли код зламався
так сильно, що це видно й без тесту.

Тому тут — ТОЧНІ значення від відомого входу. Правило просте: якщо цифра
з'явилась на екрані, у неї мусить бути тест, який знає, чим вона МУСИТЬ бути.

Межа цього файла, щоб не обіцяти більше, ніж він робить: він стереже
АРИФМЕТИКУ й ЗНАМЕНАТОРИ, тобто «код рахує те, що ми задумали». Він НЕ
перевіряє, чи задумане правильне — для цього є прилади на живій базі
(`verify_catalog`, `measure_router`) і людське око.
"""
import io
import json

import pytest

from demos.upload_app import stats


# ── Відомий вхід ────────────────────────────────────────────────────────────
#
# Числа навмисно НЕ круглі й усі різні: круглі й однакові числа приховують
# помилку «взяли не те поле» — 100 у двох місцях виглядає правильно, чим би
# воно не було.
ROWS = {
    # Загальна кількість документів рахується з розкладки ЗА СТАТУСОМ, а не
    # за доменом. Числа підібрані так, щоб обидві розкладки давали ту саму
    # суму 203: якби вони розійшлись, тест би це й показав -- і це теж
    # корисна перевірка, бо два джерела одного числа мусять збігатись.
    stats.SQL_DOCS_BY_STATUS: [{"status": "confirmed", "n": 158},
                               {"status": "needs_review", "n": 42},
                               {"status": "failed", "n": 3}],
    stats.SQL_DOCS_BY_DOMAIN: [{"domain": "leave", "n": 95},
                               {"domain": "deployment", "n": 63},
                               {"domain": "normative", "n": 44},
                               {"domain": "staffing", "n": 1}],
    stats.SQL_FACTS_BY_STATUS: [{"status": "confirmed", "n": 1879},
                                {"status": "unconfirmed", "n": 132}],
    stats.SQL_PEOPLE: [{"n": 303}],
    stats.SQL_REVIEW_OPEN: [{"queue_type": "new_person", "n": 4},
                            {"queue_type": "qa_sample", "n": 6},
                            {"queue_type": "unconfirmed_fact", "n": 20},
                            {"queue_type": "unknown_type", "n": 2}],
    stats.SQL_PEOPLE_UNMATCHED: [{"n": 3}],
    stats.SQL_DOCS_PENDING: [{"n": 31}],
    stats.SQL_DOCS_PENDING_SUBSTANTIVE: [{"n": 28}],
}


def _query(sql, params=None):
    norm = " ".join(sql.split())
    for key, rows in ROWS.items():
        if " ".join(key.split()) == norm:
            return rows
    raise AssertionError("макет не знає запиту:\n" + norm)


@pytest.fixture()
def out():
    return stats.collect(query=_query, report_path="нема-такого-файлу.json")


# ── Числа з бази ────────────────────────────────────────────────────────────


def test_facts_confirmed_and_drafts_exactly(out):
    """Два головні числа сторінки. Мутація «чернетки → 0» падала й раніше, а
    ось підміна самого `confirmed` — ні."""
    assert out["facts"]["confirmed"] == 1879
    assert out["facts"]["unconfirmed"] == 132
    assert out["facts"]["rows_total"] == 2011


def test_documents_total_and_personnel_split_exactly(out):
    """Сторінка показує «204 документи, з них 158 кадрових». Обидва числа
    рахуються з `by_domain`, тому мусять бути точними, а не «більше нуля»."""
    d = out["documents"]
    # 158 + 42 + 3 за статусом == 95 + 63 + 44 + 1 за доменом. Дві розкладки
    # одного й того самого набору документів мусять давати ту саму суму.
    assert d["total"] == 203
    assert sum(d["by_domain"].values()) == d["total"],         "розкладки за статусом і за доменом розійшлись -- одна з них бреше"
    assert d["by_domain"]["leave"] == 95
    assert d["by_domain"]["deployment"] == 63
    assert d["by_domain"]["normative"] == 44
    # Кадрові = відпустки + відрядження. Саме це число сторінка й підписує.
    assert d["by_domain"]["leave"] + d["by_domain"]["deployment"] == 158
    assert d["failed"] == 3
    assert d["by_status"]["confirmed"] == 158


def test_people_exactly(out):
    assert out["people"] == 303


def test_queue_three_numbers_are_three_different_numbers(out):
    """Найтонше місце. У черзі три різні числа, і до 27.08 фікстура підсовувала
    їм ті самі рядки — тобто тест дивився на одне число замість трьох. Тепер
    вони мусять відрізнятись, і кожне мусить бути своїм."""
    q = out["review_queue"]
    assert q["open_total"] == 32                    # 4 + 6 + 20 + 2
    assert q["documents_pending"] == 31             # це число ПОКАЗУЄ сторінка
    assert q["documents_pending_substantive"] == 28  # це — ні, лишається в API
    assert q["people_unmatched"] == 3
    # І вони справді різні: якби запити знову позичили один одному рядки,
    # рівність тут це й покаже.
    assert len({q["open_total"], q["documents_pending"],
                q["documents_pending_substantive"], q["people_unmatched"]}) == 4


def test_api_keeps_both_pending_numbers_and_the_page_shows_neither():
    """Рядок черги прибраний 29.08 на прохання Ані -- перевірка переїхала в API.

    Історія цієї перевірки. 26.08 з'ясувалось, що фільтр «крім нової особи»
    приховує три ЖИВІ документи, і тест зафіксував: на екран іде
    `documents_pending` (31), а не `documents_pending_substantive` (28). Потім
    рядок черги зі сторінки прибрали цілком, і тест упав -- він міряв розмітку.

    Тому тепер перевіряється те, що лишилось істинним:
      1. в API є ОБИДВА числа. Одне без другого вводить в оману в обидві
         сторони, тому жодне не викидається;
      2. на сторінці немає НІ ОДНОГО з них -- рядок прибраний свідомо, і якщо
         він колись вернеться, тест мусить упасти й змусити обрати число
         явно, а не взяти те, що під рукою.
    """
    # Перевіряємо КОД, а не живу збірку: без Postgres `collect()` віддає
    # `db_available: false` і жодних лічильників, тобто тест міряв би наявність
    # бази замість наявності охорони. Сервер вимкнений 01.09 -- саме так це й
    # виявилось.
    src = io.open("demos/upload_app/stats.py", encoding="utf-8").read()
    assert '"documents_pending": docs_pending' in src
    assert '"documents_pending_substantive": docs_pending_sub' in src

    html = io.open("demos/upload_app/static/stats.html", encoding="utf-8").read()
    assert "q.documents_pending" not in html, (
        "рядок черги повернувся -- обери число ЯВНО і поправ цей тест: "
        "на екран іде documents_pending (31), не відфільтроване (28)")

# ── Сталі числа заміру ──────────────────────────────────────────────────────


def _quality(tmp_path, payload):
    path = tmp_path / "baseline.json"
    io.open(path, "w", encoding="utf-8").write(
        json.dumps(payload, ensure_ascii=False))
    return stats.quality_metrics(str(path))[0]


def test_tests_baseline_denominator_counts_xfail(tmp_path):
    """«299 з 300»: знаменник = passed + failed + xfailed. Мутація
    «знаменник = лише passed» давала «299 з 299» і проходила."""
    q = _quality(tmp_path, {"measurements": {
        "tests": {"passed": 299, "failed": 0, "xfailed": 1}}})
    assert q["tests_passed"] == 299
    assert q["tests_failed"] == 0
    assert q["tests_xfailed"] == 1
    assert q["tests_total"] == 300, \
        "без xfail знаменник дорівнює чисельнику, і плитка завжди «все добре»"


def test_normative_number_survives(tmp_path):
    """«41/42 класифіковано». Мутація `normative_ok = None` давала «null/42»
    і проходила."""
    q = _quality(tmp_path, {"measurements": {
        "normative": {"confirmed_normative": 41, "documents": 42}}})
    assert q["normative_ok"] == 41
    assert q["normative_total"] == 42


def test_router_percent_denominator_is_what_the_tier_took(tmp_path, monkeypatch):
    """Відсоток маршрутизації рахується від `routed`, а не від усіх питань.

    Мутація «знаменник = questions» давала 11.1% замість 100% і проходила.
    Сам відсоток зі сторінки прибрано (він рахувався по шести питаннях зі
    124), але поле лишається в API, і його арифметика мусить бути стережена:
    інакше наступний, хто його візьме, візьме неправильне.
    """
    path = tmp_path / "router.json"
    io.open(path, "w", encoding="utf-8").write(json.dumps({
        "questions": 54, "routed": 6, "routed_ok": 6,
        "confidently_wrong": 0, "encoder": "test", "threshold": 0.92,
        "production_view": True, "measured_at": "2026-08-27T10:00:00"}))
    monkeypatch.setattr(stats, "ROUTER_REPORT", str(path))
    monkeypatch.setattr(stats, "CATALOG_REPORT", "нема.json")
    r = stats.chat_quality()["router"]
    assert r["routed"] == 6 and r["questions"] == 54
    assert r["pct"] == 100.0, "знаменник -- routed, а не questions"
    assert r["confidently_wrong"] == 0


def test_catalog_percent_denominator_is_checks(tmp_path, monkeypatch):
    """«34 з 34». Мутація «знаменник × 2» давала 50% і проходила."""
    path = tmp_path / "catalog.json"
    io.open(path, "w", encoding="utf-8").write(json.dumps({
        "checks": 34, "matched": 34, "failures": 0, "templates": 28,
        "as_of": "2026-08-27", "measured_at": "2026-08-27T10:17:57"}))
    monkeypatch.setattr(stats, "CATALOG_REPORT", str(path))
    monkeypatch.setattr(stats, "ROUTER_REPORT", "нема.json")
    c = stats.chat_quality()["catalog"]
    assert c["checks"] == 34 and c["matched"] == 34
    assert c["pct"] == 100.0
    # І неповна звірка мусить давати НЕ 100%, інакше плитка не вміє червоніти.
    io.open(path, "w", encoding="utf-8").write(json.dumps({
        "checks": 34, "matched": 29, "failures": 5}))
    c = stats.chat_quality()["catalog"]
    assert c["pct"] == 85.3, c["pct"]


# ── Те, чого на сторінці більше не має бути ─────────────────────────────────


def _visible(text):
    """Прибрати коментарі: перевіряємо те, що бачить ЛЮДИНА.

    Перша версія цього тесту впала на моєму ж коментарі -- у ньому написано,
    яку саме обіцянку прибрано, і дослівно. Це не хиба тесту, а нагадування:
    шукати рядок «десь у файлі» і шукати його «на екрані» -- різні речі.
    """
    import re
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)     # блокові в js і css
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)     # html
    text = re.sub(r"(?m)^\s*//.*$", "", text)              # рядкові в js
    return text


def test_removed_claims_stay_removed():
    """Три твердження прибрані свідомо (рішення Ані 27.08), і мусять лишитись
    прибраними: кожне з них не могло бути неправдою за побудовою.

    Тест на текст сторінки — рідкісний випадок, коли це доречно: стережеться
    не поведінка, а ОБІЦЯНКА, а обіцянка й живе в тексті.
    """
    html = _visible(io.open("demos/upload_app/static/stats.html",
                            encoding="utf-8").read())
    # 1. «полів витягнуто правильно» читалось із файла ОЧІКУВАНИХ цифр --
    #    плитка не могла показати не-100% ніколи.
    assert "полів витягнуто правильно" not in html
    # 2. «Заміряно ‹дата›» -- це був mtime файла, тобто час git pull.
    assert "Заміряно " not in html
    # 3. «підтверджених» на цій сторінці означало б «людина підтвердила», а в
    #    базі немає ні confirmed_by, ні confirmed_at.
    assert "підтверджених фактів" not in html
    assert "фактів витягнуто впевнено" in html
