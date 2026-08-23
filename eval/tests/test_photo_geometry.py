# -*- coding: utf-8 -*-
"""Тести на ПЕРЕТИН РАМОК на фото (known-weak-spots.md розд. 11).

Заміряно 23.08.2026 на `DEMO-02.jpg` демо-набору. На знімку телефоном OCR
віддає рамки сусідніх рядків із перетином на третину висоти рядка -- не на
два пікселі, під які був відкалібрований допуск `0.25 * h_med`. Наслідки
виміряні обидва:

  1. правильний кандидат-значення випадав із розгляду ВЗАГАЛІ (не "праворуч",
     не "над", не "під"), і найближчим зверху ставала дужкова примітка
     ПОПЕРЕДНЬОГО поля -- друкований текст бланка;
  2. а оскільки примітка -- друкований рядок, поле оголошувалось «доведено
     порожнім» (`confirmed_empty_slot:printed_form_text`) при тому, що
     значення стояло в документі рядком вище.

Друге гірше за перше: прогалину рев'юер піде перевіряти, а «доведено
порожньо» -- підтвердить, не дивлячись у документ.

Числа в тестах -- справжні рамки з `DEMO-02.jpg`, не вигадані.

Запуск (без LLM, без OCR):
    python -m pytest eval/tests/test_photo_geometry.py -q
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from pipeline.extraction.extract import (
    _geometric_candidate,
    find_block_before_label,
    group_blocks_into_lines,
    proves_empty,
)

# Рамки з DEMO-02.jpg (сторінка 0). Значення заходить на рамку свого лейбла:
# ПІБ 577-622 проти лейбла 608-656 (14 px), посада 681-723 проти лейбла
# 707-745 (16 px) -- при висоті рядка ~37 px і колишньому допуску 9 px.
_PHOTO_BLOCKS = [
    {"text": "Видано", "bbox": [128.2, 548.9, 217.0, 577.4], "page": 0},
    {"text": "сержант ГАВРИШ Адам Станіславович",
     "bbox": [125.6, 577.4, 548.2, 621.9], "page": 0},
    {"text": "(військове звання, прізвище ім'я по батькові)",
     "bbox": [125.6, 607.7, 619.3, 655.8], "page": 0},
    {"text": "старшина роти, військова частина Ж3085",
     "bbox": [121.8, 680.7, 577.4, 723.5], "page": 0},
    {"text": "(посада, місце роботи)",
     "bbox": [121.8, 707.5, 374.4, 744.9], "page": 0},
    {"text": "відрядженому до", "bbox": [118.0, 777.0, 312.2, 812.6], "page": 0},
]

_POSITION_LABEL = "посада, місце роботи"


def _blocks():
    return group_blocks_into_lines([dict(b) for b in _PHOTO_BLOCKS])


def test_value_overlapping_its_label_is_still_above_it():
    """САМ ДЕФЕКТ: значення, чия рамка заходить на рамку лейбла на третину
    рядка, мусить лишатись кандидатом «над лейблом»."""
    blocks = _blocks()
    label_i = next(i for i, b in enumerate(blocks)
                   if any(_POSITION_LABEL in ln for ln in b["lines"]))
    h_med = 37.4
    chosen = _geometric_candidate(blocks, label_i, h_med)
    assert chosen is not None, "правильний кандидат не має випадати з розгляду"
    assert any("старшина роти" in ln for ln in blocks[chosen]["lines"]),         blocks[chosen]["lines"]


def test_previous_field_note_does_not_win_as_candidate():
    """І не просто «щось знайшлось»: примітка ПОПЕРЕДНЬОГО поля
    ("(військове звання...)") не має ставати значенням посади."""
    blocks = _blocks()
    raw, reason = find_block_before_label(blocks, _POSITION_LABEL)
    assert reason == "matched", reason
    assert "старшина роти" in str(raw), raw
    assert "військове звання" not in str(raw), raw


def test_name_group_block_also_survives_the_overlap():
    """Той самий перетин ламав і групу ПІБ: на DEMO-02 усі чотири поля особи
    йшли не детермінованим шляхом, а моделлю з довірою 0.6."""
    blocks = _blocks()
    raw, reason = find_block_before_label(
        blocks, "військове звання, прізвище ім'я по батькові")
    assert reason == "matched", reason
    assert "ГАВРИШ" in str(raw), raw


def test_label_note_never_proves_a_slot_empty():
    """ДРУГА ПОЛОВИНА: навіть якщо промах повториться, дужкова примітка
    бланка не є доказом порожнечі -- вона доказ того, що взято не той блок."""
    assert proves_empty("printed_form_text",
                        "(військове звання, прізвище ім'я по батькові)") is False
    assert proves_empty("printed_form_text", "(посада, місце роботи)") is False
    # Кілька приміток підряд -- те саме.
    assert proves_empty("printed_form_text",
                        "(мета відрядження)\n(пункти призначень)") is False


def test_real_empty_slot_still_proves_emptiness():
    """Запобіжник не має з'їсти те, для чого доказ і існує: незаповнений
    скелет слота лишається доказом порожнечі."""
    assert proves_empty("printed_form_text", 'терміном на "__" діб') is True
    assert proves_empty("blank_value", "_____") is True
    # Підозрілі причини доказом не були й не стають.
    assert proves_empty("type_mismatch", "абв") is False
    assert proves_empty("printed_label_in_value", "(посада)") is False


def test_block_enclosing_the_label_is_neither_above_nor_below():
    """Межа правила: блок, що ОХОПЛЮЄ лейбл (межа вище й межа нижче),
    напрямку не має -- інакше мега-рамка «вирівнювалась» би з чим завгодно."""
    blocks = group_blocks_into_lines([
        {"text": "(посада, місце роботи)",
         "bbox": [120.0, 700.0, 370.0, 740.0], "page": 0},
        {"text": "шапка документа з кількох рядків",
         "bbox": [118.0, 600.0, 600.0, 900.0], "page": 0},
    ])
    label_i = next(i for i, b in enumerate(blocks)
                   if any(_POSITION_LABEL in ln for ln in b["lines"]))
    assert _geometric_candidate(blocks, label_i, 37.4) is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
