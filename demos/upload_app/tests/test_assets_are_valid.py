# -*- coding: utf-8 -*-
"""Знак продукту мусить бути РОЗБІРНИМ XML.

Знайдено двома втраченими іконками 27.08. У коментарі SVG-файла стояли два
дефіси підряд («-- окремий документ, який…»), бо в наших коментарях подвійний
дефіс це звичайне тире. Але SVG це XML, а в XML послідовність із двох дефісів
усередині коментаря заборонена: браузер не розбирає документ і показує порожнє
місце замість картинки.

Найгірше в цій поломці те, як вона виглядала. Файл віддавався з кодом 200 і
правильним типом, у мережі жодної помилки, а той САМИЙ знак у чаті малювався
нормально, бо туди він іде інлайном і його розбирає парсер HTML, який подвійний
дефіс терпить. Тобто симптом був «в одному місці є, в інших немає», і причина
шукалась у маршрутах статики, у кеші й у кольорах, а була в коментарі.

Тест дешевий і стоїть у звичайному прогоні саме тому, що око тут не допомагає:
поламаний файл виглядає як правильний.
"""
import glob
import os
import xml.dom.minidom

import pytest

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "chat_gradio", "assets")
SVGS = sorted(glob.glob(os.path.join(ASSETS, "*.svg")))


def test_there_is_at_least_one_mark():
    assert SVGS, "у chat_gradio/assets немає жодного svg -- знак загубився"


@pytest.mark.parametrize("path", SVGS, ids=[os.path.basename(p) for p in SVGS])
def test_svg_parses_as_xml(path):
    """Розбирається як XML -> намалюється і через <img>, не лише інлайном."""
    xml.dom.minidom.parse(path)


@pytest.mark.parametrize("path", SVGS, ids=[os.path.basename(p) for p in SVGS])
def test_no_double_hyphen_in_comments(path):
    """Окремо й прямо: два дефіси підряд у коментарі. Помилка XML-парсера
    зрозуміла не з першого погляду, а це повідомлення -- з першого."""
    text = open(path, encoding="utf-8").read()
    for chunk in text.split("<!--")[1:]:
        body = chunk.split("-->")[0]
        assert "--" not in body, (
            "у коментарі " + os.path.basename(path) + " є два дефіси підряд. "
            "У XML це заборонено, і файл мовчки не намалюється через <img>. "
            "Заміни на довге тире або двокрапку.")


@pytest.mark.parametrize("path", SVGS, ids=[os.path.basename(p) for p in SVGS])
def test_mark_for_img_has_explicit_colour(path):
    """Файл, призначений для <img>, не має права покладатись на currentColor:
    в окремому документі він нічого не наслідує і стає чорним, а на темній
    брендовій смузі знак зникає. Саме через це шапка була порожня."""
    if "avatar" not in os.path.basename(path):
        return
    # Коментарі вирізаємо: у них currentColor згадується САМЕ тому, що там
    # написано, чому його тут немає. Перевіряти треба розмітку, не пояснення.
    text = open(path, encoding="utf-8").read()
    markup = "".join(chunk.split("-->")[-1]
                     for chunk in ("|" + text).split("<!--"))
    assert "currentColor" not in markup
    assert "fill=\"#" in markup
