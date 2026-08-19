# -*- coding: utf-8 -*-
"""Регресійні тести на САМОЗЦІЛЕННЯ OCR: коли перезапускати сервер
розпізнавання, а коли навмисно НЕ перезапускати.

Окремий файл, а не `test_regressions.py`: там регресії екстракції й
нормалізації -- чисті функції над текстом. Тут же перевіряється рішення про
ПЕРЕЗАПУСК ВАЖКОГО РЕСУРСУ, і ціна помилки інша: зайвий перезапуск коштує
хвилин на кожному документі, пропущений -- тихої втрати ВСІХ наступних
документів пакета.

Дві поведінки, заміряні 14.08.2026 (`docs/known-weak-spots.md`, розд. 2.18 і
2.19), обидві мусять бути зафіксовані саме тут:

1. **Мертвий сервер -> перезапуск і друга спроба.** Заміряно: сервер помер на
   5-му (в іншому прогоні -- на 10-му) документі, і документи 5-16 отримували
   РІВНО 0 блоків. Бекенд surya лишав `handle` виставленим, тому сам він більше
   не піднімався. Це та сама тиха втрата, через яку "57.5% точності" виявились
   недійсним числом.
2. **Живий сервер, що відповідає `400 failed to parse grammar` ->
   перезапуску НЕ буде.** Це НАВМИСНА поведінка, не баг, і тест існує саме щоб
   її не "виправили" як баг: перезапуск ваг на помилку ЗАПИТУ платить хвилинами
   там, де сервер здоровий, а 400 повториться, бо проблема в запиті. Документ
   при цьому виходить чесно порожнім -- `unresolved` плюс попередження, тобто
   людина побачить його в черзі, а не втратить безшумно.

Тести бігають БЕЗ surya, без ваг моделі й без жодного зображення: surya, PIL і
`probe_health` підставляються фальшивками. Це не зручність, а вимога -- умову
перезапуску треба перевіряти на кожному прогоні тестів, а не раз на пів дня
разом із 3,2-хвилинним OCR-прогоном.

Запуск:
    python -m pytest eval/tests/test_ocr_selfheal.py -q
"""
import contextlib
import os
import sys
import types

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from pipeline.ocr import surya_reader
from pipeline.ocr.surya_reader import make_surya_reader, restart_needed


# --- Частина 1: чиста умова перезапуску ------------------------------------
#
# `restart_needed` винесена окремою функцією саме щоб її можна було перевірити
# без surya. Перебираємо ВСІ значущі комбінації, а не приклади: комбінацій
# рівно шість (порожньо/непорожньо x True/False/None), і кожна має свою ціну.

def test_restart_needed_covers_every_combination():
    """Повна таблиця істинності. Перезапуск виправданий РІВНО в одному з шести
    випадків -- це кон'юнкція, а не «порожньо => перезапуск»."""
    blocks = [{"text": "абв", "bbox": (0, 0, 1, 1)}]
    table = {
        # (є блоки?, health) -> перезапускати?
        (False, False): True,   # мертвий сервер і нуль тексту -- єдиний випадок
        (False, True): False,   # чистий аркуш на живому сервері -- законно
        (False, None): False,   # «не змогли перевірити» -- не діагноз
        (True, False): False,   # текст уже отримано, сервер підніметься сам
        (True, True): False,    # нормальний прогін
        (True, None): False,
    }
    for (has_blocks, health), expected in table.items():
        got = restart_needed(blocks if has_blocks else [], health)
        assert got is expected, (has_blocks, health, got, expected)


def test_empty_result_alone_never_restarts():
    """Порожня сторінка сама по собі перезапуску НЕ викликає.

    Чому це окремий тест, а не рядок таблиці: спокуса «порожньо => перезапуск»
    виглядає безпечною, і саме її треба заблокувати явно. Ціна -- хвилини на
    перечитування ваг за КОЖЕН пустий скан, а пустий аркуш у пакеті сканів
    буває законно (зворотний бік бланка)."""
    for health in (True, None):
        assert restart_needed([], health) is False, health


