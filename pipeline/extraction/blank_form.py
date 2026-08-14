# -*- coding: utf-8 -*-
"""Перевірка «кандидат — це друкований текст самого бланка, а не значення».

Закриває known-weak-spots.md 5.9 (порожній шаблон віддавав два друковані
рядки як значення) і 5.8 (незаповнений слот ДАТИ не вважався placeholder-ом).

## Чому шаблон, а не перелік фраз у схемі

Розглядалось два варіанти (docs/work-plan-2026-08-14.md, Агент 4):

1. **оголошувати друковані фрази в схемі** -- слабше, зате без нових
   залежностей;
2. **звіряти кандидата з текстом ПОРОЖНЬОГО бланка** -- сильніше й
   універсальніше, але вимагає, щоб шаблон лежав поруч зі схемою.

Обрано (2), і причина не в силі, а в тому, ХТО веде перелік. Перелік фраз у
схемі веде людина: щойно бланк змінить формулювання -- перелік мовчки
застаріє, і повернеться той самий клас помилки, тільки пізніше й тихіше.
Порожній бланк -- це артефакт, який у частині й так існує в одному
екземплярі на форму, і він оновлюється разом із формою. Перелік фраз стає
похідним від нього, а не окремою сутністю, яку треба синхронізувати.

Ціна вибору чесна: схема без `blank_template:` не отримує перевірки взагалі
(див. `printed_lines` -> порожній frozenset). Це навмисно -- новий бланк без
оголошеного шаблону НЕ має почати мовчки відхиляти значення.

## Межа перевірки

Відхиляється лише кандидат, який ЦІЛКОМ складається з рядків бланка. Якщо
друкований рядок приклеївся до справжнього значення -- значення лишається:
там є текст, вписаний людиною, і втратити його гірше, ніж віддати з
приклеєним лейблом. Цей клас ловлять інші перевірки
(`_value_lines_after_label_note`, `printed_label_in_value`).
"""
import os

from pipeline.classification.classify import normalize_ws

#: Ключ схеми: шлях до порожнього бланка ЦІЄЇ форми, від кореня репозиторію.
BLANK_TEMPLATE_KEY = "blank_template"

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Гліфи, що позначають той самий знак у різних джерелах. Той самий друкований
# рядок приходить із docx із фігурним ’, а з OCR -- із прямим '. Якби
# порівняння залежало від гліфа, перевірка працювала б на docx і мовчки НЕ
# працювала на фото -- тобто рівно там, де помилок більше.
_GLYPH_FOLD = str.maketrans({
    "’": "'", "‘": "'", "`": "'", "´": "'", "ʼ": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"',
    "–": "-", "—": "-", "‑": "-",
})

_cache = {}


def _fold(text: str) -> str:
    """Порівнювана форма рядка: єдині гліфи, згорнуті пробіли, нижній
    регістр."""
    return normalize_ws(str(text).translate(_GLYPH_FOLD)).strip().lower()


def _has_letter(text: str) -> bool:
    """Чи є в рядку хоч одна літера.

    Потрібно, бо в бланку окремим блоком стоїть номер сторінки "2", а "2" --
    цілком законне значення (TRIP-001: 'Термін відрядження "2" днів'). Рядки
    без літер до переліку друкованого НЕ беруться, інакше перевірка з'їдала б
    короткі числові значення.
    """
    return any(ch.isalpha() for ch in text)


def blank_template_path(schema: dict) -> str:
    """Абсолютний шлях до порожнього бланка, оголошеного схемою."""
    declared = schema.get(BLANK_TEMPLATE_KEY)
    if not declared:
        return ""
    return os.path.normpath(os.path.join(_PROJECT_ROOT, declared))


def printed_lines(schema: dict) -> frozenset:
    """Множина друкованих рядків бланка у порівнюваній формі.

    Порожній frozenset, якщо схема шаблону не оголосила -- перевірка тоді
    інертна (див. докстрінг модуля).
    """
    path = blank_template_path(schema)
    if not path:
        return frozenset()
    cached = _cache.get(path)
    if cached is not None:
        return cached
    lines = _read_lines(path)
    result = frozenset(f for f in (_fold(ln) for ln in lines) if f and _has_letter(f))
    _cache[path] = result
    return result


def _read_lines(path: str) -> list:
    """Рядки порожнього бланка. Читається ТИМ САМИМ інжестом, що й документи
    (`extract_docx_blocks`), а не окремим читачем: інакше шаблон і документ
    розбивались би на рядки по-різному, і перевірка мовчки не збігалася б
    саме на верстці з таблицями, заради якої вона й потрібна."""
    if not os.path.exists(path):
        return []
    from pipeline.ingestion.ingest import extract_docx_blocks
    # Порядок значень саме такий: (суцільний текст, список блоків).
    _text, blocks = extract_docx_blocks(path)
    out = []
    for block in blocks:
        text = block if isinstance(block, str) else (block or {}).get("text", "")
        out.extend(str(text).split("\n"))
    return out


def is_printed_form_text(candidate, printed) -> bool:
    """True, якщо КОЖЕН рядок кандидата -- друкований рядок бланка.

    "Кожен", а не "хоч один": змішаний кандидат (друкований рядок + вписане
    людиною значення) значенням лишається -- див. межу перевірки в докстрінгу
    модуля.
    """
    if not printed or candidate is None:
        return False
    folded = [f for f in (_fold(ln) for ln in str(candidate).split("\n")) if f]
    if not folded:
        return False
    return all(line in printed for line in folded)
