"""Сторінка «Статистика» (задача B2): цифри бази + останній звіт прогону.

Живої бази локально немає, тому підключення підставляється фікстурою: у
`collect(query=...)` їде функція, яка віддає рядки замість psycopg. Це не
обхід перевірки, а її умова -- SQL-форми вже звірені з живою базою в
`query_catalog.yaml`, а перевірити треба СКЛАДАННЯ чисел і поведінку на
відмові.

Що доводиться:
  1. чернетки НЕ змішуються з підтвердженими -- окремі числа, і жодне поле
     відповіді не є їхньою сумою, яку можна показати як «фактів у базі»;
  2. «чекає підтвердження» рахується лише по нерозв'язаних записах черги
     (resolved_at IS NULL) -- умова присутня в самому SQL;
  3. немає бази -> `db_available: false` + причина, а НЕ нулі й не виняток
     (нуль означав би «у базі порожньо», а це інша річ);
  4. немає run-report.json -> це стан, не помилка;
  5. маршрути апки: /stats віддає сторінку, /api/stats -- JSON, і обидва
     живуть без бази;
  6. спільне обличчя (задача B3): сторінки тягнуть ОДИН набір токенів, і
     власного набору кольорів у сторінки завантаження більше немає.
"""
import json
import os

from fastapi.testclient import TestClient

import demos.upload_app.app as upapp
import demos.upload_app.stats as stats

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── Фікстура бази ───────────────────────────────────────────────────────────

def fake_query(rows_by_sql):
    """Підключення-макет: відповідає за КОНСТАНТОЮ запиту, а не за підрядком.

    Було: зіставлення підрядком (`if needle in sql`). Аудит показав, чому це
    не працює: ключ «FROM review_queue» збігається з ТРЬОМА різними запитами
    -- розкладкою черги за типом, «скільком документів чекає людини» і тим
    самим із фільтром. Через це обидва лічильники документів читали рядки
    групування черги й давали 120 -- число, яке до них не стосується. І
    невідомий запит не падав, а мовчки позичав чужі рядки, хоч коментар тут
    обіцяв рівно протилежне.

    Це третій випадок того самого класу: валідатор SQL шукав підрядок
    `confirmed`, сторож дужок вирізав `//` перед рядками, тепер фікстура.
    Зіставлення підрядком у перевірці -- не перевірка, а її подоба.

    Тепер ключ -- сама константа зі `stats.py`, тобто той самий об'єкт, який
    код і виконує. Розійтись вони не можуть за побудовою: перейменують
    константу -- тест упаде на імпорті, а не тихо почне міряти інше.
    """
    def query(sql, params=None):
        norm = " ".join(sql.split())
        for key, rows in rows_by_sql.items():
            if " ".join(key.split()) == norm:
                return rows
        raise AssertionError(
            "макет не знає цього запиту -- додай його у LIVE, інакше тест "
            "міряє не те, що код виконує:\n" + norm)
    return query


#: Ключі -- КОНСТАНТИ зі `stats.py`, не тексти. Тоді макет і код фізично не
#: можуть розійтися: перейменують константу -- тест упаде на імпорті.
LIVE = {
    stats.SQL_DOCS_BY_STATUS: [{"status": "confirmed", "n": 150},
                               {"status": "needs_review", "n": 46},
                               {"status": "failed", "n": 2}],
    stats.SQL_DOCS_BY_DOMAIN: [{"domain": "leave", "n": 120},
                               {"domain": "deployment", "n": 37},
                               {"domain": "normative", "n": 41}],
    stats.SQL_FACTS_BY_STATUS: [{"status": "confirmed", "n": 1100},
                                {"status": "unconfirmed", "n": 178}],
    stats.SQL_PEOPLE: [{"n": 133}],
    stats.SQL_REVIEW_OPEN: [{"queue_type": "new_person", "n": 120},
                            {"queue_type": "unknown_type", "n": 41}],
    # Скільком осіб немає відповідника у штатці.
    stats.SQL_PEOPLE_UNMATCHED: [{"n": 3}],
    # ТРИ РІЗНІ ЧИСЛА, які до цієї правки читали рядки черги вище й давали
    # 120 кожне. Тепер у кожного свій рядок, і різниця між ними видна в тесті
    # так само, як у коді: «скільком документів чекає людини» проти «того
    # самого без завдань про нову особу».
    stats.SQL_DOCS_PENDING: [{"n": 31}],
    stats.SQL_DOCS_PENDING_SUBSTANTIVE: [{"n": 28}],
}


def _collect(rows=None, report_path="нема-такого-файлу.json"):
    return stats.collect(query=fake_query(rows or LIVE), report_path=report_path)


# ── 1-2. Правила продукту в числах ──────────────────────────────────────────


