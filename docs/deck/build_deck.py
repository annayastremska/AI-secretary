# -*- coding: utf-8 -*-
"""Складає презентацію одним файлом: шрифти вкладені, зовнішніх запитів нуль.

Стиль, шрифти й кольори -- із дизайн-системи Claude Design (handoff). Зміст
слайдів наш, логічний, узятий з опису проєкту.

Нащо base64, а не посилання на файли: презентацію відкривають з флешки, з
іншого ноутбука, з теки «Завантаження». Файл, який тягне шрифт із сусідньої
теки, у цих умовах ламається молча -- лишається системний шрифт, і розкладка
з'їжджає. Один файл цього не вміє.
"""
import base64
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HANDOFF = (r"C:\Users\Lenovo\Desktop\Agentic AI"
           r"\AI-секретар Design System-handoff\ai-design-system\project")
FONTS = os.path.join(HANDOFF, "assets", "fonts")

#: Лише латинські підмножини: колода англійською. Кириличні не вкладаємо --
#: це 190 КБ, які нічого не показують.
WANT = [
    ("Plex", 400, "plexsans-400-latin.woff2",
     "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+2000-206F,U+2192,U+2212,U+2215"),
    ("Plex", 400, "plexsans-400-latin-ext.woff2",
     "U+0100-02AF,U+0304-0308,U+1E00-1EFF,U+2020,U+20A0-20AB"),
    ("Plex", 600, "plexsans-600-latin.woff2",
     "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+2000-206F,U+2192,U+2212,U+2215"),
    ("Plex", 700, "plexsans-700-latin.woff2",
     "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+2000-206F,U+2192,U+2212,U+2215"),
    ("Plex", 700, "plexsans-700-latin-ext.woff2",
     "U+0100-02AF,U+0304-0308,U+1E00-1EFF,U+2020,U+20A0-20AB"),
    ("PlexMono", 400, "plexmono-400-latin.woff2",
     "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+2000-206F,U+2192,U+2212,U+2215"),
]

faces = []
total = 0
for family, weight, name, urange in WANT:
    path = os.path.join(FONTS, name)
    raw = io.open(path, "rb").read()
    total += len(raw)
    b64 = base64.b64encode(raw).decode("ascii")
    faces.append(
        '@font-face{font-family:"%s";font-style:normal;font-weight:%d;'
        'font-display:swap;src:url("data:font/woff2;base64,%s") format("woff2");'
        'unicode-range:%s}' % (family, weight, b64, urange))

FONT_CSS = "\n".join(faces)
print("вкладено шрифтів: %d, %d КБ сирих" % (len(WANT), total // 1024))

TEMPLATE = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "deck_body.html"), encoding="utf-8").read()
html = TEMPLATE.replace("/*__FONTS__*/", FONT_CSS)
OUT = r"C:\Users\Lenovo\Desktop\Agentic AI\AI-секретар — Demo Day.html"
io.open(OUT, "w", encoding="utf-8").write(html)
print("готово:", OUT, "|", os.path.getsize(OUT) // 1024, "КБ")
