"""Видимий стан обробки питання в чаті (задача B1, критерій приймання Ш3:
жодної операції довшої за 3 с без видимого стану).

Чому це окремий файл, а не три рядки в app.py
---------------------------------------------
`answer()` -- одна блокуюча функція, яка внутрішньо перебирає яруси
(правила -> вектори -> модель-класифікатор -> вільний SELECT). Виклик моделі
на CPU -- 43-47 с. Щоб людина бачила НЕ просто «щось крутиться», а ЯКИМ
ярусом іде відповідь, потрібні дві незалежні речі:

  1. яруси мусять КАЗАТИ, де вони зараз (`progress.stage("vector")`) --
     дешевий однорядковий виклик у кожній дорозі, без зміни їхньої логіки
     й без нового параметра в кожній функції (їх десяток, і половина
     викликається з іншої половини);
  2. вікно мусить читати цей стан, поки `answer()` ще не повернувся --
     тобто `answer()` виконується в окремому потоці, а генератор-обробник
     Gradio тікає таймером і віддає кадри стану (`stream()` нижче).

Обидві речі тримаються тут, щоб їх можна було перевірити тестом БЕЗ Gradio,
без бази й без моделі (demos/upload_app/tests/test_progress.py).

Чому потік + таймер, а не `gr.Progress`
---------------------------------------
`gr.Progress` малює власну смужку Gradio і вміє показати відсоток, якого в
нас немає: скільки триватиме ярус, наперед невідомо (0.2 с правилами або
47 с моделлю). Нам потрібен НАЗВАНИЙ стан і секундомір, і він мусить
з'явитися на місці відповіді, у стрічці, поруч із питанням. Тому yield-
стріминг у самому обробнику -- засіб Gradio, нічого зовнішнього не тягнемо
(CSP і «жодного зовнішнього запиту» -- тверде правило апки).

Носій «поточного трекера» -- threading.local: `answer()` цілком виконується
в одному потоці, а паралельні питання (Gradio queue, до 4) не мусять бачити
станів одне одного.
"""
import html
import threading
import time

# Ключ ярусу -> що бачить людина. Ключі англійською (код), підписи
# українською (текст продукту). Порядок -- порядок доріг у app._extra_tiers.
STAGE_LABELS = {
    "parse": "розбираю питання",
    "rules": "шукаю готовий шаблон (правила)",
    "db": "запит до бази",
    "vector": "порівнюю з прикладами (вектори)",
    "model_route": "модель обирає маршрут",
    "model_catalog": "модель обирає шаблон із каталогу",
    "tier2": "модель складає SQL-запит",
}

# Короткий підпис для сліду пройдених ярусів (там довгі назви не влазять).
STAGE_SHORT = {
    "parse": "розбір",
    "rules": "правила",
    "db": "база",
    "vector": "вектори",
    "model_route": "модель: маршрут",
    "model_catalog": "модель: шаблон",
    "tier2": "модель: SQL",
}

# Яруси, які платяться викликом локальної моделі. Для них показуємо, ЧОМУ
# довго: інакше 47 с очікування виглядають як збій, навіть із секундомером.
MODEL_STAGES = ("model_route", "model_catalog", "tier2")

MODEL_HINT = ("виклик локальної моделі на процесорі — до хвилини; "
              "дані з бази, відповідь формує код")

# Через скільки секунд одного ярусу показувати пояснення «чому довго».
HINT_AFTER_S = 3.0

# Крок таймера кадрів стану. 0.3 с: секундомір рухається без ривків, і це
# ~3 кадри на секунду -- для Gradio-черги дешево.
TICK_S = 0.3


