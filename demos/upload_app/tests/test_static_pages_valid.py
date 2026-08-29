# -*- coding: utf-8 -*-
"""Сторінки апки мусять бути СИНТАКСИЧНО цілі — інакше вони просто порожні.

Заміряний випадок, 25.08: я спрощувала сторінку «Статистика» і своєю ж правкою
зняла закривну дужку функції `render()`. Наслідок: браузер отримував
`SyntaxError`, жодного рядка скрипта не виконувалось, і сторінка малювалась
**порожньою** — при повністю робочому `/api/stats`, який віддавав усі цифри.

Знайшла це не я і не тест, а Аня, відкривши сторінку. Тобто дірка була рівно
там, куди тести не дивились: HTML і JS у нас не перевірялись нічим.

Що перевіряємо (дешево, без браузера):
  * баланс дужок у кожному inline-`<script>` -- рівно ця помилка;
  * РОЗБІР кожного окремого `static/*.js` рушієм JavaScript (`node --check`).
    Додано 27.08 за аудитом: сторож дивився лише на inline-скрипти двох
    сторінок і не бачив 22 КБ у файлах, а `theme-toggle.js` стоїть у <head>
    БЕЗ `defer` -- помилка там дасть рівно ту саму порожню сторінку;
  * що сторінка не посилається на скрипт, якого немає;
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
import shutil
import subprocess
import re

import pytest

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "static")
PAGES = sorted(f for f in os.listdir(STATIC) if f.endswith(".html"))
STYLES = sorted(f for f in os.listdir(STATIC) if f.endswith(".css"))
#: ОКРЕМІ файли скриптів. Додано 27.08 за аудитом: сторож дивився лише на
#: inline-скрипти двох сторінок і не бачив 22 КБ у `static/*.js`. А
#: `theme-toggle.js` стоїть у <head> БЕЗ `defer` -- помилка синтаксису там
#: дасть рівно ту порожню сторінку, від якої цей файл і народився.
SCRIPTS = sorted(f for f in os.listdir(STATIC) if f.endswith(".js"))


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
    # РЕГУЛЯРНІ ЛІТЕРАЛИ -- останніми, і це не дрібниця.
    #
    # Знайдено 29.08: цей сторож ПОСТІЙНО падав на сервері й ніколи локально.
    # Різниця була в тому, що локально є `node` (тоді йде справжній розбір, і
    # файл валідний), а на сервері його немає -- і працює цей підрахунок. У
    # `lang-toggle.js` стоїть регулярка `/SELECT|FROM|%\(|::/`, тобто ЕКРАНОВАНА
    # відкриваюча дужка всередині літерала. Підрахунок бачив її як код, і
    # сторож роками (на сервері) кричав про незбалансовані дужки у файлі, який
    # розбирається без помилок.
    #
    # Це гірше за відсутність сторожа: прилад, який завжди червоний, привчає
    # не дивитись на нього. Тому літерал вирізається -- консервативно, лише
    # там, де регулярка в JS може стояти за синтаксисом (після `(`, `,`, `=`,
    # `!`, `&`, `|`, `:`, `;`, `return`), щоб не сплутати з діленням.
    code = re.sub(
        r"(?<=[(,=!&|:;\s])/(?:\\.|\[(?:\\.|[^\]\\])*\]|[^/\\\n\[])+/[gimsuy]*",
        "/RX/", code)
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


# ── Окремі файли скриптів ───────────────────────────────────────────────────


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_file_parses(script):
    """Кожен `static/*.js` мусить РОЗБИРАТИСЬ.

    Найсильніша перевірка, яку тут можна зробити дешево: віддати файл самому
    рушію JavaScript (`node --check`). Це не підрахунок дужок, а справжній
    розбір -- він ловить і незакриту дужку, і зайву кому, і `return` поза
    функцією.

    Якщо `node` у системі немає, падаємо назад на підрахунок дужок: гірше,
    ніж розбір, але краще, ніж нічого. Пропускати перевірку зовсім не можна --
    саме «пропустити, якщо чогось немає» і зробило беззубими два інші файли
    тестів (див. test_theme_switch: `if css is None: return`).
    """
    path = os.path.join(STATIC, script)
    node = shutil.which("node")
    if node:
        done = subprocess.run([node, "--check", path],
                              capture_output=True, text=True)
        assert done.returncode == 0, (
            f"{script}: рушій JavaScript не розбирає файл: "
            + (done.stderr or done.stdout))
        return
    code = _strip_strings_and_comments(io.open(path, encoding="utf-8").read())
    for opener, closer, what in (("{", "}", "фігурні"),
                                 ("(", ")", "круглі"),
                                 ("[", "]", "квадратні")):
        assert code.count(opener) == code.count(closer), (
            f"{script}: {what} дужки не збалансовані "
            f"({code.count(opener)} проти {code.count(closer)})")


@pytest.mark.parametrize("page", PAGES)
def test_pages_only_reference_scripts_that_exist(page):
    """Сторінка не має посилатись на скрипт, якого немає.

    Друга половина того самого класу поломок: файл перейменували, посилання
    лишилось -- і скрипт мовчки не виконується. У випадку `theme-toggle.js`
    (він у <head> без defer) це не мовчки, а порожня сторінка.
    """
    html = _read(page)
    for src in re.findall(r'<script[^>]+src="/static/([^"]+)"', html):
        assert os.path.exists(os.path.join(STATIC, src)), \
            f"{page} посилається на /static/{src}, якого немає"


# ── Метатег viewport ────────────────────────────────────────────────────────


@pytest.mark.parametrize("page", PAGES)
def test_page_has_viewport_meta(page):
    """Без `width=device-width` мобільний браузер вважає сторінку шириною
    980px, і КОЖЕН медіа-запит із `max-width` не спрацьовує. Телефон показує
    зменшений комп'ютерний макет, а телефонна розкладка мовчки не
    застосовується -- саме це й сталося з чатом 27.08.
    """
    html = _read(page)
    metas = re.findall(r'<meta name="viewport"[^>]*>', html)
    assert metas, f"{page}: немає метатегу viewport"
    assert "width=device-width" in metas[0], metas[0]
    # Сучасний механізм для клавіатури: браузер зменшує сам вьюпорт, тому
    # притиснуте до низу поле не ховається під нею.
    assert "interactive-widget=resizes-content" in metas[0], metas[0]


def test_chat_head_carries_viewport_meta():
    """Сторінку чата малює Gradio, і вона метатега НЕ додає -- ми кладемо його
    у `head` самі. Тест на код, бо готової сторінки тут немає: вона
    складається при запуску сервісу.
    """
    src = io.open(os.path.join(os.path.dirname(STATIC), "chat_gradio",
                               "app.py"), encoding="utf-8").read()
    assert 'name="viewport"' in src,         "у head чата немає метатегу viewport -- телефонна розкладка не діє"
    assert "width=device-width" in src
    assert "interactive-widget=resizes-content" in src


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
