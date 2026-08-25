"""Тести векторного ярусу (етап 3 плану 2026-08-24_app-chat-plan.md).

Що перевіряється БЕЗ ваг encoder-а (тести не мають вантажити сотні МБ і
ходити в мережу -- encoder підмінюється стабом із детермінованими
векторами по триграмах символів):

  - маршрути будуються з examples каталогу (включно зі smalltalk);
  - поріг працює: бал нижче THRESHOLD -> (None, score);
  - e5-префікси накладаються: "passage: " на приклади, "query: " на питання;
  - розмовний маршрут не їде в SQL: відповідь фіксована, база не смикається;
  - відсутність ваг -> деградація з попередженням у лог, не падіння;
  - params_for_template: дефолти дат і чесний None без обов'язкового
    параметра.

Стаб-вектори -- лічильники хеш-триграм, L2-нормовані: однаковий текст дає
близькість 1.0, неперетинний -- ~0. Цього досить, щоб перевірити механіку
(побудова, поріг, префікси) без справжньої моделі.
"""
import datetime

import numpy as np
import pytest

import demos.upload_app.chat_gradio.app as chat_app

tier_chat = chat_app.tier_chat
# ВАЖЛИВО: той самий екземпляр модуля, яким користується app.py (він
# імпортує vector_route пласко через sys.path, а не через пакет) --
# інакше monkeypatch не дістав би до продового коду.
vr = chat_app.tier_vector


def _trigram_vec(text, dim=256):
    # стаб зображає БАГАТОМОВНИЙ encoder, для якого query:/passage: --
    # службові маркери, а не зміст: прибираємо їх перед векторизацією,
    # інакше однаковий текст із різними префіксами не зійдеться
    for pref in ("query: ", "passage: "):
        if text.startswith(pref):
            text = text[len(pref):]
    v = np.zeros(dim)
    t = f"  {text.lower()}  "
    for i in range(len(t) - 2):
        v[hash(t[i:i + 3]) % dim] += 1.0
    n = np.linalg.norm(v)
    return (v / n if n else v).tolist()


def _make_stub_encoder(captured):
    """Encoder-стаб, сумісний із SemanticRouter: пише всі docs у captured."""
    from semantic_router.encoders import DenseEncoder

    class StubEncoder(DenseEncoder):
        name: str = "stub"
        type: str = "stub"

        def __call__(self, docs, **kwargs):
            captured.extend(docs)
            return [_trigram_vec(d) for d in docs]

    return StubEncoder(score_threshold=0.0)


@pytest.fixture()
def stub_router(monkeypatch):
    """Роутер на стаб-encoder-і + чистий стан модуля до і після тесту."""
    captured = []
    monkeypatch.setattr(vr, "_make_encoder",
                        lambda name=None: _make_stub_encoder(captured))
    monkeypatch.setattr(vr, "_ROUTER", None)
    monkeypatch.setattr(vr, "_FAILED", False)
    return captured


def test_routes_built_from_catalog(stub_router):
    router = vr._get_router()
    assert router is not None
    route_names = {r.name for r in router.routes}
    catalog_ids = {tid for tid, _ in vr.catalog_routes()}
    assert route_names == catalog_ids
    # розмовний маршрут (3.5) і заблокований шаблон теж мають маршрути:
    # питання про підрозділи мусить влучати у чесну відмову, не у фолбек
    assert "smalltalk" in route_names
    assert "subdivision_blocked" in route_names


def test_prefixes_of_the_active_encoder_are_applied(stub_router):
    """Префікси беруться зі специфікації ДІЮЧОГО encoder-а, а не з e5.

    Тест був написаний під e5 (`passage: `/`query: `) і впав, коли за
    заміром 25.08 обрано bge-m3 -- у неї префіксів немає взагалі. Впав
    правильно: перевіряти треба не літерал «passage: », а те, що на кожному
    боці стоїть саме той префікс, який картка моделі вимагає. Інакше зміна
    encoder-а тихо роз'їжджається з тим, як ми його кличемо -- а це рівно та
    помилка, через яку arctic давав 14.5%.
    """
    spec = vr.ENCODER_SPECS[vr.ENCODER_NAME]
    q_pref, p_pref = spec["query_prefix"], spec["passage_prefix"]
    vr._get_router()
    # (DenseEncoder при ініціалізації кодує службовий док "test" для
    # визначення розмірності -- він не з каталогу, виключаємо)
    assert stub_router, "encoder не викликався при побудові індексу"
    for _, examples in vr.catalog_routes():
        for u in examples:
            assert (p_pref + u) in stub_router
    n_before = len(stub_router)
    vr.vector_route("Скільки людей зараз у відпустці?")
    queries = stub_router[n_before:]
    assert queries == [q_pref + "Скільки людей зараз у відпустці?"]


def test_asymmetric_and_symmetric_specs_differ_on_the_passage_side():
    """Запобіжник проти «префікси є, але однакові»: тест вище на
    порожніх префіксах bge-m3 пройшов би і тоді, коли код префікси взагалі
    не застосовує. Тому окремо -- що специфікації РІЗНІ там, де мають бути
    різні, і що асиметричний кандидат лишився асиметричним."""
    e5 = vr.ENCODER_SPECS["intfloat/multilingual-e5-base"]
    sym = vr.ENCODER_SPECS["intfloat/multilingual-e5-base@sym"]
    assert e5["query_prefix"] == sym["query_prefix"] == "query: "
    assert e5["passage_prefix"] == "passage: "
    assert sym["passage_prefix"] == "query: "


