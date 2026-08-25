"""Видимий стан обробки питання в чаті (задача B1, критерій приймання Ш3:
жодної операції довшої за 3 с без видимого стану).

Що саме тут доводиться -- і чому саме це:

  1. `stream()` віддає кадр стану ОДРАЗУ, ще до першого тіку. Порожнє місце
     на першу секунду -- це і є «зависло», яке ми лікуємо;
  2. поки робота триває, кадрів БІЛЬШЕ ОДНОГО (секундомір рухається) --
     інакше це не індикатор, а картинка;
  3. кадр НАЗИВАЄ ярус: «модель складає SQL-запит», а не абстрактне
     «обробляю». Це і є наша розповідь про продукт;
  4. виняток у роботі не ховається під індикатором -- приходить нагору як
     ("error", exc);
  5. `progress.stage()` без активного трекера -- безпечний no-op: `answer()`
     мусить однаково працювати з тестів, з CLI і з вікна;
  6. реальний `answer()` (без моделі, без бази) справді проходить через
     названі яруси -- тобто позначки стоять на дорогах, а не поруч із ними.

Модель і база тут не потрібні: CHAT_MODEL_PATH у conftest вказує на
неіснуючий шлях, і чат живе на правилах.
"""
import threading
import time

import pytest

# Беремо РІВНО той модуль, який тримає в собі чат: chat_gradio кладе свою
# теку в sys.path і імпортує сусідів коротким іменем ("import progress"),
# тому `demos.upload_app.chat_gradio.progress` -- це ІНШИЙ об'єкт модуля з
# власним threading.local. Тест на чужому екземплярі нічого б не доводив.
import demos.upload_app.chat_gradio.app as chat_app

progress = chat_app.progress


def _drain(gen):
    """Усі кадри стану + останній елемент (результат або помилка)."""
    frames, final = [], None
    for kind, payload in gen:
        if kind == "stage":
            frames.append(payload)
        else:
            final = (kind, payload)
    return frames, final


# ── 1-3. Кадри стану ────────────────────────────────────────────────────────


def test_first_frame_comes_before_any_tick():
    """Перший кадр -- не через тік, а негайно: інакше на початку кожного
    питання є вікно порожнечі."""
    gen = progress.stream(lambda: "готово", tick=5.0)
    kind, payload = next(gen)
    assert kind == "stage"
    assert "working" in payload
    gen.close()


def test_frames_keep_coming_while_work_runs():
    """Довга робота -> багато кадрів. Тік маленький, робота ~0.5 с."""
    frames, final = _drain(progress.stream(lambda: (time.sleep(0.5), "ок")[1],
                                           tick=0.05))
    assert final == ("result", "ок")
    assert len(frames) >= 3, f"кадрів лише {len(frames)} -- секундомір стоїть"


def test_frame_names_the_tier_and_shows_seconds():
    """Кадр мусить казати, ЯКИМ ярусом іде відповідь, і скільки це триває."""
    seen = []

    def work():
        progress.stage("tier2")
        time.sleep(0.25)
        return "ок"

    for kind, payload in progress.stream(work, tick=0.05):
        if kind == "stage":
            seen.append(payload)
    html = seen[-1]
    assert progress.STAGE_LABELS["tier2"] in html, html
    assert " с" in html                      # секундомір на місці
    assert "пройдено" in html                # слід пройдених ярусів


def test_model_stage_explains_why_it_is_slow():
    """На модельному ярусі після HINT_AFTER_S показуємо, чому довго:
    «до хвилини» -- це очікування, а не збій."""
    snap = {"stage": "tier2", "elapsed": 44.0, "stage_elapsed": 40.0,
            "trail": ["parse", "rules", "tier2"]}
    assert progress.MODEL_HINT in progress.render(snap)
    # той самий ярус на початку -- ще без підказки (не лякати на 1-й секунді)
    early = dict(snap, stage_elapsed=0.5)
    assert progress.MODEL_HINT not in progress.render(early)
    # швидкий ярус довгої підказки не отримує ніколи
    fast = dict(snap, stage="rules", stage_elapsed=40.0)
    assert progress.MODEL_HINT not in progress.render(fast)


def test_render_escapes_and_labels_unknown_stage():
    snap = {"stage": None, "elapsed": 0, "stage_elapsed": 0, "trail": []}
    out = progress.render(snap)
    assert "обробляю запит" in out
    assert "<script" not in progress.render(
        {"stage": "<script>x</script>", "elapsed": 1,
         "stage_elapsed": 1, "trail": ["<script>"]})


def test_fmt_seconds_switches_to_minutes():
    assert progress.fmt_seconds(0) == "0 с"
    assert progress.fmt_seconds(47.4) == "47 с"
    assert progress.fmt_seconds(65) == "1 хв 05 с"


# ── 4-5. Межі: помилки й відсутній трекер ───────────────────────────────────


def test_error_is_not_swallowed():
    def boom():
        raise ValueError("тріснуло")

    frames, final = _drain(progress.stream(boom, tick=0.05))
    assert final[0] == "error"
    assert isinstance(final[1], ValueError)


def test_stage_without_tracker_is_noop():
    """Головна гарантія невтручання: позначки в answer() нічого не ламають,
    коли їх ніхто не слухає."""
    progress.clear_current()
    progress.stage("tier2")          # не мусить кинути
    assert progress.current() is None


