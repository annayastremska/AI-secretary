# -*- coding: utf-8 -*-
"""Сторінки апки мусять бути СИНТАКСИЧНО цілі — інакше вони просто порожні.

Заміряний випадок, 25.08: я спрощувала сторінку «Статистика» і своєю ж правкою
зняла закривну дужку функції `render()`. Наслідок: браузер отримував
`SyntaxError`, жодного рядка скрипта не виконувалось, і сторінка малювалась
**порожньою** — при повністю робочому `/api/stats`, який віддавав усі цифри.

Знайшла це не я і не тест, а Аня, відкривши сторінку. Тобто дірка була рівно
там, куди тести не дивились: HTML і JS у нас не перевірялись нічим.

Що перевіряємо (дешево, без браузера й без node):
  * баланс дужок у кожному `<script>` -- рівно ця помилка;
  * баланс дужок у CSS;
  * усі `id`, до яких скрипт звертається через `$("...")`, справді є в розмітці
    -- друга половина того самого класу поломок: прибрали блок, а звертання
    лишилось;
  * жодного зовнішнього запиту (правило «нічого з CDN», перевірка №7 README).

Запуск:
    python -m pytest demos/upload_app/tests/test_static_pages_valid.py -q
"""
import io
import os
import re

import pytest

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "static")
PAGES = sorted(f for f in os.listdir(STATIC) if f.endswith(".html"))
STYLES = sorted(f for f in os.listdir(STATIC) if f.endswith(".css"))


def _read(name, folder=STATIC):
    return io.open(os.path.join(folder, name), encoding="utf-8").read()


def _strip_strings_and_comments(code):
    """Прибрати рядкові літерали й комментарі -- інакше дужка в тексті
    («Причина: ") зіпсувала б підрахунок.

    ПОРЯДОК ВАЖЛИВИЙ, і саме на ньому сторож 27.08 обдурив сам себе. Спершу
    вирізались коментарі `//...`, і рядок з адресою (`"http://…"`) рвався
    надвоє: лапка лишалась незакритою, після чого «рядком» ставала вся решта
    скрипта. Сторож рахував дужки, яких немає, і не бачив тих, що є. Тепер
    рядкові літерали вирізаються ПЕРШИМИ: у справжньому JS коментар не може
    початися всередині рядка, а адреса всередині рядка -- може.
    """
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    for quote in ('"', "'", "`"):
        code = re.sub(quote + r"(?:\\.|[^" + quote + r"\\])*" + quote,
                      quote + quote, code, flags=re.S)
    code = re.sub(r"(?m)//.*$", "", code)
    return code


@pytest.mark.parametrize("page", PAGES)
def test_script_braces_balanced(page):
    """ГОЛОВНЕ: незакрита функція = порожня сторінка при робочому API."""
    html = _read(page)
    for script in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S):
        code = _strip_strings_and_comments(script)
        for opener, closer, what in (("{", "}", "фігурні"),
                                     ("(", ")", "круглі"),
                                     ("[", "]", "квадратні")):
            assert code.count(opener) == code.count(closer), (
                f"{page}: {what} дужки не збалансовані "
                f"({code.count(opener)} проти {code.count(closer)})")


@pytest.mark.parametrize("page", PAGES)
def test_every_referenced_id_exists(page):
    """Скрипт не має звертатись до елементів, яких у розмітці немає."""
    html = _read(page)
    ids = set(re.findall(r'id="([^"]+)"', html))
    wanted = set(re.findall(r'\$\("([^"]+)"\)', html))
    missing = wanted - ids
    assert not missing, f"{page}: скрипт кличе неіснуючі id: {sorted(missing)}"


@pytest.mark.parametrize("style", STYLES)
def test_css_braces_balanced(style):
    code = _strip_strings_and_comments(_read(style))
    assert code.count("{") == code.count("}"), style


@pytest.mark.parametrize("page", PAGES)
def test_no_external_requests(page):
    """Правило «жодного зовнішнього запиту»: документи не покидають сервер, і
    сторінка не тягне нічого з інтернету."""
    html = _read(page)
    for bad in ("http://", "https://", "fonts.googleapis", "cdn."):
        assert bad not in html, f"{page}: зовнішнє посилання «{bad}»"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
