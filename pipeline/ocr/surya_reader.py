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


def make_surya_reader(llama_server_path=None, inference_parallel="2"):
    """Повертає callable(image_path) -> list[{"text","bbox"}].

    Модель вантажиться один раз на процес (замикання), не на кожен файл --
    інакше пакетна обробка папки перечитувала б ваги для кожного документа.

    llama_server_path: Surya всередині запускає llama.cpp-сервер. На Linux/
    Colab він збирається з джерел; на Windows простіше вказати шлях до вже
    готового бінарника, ніж тягнути тулчейн.
    """
    os.environ.setdefault("SURYA_INFERENCE_PARALLEL", inference_parallel)
    if llama_server_path:
        os.environ["LLAMA_CPP_BINARY"] = llama_server_path

    from PIL import Image, ImageOps
    from surya.inference import SuryaInferenceManager
    from surya.recognition import RecognitionPredictor

    manager = SuryaInferenceManager()
    predictor = RecognitionPredictor(manager)

    def read(image_path: str):
        image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
        predictions = predictor([image])
        blocks = []
        for block in predictions[0].blocks:
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

    return read
