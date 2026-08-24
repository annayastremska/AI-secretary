"""Surya OCR як звичайний модуль (раніше жив у клітинці ноутбука).

Повертає блоки з геометрією: [{"text": ..., "bbox": (x1,y1,x2,y2)}].
Впорядкування -- відповідальність pipeline.ingestion.ingest, не цього
модуля: OCR лише "читає й віддає що бачить".

surya імпортується ліниво -- пайплайн має завантажуватись і на машині без
неї (docx-шлях OCR не потребує взагалі).
"""
import html
import ipaddress
import os
import re
import sys
from urllib.parse import urlsplit

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>")


DEFAULT_INFERENCE_PARALLEL = "2"

# Host prompt cache внутрішнього llama-server (МіБ, env LLAMA_ARG_CACHE_RAM;
# дефолт самого сервера -- 8192, PR ggml-org/llama.cpp#16391). 0 = вимкнено,
# і це НАШ дефолт: сервер серіалізує KV-стан кожного унікального промпту, а в
# OCR кожен промпт містить УНІКАЛЬНЕ зображення, тож кеш не має жодного
# влучення й монотонно росте -- заміряно +170…195 МБ/фото (виглядало як витік,
# docs/known-weak-spots.md розд. 2.18, №3), з вимкненим кешем -- +0.3 МБ/фото
# без зміни часу і якості. Апстрім закрив це як «not planned»
# (ggml-org/llama.cpp#22629): для OCR-навантаження кеш марний за побудовою.
DEFAULT_CACHE_RAM_MB = "0"

# Guided decoding (JSON-схема) для layout-проходу (env SURYA_GUIDED_LAYOUT;
# дефолт surya true). На бекенді llama.cpp зламаний ЗАВЖДИ: `\d` у схемі не
# парситься в GBNF (400 failed to parse grammar), тому поблоковий запасний
# шлях -- єдиний вихід для кадру, чий повносторінковий прохід не вдався, --
# без цього не працює взагалі (давав «unresolved, 2 поля» замість тексту).
DEFAULT_GUIDED_LAYOUT = "0"

# СВІДОМО БЕЗ дефолтів пайплайна (None = не чіпати surya: стеля 12288,
# 3 повтори): стеля `SURYA_MAX_TOKENS_FULL_PAGE=3072` + `max_retries=1`
# давали 2.4-4.7× на патологічному кадрі (LEAVE-011: 1814 с -> 749 с), але
# ЗАМІРЯНЕ просідання якості: примусовий поблоковий шлях віддає інший
# порядок блоків, і детермінований витяг на ньому взяв ПІБ командира за
# суб'єкта документа з довірою 0.9 (leave/png 154/154 -> 150/154,
# known-weak-spots.md розд. 9 розд. 2). Тиха чужа ідентичність дорожча за 18 хвилин.
# Ручки лишаються в конфізі для експериментів -- вмикати лише з передзаміром
# якості на повному корпусі.

# Змінна середовища surya, якою МОЖНА відправити зображення документів на чужу
# машину: якщо вона виставлена, surya не піднімає локальний сервер, а
# під'єднується до вказаного URL
# (`surya/inference/backends/spawn.py`, «0. If user pinned an external URL»).
INFERENCE_URL_ENV = "SURYA_INFERENCE_URL"


class ExternalInferenceRefused(RuntimeError):
    """OCR відмовився працювати, бо розпізнавання пішло б за межі машини."""


def _is_loopback_host(host: str) -> bool:
    if not host:
        return False
    host = host.strip().strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Не IP і не localhost -- імені ми не розв'язуємо: DNS міг би вказати
        # куди завгодно, і «схоже на локальне» не є доказом локальності.
        return False


