"""Surya OCR як звичайний модуль (раніше жив у клітинці ноутбука).

Повертає блоки з геометрією: [{"text": ..., "bbox": (x1,y1,x2,y2)}].
Впорядкування -- відповідальність pipeline.ingestion.ingest, не цього
модуля: OCR лише "читає й віддає що бачить".

surya імпортується ліниво -- пайплайн має завантажуватись і на машині без
неї (docx-шлях OCR не потребує взагалі).
"""
import html
import os
import re
import sys

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>")


DEFAULT_INFERENCE_PARALLEL = "2"


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


def make_surya_reader(llama_server_path=None, inference_parallel=None):
    """Повертає callable(image_path) -> list[{"text","bbox"}].

    Модель вантажиться один раз на процес (замикання), не на кожен файл --
    інакше пакетна обробка папки перечитувала б ваги для кожного документа.

    llama_server_path: Surya всередині запускає llama.cpp-сервер. На Linux/
    Colab він збирається з джерел; на Windows простіше вказати шлях до вже
    готового бінарника, ніж тягнути тулчейн.
    """
    # Явне присвоєння, не setdefault: якщо змінна вже була в середовищі,
    # setdefault мовчки ігнорував конфіг, і конфіг перестав описувати те, що
    # реально працює. `is None`, а не `or`, щоб явний 0 не перетворювався на 2.
    if inference_parallel is None:
        inference_parallel = DEFAULT_INFERENCE_PARALLEL
    os.environ["SURYA_INFERENCE_PARALLEL"] = str(inference_parallel)
    if llama_server_path:
        os.environ["LLAMA_CPP_BINARY"] = llama_server_path

    from PIL import Image, ImageOps
    from surya.inference import SuryaInferenceManager
    from surya.recognition import RecognitionPredictor

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

    return read
