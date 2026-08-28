# -*- coding: utf-8 -*-
"""Перемикач мови: механіка й ПОВНОТА словника.

Завдання Ані 28.08: кнопка перекладу на англійську, «як це дешево
реалізувати». Обраний спосіб — словник по DOM, а не `t()` у 105 місцях
(розбір — у самому `static/lang-toggle.js`).

## Головний тест тут — не механіка, а повнота

Слабке місце вибраного способу одне: **напівперекладена сторінка**. Механіка
працює, а новий підпис, доданий пізніше, лишається українським — і ніхто цього
не помітить, доки не побачить на екрані.

Тому тест витягає українські рядки з РОЗМІТКИ Й СКРИПТІВ сторінок і зіставляє
зі словником. З'явився новий підпис без перекладу — тест червоний.

Те, що не є підписом для людини (діагностика в консоль, службові маркери,
фрагменти коду, які ловить регулярка), виноситься в `NOT_UI` — **із причиною на
кожен рядок**. Без цього переліку тест був би або нечесним (пропускав би все),
або незручним настільки, що його вимкнули б.

Запуск:
    python -m pytest demos/upload_app/tests/test_language_toggle.py -q
"""
import io
import os
import re

import pytest

STATIC = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "static")

PAGES = ["index.html", "stats.html"]
SCRIPTS = ["access.js", "theme-toggle.js", "mobile.js"]

#: Рядки, які людина на сторінці НЕ бачить. Кожен -- із причиною.
NOT_UI = {
    # Порожній стан списку в `kvList`: не показується на жодній живій сторінці
    # (усі виклики мають дані), але лишається як запобіжник.
    "порожньо",
    # Технічні мітки, які їдуть у консоль або в data-атрибути.
    "невідомо",
}


def _markup_texts(path):
    """Видимий текст РОЗМІТКИ (не літерали) -- заголовки, навігація, абзаци.

    Сліпе місце, яке я знайшла у власному тесті: перша версія перевіряла лише
    рядкові ЛІТЕРАЛИ скриптів, а `<h1>Статистика</h1>` і абзац під ним --
    це текст у розмітці, і жоден літерал його не містить. Тобто тест був
    зелений, поки половина сторінки могла лишатись неперекладеною.
    """
    if not path.endswith(".html"):
        return set()
    s = io.open(path, encoding="utf-8").read()
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<script.*?</script>", "", s, flags=re.S)
    s = re.sub(r"<style.*?</style>", "", s, flags=re.S)
    s = re.sub(r"<title>.*?</title>", "", s, flags=re.S)
    out = set()
    for chunk in re.split(r"<[^>]+>", s):
        # Розмітка ламає абзаци переносами -- склеюємо в один рядок так, як їх
        # побачить браузер, інакше ключі не збігатимуться з тим, що в DOM.
        t = " ".join(chunk.split())
        if len(t) > 1 and re.search(r"[А-ЯІЇЄҐа-яіїєґ]", t):
            out.add(t)
    return out


def _literals(path):
    """Українські рядкові літерали файла -- без коментарів."""
    s = io.open(path, encoding="utf-8").read()
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)          # блокові коментарі JS
    s = re.sub(r"(?m)^\s*//.*$", "", s)                  # рядкові коментарі JS
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)         # коментарі HTML
    out = set()
    for lit in re.findall(r'"([^"\n]*[А-ЯІЇЄҐа-яіїєґ][^"\n]*)"', s):
        t = lit.strip()
        # Довгі уривки з переносами -- артефакт регулярки на HTML, не підпис.
        if len(t) > 1 and "\\n" not in t and "<" not in t:
            out.add(t)
    return out


def _dict_keys():
    """Ключі словника з `lang-toggle.js` -- читаємо файл, не дублюємо список."""
    s = io.open(os.path.join(STATIC, "lang-toggle.js"), encoding="utf-8").read()
    body = s.split("var DICT = {", 1)[1].split("\n  };", 1)[0]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    keys = set()
    for m in re.finditer(r'^\s*"((?:[^"\\]|\\.)*)"\s*:', body, re.M):
        keys.add(m.group(1))
    return keys


def test_dictionary_is_not_empty():
    keys = _dict_keys()
    assert len(keys) > 80, len(keys)