def check_inference_url(url) -> None:
    """Пускає далі лише порожнє значення або loopback. Інакше -- виняток.

    Найдорожче обмеження проєкту (`docs/spec/security-constraints.md`):
    зображення документів не виходять за межі машини. Гарантію дає те, що
    внутрішній сервер розпізнавання слухає лише loopback на випадковому порту
    (`s.bind(("127.0.0.1", 0))` у surya), АЛЕ ця гарантія умовна: виставлена
    `SURYA_INFERENCE_URL` її скасовує, і зробити це може будь-хто на машині,
    поза нашим конфігом і поза git. Тому тут саме ПЕРЕВІРКА, а не покладання
    на те, що ми її ніде не виставляємо.

    Відмова, а не попередження: попередження в пакетному прогоні на 16
    документів побачить хіба той, хто читає stderr, -- а ціна пропущеного
    попередження тут не «поле не витягли», а бойові документи на чужому сервері.

    Ім'я, яке не є ні IP, ні `localhost`, теж відмова: покладатись на DNS
    означало б, що безпека залежить від вмісту hosts-файлу.
    """
    if not url or not str(url).strip():
        return
    raw = str(url).strip()
    # Без схеми ("192.168.1.5:8080") urlsplit кладе все в path і hostname дає
    # None -- тоді ми б помилково побачили "порожній хост" і пропустили адресу.
    parsed = urlsplit(raw if "//" in raw else "//" + raw)
    if _is_loopback_host(parsed.hostname or ""):
        return
    raise ExternalInferenceRefused(
        f"{INFERENCE_URL_ENV}={raw} вказує за межі цієї машини -- розпізнавання "
        "відправляло б зображення документів на зовнішній сервер. OCR не "
        "запускається. Приберіть змінну середовища (або вкажіть 127.0.0.1). "
        "Див. docs/spec/security-constraints.md."
    )


def restart_needed(blocks, server_healthy) -> bool:
    """Чи перезапускати сервер розпізнавання перед наступною спробою.

    Винесено окремою чистою функцією саме щоб її можна було перевірити без
    surya й без моделі: сама умова -- це те, що колись давало ТИХУ втрату
    документів, і вона мусить мати регресійний тест.

    Умова -- кон'юнкція, а не «порожньо => перезапуск»: порожня сторінка на
    живому сервері буває законно (чистий аркуш), і перезапускати модель через
    неї означало б хвилини втрат на кожному пустому скані. І навпаки, мертвий
    сервер при НЕпорожньому результаті перезапускати нема сенсу: текст уже
    отримано, сервер підніметься сам на наступному документі.

    `server_healthy is None` -- «не змогли перевірити» (сервер ще не стартував
    або немає сентинелі). Це не «мертвий»: невідомість не є діагнозом, інакше
    перший документ пакета перезапускав би щойно піднятий сервер.
    """
    return not blocks and server_healthy is False


def _health_of(manager):
    """True/False/None -- живий / мертвий / неможливо перевірити.

    Питаємо САМ сервер через /health, а не «чи існує процес з таким pid»:
    Windows перевикористовує pid, тому живий сторонній процес із тим самим
    номером виглядав би як живий сервер. Виміряна проблема була саме тиха:
    процес llama-server зникав, а `handle` у бекенді лишався виставленим, тому
    surya більше НІКОЛИ не пробувала його підняти -- і всі наступні документи
    отримували порожній результат.
    """
    handle = getattr(getattr(manager, "backend", None), "handle", None)
    if handle is None:
        return None
    base = getattr(handle, "base_url", None)
    if not base:
        return None
    root = base[: -len("/v1")] if base.endswith("/v1") else base
    try:
        from surya.inference.backends.spawn import probe_health

        return bool(probe_health(root))
    except Exception:
        return None


