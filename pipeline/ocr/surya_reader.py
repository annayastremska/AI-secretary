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

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>")


DEFAULT_INFERENCE_PARALLEL = "2"


def make_surya_reader(llama_server_path=None, inference_parallel=None):
    """Повертає callable(image_path) -> list[{"text","bbox"}].

    Модель вантажиться один раз на процес (замикання), не на кожен файл --
    інакше пакетна обробка папки перечитувала б ваги для кожного документа.

    llama_server_path: Surya всередині запускає llama.cpp-сервер. На Linux/
    Colab він збирається з джерел; на Windows простіше вказати шлях до вже
    готового бінарника, ніж тягнути тулчейн.
    """
    os.environ.setdefault("SURYA_INFERENCE_PARALLEL",
                          str(inference_parallel or DEFAULT_INFERENCE_PARALLEL))
    if llama_server_path:
        os.environ["LLAMA_CPP_BINARY"] = llama_server_path

    from PIL import Image, ImageOps
    from surya.inference import SuryaInferenceManager
    from surya.recognition import RecognitionPredictor

    manager = SuryaInferenceManager()
    predictor = RecognitionPredictor(manager)

    def _blocks_from(prediction):
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
                blocks.append({"text": plain, "bbox": getattr(block, "bbox", None)})
        return blocks

    def read(image_path: str):
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
            blocks.extend(_blocks_from(predictor([image])[0]))
        return blocks

    return read