@pytest.mark.parametrize("name", PAGES + SCRIPTS)
def test_every_visible_string_has_a_translation(name):
    """ГОЛОВНИЙ тест: напівперекладеної сторінки бути не може."""
    keys = _dict_keys()
    path = os.path.join(STATIC, name)
    found = _literals(path) | _markup_texts(path)
    missing = sorted(s for s in found if s not in keys and s not in NOT_UI)
    assert not missing, (
        f"{name}: {len(missing)} підписів без перекладу. Додайте їх у DICT "
        f"у static/lang-toggle.js або, якщо це не текст для людини, у NOT_UI "
        f"із причиною:\n" + "\n".join("  " + m for m in missing))


def test_translations_are_not_copies_of_the_original():
    """Порожній переклад («ключ»: «ключ») -- гірший за відсутність: тест
    зелений, а сторінка українська."""
    s = io.open(os.path.join(STATIC, "lang-toggle.js"), encoding="utf-8").read()
    body = s.split("var DICT = {", 1)[1].split("\n  };", 1)[0]
    pairs = re.findall(r'"((?:[^"\\]|\\.)*)"\s*:\s*\n?\s*"((?:[^"\\]|\\.)*)"',
                       body)
    same = [k for k, v in pairs if k == v]
    assert not same, same


def test_english_values_have_no_cyrillic():
    """Переклад із кирилицею -- недороблений переклад. Виняток один: підпис
    кнопки «УКР», який і мусить бути українською."""
    s = io.open(os.path.join(STATIC, "lang-toggle.js"), encoding="utf-8").read()
    body = s.split("var DICT = {", 1)[1].split("\n  };", 1)[0]
    pairs = re.findall(r'"((?:[^"\\]|\\.)*)"\s*:\s*\n?\s*"((?:[^"\\]|\\.)*)"',
                       body)
    bad = [(k, v) for k, v in pairs if re.search(r"[А-ЯІЇЄҐа-яіїєґ]", v)]
    assert not bad, bad


def test_button_and_script_are_wired_on_both_pages():
    """Кнопка без скрипта -- мертва кнопка; скрипт без кнопки -- невидима
    можливість. Обидві сторінки мусять мати обидва."""
    for name in PAGES:
        s = io.open(os.path.join(STATIC, name), encoding="utf-8").read()
        assert 'class="lang-toggle"' in s, name
        assert "/static/lang-toggle.js" in s, name
        # Без defer: інакше сторінка блимне українською й перемалюється.
        head = s.split("</head>", 1)[0]
        assert "lang-toggle.js" in head, name
        line = [ln for ln in head.splitlines() if "lang-toggle.js" in ln][0]
        assert "defer" not in line, line


def test_script_is_served():
    """Файл мусить бути в переліку статики, інакше кнопка отримає 404."""
    from demos.upload_app import app as web
    assert "lang-toggle.js" in web.STATIC_FILES


def test_no_external_requests():
    """Правило проєкту: зі сторінок не йде жодного ЗОВНІШНЬОГО запиту.

    Тест уточнений 28.08. Перша версія забороняла `XMLHttpRequest` узагалі --
    і впала, коли переклад став двошаровим: другий шар питає НАШ власний
    `/api/translate`. Заборона будь-якого запиту була надто широкою й ловила
    не те: правило проєкту про ЧУЖІ хости (жодного CDN, жодного перекладача
    назовні), а не про власний сервер.

    Тому тепер перевіряється саме це: абсолютних URL немає, а єдина адреса
    запиту -- наш маршрут.
    """
    s = io.open(os.path.join(STATIC, "lang-toggle.js"), encoding="utf-8").read()
    code = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    for bad in ("http://", "https://", "import(", "//cdn", "googleapis"):
        assert bad not in code, bad
    urls = re.findall(r'\.open\(\s*"[A-Z]+"\s*,\s*"([^"]+)"', code)
    assert urls == ["/api/translate"], urls


def test_chat_answers_are_not_translated():
    """РІШЕННЯ, а не недоробка: відповіді чата лишаються українськими.

    Вони складаються з дослівних цитат нормативних документів; перекладена
    норма -- це вже переказ, і перевірити її по документу неможливо. Тест
    стежить, щоб у словнику не з'явились рядки відповідей чата.
    """
    keys = _dict_keys()
    for forbidden in ("Доповідаю:", "Зріз:", "Чернетки (не в підрахунку):",
                      "Поіменно:"):
        assert forbidden not in keys, forbidden