def test_trackers_do_not_leak_between_threads():
    """Двоє питань одночасно (Gradio queue до 4) не бачать станів одне
    одного: носій -- threading.local."""
    seen = {}

    def work(name, key):
        progress.stage(key)
        time.sleep(0.15)
        seen[name] = progress.current().snapshot()["stage"]
        return name

    threads = [threading.Thread(
        target=lambda n=n, k=k: _drain(progress.stream(
            lambda: work(n, k), tick=0.05)))
        for n, k in (("a", "vector"), ("b", "tier2"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert seen == {"a": "vector", "b": "tier2"}


def test_repeated_stage_does_not_restart_the_clock():
    clock = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    tracker = progress.Tracker(clock=lambda: next(clock))
    tracker.stage("rules")           # 1.0
    tracker.stage("rules")           # без змін -- той самий ярус
    snap = tracker.snapshot()        # 2.0
    assert snap["trail"] == ["rules"]
    assert snap["stage_elapsed"] == pytest.approx(1.0)


# ── 6. Позначки стоять на РЕАЛЬНИХ дорогах answer() ─────────────────────────


def test_real_answer_reports_the_tiers_it_walked(monkeypatch):
    """Позначки мусять стояти на РЕАЛЬНИХ дорогах answer(), а не поруч.

    Живої бази локально немає, тому обидва входи в неї (db._query і
    tiers._connect) підміняються на порожній результат: питання все одно
    проходить через яруси -- саме їх ми й міряємо. Це доказ, що
    `progress.stage()` викликається зсередини answer(), а не тільки в
    тестах на сам модуль.
    """
    def empty_db(sql, *_a, **_kw):
        # порожня база: переліки -> [], лічильники -> нуль. Розділення
        # потрібне, бо db.py читає rows[0]["n"] у лічильниках і ключі рядка
        # у переліках -- одна відповідь на двох не годиться.
        if "COUNT(" in sql:
            return [{"n": 0, "docs": 0}]
        return []

    def no_connect(*_a, **_kw):
        raise RuntimeError("бази в тесті немає")

    monkeypatch.setattr(chat_app.db, "_query", empty_db)
    monkeypatch.setattr(chat_app.tier_chat, "_connect", no_connect)

    tracker = progress.Tracker()
    progress.set_current(tracker)
    try:
        out = chat_app.answer("Хто був у відпустці 5 травня?")
    finally:
        progress.clear_current()
    assert out                                    # відповідь (чесна відмова) є
    trail = tracker.snapshot()["trail"]
    assert "db" in trail, \
        f"звернення до бази не позначилось ({trail}) -- індикатор мовчав би"
    assert set(trail) <= set(progress.STAGE_LABELS), trail
    # кожен пройдений ярус має людський підпис -- інакше в кадрі буде код
    assert all(progress.STAGE_LABELS.get(k) for k in trail)


# ── 7. Те, що справді підключене до вікна ───────────────────────────────────


def _respond_fn():
    """Обробник питання РІВНО такий, як його зареєстровано в Blocks. Тест
    через нього, а не через копію логіки: інакше можна мати ідеальний
    progress.py і не підключити його до кнопки."""
    demo = chat_app.build_blocks()
    fns = demo.fns
    fns = list(fns.values()) if hasattr(fns, "values") else list(fns)
    for block_fn in fns:
        fn = getattr(block_fn, "fn", None)
        if getattr(fn, "__name__", "") == "respond":
            return fn
    raise AssertionError("у Blocks немає обробника respond")


def test_chat_handler_streams_named_state(monkeypatch):
    """Головна перевірка задачі B1: довге питання дає в стрічці КІЛЬКА
    кадрів, і в них названо ярус. Одна відповідь у кінці -- це те, що було."""
    def slow_answer(question, history=None):
        progress.stage("tier2")            # так робить реальний ярус 2
        time.sleep(0.4)
        return "Доповідаю: 0 осіб."

    monkeypatch.setattr(chat_app, "answer", slow_answer)
    monkeypatch.setattr(progress, "TICK_S", 0.05)

    frames = list(_respond_fn()("Скільки в середньому діб?", []))
    assert len(frames) >= 3, f"кадрів усього {len(frames)} -- стріму немає"

    def content(frame):
        value = frame[1].get("value") if isinstance(frame[1], dict) else None
        if value is None:
            value = getattr(frame[1], "value", []) or []
        return str(value[-1]["content"]) if value else ""

    middle = "".join(content(f) for f in frames[:-1])
    assert progress.STAGE_LABELS["tier2"] in middle, middle[:300]
    assert "Доповідаю" in content(frames[-1])       # остання -- відповідь


def test_chat_handler_shows_error_state(monkeypatch):
    """Виняток у дорозі -> у стрічці блок помилки, а не вічний індикатор."""
    def boom(question, history=None):
        raise RuntimeError("тріснуло")

    monkeypatch.setattr(chat_app, "answer", boom)
    frames = list(_respond_fn()("Хто відсутній?", []))
    last = frames[-1]
    value = last[1].get("value") if isinstance(last[1], dict) else last[1].value
    assert "note--error" in str(value[-1]["content"])
    assert "RuntimeError" in str(value[-1]["content"])