class Tracker:
    """Хто де зараз і скільки це вже триває. Потокобезпечний: пише його
    потік-виконавець, читає потік-генератор."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self.started = clock()
        self._stage = None
        self._stage_started = self.started
        self.trail = []          # [(ключ, секунда від старту)] -- у порядку

    def stage(self, key):
        """Зайшли в ярус. Повторний виклик того самого ключа нічого не
        робить: ярус може смикнутись двічі (наприклад каталог правилами до і
        після маршрутизації), а секундомір ярусу від цього перезапускатись
        не мусить."""
        with self._lock:
            if self._stage == key:
                return
            now = self._clock()
            self._stage = key
            self._stage_started = now
            self.trail.append((key, now - self.started))

    def snapshot(self):
        with self._lock:
            now = self._clock()
            return {
                "stage": self._stage,
                "elapsed": now - self.started,
                "stage_elapsed": now - self._stage_started,
                "trail": [key for key, _ in self.trail],
            }


_local = threading.local()


def set_current(tracker):
    _local.tracker = tracker


def clear_current():
    _local.tracker = None


def current():
    return getattr(_local, "tracker", None)


def stage(key):
    """Виклик із будь-якої дороги. Без активного трекера -- нічого не
    робить: `answer()` мусить однаково працювати з тестів і з CLI."""
    tracker = current()
    if tracker is not None:
        tracker.stage(key)


def fmt_seconds(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} с"
    return f"{seconds // 60} хв {seconds % 60:02d} с"


def render(snap):
    """Кадр стану -- той самий HTML-словник класів, що й решта вікна
    (theme.css, .working). Текст екранується: у підписи ярусів людський
    ввід не потрапляє, але правило «нічого в innerHTML без escape» тримаємо
    однаково -- один виняток згодом стає дірою."""
    key = snap.get("stage")
    label = STAGE_LABELS.get(key, "обробляю запит")
    parts = [
        '<div class="working" role="status" aria-live="polite">',
        '<div class="typing"><i></i><i></i><i></i></div>',
        '<div class="working-body">',
        f'<div class="working-now"><b>{html.escape(label)}</b>'
        f'<span class="working-time">{fmt_seconds(snap.get("elapsed", 0))}</span>'
        '</div>',
    ]
    trail = [STAGE_SHORT.get(k, k) for k in snap.get("trail", [])[:-1]]
    if trail:
        parts.append('<div class="working-trail">пройдено: '
                     + html.escape(" → ".join(trail)) + '</div>')
    if key in MODEL_STAGES and snap.get("stage_elapsed", 0) >= HINT_AFTER_S:
        parts.append(f'<div class="working-hint">{html.escape(MODEL_HINT)}</div>')
    parts.append('</div></div>')
    return "".join(parts)


def stream(work, tick=None, clock=time.monotonic):
    """Виконує `work()` в окремому потоці й ВИДАЄ кадри стану, поки він
    триває. Останній елемент -- результат:

        ("stage", html)   -- проміжний кадр (їх стільки, скільки тіків;
                             перший -- одразу, без затримки)
        ("result", value) -- work() повернув
        ("error", exc)    -- work() кинув (виняток НЕ ховається: обробник
                             вище малює його як стан помилки)

    Потік daemon: якщо процес закривається посеред 47-секундного виклику
    моделі, він не тримає вихід.
    """
    # tick читається ТУТ, а не в дефолті сигнатури: дефолт прив'язався б до
    # значення на момент import і не піддавався б підміні в тесті.
    tick = TICK_S if tick is None else tick
    tracker = Tracker(clock=clock)
    box = {}

    def run():
        set_current(tracker)
        try:
            box["result"] = work()
        except BaseException as exc:          # noqa: BLE001 -- віддаємо вище
            box["error"] = exc
        finally:
            clear_current()

    worker = threading.Thread(target=run, daemon=True)
    tracker.stage("parse")
    worker.start()
    # Перший кадр -- НЕ чекаючи тіку: порожнє місце на секунду вже читається
    # як «зависло».
    yield ("stage", render(tracker.snapshot()))
    while True:
        worker.join(tick)
        if not worker.is_alive():
            break
        yield ("stage", render(tracker.snapshot()))
    if "error" in box:
        yield ("error", box["error"])
    else:
        yield ("result", box.get("result"))
