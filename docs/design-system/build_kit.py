# -*- coding: utf-8 -*-
"""Складає набір дизайн-системи «AI-секретар» для Claude Design.

Нащо скриптом, а не руками: набір мусить бути ТИМ САМИМ, що в коді сайту.
Токени тут читаються з `static/theme-tokens-v3.css` -- одного джерела правди,
тому картки в Claude Design не можуть розійтися з тим, що бачить людина на
сторінці. Правка токена -> перезбірка -> синхронізація.

Запуск:
    python docs/design-system/build_kit.py
    -> docs/design-system/kit/**  (те, що вивантажується в Claude Design)
"""
import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(HERE, "kit")
TOKENS_V2 = os.path.join(ROOT, "demos", "upload_app", "static",
                         "theme-tokens-v2.css")
TOKENS_V3 = os.path.join(ROOT, "demos", "upload_app", "static",
                         "theme-tokens-v3.css")


def w(path, text):
    full = os.path.join(OUT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    io.open(full, "w", encoding="utf-8", newline="\n").write(text)
    return path


def read_tokens():
    """@font-face з v2 (файли шрифтів ті самі) + :root із v3."""
    v2 = io.open(TOKENS_V2, encoding="utf-8").read()
    v3 = io.open(TOKENS_V3, encoding="utf-8").read()
    faces = "\n".join(re.findall(r"@font-face\s*\{.*?\}", v2, re.S))
    # у наборі шрифт лежить поряд, тому шлях інший, ніж на сайті
    faces = faces.replace("/static/fonts/", "fonts/")
    roots = v3[v3.index(":root {"):]
    return faces, roots


CARD = '<!-- @dsCard group="{group}" name="{name}" subtitle="{sub}" viewport="{vp}" -->\n'

HEAD = """<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI-секретар — {title}</title>
<link rel="stylesheet" href="{up}styles.css">
</head>
<body>
<main class="demo">
"""
FOOT = """</main>
</body>
</html>
"""


def page(card, title, body, up="../"):
    return (CARD.format(**card) + HEAD.format(title=title, up=up) + body + FOOT)


def build():
    os.makedirs(OUT, exist_ok=True)
    faces, roots = read_tokens()
    files = []

    # ── theme.json: те саме, що в токенах ────────────────────────────────────
    #
    # Палітра ЧИТАЄТЬСЯ з токенів, а не стоїть тут літералами. Було
    # літералами -- і 27.08 це вилізло: зелений у токенах зсунули, styles.css
    # перезібрався, а theme.json лишився зі старим кольором. Тобто файл, який
    # називається «тема», описував тему, якої вже немає.
    def tok(name, fallback):
        m = re.search(r"--" + name + r":\s*([^;]+);", roots)
        return m.group(1).strip() if m else fallback

    files.append(w("theme.json", json.dumps({
        "name": "AI-секретар",
        "palette": {"band": "light", "scheme": "mono",
                    "bg": tok("c-bg", "#f7f6f4"),
                    "surface": tok("c-surface", "#efedea"),
                    "text": tok("c-text", "#1c1b19"),
                    "accent": tok("c-accent", "#46682f"),
                    "accent2": tok("c-brand", "#35472c")},
        "fonts": {"heading": {"family": "IBM Plex Sans", "class": "grotesque",
                              "weights": [400, 600, 700]},
                  "body": {"family": "IBM Plex Sans", "class": "grotesque",
                           "weights": [400, 600, 700]},
                  "headingWeight": 700},
        "density": 1, "radius": 3, "layoutStyle": "left",
        "dividers": "strong", "buttonAlign": "center",
        "imageTreatment": "none",
    }, ensure_ascii=False, indent=2) + "\n"))

    # ── styles.css: токени + класи компонентів ──────────────────────────────
    files.append(w("styles.css", f"""/* AI-секретар -- обличчя продукту.
 *
 * Джерело правди: demos/upload_app/static/theme-tokens-v3.css у репозиторії.
 * Цей файл СКЛАДАЄТЬСЯ скриптом docs/design-system/build_kit.py, тому набір
 * у Claude Design не може розійтися з тим, що бачить людина на сайті.
 *
 * Значення кольорів -- не настрій, а зміст:
 *   олива      -- дія (кнопка, активна сторінка, посилання)
 *   бірюза     -- підтверджено (входить у підрахунки)
 *   коричневий -- чекає підтвердження людиною (у підрахунки НЕ входить)
 *   бурштин    -- увага: система не впевнена
 *   червоний   -- помилка або відмова
 */
{faces}

{roots}

/* ══ базове ══════════════════════════════════════════════════════════════ */
*, *::before, *::after {{ box-sizing: border-box }}
body {{
    margin: 0; background: var(--c-bg); color: var(--c-text);
    font-family: var(--font); font-size: var(--fs-body); line-height: 1.55;
    -webkit-font-smoothing: antialiased;
}}
.demo {{ max-width: 420px; margin: 0 auto; padding: 0 }}
h1, h2, h3 {{ font-family: var(--font-heading); font-weight: var(--w-heading);
             letter-spacing: -.015em; line-height: 1.15; margin: 0 0 var(--s-2) }}
h1 {{ font-size: var(--fs-h1) }}
h3 {{ font-size: var(--fs-h2) }}
.kicker {{
    font-size: var(--fs-kicker); letter-spacing: .1em; text-transform: uppercase;
    color: var(--c-muted); font-weight: 600; margin: 0 0 var(--s-2);
}}
.muted {{ color: var(--c-muted) }}
.small {{ font-size: var(--fs-small) }}
.num, .display, td.n, .tile-num {{ font-variant-numeric: tabular-nums }}
code, pre {{ font-family: var(--font-mono); font-size: 12.5px }}
* {{ -webkit-tap-highlight-color: transparent }}
:focus {{ outline: none }}
:focus-visible {{ outline: 2px solid var(--c-accent); outline-offset: 2px }}
@media (prefers-reduced-motion: reduce) {{
    *, *::before, *::after {{ transition-duration: .001ms !important;
                             animation-duration: .001ms !important }}
}}

/* ══ брендовий блок ═════════════════════════════════════════════════════ */
.appbar {{ background: var(--c-brand); color: var(--c-brand-text) }}
.appbar .brand {{ display: flex; align-items: center; gap: var(--s-3);
                 padding: var(--s-3) var(--s-4) }}
.appbar .mark {{
    width: 30px; height: 30px; flex: 0 0 auto; border-radius: var(--r-sm);
    background: var(--c-brand-text); color: var(--c-brand);
    display: grid; place-items: center; font-weight: 700; font-size: 13px;
}}
.appbar .bname {{ font-weight: 700; font-size: 16px; letter-spacing: -.01em }}
.appbar .bsub {{ font-size: 12px; opacity: .72 }}
.tabs {{ display: grid; grid-template-columns: repeat(3, 1fr);
        background: rgba(0,0,0,.14) }}
.tabs a {{
    min-height: 46px; display: flex; align-items: center; justify-content: center;
    font-size: 14px; font-weight: 600; text-decoration: none;
    color: color-mix(in srgb, var(--c-brand-text) 72%, transparent);
    border-bottom: 2px solid transparent; transition: color var(--dur) var(--ease),
    background-color var(--dur) var(--ease);
}}
.tabs a:hover {{ color: var(--c-brand-text); background: rgba(255,255,255,.06) }}
.tabs a[aria-current="page"] {{
    color: var(--c-brand-text); background: rgba(255,255,255,.10);
    border-bottom-color: var(--c-brand-text);
}}

/* ══ полотно ════════════════════════════════════════════════════════════ */
.body {{ padding: var(--s-5) var(--s-4) var(--s-6) }}
.card {{
    background: var(--c-canvas); border: 1px solid var(--c-border);
    border-radius: var(--r-md); padding: var(--s-4); margin: var(--s-4) 0;
    box-shadow: var(--shadow-sm);
}}
.card--flat {{ box-shadow: none }}
.sunken {{ background: var(--c-surface); border: 1px solid var(--c-hairline);
          border-radius: var(--r-sm); padding: var(--s-3) }}
.rule {{ height: 2px; border: 0; background: var(--c-divider);
        margin: var(--s-5) 0 }}

/* ══ кнопки ═════════════════════════════════════════════════════════════ */
.btn {{
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    min-height: 48px; width: 100%; padding: 0 var(--s-4);
    font-family: var(--font-heading); font-weight: 600; font-size: 15px;
    border: 1px solid transparent; border-radius: var(--r-md); cursor: pointer;
    background: var(--c-accent); color: var(--c-on-accent);
    transition: background-color var(--dur) var(--ease),
                border-color var(--dur) var(--ease),
                transform 80ms var(--ease), box-shadow var(--dur) var(--ease);
}}
.btn:hover {{ background: var(--c-accent-600); box-shadow: var(--shadow-md);
             transform: translateY(-1px) }}
.btn:active {{ background: var(--c-accent-700); transform: translateY(1px);
              box-shadow: none }}
.btn:disabled {{ opacity: .45; cursor: not-allowed; transform: none;
                box-shadow: none }}
.btn--ghost {{ background: var(--c-canvas); color: var(--c-text);
              border-color: var(--c-divider) }}
.btn--ghost:hover {{ background: var(--c-surface); border-color: var(--c-text) }}
.btn--danger {{ background: transparent; color: var(--c-error);
               border-color: color-mix(in srgb, var(--c-error) 40%, transparent) }}
.btn--danger:hover {{ background: var(--c-error-soft) }}
.btn + .btn {{ margin-top: var(--s-2) }}
.btn .ic {{ font-size: 17px; line-height: 1 }}

/* ══ позначки стану ═════════════════════════════════════════════════════ */
.flag {{
    display: inline-flex; align-items: center; gap: 6px; border-radius: var(--r-pill);
    padding: 3px 10px; font-size: 12px; font-weight: 600; line-height: 1.5;
}}
.flag--ok {{ background: var(--c-success-soft); color: var(--c-success) }}
.flag--pending {{ background: var(--c-pending-soft); color: var(--c-pending) }}
.flag--warn {{ background: var(--c-warn-soft); color: var(--c-warn) }}
.flag--error {{ background: var(--c-error-soft); color: var(--c-error) }}
.flag--muted {{ background: var(--c-surface); color: var(--c-muted) }}

/* ══ кроки обробки ══════════════════════════════════════════════════════ */
.steps {{ list-style: none; margin: 0; padding: 0; font-size: 14px }}
.steps li {{ display: flex; align-items: center; gap: 10px; padding: 7px 0;
            border-bottom: 1px solid var(--c-hairline) }}
.steps li:last-child {{ border-bottom: 0 }}
.dot {{ width: 9px; height: 9px; border-radius: var(--r-full); flex: 0 0 auto;
       background: var(--c-success) }}
.dot--run {{ background: var(--c-accent); animation: pulse 1.2s var(--ease) infinite }}
.dot--wait {{ background: var(--c-border) }}
@keyframes pulse {{ 50% {{ opacity: .3 }} }}
.steps .t {{ margin-left: auto; color: var(--c-muted); font-size: 13px;
            font-variant-numeric: tabular-nums }}

/* ══ поля документа ═════════════════════════════════════════════════════ */
.rows {{ font-size: 14.5px }}
.rows .row {{ display: flex; gap: var(--s-3); padding: 8px 0;
             border-bottom: 1px solid var(--c-hairline) }}
.rows .row:last-child {{ border-bottom: 0 }}
.rows .k {{ color: var(--c-muted); flex: 0 0 40% }}
.rows .v {{ font-weight: 600 }}

/* ══ цифри ══════════════════════════════════════════════════════════════ */
.display {{
    font-family: var(--font-heading); font-weight: 700; font-size: var(--fs-display);
    letter-spacing: -.03em; line-height: 1;
}}
.hero-num {{ background: var(--c-accent-soft); border: 1px solid var(--c-accent-line);
            border-radius: var(--r-md); padding: var(--s-4); text-align: center }}
.tiles {{ display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-2) }}
.tile {{ background: var(--c-canvas); border: 1px solid var(--c-border);
        border-radius: var(--r-md); padding: var(--s-3);
        transition: border-color var(--dur) var(--ease) }}
.tile:hover {{ border-color: var(--c-divider) }}
.tile-num {{ font-family: var(--font-heading); font-weight: 700; font-size: 24px;
            letter-spacing: -.02em }}
.tile-label {{ font-size: 12.5px; color: var(--c-muted); line-height: 1.3;
              margin-top: 2px }}
.tile--pending .tile-num {{ color: var(--c-pending) }}
.list-num {{ background: var(--c-canvas); border: 1px solid var(--c-border);
            border-radius: var(--r-md) }}
.list-num div {{ display: flex; justify-content: space-between; gap: var(--s-3);
                padding: 10px var(--s-3); border-bottom: 1px solid var(--c-hairline) }}
.list-num div:last-child {{ border-bottom: 0 }}
.list-num b {{ font-variant-numeric: tabular-nums }}

/* ══ таблиця ════════════════════════════════════════════════════════════ */
table {{ width: 100%; border-collapse: collapse; font-size: 14px }}
th {{ text-align: left; font-size: var(--fs-kicker); letter-spacing: .08em;
     text-transform: uppercase; color: var(--c-muted); font-weight: 600;
     padding: var(--s-2); border-bottom: 2px solid var(--c-divider) }}
td {{ padding: var(--s-2); border-bottom: 1px solid var(--c-hairline) }}
tbody tr {{ transition: background-color var(--dur) var(--ease) }}
tbody tr:hover {{ background: var(--c-surface) }}

/* ══ чат ════════════════════════════════════════════════════════════════ */
.msg {{ border-radius: var(--r-md); padding: var(--s-3); margin: var(--s-3) 0;
       font-size: 14.5px }}
.msg--me {{ background: var(--c-accent); color: var(--c-on-accent);
           margin-left: 18%; }}
.msg--bot {{ background: var(--c-canvas); border: 1px solid var(--c-border);
            border-left: 3px solid var(--c-accent); box-shadow: var(--shadow-sm) }}
.msg--bot .lead {{ font-weight: 700; font-size: 15.5px }}
.msg--bot ul {{ margin: var(--s-2) 0 0; padding-left: 18px }}
.msg--bot li {{ margin: 3px 0 }}
.msg--refusal {{ border-left-color: var(--c-warn) }}
.src {{ margin-top: var(--s-3); padding-top: var(--s-2);
       border-top: 1px dashed var(--c-border); font-size: 12.5px;
       color: var(--c-muted) }}
.composer {{ display: flex; gap: var(--s-2); margin-top: var(--s-4) }}
.composer .in {{ flex: 1; min-height: 48px; display: flex; align-items: center;
                padding: 0 var(--s-3); background: var(--c-canvas);
                border: 1px solid var(--c-border); border-radius: var(--r-md);
                color: var(--c-muted); font-size: 15px }}
.composer .go {{ width: 48px; min-height: 48px; display: grid; place-items: center;
                background: var(--c-accent); color: var(--c-on-accent);
                border-radius: var(--r-md); font-size: 18px; cursor: pointer;
                transition: background-color var(--dur) var(--ease) }}
.composer .go:hover {{ background: var(--c-accent-600) }}
.chips {{ display: flex; flex-wrap: wrap; gap: var(--s-2); margin-top: var(--s-3) }}
.chip {{ background: var(--c-canvas); border: 1px solid var(--c-border);
        border-radius: var(--r-pill); padding: 7px 12px; font-size: 13px;
        color: var(--c-muted); cursor: pointer;
        transition: border-color var(--dur) var(--ease), color var(--dur) var(--ease) }}
.chip:hover {{ border-color: var(--c-accent); color: var(--c-accent) }}

/* ══ службові стани ═════════════════════════════════════════════════════ */
.note {{ border-left: 3px solid var(--c-border); padding: var(--s-2) var(--s-3);
        font-size: 13.5px; color: var(--c-muted); background: var(--c-surface) }}
.note--warn {{ border-left-color: var(--c-warn); color: var(--c-warn);
              background: var(--c-warn-soft) }}
.note--error {{ border-left-color: var(--c-error); color: var(--c-error);
               background: var(--c-error-soft) }}
.note--ok {{ border-left-color: var(--c-success); color: var(--c-success);
            background: var(--c-success-soft) }}
.skeleton {{ height: 12px; border-radius: var(--r-sm); background: var(--c-surface);
            position: relative; overflow: hidden }}
.skeleton + .skeleton {{ margin-top: var(--s-2) }}
.skeleton::after {{
    content: ""; position: absolute; inset: 0;
    background: linear-gradient(90deg, transparent,
        color-mix(in srgb, var(--c-canvas) 70%, transparent), transparent);
    animation: shimmer 1.3s var(--ease) infinite;
}}
@keyframes shimmer {{ 0% {{ transform: translateX(-100%) }}
                     100% {{ transform: translateX(100%) }} }}
"""))

    APPBAR = """<header class="appbar">
  <div class="brand"><span class="mark">AI</span><span>
    <span class="bname">AI-секретар</span><br>
    <span class="bsub">облік особового складу</span></span></div>
  <nav class="tabs"><a href="#" aria-current="page">Документ</a>
    <a href="#">Чат</a><a href="#">Цифри</a></nav>
</header>
"""

    # ── foundations ─────────────────────────────────────────────────────────
    sw = "".join(
        f'<div class="row"><span class="k"><span class="flag flag--{c}">{lab}</span></span>'
        f'<span class="v small muted">{mean}</span></div>'
        for c, lab, mean in (
            ("ok", "підтверджено", "входить у підрахунки"),
            ("pending", "чернетка", "у підрахунки НЕ входить"),
            ("warn", "система не впевнена", "поле потребує ока"),
            ("error", "помилка / відмова", "дані не показуємо"),
            ("muted", "не розпізнано", "у базу піде лише файл")))
    files.append(w("foundations/color.html", page(
        {"group": "Основи", "name": "Колір", "sub": "олива — дія; кожен інший колір має значення",
         "vp": "420x520"}, "Колір",
        f"""{APPBAR}<div class="body">
<p class="kicker">Колір як значення</p>
<h1>Палітра</h1>
<p class="muted small">Колір у цій системі не настрій, а зміст. Тому їх мало, і
кожен закріплений за станом.</p>
<div class="card"><div class="rows">{sw}</div></div>
<p class="kicker">Дія</p>
<div class="card card--flat"><button class="btn">Підтвердити</button>
<button class="btn btn--ghost">Не підтверджувати</button></div>
<p class="note">Червоний навмисно НЕ використаний як акцент: він означає
помилку, і кнопка дії злилася б із попередженням.</p>
</div>""")))

    files.append(w("foundations/type.html", page(
        {"group": "Основи", "name": "Типографіка", "sub": "IBM Plex Sans + Mono, контраст розмірів 4:1",
         "vp": "420x560"}, "Типографіка",
        f"""{APPBAR}<div class="body">
<p class="kicker">Рубрика · 11 / розріджена</p>
<h1>Заголовок сторінки · 30</h1>
<h3>Підзаголовок блоку · 19</h3>
<p>Тіло тексту · 15 / 1.55. Українська кирилиця в цьому шрифті повна — саме
через це відкинутий Archivo, у якого її немає.</p>
<p class="small muted">Дрібний підпис · 13, тихіший за тіло на один щабель.</p>
<div class="card"><span class="display num">1 899</span>
<div class="tile-label">дисплейний розмір · 48 · для головного числа</div></div>
<div class="sunken"><code>№ 1030 · 2026-08-13 — 2026-09-01 · UNIT-0048</code>
<div class="small muted" style="margin-top:6px">моноширинний — номери, дати,
табельні, SQL</div></div>
</div>""")))

    # ── components ──────────────────────────────────────────────────────────
    files.append(w("components/buttons.html", page(
        {"group": "Компоненти", "name": "Кнопки", "sub": "дія / другорядна / відмова + стани",
         "vp": "420x460"}, "Кнопки",
        f"""{APPBAR}<div class="body">
<p class="kicker">Кнопки</p>
<button class="btn">Підтвердити — записати в базу</button>
<button class="btn btn--ghost"><span class="ic">□</span> Вибрати файл</button>
<button class="btn btn--danger">Відхилити документ</button>
<button class="btn" disabled>Недоступно, доки триває обробка</button>
<p class="note" style="margin-top:16px">Наведення підіймає на 1px і додає тінь,
натиск втоплює. 140 мс, одна крива на всі стани.</p>
</div>""")))

    files.append(w("components/upload.html", page(
        {"group": "Компоненти", "name": "Завантаження", "sub": "вибір файла, кроки, витягнуті поля",
         "vp": "420x820"}, "Завантаження",
        f"""{APPBAR}<div class="body">
<p class="kicker">Крок 1 · файл</p>
<h1>Завантаження документа</h1>
<p class="muted small">Система прочитає документ і покаже, що витягла. У базу це
піде лише після вашого підтвердження.</p>
<button class="btn"><span class="ic">◉</span> Зняти камерою</button>
<button class="btn btn--ghost"><span class="ic">□</span> Вибрати файл</button>
<p class="small muted" style="text-align:center;margin-top:8px">docx, pdf або фото</p>
<div class="card"><p class="kicker">Крок 2 · обробка</p>
<ul class="steps">
  <li><span class="dot"></span> файл прийнято <span class="t">0,1 с</span></li>
  <li><span class="dot"></span> текст прочитано <span class="t">2,6 с</span></li>
  <li><span class="dot dot--run"></span> витяг полів <span class="t">1,4 с</span></li>
  <li><span class="dot dot--wait"></span> запис у базу <span class="t">—</span></li>
</ul></div>
<div class="card"><p class="kicker">Крок 3 · що витягнуто</p>
<div class="rows">
  <div class="row"><span class="k">номер</span><span class="v num">№ 1030 · 05.08.2026</span></div>
  <div class="row"><span class="k">особа</span><span class="v">Влох Святослав Олесьович</span></div>
  <div class="row"><span class="k">відпустка</span><span class="v num">13.08 — 01.09 · 20 днів</span></div>
  <div class="row"><span class="k">куди</span><span class="v">с. Соснова Гряда</span></div>
  <div class="row"><span class="k">проїзний</span><span class="v num">4204/26</span></div>
</div>
<p style="margin:12px 0 0"><span class="flag flag--ok">✓ критичні поля прочитані</span></p>
<button class="btn">Підтвердити — записати в базу</button>
<button class="btn btn--ghost">Не підтверджувати</button></div>
</div>""")))

    files.append(w("components/chat.html", page(
        {"group": "Компоненти", "name": "Чат", "sub": "відповідь із джерелом, відмова, підказки",
         "vp": "420x760"}, "Чат",
        f"""{APPBAR}<div class="body">
<div class="msg msg--me">Хто у відпустці у 2 роті 30 серпня?</div>
<div class="msg msg--bot"><div class="lead">2 особи у відпустці — 2-га механізована рота</div>
<ul><li>Влох Святослав Олесьович — <span class="num">13.08 — 01.09</span>, документ <span class="num">№1030</span></li>
<li>Приймак Єлисей Романович — <span class="num">29.08 — 11.09</span>, документ <span class="num">№112</span></li></ul>
<div class="src">склад роти за штаткою <b class="num">90</b> · зріз на
<b class="num">30.08</b> · чернеток <b class="num">0</b> ·
<code>звернення 8f31c2</code></div></div>
<div class="msg msg--me">А в 5 роті?</div>
<div class="msg msg--bot msg--refusal"><div class="lead">Такої роти в штатці немає</div>
<p class="small" style="margin:6px 0 0">Нуль тут означав би «нікого немає», а
насправді немає самого підрозділу. У базі: 1-ша, 2-га, 3-тя роти, взвод
забезпечення, управління батальйону.</p></div>
<div class="composer"><span class="in">Ваше питання…</span><span class="go">↑</span></div>
<div class="chips"><span class="chip">скільком зараз у відпустці</span>
<span class="chip">хто повертається 31.08</span>
<span class="chip">відсутні по підрозділах</span></div>
</div>""")))

    files.append(w("components/stats.html", page(
        {"group": "Компоненти", "name": "Цифри", "sub": "головне число, плитки, список",
         "vp": "420x720"}, "Цифри",
        f"""{APPBAR}<div class="body">
<p class="kicker">Заміряна якість</p>
<div class="hero-num"><div class="display num">100%</div>
<div class="tile-label">полів витягнуто правильно · <span class="num">953 з 953</span></div></div>
<p class="kicker" style="margin-top:24px">Що зараз у базі</p>
<div class="tiles">
  <div class="tile"><div class="tile-num num">204</div><div class="tile-label">документи</div></div>
  <div class="tile"><div class="tile-num num">303</div><div class="tile-label">особи в реєстрі</div></div>
  <div class="tile"><div class="tile-num num">1 899</div><div class="tile-label">підтверджені факти</div></div>
  <div class="tile tile--pending"><div class="tile-num num">112</div>
    <div class="tile-label">чернетки — у підрахунки не входять</div></div>
</div>
<p class="kicker" style="margin-top:24px">Робота людини</p>
<div class="list-num">
  <div><span>документів чекає перевірки</span><b class="num">31</b></div>
  <div><span>нормативних документів</span><b class="num">41</b></div>
  <div><span>перевірених запитів чата</span><b class="num">28</b></div>
</div>
</div>""")))

    files.append(w("components/states.html", page(
        {"group": "Компоненти", "name": "Стани", "sub": "порожньо, вантажиться, помилка, відмова",
         "vp": "420x620"}, "Стани",
        f"""{APPBAR}<div class="body">
<p class="kicker">Вантажиться</p>
<div class="card"><div class="skeleton" style="width:60%"></div>
<div class="skeleton"></div><div class="skeleton" style="width:80%"></div></div>
<p class="kicker">Порожньо</p>
<div class="note">Документів у черзі немає — усе перевірене.</div>
<p class="kicker" style="margin-top:20px">Система не впевнена</p>
<div class="note note--warn">У номері документа нецифровий символ: №3О4.
Не виправляємо й схожого не підставляємо — це місце, де система не впевнена.</div>
<p class="kicker" style="margin-top:20px">Збій доступу</p>
<div class="note note--error">База зараз недоступна. Це не «нічого не
знайшлося»: цифри на місці, але чат до них не дістає.</div>
<p class="kicker" style="margin-top:20px">Готово</p>
<div class="note note--ok">Записано: документ №1030, 8 фактів, зріз 27.08.</div>
</div>""")))

    files.append(w("components/table.html", page(
        {"group": "Компоненти", "name": "Таблиця", "sub": "капітельна голова, моноширинні цифри",
         "vp": "420x440"}, "Таблиця",
        f"""{APPBAR}<div class="body">
<p class="kicker">Розклад по підрозділах</p>
<div class="card card--flat"><table>
<thead><tr><th>підрозділ</th><th class="n">відсутні</th><th class="n">склад</th></tr></thead>
<tbody>
<tr><td>1-ша механізована рота</td><td class="n num">5</td><td class="n num">90</td></tr>
<tr><td>2-га механізована рота</td><td class="n num">4</td><td class="n num">90</td></tr>
<tr><td>3-тя механізована рота</td><td class="n num">3</td><td class="n num">90</td></tr>
<tr><td>управління батальйону</td><td class="n num">1</td><td class="n num">15</td></tr>
</tbody></table></div>
<p class="small muted">Підрозділи, яких у переліку немає, — це нуль відсутніх, а
не брак даних.</p>
</div>""")))

    files.append(w("readme.md", """# AI-секретар — обличчя продукту

Набір складається скриптом `docs/design-system/build_kit.py` із того самого
файла токенів, що працює на сайті (`static/theme-tokens-v3.css`). Тому картки
тут не можуть розійтися з тим, що бачить людина: правка токена → перезбірка →
синхронізація.

## Правило кольору

Колір означає стан, а не настрій:

| колір | значення |
|---|---|
| олива | дія: кнопка, активна сторінка, посилання |
| бірюза | підтверджено — входить у підрахунки |
| коричневий | чекає підтвердження людиною — у підрахунки НЕ входить |
| бурштин | система не впевнена |
| червоний | помилка або відмова |

Червоний навмисно не є акцентом: інакше кнопка дії злилася б із
попередженням.

## Дві межі, які тримає цей набір

* **шрифт лежить локально** — жодного зовнішнього запиту зі сторінки;
* **кирилиця обов'язкова** — через це відкинутий Archivo з «Modernist»: у
  нього немає кириличної підмножини, і український текст падав би на
  системний шрифт.
"""))
    print("складено файлів:", len(files) + 1)
    for f in sorted(files):
        print("  ", f)


if __name__ == "__main__":
    build()