def test_unknown_health_is_not_death():
    """`server_healthy is None` -- «не змогли перевірити», не «мертвий».

    Інакше найперший документ пакета перезапускав би щойно піднятий сервер:
    на першому виклику `handle` ще не виставлений, тому health законно None."""
    assert restart_needed([], None) is False
    # І -- жорсткіше -- саме `is False`, а не будь-яке хибне значення:
    # `None == False` хибне в Python, і код на це спирається.
    assert restart_needed([], 0) is False


# --- Підставні surya / PIL -------------------------------------------------
#
# Далі перевіряється не чиста функція, а ФАКТИЧНИЙ шлях `read()` з
# `make_surya_reader`. Робити це через справжню surya не можна: тест повинен
# бігати без 1.5 ГБ ваг. Тому підставляємо рівно ті чотири імпорти, які
# `surya_reader` робить ліниво всередині функцій.

class _FakeBlock:
    def __init__(self, text):
        self.html = text
        self.bbox = (0, 0, 10, 10)


class _FakePrediction:
    def __init__(self, texts):
        self.blocks = [_FakeBlock(t) for t in texts]


class _FakeImage:
    n_frames = 1

    def convert(self, mode):
        return self

    def seek(self, index):
        pass


class _FakeHandle:
    base_url = "http://127.0.0.1:8080/v1"


class _FakeBackend:
    def __init__(self):
        self.handle = _FakeHandle()


class _FakeManager:
    """Менеджер, який рахує перезапуски. Число `restarts` -- це і є те, що
    перевіряють тести нижче: не «чи є текст», а чи платили ми за перезапуск."""

    def __init__(self, start_fails=False):
        self.backend = _FakeBackend()
        self.restarts = 0
        self._start_fails = start_fails

    def stop(self):
        pass

    def start(self):
        self.restarts += 1
        if self._start_fails:
            raise RuntimeError("Failed to stop llamacpp: [WinError 5]")


@contextlib.contextmanager
def _fake_surya(attempts, healthy, reader_kwargs=None):
    """attempts -- список результатів послідовних спроб розпізнавання, кожен
    список рядків. healthy -- що віддасть `/health`: True/False, або None щоб
    зробити перевірку неможливою (probe_health кине виняток).

    Повертає (reader, manager), щоб тест міг спитати `manager.restarts`.
    """
    manager = _FakeManager()
    calls = {"n": 0}

    def _predictor(images):
        index = min(calls["n"], len(attempts) - 1)
        calls["n"] += 1
        return [_FakePrediction(attempts[index])]

    def _probe_health(root):
        # Перевіряємо заодно, що з base_url знято хвіст "/v1": питати
        # http://host/v1/health безглуздо, і помилка була б тихою -- виняток
        # усередині _health_of повернув би None, тобто «не змогли перевірити»,
        # і мертвий сервер НІКОЛИ не перезапускався б.
        assert root == "http://127.0.0.1:8080", root
        if healthy is None:
            raise OSError("probe failed")
        return healthy

    pil = types.ModuleType("PIL")
    pil.Image = types.SimpleNamespace(open=lambda path: _FakeImage())
    pil.ImageOps = types.SimpleNamespace(exif_transpose=lambda image: image)

    surya = types.ModuleType("surya")
    inference = types.ModuleType("surya.inference")
    inference.SuryaInferenceManager = lambda: manager
    backends = types.ModuleType("surya.inference.backends")
    spawn = types.ModuleType("surya.inference.backends.spawn")
    spawn.probe_health = _probe_health
    # Бекенд llamacpp -- у ньому make_surya_reader обмежує max_retries
    # (партіал поверх імені модуля, бо env-ручки в surya немає). Стаб мусить
    # його мати, інакше тест перевіряв би реader без цієї гілки.
    llamacpp = types.ModuleType("surya.inference.backends.llamacpp")

    def _fake_chat_completions_batch(batch, max_retries=3, **kwargs):
        return []

    llamacpp.chat_completions_batch = _fake_chat_completions_batch
    recognition = types.ModuleType("surya.recognition")
    recognition.RecognitionPredictor = lambda mgr: _predictor

    fakes = {
        "PIL": pil,
        "surya": surya,
        "surya.inference": inference,
        "surya.inference.backends": backends,
        "surya.inference.backends.spawn": spawn,
        "surya.inference.backends.llamacpp": llamacpp,
        "surya.recognition": recognition,
    }
    saved = {name: sys.modules.get(name) for name in fakes}
    saved_env = os.environ.get("SURYA_INFERENCE_PARALLEL")
    sys.modules.update(fakes)
    try:
        yield make_surya_reader(**(reader_kwargs or {})), manager
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if saved_env is None:
            os.environ.pop("SURYA_INFERENCE_PARALLEL", None)
        else:
            os.environ["SURYA_INFERENCE_PARALLEL"] = saved_env