def test_drafts_are_separate_from_confirmed():
    out = _collect()
    assert out["db_available"] is True
    assert out["facts"]["confirmed"] == 1100
    assert out["facts"]["unconfirmed"] == 178
    # сума є, але вона НЕ називається «фактами»: це рядки таблиці
    assert out["facts"]["rows_total"] == 1278
    assert "total" not in out["facts"], \
        "поле 'total' у фактах прочитають як підсумок для підрахунків"


def test_documents_and_people_counted():
    out = _collect()
    assert out["documents"]["total"] == 198
    assert out["documents"]["by_domain"]["normative"] == 41
    assert out["documents"]["failed"] == 2
    assert out["people"] == 133


def test_pending_review_counts_only_unresolved():
    out = _collect()
    assert out["review_queue"]["open_total"] == 161
    assert out["review_queue"]["by_type"] == {"new_person": 120,
                                             "unknown_type": 41}
    # умова «нерозв'язані» -- у самому SQL, не в постобробці
    assert "resolved_at IS NULL" in stats.SQL_REVIEW_OPEN


def test_labels_cover_every_returned_code():
    """Кожен код зі бази має український підпис -- інакше на сторінці буде
    `unknown_type` замість слів."""
    out = _collect()
    for code in out["review_queue"]["by_type"]:
        assert out["labels"]["queue"].get(code), code
    for code in out["facts"]["by_status"]:
        assert out["labels"]["fact_status"].get(code), code
    for code in out["documents"]["by_domain"]:
        assert out["labels"]["domain"].get(code), code


def test_empty_and_null_values_are_visible_not_hidden():
    # Ключ -- константа, а не текст: із текстовим ключем ця підміна просто
    # ДОДАВАЛА б нову пару, а справжній запит і далі віддавав би рядки з LIVE.
    # Саме так і сталось при переході на точне зіставлення -- тест упав і
    # показав, що підміна не діяла.
    rows = dict(LIVE)
    rows[stats.SQL_DOCS_BY_DOMAIN] = [{"domain": None, "n": 5}]
    out = _collect(rows)
    # Підпис -- «тип не визначено», а не «(не вказано)» (Аня 30.08): дужки з
    # порожнім словом у рядку типів читались як недоробка сторінки, а не як
    # факт про документ. Порожній ключ і далі ВИДНО -- це головне, що тут
    # перевіряється; змінилось лише те, ЧОГО саме, за словами, немає.
    assert out["documents"]["by_domain"] == {"тип не визначено": 5}


# ── 3-4. Межі: немає бази, немає звіту ──────────────────────────────────────


def test_db_down_says_so_instead_of_zeros():
    def broken(sql, params=None):
        raise RuntimeError("connection timeout expired")

    out = stats.collect(query=broken, report_path="нема.json")
    assert out["db_available"] is False
    assert "connection timeout" in out["db_error"]
    # ЖОДНОГО лічильника: нуль читався б як «у базі порожньо»
    for key in ("documents", "facts", "people", "review_queue"):
        assert key not in out, f"{key} показали б як нуль при недоступній базі"


def test_missing_run_report_is_a_state_not_an_error():
    out = _collect(report_path=os.path.join(APP_DIR, "нема-звіту.json"))
    assert out["run_report"] is None
    assert out["run_report_error"] is None
    assert out["run_report_mtime"] is None
    assert out["run_report_path"].endswith("нема-звіту.json")


def test_broken_run_report_reports_the_reason(tmp_path):
    bad = tmp_path / "run-report.json"
    bad.write_text("{це не json", encoding="utf-8")
    out = _collect(report_path=str(bad))
    assert out["run_report"] is None
    assert "JSONDecodeError" in out["run_report_error"]


def test_real_run_report_shape_is_read(tmp_path):
    """Читаємо звіт РІВНО тієї структури, яку будує pipeline/run_report.py --
    не власну вигадку про неї (модуль звіту не чіпаємо, лише викликаємо)."""
    from pipeline.run_report import build_report

    report = build_report([
        {"status": "confirmed", "template": "leave", "domain": "leave",
         "facts": [{"confirmed": True}, {"confirmed": False}],
         "field_provenance": {"surname": {"method": "matched"},
                              "rank": {"method": "no_value"}}},
    ])
    path = tmp_path / "run-report.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    out = _collect(report_path=str(path))
    r = out["run_report"]
    assert r["документів"]["усього"] == 1
    assert r["факти"]["підтверджені"] == 1
    assert r["факти"]["чернетки"] == 1          # окремим числом і у звіті
    assert out["run_report_mtime"]


def test_report_path_follows_the_pipeline_profile(tmp_path):
    """Тека виходу -- з профілю пайплайна: профілів кілька і теки в них
    різні (data/output проти data/output-demo)."""
    cfg = tmp_path / "config-x.yaml"
    cfg.write_text("paths:\n  output_dir: data/output-demo\n", encoding="utf-8")
    path = stats.default_report_path(str(cfg))
    assert path.replace("\\", "/").endswith("data/output-demo/run-report.json")
    # нечитабельний профіль -- дефолт, не виняток
    assert stats.default_report_path(str(tmp_path / "нема.yaml")) \
        .replace("\\", "/").endswith("data/output/run-report.json")


