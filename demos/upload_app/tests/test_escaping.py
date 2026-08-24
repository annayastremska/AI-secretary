"""Екранування недовіреного тексту в рендері чата (задача 1.5, Андрій §8).

Текст документа, значення з бази і вивід моделі — недовірений вхід: усе, що
чат вставляє в Markdown/HTML (gr.Chatbot рендерить HTML), мусить проходити
html.escape. Еталонний кейс з плану: рядок `<script>alert(1)</script>` у
тексті документа рендериться літерами, а не виконується.
"""
import json

import demos.upload_app.chat_gradio.app as chat_app
import demos.upload_app.chat_gradio.tiers as tiers

XSS = "<script>alert(1)</script>"


def _no_raw_script(text):
    assert XSS not in text, "сирий <script> дійшов до рендера"
    assert "&lt;script&gt;" in text, "текст мусить лишитись видимим літерами"


def test_reference_text_is_escaped(monkeypatch):
    """Головний кейс §8: текст розділу документа їде в <details> без моделі.

    В оригіналі Колі текст вставлявся сирим -- <script> із документа
    виконався б у браузері користувача чата."""
    hit = {"text": f"Пункт 1. {XSS} Далі текст розділу.",
           "section_title": "<b>Розділ</b>",
           "section_number": "4.2",
           "doc_title": f"Наказ {XSS}",
           "source_note": "тестовий акт"}
    monkeypatch.setattr(chat_app.db, "search_reference",
                        lambda q, limit=3: [hit])
    out = chat_app.answer_reference("Як оформити відпустку?")
    _no_raw_script(out)
    assert "<b>Розділ</b>" not in out          # заголовок теж недовірений


def test_fmt_source_block_escapes():
    out = chat_app._fmt_source_block([XSS, "рядок & <тег>"], "тест")
    _no_raw_script(out)
    # & екранується теж: інакше &lt; можна протягти як текст і розекранувати
    assert "&amp;lt;" not in out               # подвійного екранування немає
    assert "рядок &amp; &lt;тег&gt;" in out


def test_person_label_escapes():
    row = {"person_name_raw": XSS, "service_id": None}
    _no_raw_script(chat_app.person_label(row))


def test_describe_doc_escapes():
    row = {"doc_number": f"№301{XSS}", "doc_type": XSS,
           "person_name_raw": "Петренко", "date_from": "2026-05-01",
           "date_to": "2026-05-10", "status": "чинний",
           "reason": f"причина {XSS}", "place": "<img src=x onerror=alert(1)>",
           "superseded_by": None}
    out = chat_app.describe_doc(row)
    _no_raw_script(out)
    assert "<img" not in out


def test_footer_escapes_source():
    out = chat_app.footer("підрахунок", source=XSS, cut=f"зріз {XSS}")
    _no_raw_script(out)
    # службова розмітка самого footer лишається робочою
    assert "<details" in out and "</details>" in out


def test_state_marker_survives_comment_breakout():
    """«-->» усередині значення слота не закриває HTML-коментар достроково,
    а json.loads повертає значення без втрат."""
    params = {"intent": "документи_людини", "date": None, "subdivision": None,
              "name": "x--><script>alert(1)</script>", "doc_number": None}
    marker = chat_app._state_marker(params)
    payload = chat_app.STATE_RE.search(marker).group(1)
    assert "-->" not in payload
    assert json.loads(payload)["name"] == params["name"]
    # і читання стану з історії віддає те саме
    hist = [{"role": "assistant", "content": "відповідь" + marker}]
    assert chat_app._read_state(hist)["name"] == params["name"]


def test_tiers_esc_basics():
    assert tiers._esc(XSS) == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert tiers._esc("Мар'яна & Ко") == "Мар'яна &amp; Ко"


def test_blocked_template_returns_refusal_without_sql():
    """Доробка каталогу: blocked: true перевіряється ДО читання sql --
    у subdivision_blocked його немає взагалі, відповідь -- дослівний refusal."""
    text, source = tiers.run_template("subdivision_blocked", {})
    assert "підрозділ" in text.lower()
    assert "не маю даних" in text.lower() or "не можу" in text.lower()
    assert any("заблоковано" in s for s in source)


def test_blocked_refusal_is_escaped(monkeypatch):
    """Знахідка верифікатора: refusal ішов у рендер без _esc. Репро -- фейковий
    blocked-шаблон із розміткою в refusal (у живому каталозі її немає, але
    правило «одна точка захисту» мусить триматись і на майбутнє)."""
    fake = {"id": "fake_blocked", "title": "тест", "blocked": True,
            "refusal": f"відмова {XSS}"}
    monkeypatch.setitem(tiers._CATALOG, "fake_blocked", fake)
    text, _ = tiers.run_template("fake_blocked", {})
    _no_raw_script(text)


def test_sql_params_passes_query():
    """Доробка каталогу: параметр query (normative_search, FTS) проходить
    фільтр _sql_params; зайве (state) -- відсікається."""
    sp, t = tiers._sql_params("normative_search",
                              {"query": "відпустка", "state": "leave"})
    assert sp == {"query": "відпустка"}
    assert "%(query)s" in t["sql"]


def test_clarify_hint_from_model_is_escaped(monkeypatch):
    """Текст уточнення пише модель -- він теж екранується перед рендером."""
    result = chat_app.dispatch_count(
        {"intent": "хто_відсутній", "date": None, "subdivision": None,
         "name": None, "doc_number": None},
        clarified=False, clarify_hint=f"Уточніть дату {XSS}")
    assert isinstance(result, tuple) and result[0] == "clarify"
    _no_raw_script(result[1])
