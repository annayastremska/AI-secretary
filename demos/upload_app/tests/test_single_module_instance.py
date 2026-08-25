# -*- coding: utf-8 -*-
"""У процесі апки `tiers` мусить бути ОДИН, а не два.

Знайдено 25.08.2026, коли мій же тест проходив вхолосту. Той самий файл
`chat_gradio/tiers.py` може лежати в `sys.modules` двічі:

    tiers                                (chat_gradio кладе свою теку в
                                          sys.path і імпортує сусідів коротко)
    demos.upload_app.chat_gradio.tiers   (звичайний пакетний імпорт)

Це два РІЗНІ обʼєкти зі своїм станом. Наслідки, за зростанням шкоди:

  1. тест патчить одну копію, апка вживає іншу -- перевірка зелена й
     безглузда (саме це й сталося);
  2. `_MODEL`, `_MODEL_FAILED` і `_MODEL_LOCK` дублюються, а це руйнує
     гарантію 4.4 «один екземпляр моделі на процес + лок»: два екземпляри
     27B це 32 ГБ VRAM замість 16, і два локи, які один одного не бачать.

Друге поки НЕ відбувається: перевірено -- процес апки завантажує лише плоску
копію. Цей тест сторожить саме це, бо зламати легко однією зміною імпорту.

Запуск:
    python -m pytest demos/upload_app/tests/test_single_module_instance.py -q
"""
import subprocess
import sys

import pytest


_PROBE = """
import os, sys
os.environ["CHAT_MODEL_PATH"] = os.path.join("nonexistent", "no-model.gguf")
sys.path.insert(0, ".")
import demos.upload_app.app          # так її запускає uvicorn
dups = [k for k in sys.modules if k == "tiers" or k.endswith(".tiers")]
print(",".join(sorted(dups)))
"""


def test_app_process_loads_tiers_once():
    """Окремий процес: у пітоні цієї сесії тести самі могли завантажити другу
    копію, тому питаємо чистий інтерпретатор."""
    out = subprocess.run([sys.executable, "-c", _PROBE],
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr[-800:]
    loaded = [m for m in out.stdout.strip().split(",") if m]
    assert len(loaded) == 1, (
        "у процесі апки дві копії tiers -- ламається гарантія «одна модель на "
        f"процес + лок» (4.4): {loaded}")


def test_model_state_lives_in_one_place():
    """Друга половина: стан моделі не має бути в двох місцях одночасно."""
    from demos.upload_app.chat_gradio import app as chat_app
    assert hasattr(chat_app.tier_chat, "_MODEL_LOCK")
    assert chat_app.tier_chat.__name__ == "tiers", (
        "апка перейшла на пакетний імпорт -- тоді перевірте, що плоска копія "
        "більше нікуди не імпортується, інакше стан моделі роздвоїться")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