# --- Частина 2: мертвий сервер -> перезапуск і друга спроба -----------------

def test_dead_server_restarts_and_second_attempt_returns_text():
    """Розд. 2.18: заміряний збій. Перша спроба -- нуль блоків при health=False,
    після перезапуску друга спроба віддає текст.

    Саме це раніше губило пакет: без перезапуску `handle` лишався виставленим,
    surya більше не піднімала сервер, і всі наступні документи отримували 0
    блоків, які виглядали як «чиста сторінка»."""
    with _fake_surya([[], ["Відпускний квиток", "ІВАНЕНКО Іван"]],
                     healthy=False) as (read, manager):
        blocks = read("LEAVE-003.png")
    assert manager.restarts == 1
    assert [b["text"] for b in blocks] == ["Відпускний квиток", "ІВАНЕНКО Іван"]


def test_healthy_server_with_text_never_restarts():
    """Нормальний прогін: перезапусків нуль. Тест-протилежність до попереднього
    -- без нього «перезапускати завжди» теж пройшло б."""
    with _fake_surya([["Відпускний квиток"]], healthy=True) as (read, manager):
        blocks = read("LEAVE-001.png")
    assert manager.restarts == 0
    assert len(blocks) == 1


def test_blank_page_on_healthy_server_never_restarts():
    """Порожня сторінка на ЖИВОМУ сервері -- законно чистий аркуш. Нуль
    перезапусків: інакше кожен пустий скан коштував би перечитування ваг."""
    with _fake_surya([[], ["не мусить дійти"]], healthy=True) as (read, manager):
        blocks = read("blank.png")
    assert manager.restarts == 0
    assert blocks == []


def test_unverifiable_health_does_not_restart():
    """`/health` недоступний -> None -> перезапуску немає, навіть при нулі
    блоків. Це поведінка першого документа пакета."""
    with _fake_surya([[], ["не мусить дійти"]], healthy=None) as (read, manager):
        blocks = read("first.png")
    assert manager.restarts == 0
    assert blocks == []


def test_failed_restart_returns_empty_instead_of_raising():
    """Заміряний `Failed to stop llamacpp: [WinError 5]`: якщо перезапуск не
    вдався, `read` мусить повернути порожній результат, а не кинути виняток.

    Причина саме така: виняток обвалив би ВЕСЬ пакетний прогін, тоді як
    порожній результат далі чесно стане `unresolved` із попередженням про нуль
    блоків -- один документ у черзі замість зупиненого прогону."""
    with _fake_surya([[], ["не мусить дійти"]], healthy=False) as (read, manager):
        manager._start_fails = True
        blocks = read("LEAVE-003.png")
    assert manager.restarts == 1          # спробували перезапустити
    assert blocks == []                   # і повернули порожнє, а не виняток


# --- Частина 3: живий сервер, 400 grammar -> перезапуску НЕ буде ------------