# ── 5. Маршрути апки ────────────────────────────────────────────────────────


def test_routes_serve_page_and_json(monkeypatch):
    monkeypatch.setattr(upapp, "BASIC_USER", "")
    monkeypatch.setattr(upapp, "BASIC_PASS", "")
    client = TestClient(upapp.app)

    page = client.get("/stats")
    assert page.status_code == 200
    assert "Статистика" in page.text
    # сторінка тягне спільні токени, а не власні кольори
    assert "/static/skin.css" in page.text
    assert "<style" not in page.text.split("<body")[0].replace(
        '<style class="dead-style-placeholder">', "")

    # база локально не піднята -- маршрут мусить віддати 200 і сказати причину
    monkeypatch.setattr(upapp.stats_mod, "db_counters",
                        lambda query=None: (None, "RuntimeError: бази немає"))
    api = client.get("/api/stats")
    assert api.status_code == 200
    assert api.json()["db_available"] is False


def test_static_route_serves_only_known_files(monkeypatch):
    monkeypatch.setattr(upapp, "BASIC_USER", "")
    monkeypatch.setattr(upapp, "BASIC_PASS", "")
    client = TestClient(upapp.app)
    for name in ("theme-tokens.css", "pages.css", "mark.svg"):
        assert client.get(f"/static/{name}").status_code == 200
    assert client.get("/static/.env").status_code == 404
    assert client.get("/static/index.html").status_code == 404


# ── 6. Спільне обличчя (задача B3) ──────────────────────────────────────────


def test_both_pages_use_the_same_tokens():
    import demos.upload_app.chat_gradio.app as chat_app

    tokens = open(os.path.join(APP_DIR, "static", "theme-tokens.css"),
                  encoding="utf-8").read()
    head = chat_app.make_head_css()
    # у чата токени приїжджають вмістом (head Gradio віддається до монтування,
    # відносний <link> під root_path=/chat не резолвиться)
    assert "--c-accent:      #4a6fa5" in tokens
    assert tokens.strip()[:40] in head or "--c-accent" in head
    assert "--c-accent" in head
    # і в theme.css чата власного :root уже НЕМА -- інакше токенів було б два
    theme = open(os.path.join(APP_DIR, "chat_gradio", "theme.css"),
                 encoding="utf-8").read()
    assert ":root {" not in theme, "у theme.css знову з'явився власний :root"


def test_upload_page_has_no_private_palette():
    html = open(os.path.join(APP_DIR, "static", "index.html"),
                encoding="utf-8").read()
    assert "/static/skin.css" in html
    for dead in ("--ink:", "--ok-bg:", "#1f6feb"):
        assert dead not in html, f"стара палітра сторінки лишилась: {dead}"
    # шапка з переходами -- та сама, що на сторінці статистики
    for href in ('href="/"', 'href="/stats"', 'href="/chat"'):
        assert href in html
    # жодного зовнішнього запиту (перевірка №7 README апки)
    for external in ("http://", "https://", "fonts.googleapis", "cdn."):
        assert external not in html, external

def test_api_carries_measured_quality():
    """Правка Ані 25.08: на сторінці мусить бути ВИМІРЯНА успішність пайплайна,
    не лише обсяг бази. Джерело -- eval/baseline.json, той самий файл, проти
    якого щодня йде перевірка «не ламає»: він у репо, тобто цифра видна в
    історії коду, а не лежить у чиємусь локальному звіті."""
    from demos.upload_app import stats
    q, err = stats.quality_metrics()
    assert err is None, err
    assert q["fields_total"] > 0
    assert 0 <= q["fields_pct"] <= 100
    assert q["fields_ok"] <= q["fields_total"]
    assert q["tests_passed"] and q["tests_passed"] > 0
    assert q["measured_at"], "без дати заміру цифра не перевірна"


def test_quality_is_about_fields_not_volume():
    """Запобіжник проти підміни: «скільком документів у базі» -- це ОБСЯГ, а не
    якість. Головна цифра якості мусить приходити з полів, звірених з
    еталоном."""
    from demos.upload_app import stats
    q, _ = stats.quality_metrics()
    assert set(q["per_corpus"]), "немає жодного корпусу з еталоном"
    total = sum(v["total"] for v in q["per_corpus"].values())
    assert total == q["fields_total"], (total, q["fields_total"])


def test_missing_baseline_is_reported_not_faked():
    """Немає файла -- кажемо про це, а не показуємо нуль: нуль означав би
    «нічого не витягнули правильно»."""
    from demos.upload_app import stats
    q, err = stats.quality_metrics("/nonexistent/baseline.json")
    assert q is None and err