def test_exact_example_routes_to_its_template(stub_router, monkeypatch):
    # МЕХАНІКА маршрутизації на контрольованому мінікаталозі: стаб-вектори
    # (триграми символів) не зображають семантику, тому справжній каталог
    # тут не годиться -- на ньому близькі за буквами приклади різних
    # шаблонів («скільки людей у 2 роті у відпустці») перетягують маршрут,
    # і це чесна властивість стаба, не коду. СЕМАНТИКУ на повному каталозі
    # міряє measure_router.py справжніми encoder-ами (задача 3.2).
    monkeypatch.setattr(vr, "catalog_routes", lambda path=None: [
        ("route_a", ["скільки людей у відпустці",
                     "скільки зараз у відпустці"]),
        ("route_b", ["покажи документ номер сто"]),
    ])
    monkeypatch.setattr(vr, "THRESHOLD", 0.35)
    tid, score = vr.vector_route("скільки людей у відпустці")
    assert tid == "route_a"
    assert score >= 0.35


def test_below_threshold_returns_none(stub_router):
    # текст без спільних триграм із прикладами: бал ~0 -> нижче порога
    tid, score = vr.vector_route("zzz qqq www")
    assert tid is None
    assert score < vr.THRESHOLD


def test_smalltalk_does_not_touch_sql(monkeypatch):
    """Розмовний маршрут (3.5): відповідь фіксована кодом, без SQL."""
    t = tier_chat._CATALOG["smalltalk"]
    assert t.get("blocked") is True
    assert "sql" not in t and "sql_unconfirmed" not in t
    assert t.get("refusal")

    def _no_db(*a, **k):
        raise AssertionError("розмовний маршрут смикнув базу")

    monkeypatch.setattr(tier_chat, "_run_template_sql", _no_db)
    monkeypatch.setattr(chat_app.tier_vector, "vector_route",
                        lambda q: ("smalltalk", 0.95))
    out = chat_app._vector_tier("Дякую за допомогу!")
    assert out is not None
    assert "Вітаю" in out
    assert "розмовний маршрут" in out


def test_vector_tier_order_after_rules_before_model(monkeypatch):
    """Порядок доріг: правила -> вектори -> модель. Якщо вектори відповіли,
    модельні яруси не викликаються."""
    monkeypatch.setattr(chat_app, "_catalog_tier", lambda q: None)
    monkeypatch.setattr(chat_app, "_vector_tier",
                        lambda q: "ВЕКТОРНА ВІДПОВІДЬ")

    def _model_must_not_run(q):
        raise AssertionError("модельний ярус викликано після векторного")

    monkeypatch.setattr(chat_app, "_model_catalog_tier", _model_must_not_run)
    monkeypatch.setattr(chat_app, "_tier2_tier", _model_must_not_run)
    assert chat_app._extra_tiers("скільки людей у відпустці") == \
        "ВЕКТОРНА ВІДПОВІДЬ"


def test_missing_weights_degrades_with_warning(monkeypatch, caplog):
    """Немає ваг encoder-а -> ярус вимкнено з попередженням, чат не падає."""
    def _boom(name=None):
        raise RuntimeError("ваги не знайдено")

    monkeypatch.setattr(vr, "_make_encoder", _boom)
    monkeypatch.setattr(vr, "_ROUTER", None)
    monkeypatch.setattr(vr, "_FAILED", False)
    with caplog.at_level("WARNING"):
        tid, score = vr.vector_route("Скільки людей зараз у відпустці?")
    assert (tid, score) == (None, 0.0)
    assert any("Векторний ярус вимкнено" in r.message for r in caplog.records)
    # падіння запам'ятовано: другий виклик не пробує вантажити знову
    monkeypatch.setattr(vr, "_make_encoder", lambda name=None: (_ for _ in ()).throw(
        AssertionError("повторна спроба завантаження після падіння")))
    assert vr.vector_route("Хто у відпустці?") == (None, 0.0)
    # і _vector_tier наскрізь: деградація, питання їде далі (None)
    assert chat_app._vector_tier("Скільки людей зараз у відпустці?") is None


def test_params_for_template_defaults_and_honest_none():
    today = datetime.date.today()
    # дефолт дати -> сьогодні (той самий, що в model_route)
    p = tier_chat.params_for_template("returning_on_date",
                                      "Хто повертається з відпустки?")
    assert p["on_date"] == today
    assert p["dims"] == tier_chat.STATE_DIMS["leave"]
    # період без дат у питанні -> сьогодні..сьогодні
    p = tier_chat.params_for_template("with_co_travelers",
                                      "Хто поїхав з родиною?")
    assert (p["date_from"], p["date_to"]) == (today, today)
    # query їде ЛИШЕ параметром -- дослівний текст питання
    p = tier_chat.params_for_template("normative_search",
                                      "Яка процедура оформлення відпустки?")
    assert p["query"] == "Яка процедура оформлення відпустки?"
    # обов'язковий параметр не витягся (без бази реєстр осіб недоступний,
    # extract_name чесно дає None) -> None, а не вигаданий параметр
    assert tier_chat.params_for_template(
        "person_status", "Що відомо про Невідомого?") is None
    # blocked-шаблон параметрів не потребує
    assert tier_chat.params_for_template("smalltalk", "Привіт!") == {}