def test_grammar_400_on_live_server_does_not_restart_weights():
    """Розд. 2.19, LEAVE-011: сервер відповів `400 Failed to initialize
    samplers: failed to parse grammar` чотири рази підряд.

    Ключова відмінність від 2.18: сервер ЖИВИЙ (health=True) -- він відповідає,
    просто помилкою на запит. surya глушить помилку й віддає порожній результат,
    тому на вході в `restart_needed` це виглядає як порожня сторінка на живому
    сервері.

    Тест фіксує саме ВІДСУТНІСТЬ перезапуску, і це навмисна поведінка, а не
    незакрита вада: перезапуск ваг на помилку ЗАПИТУ коштує хвилини й нічого не
    лікує -- 400 повториться, бо проблема в запиті, а не в сервері. Якщо колись
    умову розширять на «сервер живий, але N запитів підряд з помилкою» (розд.
    2.19 називає це можливим ПІСЛЯ встановлення причини накопичення), цей тест
    мусить бути змінений СВІДОМО, а не впасти випадково.
    """
    with _fake_surya([[], ["не мусить дійти"]], healthy=True) as (read, manager):
        blocks = read("LEAVE-011.png")
    # головне: за помилку запиту ми не платимо перезапуском ваг
    assert manager.restarts == 0
    # документ виходить ЧЕСНО порожнім -- нуль блоків, нуль вигаданого тексту
    assert blocks == []


def test_grammar_400_document_is_honestly_empty_not_silently_lost():
    """Продовження попереднього до рівня ЗАПИСУ: документ із нулем блоків
    мусить давати `unresolved` + причину «збій розпізнавання» + попередження,
    а не зливатися з «тип документа невідомий».

    Це та сама межа, що робить втрату з 2.19 чесною (людина побачить документ у
    черзі), на відміну від тихої втрати з 2.18. Перевіряємо на справжньому
    `process_file` із підставним `ocr`, який віддає рівно те, що віддає surya
    після 400: порожній список."""
    from pipeline.config import load_config
    from pipeline.run import build_resources, process_file
    cfg = load_config(os.path.join(_PROJECT_ROOT, "config.yaml"))
    res = build_resources(cfg, force_no_llm=True)
    res["store"] = None
    res["ocr"] = lambda path: []          # саме те, що дає 400 grammar
    meta = process_file(
        os.path.join(_PROJECT_ROOT, "data", "eval", "samples", "leave",
                     "synthetic-2026-05", "png", "LEAVE-011.png"), res, cfg)

    assert meta["status"] == "unresolved"
    assert meta["ocr_blocks"] == 0 and meta["ocr_chars"] == 0
    assert "збій розпізнавання" in meta["reason"]
    assert meta["warnings"], "нуль блоків мусить давати попередження у звіті"


# --- Частина 4: сама перевірка здоров'я ------------------------------------

def test_recognition_max_retries_is_capped_via_backend_patch():
    """R1-№4: env-ручки для max_retries у surya немає (літерал 3 у сигнатурі
    chat_completions_batch), тому ЯВНО заданий конфігом ліміт
    make_surya_reader застосовує партіалом поверх імені в модулі бекенда.
    Без цієї перевірки перейменування в surya тихо повернуло б дефолтні
    3 повтори по ~хвилинах на патологічному кадрі."""
    import functools
    with _fake_surya(attempts=[["текст"]], healthy=True,
                     reader_kwargs={"recognition_max_retries": 1}):
        backend = sys.modules["surya.inference.backends.llamacpp"]
        patched = backend.chat_completions_batch
        assert isinstance(patched, functools.partial)
        assert patched.keywords.get("max_retries") == 1
        # Оригінал збережено -- повторний make_surya_reader у тому самому
        # процесі не завертає partial у partial.
        assert not isinstance(backend._unpatched_chat_completions_batch,
                              functools.partial)


def test_recognition_max_retries_untouched_by_default():
    """Дефолт -- НЕ чіпати surya: стеля/ліміт повторів відкочені за
    результатом заміру якості (p2-execution.md розд. 2). Без явного конфігу
    chat_completions_batch лишається оригіналом."""
    import functools
    with _fake_surya(attempts=[["текст"]], healthy=True):
        backend = sys.modules["surya.inference.backends.llamacpp"]
        assert not isinstance(backend.chat_completions_batch, functools.partial)


