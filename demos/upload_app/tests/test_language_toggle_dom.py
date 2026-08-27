# -*- coding: utf-8 -*-
"""Перекладач мови прогоняється НА DOM, а не лише перевіряється словником.

Тест повноти (`test_language_toggle.py`) відповідає на питання «чи є переклад
для кожного підпису». На головне питання -- «чи він СПРАЦЬОВУЄ» -- він не
відповідає взагалі: словник може бути повний, а обхід DOM зламаний.

Тому тут справжній прогін `static/lang-toggle.js` у node, на мінімальній
заглушці DOM (`tests/js/lang_toggle_dom.js`). Перевіряються п'ять речей, і
кожна -- окрема причина, через яку переклад буває несправжнім:

  1. звичайний підпис перекладається;
  2. підпис із числом («35 з 35») теж -- через список шаблонів;
  3. атрибути (`title`, `aria-label`) теж, бо їх читає людина й читач екрана;
  4. **відповідь чата НЕ перекладається** -- це правило продукту: дослівна
     цитата норми не може стати переказом;
  5. повернення до української точне -- відновлюємо збережені оригінали, а не
     зворотний словник (два різні рядки могли б перекластись однаково).

Якщо node немає -- тест пропускається, а не бреше зеленим.
"""
import json
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SCRIPT = os.path.join(HERE, "js", "lang_toggle_dom.js")
TARGET = os.path.join(APP, "static", "lang-toggle.js")

node = shutil.which("node")


@pytest.mark.skipif(not node, reason="node не встановлено -- прогін у браузерному "
                                     "рантаймі неможливий")
def test_translator_actually_runs_on_a_dom():
    out = subprocess.run([node, SCRIPT, TARGET], capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("VERDICT ")]
    assert line, out.stdout[-2000:]
    v = json.loads(line[0][len("VERDICT "):])
    assert v["translated"], "звичайний підпис не переклався"
    assert v["pattern"], "підпис із числом не переклався (шаблони)"
    assert v["attr"], "атрибут title не переклався"
    assert v["chat_untouched"], (
        "відповідь чата переклалась -- це ламає правило про дослівну цитату")
    assert v["restored"], "повернення до української не відновило оригінал"
