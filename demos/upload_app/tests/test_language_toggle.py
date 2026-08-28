# -*- coding: utf-8 -*-
"""Перемикач мови: ВІДКЛЮЧЕНИЙ 28.08, і тест стежить саме за цим.

## Що сталося

Реалізація була словником по вже намальованому DOM. На екрані це дало мішанку
українського з англійським:

    «Скільки on leave 2026-10-10?»
    «Покажи document no. 102»
    «Відповідь формується з documents; source — під кожною відповіддю.»

Причина не в словнику й не в кількості рядків, а в ПІДХОДІ: рядок у DOM -- це
вже склеєний із шаблона й даних текст, і підміняти в ньому фрагменти означає
перекладати частину речення. Повний розбір і варіанти правильної реалізації --
`docs/research/2026-08-28_ui-translation-options.md`.

Рішення Ані: «якщо не виходить це зробити чисто і просто, то поки відкладаємо
реалізацію».

## Чому тести лишились, а не видалені разом із кнопкою

Файли (`static/lang-toggle.js`), кеш перекладів і маршрут `/api/translate`
лишаються -- вони знадобляться, коли робити переклад правильно. Тому тут
перевіряється ДВА стани:

  * кнопки й підключення скрипта на сторінках НЕМА (інакше мішанка вернеться
    на екран непоміченою);
  * самі файли цілі й не зіпсовані -- щоб продовжити роботу було з чого.

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


def test_toggle_is_not_wired_anywhere():
    """ГОЛОВНИЙ тест тепер такий: перемикача на сторінках НЕМА.

    Не «поки що немає», а перевіряється: якщо хтось підключить його назад, не
    змінивши підходу, мішанка з екрана вернеться, і побачить її знову Аня, а
    не тест.
    """
    for name in PAGES:
        s = io.open(os.path.join(STATIC, name), encoding="utf-8").read()
        assert 'class="lang-toggle"' not in s, name
        assert "lang-toggle.js" not in s, name
    chat = io.open(os.path.join(os.path.dirname(STATIC), "chat_gradio",
                                "app.py"), encoding="utf-8").read()
    assert 'class="lang-toggle"' not in chat
    # У коді чата шлях до файла лишився НАВМИСНО (з поясненням), а от
    # підклеювання в head -- ні.
    assert '"<script>" + lang + "</script>"' not in chat


def test_the_pieces_are_kept_for_the_next_attempt():
    """Файли цілі: продовжувати треба буде з них, а не з нуля."""
    assert os.path.exists(os.path.join(STATIC, "lang-toggle.js"))
    from demos.upload_app import translate as tr
    assert os.path.exists(tr.CACHE_PATH)
    assert len(tr.cache()) > 700


def test_script_is_still_served():
    """Маршрут лишається: файл віддається, просто сторінки його не просять.
    Так наступна спроба почнеться з робочого стану, а не з відновлення."""
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


# ── Кнопка мусить БУТИ НАТИСКАБЕЛЬНОЮ і не налазити на сусідню ─────────────
#
# Обидва дефекти знайшла Аня очима 28.08, і обидва були в CSS, не в скрипті.

def _css(name):
    if name.endswith("theme-v3.css"):
        path = os.path.join(os.path.dirname(STATIC), "chat_gradio", name)
    else:
        path = os.path.join(STATIC, name)
    return io.open(path, encoding="utf-8").read()


def test_chat_button_receives_clicks():
    """У чаті обгортка перемикачів має `pointer-events: none`, і клік ловить
    лише те, що явно повертає `pointer-events: auto`.

    Мовна кнопка успадкувала `none` і НЕ НАТИСКАЛАСЬ узагалі: обробник був
    підключений, до нього просто не доходив клік. Тест перевіряє причину, а не
    симптом.
    """
    css = _css("theme-v3.css")
    # Беремо саме ПРАВИЛО з `pointer-events: auto` і дивимось його селектори.
    # Перша версія тесту читала 400 символів після сусіднього правила -- і
    # падала, хоч CSS був правильний: вікно в символах не є структурою.
    rules = [chunk for chunk in css.split("}") if "pointer-events: auto" in chunk]
    assert rules, "у чаті ніхто не повертає pointer-events -- клік не дійде"
    selectors = " ".join(r.split("{")[0] for r in rules)
    assert ".lang-toggle" in selectors, (
        "мовна кнопка не повертає pointer-events -- вона не натискатиметься; "
        f"селектори: {selectors.strip()[:160]}")


def test_language_button_does_not_overlap_the_theme_button():
    """На звичайних сторінках обидві кнопки позиційовані АБСОЛЮТНО від смуги,
    тому другій потрібен свій `right`.

    Спершу я поставила це правило ВИЩЕ за спільне `right: var(--s-4)` --
    специфічність однакова, тому вирішує порядок, і кнопки налізли одна на одну.
    Тест фіксує саме порядок: зсув мусить стояти ПІСЛЯ спільного правила.
    """
    css = _css("pages-v3.css")
    shared = css.find(".appbar .lang-toggle {")
    offset = css.find(".appbar .lang-toggle { right:")
    assert shared >= 0 and offset >= 0, (shared, offset)
    assert offset > shared, (
        "зсув мовної кнопки стоїть ВИЩЕ за спільне правило -- при рівній "
        "специфічності виграє те, що нижче, і кнопки налізуть одна на одну")


def test_chat_button_is_styled_like_the_theme_button():
    """Кнопка без стилів у чаті виглядає як звичайна кнопка Gradio. Форма й
    розмір мусять браться з того самого правила, що в теми."""
    css = _css("theme-v3.css")
    assert "#theme-switch .theme-toggle,\n#theme-switch .lang-toggle {" in css
    assert "#theme-switch .lang-toggle {" in css