def test_health_of_returns_none_when_there_is_nothing_to_ask():
    """Немає backend/handle/base_url -> None («не змогли перевірити»), і НЕ
    False. Різниця не косметична: False означав би перезапуск на порожньому
    результаті, тобто перезапуск щойно піднятого сервера."""
    assert surya_reader._health_of(object()) is None
    assert surya_reader._health_of(
        types.SimpleNamespace(backend=types.SimpleNamespace(handle=None))) is None
    empty_handle = types.SimpleNamespace(base_url=None)
    assert surya_reader._health_of(
        types.SimpleNamespace(backend=types.SimpleNamespace(handle=empty_handle))) is None


# --- Частина 5: розпізнавання не виходить за межі машини --------------------
#
# Найдорожче обмеження проєкту (`docs/spec/security-constraints.md`): зображення
# документів не покидають машину. Гарантія тримається на тому, що внутрішній
# сервер surya слухає лише loopback (`s.bind(("127.0.0.1", 0))`), АЛЕ вона
# умовна: `SURYA_INFERENCE_URL` змушує surya під'єднатись до чужого сервера
# замість підняти свій. Виставити цю змінну може будь-хто на машині -- поза
# нашим конфігом, поза git, і пайплайн промовчав би. Тому тут перевірка кодом.
#
# Ці тести коштують мілісекунди й мусять бігати на кожному прогоні: ціна
# пропущеної відмови -- не «поле не витягли», а бойові документи на чужій
# машині.

@contextlib.contextmanager
def _inference_url(value):
    saved = os.environ.get(surya_reader.INFERENCE_URL_ENV)
    if value is None:
        os.environ.pop(surya_reader.INFERENCE_URL_ENV, None)
    else:
        os.environ[surya_reader.INFERENCE_URL_ENV] = value
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop(surya_reader.INFERENCE_URL_ENV, None)
        else:
            os.environ[surya_reader.INFERENCE_URL_ENV] = saved


def test_external_inference_url_refuses_to_start_ocr():
    """Виставлена ЗОВНІШНЯ URL -> відмова, а не попередження.

    Відмова саме тут, у `make_surya_reader`, ще ДО імпорту surya: тест не
    підставляє жодних фальшивок, тому якщо перевірка колись переїде нижче
    (після `from surya.inference import ...`), тест упаде з ImportError, а не
    пройде тихо.
    """
    import pytest

    for url in ("http://10.0.0.7:8080/v1",        # чужа машина в мережі
                "https://ocr.example.com/v1",     # чужий сервіс
                "192.168.1.5:8080",               # без схеми -- теж адреса
                "http://ocr-internal:8080",       # ім'я: DNS може вказати куди завгодно
                "http://[2001:db8::1]:8080"):     # IPv6
        with _inference_url(url):
            with pytest.raises(surya_reader.ExternalInferenceRefused):
                make_surya_reader()


def test_loopback_inference_url_is_allowed():
    """Loopback -- це та сама машина, працюємо. Інакше перевірка ламала б
    легітимний сценарій «сервер уже піднятий вручну на 127.0.0.1»."""
    for url in ("http://127.0.0.1:50564/v1", "http://localhost:8080",
                "http://127.5.5.5:1234", "http://[::1]:8080"):
        with _inference_url(url):
            with _fake_surya([["Відпускний квиток"]], healthy=True) as (read, _):
                assert len(read("LEAVE-001.png")) == 1


def test_unset_inference_url_is_allowed():
    """Не виставлена -- нормальний режим: surya піднімає свій сервер на
    loopback. Без цього тесту «відмовлятись завжди» теж пройшло б."""
    with _inference_url(None):
        with _fake_surya([["Відпускний квиток"]], healthy=True) as (read, _):
            assert len(read("LEAVE-001.png")) == 1
    # і сама чиста функція: порожні значення не є адресою
    for empty in (None, "", "   "):
        surya_reader.check_inference_url(empty)


def _run_all():
    failures = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  OK   {name}")
            except AssertionError as exc:
                failures.append((name, exc))
                print(f"  FAIL {name}: {exc}")
            except Exception as exc:
                failures.append((name, exc))
                print(f"  ERR  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{'ПРОВАЛЕНО: ' + str(len(failures)) if failures else 'усі тести пройшли'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