def make_surya_reader(llama_server_path=None, inference_parallel=None,
                      n_gpu_layers=None, hub_offline=False,
                      cache_ram_mb=None, max_tokens_full_page=None,
                      guided_layout=None, recognition_max_retries=None,
                      inference_backend=None):
    """Повертає callable(image_path) -> list[{"text","bbox"}].

    Модель вантажиться один раз на процес (замикання), не на кожен файл --
    інакше пакетна обробка папки перечитувала б ваги для кожного документа.

    llama_server_path: Surya всередині запускає llama.cpp-сервер. На Linux/
    Colab він збирається з джерел; на Windows простіше вказати шлях до вже
    готового бінарника, ніж тягнути тулчейн.

    n_gpu_layers: скільки шарів моделі розпізнавання вивантажити на GPU
    (`LLAMA_CPP_NGL` для того самого внутрішнього сервера). `None` -- не
    торкатися середовища взагалі, тобто лишити дефолт surya (99 = ВСІ шари на
    GPU). Замірено 14.08.2026: на цій машині це не «harmless no-op on pure-CPU
    builds», як пише коментар у surya/settings.py, -- бінарник winget-збірки
    має `ggml-vulkan.dll`, тому 99 означає інференс на вбудованій графіці, а в
    логах смерті сервера стоїть `vk::ErrorDeviceLost`. `0` дає чистий CPU.
    Див. docs/research/2026-08-14_ocr-ngl0-control-run.md.

    cache_ram_mb: ліміт host prompt cache внутрішнього сервера в МіБ
    (`LLAMA_ARG_CACHE_RAM`). `None` -> наш дефолт 0 (вимкнено) -- див.
    DEFAULT_CACHE_RAM_MB: для OCR кеш не дає влучень і лише з'їдає RAM.

    hub_offline: виставити `HF_HUB_OFFLINE=1`, тобто заборонити surya звертатись
    до HuggingFace за власними файлами моделі («You are sending unauthenticated
    requests to the HF Hub» у логах прогонів). Документів у тих запитах немає,
    але мережевий виклик є, і з цим прапорцем «нічого не йде в мережу» стає
    перевіряним. Вимкнено за замовчуванням: на машині без прогрітого кешу
    моделі offline зламає ПЕРШИЙ запуск, а це гірше за зайвий запит по ваги.
    """
    # Перевірка ПЕРЕД будь-яким імпортом surya: якщо розпізнавання пішло б за
    # межі машини, ми не маємо навіть піднімати менеджер.
    check_inference_url(os.environ.get(INFERENCE_URL_ENV))
    if hub_offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
    # Явне присвоєння, не setdefault: якщо змінна вже була в середовищі,
    # setdefault мовчки ігнорував конфіг, і конфіг перестав описувати те, що
    # реально працює. `is None`, а не `or`, щоб явний 0 не перетворювався на 2.
    if inference_parallel is None:
        inference_parallel = DEFAULT_INFERENCE_PARALLEL
    os.environ["SURYA_INFERENCE_PARALLEL"] = str(inference_parallel)
    # Той самий патерн, що для SURYA_INFERENCE_PARALLEL: явне присвоєння
    # (конфіг мусить описувати те, що реально працює), `is None` -- щоб явно
    # заданий 0 не плутався з «не задано». Чому дефолт 0 -- коментар до
    # DEFAULT_CACHE_RAM_MB.
    if cache_ram_mb is None:
        cache_ram_mb = DEFAULT_CACHE_RAM_MB
    os.environ["LLAMA_ARG_CACHE_RAM"] = str(cache_ram_mb)
    # Стеля токенів повносторінкового проходу: лише ЯКЩО задана конфігом --
    # дефолт surya не чіпаємо (чому: коментар «СВІДОМО БЕЗ дефолтів» вище).
    if max_tokens_full_page is not None:
        os.environ["SURYA_MAX_TOKENS_FULL_PAGE"] = str(max_tokens_full_page)
    if guided_layout is None:
        guided_layout = DEFAULT_GUIDED_LAYOUT
    # bool з YAML -> "0"/"1", бо pydantic-settings surya парсить рядок.
    if isinstance(guided_layout, bool):
        guided_layout = int(guided_layout)
    os.environ["SURYA_GUIDED_LAYOUT"] = str(guided_layout)
    # БЕКЕНД РОЗПІЗНАВАННЯ -- ОГОЛОШУЄТЬСЯ, А НЕ ВГАДУЄТЬСЯ (24.08.2026).
    #
    # Surya обирає його сама: `_autodetect_backend()` викликає `nvidia-smi -L`
    # і при будь-якій знайденій карті бере "vllm" (docker), інакше "llamacpp".
    # Заміряний випадок, коли це неправильно: на сервері vGPU втратив ліцензію,
    # карта в `nvidia-smi` ЛИШИЛАСЬ, а CUDA-обчислення зникли. Автовизначення
    # обрало vllm, той не піднявся, і кожне фото витрачало 600 с таймауту й
    # виходило `unresolved` -- при тому що шлях llamacpp на CPU дає той самий
    # документ за 47 с (перевірено на DEMO-01.jpg).
    #
    # "наявність карти" і "карта працює" -- різні твердження, і вгадувати
    # друге за першим не можна. Тому бекенд тепер можна закріпити конфігом:
    # `ocr.inference_backend: llamacpp | vllm`. None = попередня поведінка
    # (автовизначення surya), тобто на робочих машинах нічого не змінюється.
    if inference_backend:
        os.environ["SURYA_INFERENCE_BACKEND"] = str(inference_backend)
    if llama_server_path:
        os.environ["LLAMA_CPP_BINARY"] = llama_server_path
    # `is not None`, а не `if n_gpu_layers`: 0 -- це осмислене значення
    # (контрольний прогін на чистому CPU), і воно НЕ повинно бути
    # неотличимим від «не задано».
    if n_gpu_layers is not None:
        os.environ["LLAMA_CPP_NGL"] = str(n_gpu_layers)

    from PIL import Image, ImageOps
    from surya.inference import SuryaInferenceManager
    from surya.recognition import RecognitionPredictor

    # Ліміт повторів розпізнавання: env-ручки в surya НЕМАЄ (літерал 3 у
    # сигнатурі chat_completions_batch), тому partial поверх імені в модулі
    # бекенда -- llamacpp.py викликає функцію через ВЛАСНИЙ неймспейс
    # (`from ... import chat_completions_batch` на рівні модуля), тож заміна
    # атрибута модуля діє на всі виклики. Оригінал зберігається окремим
    # атрибутом, щоб повторний make_surya_reader у тому самому процесі не
    # завертав partial у partial.
    _llamacpp_backend = None
    if recognition_max_retries is not None:
        # Ліміт застосовується лише ЯКЩО заданий конфігом (дефолт -- не
        # чіпати surya): чому -- коментар «СВІДОМО БЕЗ дефолтів» вище.
        try:
            import surya.inference.backends.llamacpp as _llamacpp_backend
        except ImportError:
            # Інша структура surya (оновлення, підставна surya в тестах) --
            # ліміт не застосовується, але НЕ мовчки.
            print("[OCR] surya.inference.backends.llamacpp не імпортується -- "
                  "ліміт повторів розпізнавання НЕ застосовано (дефолт surya: 3)",
                  file=sys.stderr)
    if _llamacpp_backend is not None:
        import functools
        _orig_batch = getattr(_llamacpp_backend,
                              "_unpatched_chat_completions_batch",
                              None) or _llamacpp_backend.chat_completions_batch
        _llamacpp_backend._unpatched_chat_completions_batch = _orig_batch
        _llamacpp_backend.chat_completions_batch = functools.partial(
            _orig_batch, max_retries=int(recognition_max_retries))

    manager = SuryaInferenceManager()
    predictor = RecognitionPredictor(manager)

    def _blocks_from(prediction, page):
        blocks = []
        for block in prediction.blocks:
            plain = _BR_RE.sub("\n", block.html)
            plain = _TAG_RE.sub("", plain)
            plain = html.unescape(plain).strip()
            if plain:
                # getattr, а не block.bbox: якщо інша версія Surya перестане
                # віддавати геометрію, краще явна помилка в
                # sort_blocks_by_geometry, ніж AttributeError тут або тиха
                # робота з переплутаним порядком блоків.
                # `page` -- КАДР (для багатосторінкового TIFF), не глобальний
                # індекс: bbox кожного кадру рахується в ЙОГО ВЛАСНІЙ системі
                # координат (з нуля), тому геометричне порівняння bbox з
                # ІНШОГО кадру безглузде -- виміряний реальний баг (той самий
                # клас, що для сторінок PDF): без мітки сторінки блок з кадру
                # 2 міг "геометрично вирівнятись" із лейблом на кадрі 1 лише
                # тому, що обидва кадри рахують y з нуля.
                blocks.append({"text": plain, "bbox": getattr(block, "bbox", None), "page": page})
        return blocks

    def _read_once(image_path: str):
        """Читає ВСІ кадри багатосторінкового файлу (TIFF), не лише перший:
        Image.open() відкриває тільки перший кадр, тому решта сторінок раніше
        мовчки ігнорувалась. Кадри обробляються окремо й склеюються за
        порядком -- як сторінки PDF."""
        source = Image.open(image_path)
        frames = getattr(source, "n_frames", 1)
        blocks = []
        for index in range(frames):
            if frames > 1:
                source.seek(index)
            image = ImageOps.exif_transpose(source).convert("RGB")
            blocks.extend(_blocks_from(predictor([image])[0], index))
        return blocks

    def read(image_path: str):
        """Одна спроба, і -- якщо сервер розпізнавання помер -- ще одна після
        його перезапуску.

        Навіщо це тут, а не в surya: коли внутрішній llama-server падає, surya
        ловить помилку з'єднання й віддає ПОРОЖНІЙ результат як звичайну «чисту
        сторінку». Бекенд при цьому лишає `handle` виставленим, тому сам він
        уже не піднімається: у пакетному прогоні гине не один документ, а ВСІ
        наступні.

        ЩО САМЕ ЗАМІРЯНО (`data/eval/reports/w3-diag-batch16.log`, 14.08.2026):
        документи 1-4 -- сервер живий (health=True, rss 2.32->2.58 ГБ), 10-17
        блоків, ~1000 символів кожен; на 5-му сервер стає health=False, і
        документи 5-16 отримують РІВНО 0 блоків, 0 символів, без жодного
        винятку. Вільна RAM перед смертю: 0.44-0.95 ГБ, після -- стрибок до
        3.6 ГБ (звільнились ~2.6 ГБ підпроцесу). Точка зламу плаває: у
        попередньому прогоні це був 10-й документ, тут 5-й.

        ПРИЧИНА СМЕРТІ САМОГО СЕРВЕРА НЕ ВСТАНОВЛЕНА. Тиск на пам'ять --
        найімовірніший кандидат (цифри вище), але це кореляція: у збережених
        логах немає ні повідомлення вбивці, ні коду виходу llama-server. Не
        приписувати цьому ні OOM, ні збій графіки, поки не буде рядка з логу.

        Виправлення від причини й не залежить -- воно реагує на ФАКТ мертвого
        сервера, а не на те, чому він помер. Це навмисно: причина може бути
        іншою на іншій машині.

        Перезапуск тільки за фактом мертвого сервера, а не «на кожні N
        документів»: періодичний перезапуск платить хвилинами за завантаження
        ваг там, де сервер здоровий, і все одно не рятує документ, на якому
        стався збій.
        """
        blocks = _read_once(image_path)
        if not restart_needed(blocks, _health_of(manager)):
            return blocks
        print("[OCR] сервер розпізнавання не відповідає -- перезапуск і "
              f"друга спроба: {image_path}", file=sys.stderr)
        try:
            manager.stop()
            manager.start()
        except Exception as exc:
            # Не приховуємо: далі повернеться порожній результат, і запис уже
            # чесно скаже «0 блоків, 0 символів -- збій розпізнавання».
            print(f"[OCR] перезапуск не вдався ({type(exc).__name__}: {exc})",
                  file=sys.stderr)
            return blocks
        return _read_once(image_path)

    # Діагностиці потрібен доступ до менеджера й до перевірки здоров'я, щоб
    # писати `/health` та RSS сервера ПО КОЖНОМУ документу. Атрибути на
    # функції, а не зміна сигнатури: пайплайн (`pipeline/run.py`) далі бачить
    # звичайний `callable(image_path)`, а скрипт замірів не мусить дублювати
    # закриття над `manager` -- інакше він міряв би ІНШИЙ сервер, ніж той, на
    # якому працює прогін.
    read.manager = manager
    read.health = lambda: _health_of(manager)

    return read
