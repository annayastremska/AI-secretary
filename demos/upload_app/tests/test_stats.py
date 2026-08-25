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
    """Підключення-макет: відповідає за фрагментом SQL, а не за порядком
    викликів -- інакше тест ламався б від будь-якої перестановки в collect()."""
    def query(sql, params=None):
        for needle, rows in rows_by_sql.items():
            if needle in sql:
                return rows
        raise AssertionError(f"неочікуваний SQL у тесті: {sql}")
    return query


LIVE = {
    "FROM documents GROUP BY status": [{"status": "confirmed", "n": 150},
                                       {"status": "needs_review", "n": 46},
                                       {"status": "failed", "n": 2}],
    "FROM documents GROUP BY domain": [{"domain": "leave", "n": 120},
                                       {"domain": "deployment", "n": 37},
                                       {"domain": "normative", "n": 41}],
    "FROM facts GROUP BY status": [{"status": "confirmed", "n": 1100},
                                  {"status": "unconfirmed", "n": 178}],
    "FROM objects o": [{"n": 133}],
    "FROM review_queue": [{"queue_type": "new_person", "n": 120},
                          {"queue_type": "unknown_type", "n": 41}],
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
    rows = dict(LIVE, **{"FROM documents GROUP BY domain":
                         [{"domain": None, "n": 5}]})
    out = _collect(rows)
    assert out["documents"]["by_domain"] == {"(не вказано)": 5}


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
    assert "/static/theme-tokens.css" in page.text
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
    assert "/static/theme-tokens.css" in html
    assert "/static/pages.css" in html
    for dead in ("--ink:", "--ok-bg:", "#1f6feb"):
        assert dead not in html, f"стара палітра сторінки лишилась: {dead}"
    # шапка з переходами -- та сама, що на сторінці статистики
    for href in ('href="/"', 'href="/stats"', 'href="/chat"'):
        assert href in html
    # жодного зовнішнього запиту (перевірка №7 README апки)
    for external in ("http://", "https://", "fonts.googleapis", "cdn."):
        assert external not in html, external
