# -*- coding: utf-8 -*-
"""«Запис №33 у базі» мусить знаходитись.

Дефект був такий: під КОЖНОЮ відповіддю ми самі показуємо «документ №207
(запис №33 у базі)», а спитати про запис було неможливо -- `extract_doc_number`
хапав 33 як номер на папері, і відповідь виходила «документа №33 у базі немає».
Тобто система показувала ідентифікатор, яким не можна скористатись, а спроба
давала впевнену відповідь про ІНШИЙ документ.

Найважливіше це там, де номера на папері немає взагалі (58 документів із 205):
запис -- єдиний спосіб на такий документ послатись.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CHAT = os.path.abspath(os.path.join(HERE, "..", "chat_gradio"))
sys.path.insert(0, CHAT)

import importlib.util                               # noqa: E402

import tiers                                        # noqa: E402


def _load_chat_app():
    """Модуль чата за ШЛЯХОМ, під власним іменем.

    `import app` у спільному прогоні дає `demos/upload_app/app.py`
    (завантажувач): обидва файли звуться однаково, і перемагає той, чию теку
    раніше поклали в sys.path сусідні тести.
    """
    spec = importlib.util.spec_from_file_location(
        "chat_app_under_test", os.path.join(CHAT, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


chat = _load_chat_app()


# ── Маршрут ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("q,rid", [
    ("покажи запис №33 у базі", "33"),
    ("що за запис 245", "245"),
    ("запис №9999", "9999"),
    ("відкрий записи №12", "12"),
])
def test_record_questions_route_to_the_record_road(q, rid):
    assert tiers.rules_route(q) == ("doc_by_record", {"record_id": rid}), q


def test_paper_number_still_goes_its_own_way():
    """Дорога за номером на папері не зламана: обидві живуть на числі в
    питанні, і саме тому порядок правил тут несе всю вагу."""
    tid, params = tiers.rules_route("покажи документ №207")
    assert tid == "doc_by_number"
    assert params["doc_number"] == "207"


# ── Параметри старої дороги (місце, де дефект і жив) ──────────────────────────


def test_params_prefer_the_record_over_the_paper_number():
    """Маршрут правила ставили правильно ще до правки -- а відповідь усе одно
    йшла за номером на папері, бо параметри збирає ІНША функція. Шостий
    випадок класу «правило додане в одному місці з двох» за три дні, тому
    тест саме на `rules_params`."""
    p = chat.rules_params("покажи запис №33 у базі")
    assert p["intent"] == "документ_за_записом"
    assert p["record_id"] == "33"
    # Номер на папері мусить бути ЗНЯТИЙ: інакше картка піде за ним.
    assert not p["doc_number"]


def test_params_keep_the_paper_number_when_asked_about_a_document():
    """Номер приходить РАЗОМ ІЗ «№» -- так його віддає `extract_doc_number`, і
    так його чекає `db.document_by_number` (сама знімає префікс). Перша версія
    цього тесту чекала «207» і впала: помилка була в тесті, не в коді."""
    p = chat.rules_params("покажи документ №207")
    assert p["intent"] == "документ_за_номером"
    assert p["doc_number"].lstrip("№") == "207"


# ── Пошук у базі ─────────────────────────────────────────────────────────────


def test_record_lookup_rejects_nonsense():
    """Не число -> порожньо, без винятку: у запит нічого не підставляємо."""
    assert chat.db.document_by_record_id("абв") == []
    assert chat.db.document_by_record_id(None) == []


def test_record_lookup_accepts_the_shape_we_print():
    """Ми друкуємо «запис №33», тому й «№33» мусить читатись."""
    called = {}

    def fake_query(sql, params):
        called["params"] = params
        return []

    orig = chat.db._query
    try:
        chat.db._query = fake_query
        chat.db.document_by_record_id("№33")
    finally:
        chat.db._query = orig
    assert called["params"]["rid"] == 33


def test_one_render_for_both_roads():
    """Той самий документ не може виглядати двома різними картками залежно від
    того, чим його спитали: якщо номер на папері є -- дорога за записом віддає
    його `answer_doc`."""
    import io
    with io.open(os.path.join(CHAT, "app.py"), encoding="utf-8") as fh:
        src = fh.read()
    body = src[src.index("def answer_doc_by_record"):]
    body = body[:body.index("def answer_doc(")]
    assert "return answer_doc(num)" in body
    assert "doc_lead(rows[0])" in body        # для документів без номера
