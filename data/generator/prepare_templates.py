#!/usr/bin/env python3
"""Готує бланки з мітками.

Бере офіційні бланки (Додаток 30 і Додаток 28) і замінює порожні лінії
на мітки виду {{PERSON_FULL}}. Верстка бланка не змінюється — міняється
тільки текст усередині окремих абзаців.

Запускається один раз. Результат — два файли *_мітки.docx у templates/.
Оригінальні бланки не чіпаються.

Номери абзаців — це порядкові номери в обході тіла документа (див. walk).
Вони прив'язані до конкретних файлів бланків. Якщо бланк переверстати —
номери треба перезняти.
"""

import copy
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.text.paragraph import Paragraph

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "templates"

# Мітки у відпускному квитку (Додаток 30): номер абзацу -> новий текст
LEAVE_MARKS = {
    6: "військова частина {{UNIT}}",
    9: "№ {{DOC_NUMBER}}    від {{ISSUE_DATE}}",
    10: "{{PERSON_FULL}}",
    12: "{{RELEASED}}",
    13: "{{LEAVE_TYPE}}",
    16: "{{LEAVE_PLACE}}",
    19: "{{DAYS_WORDS}}",
    22: "з “{{START_D}}” {{START_M}} 20{{START_Y}} р.  по “{{END_D}}” {{END_M}} 20{{END_Y}} р.",
    25: "{{PERSON_SHORT}}",
    28: "{{OBLIGED}} прибути до місця служби у ",
    32: "{{RETURN_UNIT}}",
    35: "“{{RET_D}}” {{RET_M}} 20{{RET_Y}} р.",
    41: "{{VPD}}",
    49: "{{SIGNER_RANK}}                                        {{SIGNER_NAME}}",
    63: "{{COMPANIONS}}",
    74: "{{SIGNER_RANK}}                                        {{SIGNER_NAME}}",
}
# Абзаци, яким треба примусово поставити вирівнювання по лівому краю
LEAVE_ALIGN_LEFT = (63,)
# Порожні абзаци-запас наприкінці лицьового боку. Прибираємо, щоб довгий текст
# у полях не виштовхував зворотний бік на третю сторінку.
LEAVE_DROP = ()

# Мітки в посвідченні про відрядження (Додаток 28)
TRIP_MARKS = {
    7: "військова частина {{UNIT}}",
    11: "№ {{DOC_NUMBER}}    від {{ISSUE_DATE}}",
    13: "{{PERSON_FULL}}",
    16: "{{POSITION}}",
    19: "{{SENT_TO}} до ",
    20: "{{DEST}}",
    23: "{{DEST_ORG}}",
    27: (
        "Термін відрядження “{{DAYS}}” днів   з “{{START_D}}” {{START_M}} 20{{START_Y}} р."
        " по “{{END_D}}” {{END_M}} 20{{END_Y}} р."
    ),
    28: "{{PURPOSE}}",
    34: "Підстава відрядження: {{ORDER_BASIS}}",
    40: "{{SIGNER_RANK}}                                        {{SIGNER_NAME}}",
    # зворотний бік, пункт 1: виїзд із частини і прибуття в пункт призначення
    # LEFT / ARRIVED — рід за колонкою gender: «Вибув» або «Вибула»
    59: "{{LEFT}} із {{HOME_CITY}}",
    60: "“{{DEP1_D}}” {{DEP1_M}} 20{{DEP1_Y}}",
    65: "{{ARRIVED}} до {{DEST_CITY}}",
    66: "“{{ARR1_D}}” {{ARR1_M}} 20{{ARR1_Y}}",
    # зворотний бік, пункт 2: виїзд назад, харчування, прибуття в частину
    70: "{{LEFT}} із {{DEST_CITY}}",
    71: "“{{DEP2_D}}” {{DEP2_M}} 20{{DEP2_Y}}",
    74: "з {{MEAL_FROM}} по {{MEAL_TO}} 20{{MEAL_Y}} р.",
    80: "{{ARRIVED}} до {{HOME_CITY}}",
    81: "“{{ARR2_D}}” {{ARR2_M}} 20{{ARR2_Y}}",
}
TRIP_ALIGN_LEFT = ()
# Лицьовий бік посвідчення має великий запас порожніх рядків. Довга посада або
# довга мета відрядження виштовхували зворотний бік на третю сторінку.
TRIP_DROP = (45, 46)


def walk(element, doc):
    """Повертає всі абзаци документа в порядку обходу — тіло і клітинки таблиць."""
    out = []
    for child in element:
        tag = child.tag.replace(W, "")
        if tag == "p":
            out.append(Paragraph(child, doc))
        elif tag == "tbl":
            for row in child.findall(W + "tr"):
                for cell in row.findall(W + "tc"):
                    out += walk(cell, doc)
    return out


def set_text(paragraph, text):
    """Кладе текст в абзац, зберігаючи його оформлення."""
    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ""
        return
    run = paragraph.add_run(text)
    pPr = paragraph._p.find(W + "pPr")
    if pPr is not None:
        rPr = pPr.find(W + "rPr")
        if rPr is not None:
            run._r.insert(0, copy.deepcopy(rPr))


def build(source, target, marks, align_left, drop):
    doc = Document(str(source))
    paragraphs = walk(doc.element.body, doc)
    for index, text in marks.items():
        if index >= len(paragraphs):
            sys.exit(f"У бланку {source.name} немає абзацу №{index}")
        set_text(paragraphs[index], text)
    for index in align_left:
        paragraphs[index].alignment = WD_ALIGN_PARAGRAPH.LEFT
    for index in drop:
        paragraph = paragraphs[index]
        if paragraph.text.strip():
            sys.exit(f"Абзац №{index} у {source.name} не порожній, прибирати не можна")
        paragraph._p.getparent().remove(paragraph._p)
    doc.save(str(target))
    print(f"  {target.name}  ({len(marks)} міток)")


def main():
    print("Готую бланки з мітками:")
    build(
        TEMPLATES / "відпускний_шаблон.docx",
        TEMPLATES / "відпускний_квиток_мітки.docx",
        LEAVE_MARKS,
        LEAVE_ALIGN_LEFT,
        LEAVE_DROP,
    )
    build(
        TEMPLATES / "посвідчення_відрядження.docx",
        TEMPLATES / "посвідчення_відрядження_мітки.docx",
        TRIP_MARKS,
        TRIP_ALIGN_LEFT,
        TRIP_DROP,
    )


if __name__ == "__main__":
    main()
