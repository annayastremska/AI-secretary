"""Спільна підготовка тестів апки.

Ці тести НЕ входять у прилад пайплайна (eval/tests, 296 тестів) -- вони
живуть окремо і ганяються окремо: `python -m pytest demos/upload_app/tests`.

Дві речі, які мусять статися ДО імпорту модулів апки:
  1. CHAT_MODEL_PATH -> свідомо неіснуючий шлях: тести не мають вантажити
     ваги 4B (задача каже: модель локально не запускати; без ваг чат чесно
     живе на правилах -- саме цей режим і тестуємо);
  2. корінь репозиторію в sys.path, щоб `import demos.upload_app...`
     працював незалежно від того, звідки запущено pytest.
"""
import os
import sys

# до будь-якого імпорту tiers/app: MODEL_PATH читається при імпорті модуля
os.environ["CHAT_MODEL_PATH"] = os.path.join("nonexistent", "no-model.gguf")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
