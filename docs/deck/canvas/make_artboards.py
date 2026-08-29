# -*- coding: utf-8 -*-
"""Складає дев'ять артбордів колоди з одного опису.

Нащо скриптом, а не дев'ятьма файлами руками: слайди мусять бути ОДНАКОВІ за
геометрією -- один відступ, один розмір надзаголовка, одна лінійка. Дев'ять
копій розмітки розходяться на першій правці, і потім їх ніхто не вирівнює.
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))

DARK, LIGHT = "#141a10", "#fbfbfa"
ON_DARK, ON_LIGHT = "#e8ebe4", "#111312"
OLIVE, OLIVE_DEEP = "#9fc47a", "#46682f"
DIM_DARK, DIM_LIGHT = "#9aa094", "#61645f"
BODY_DARK, BODY_LIGHT = "#c9cec3", "#3a3d38"
WARN = "#b45309"

HEAD = io.open(os.path.join(HERE, "_head.txt"), encoding="utf-8").read()
TAIL = "</x-dc>\n</body>\n</html>\n"


def slide(dark, kicker, h1, blocks, footer=None, h1_size=64):
    bg, fg = (DARK, ON_DARK) if dark else (LIGHT, ON_LIGHT)
    kc = OLIVE if dark else DIM_LIGHT
    fc = DIM_DARK if dark else DIM_LIGHT
    line = "rgba(255,255,255,.13)" if dark else "#e2e4e0"
    out = [HEAD]
    out.append(
        '<div style="width:1920px;height:1080px;background:%s;color:%s;'
        'font-family:\'IBM Plex Sans\',system-ui,sans-serif;'
        'padding:104px 120px;display:flex;flex-direction:column;'
        'justify-content:center;box-sizing:border-box">' % (bg, fg))
    out.append('<p class="kick" style="color:%s">%s</p>' % (kc, kicker))
    if h1:
        out.append(
            '<h1 style="font-size:%dpx;font-weight:700;letter-spacing:-.02em;'
            'margin:0 0 64px;line-height:1.08;max-width:1560px;'
            'text-wrap:balance">%s</h1>' % (h1_size, h1))
    out.append(''.join(blocks(dark, line, fc)))
    if footer:
        out.append(
            '<div style="margin-top:auto;padding-top:36px;'
            'border-top:1px solid %s"><span class="lab" style="color:%s">%s'
            '</span></div>' % (line, fc, footer))
    out.append("</div>")
    out.append(TAIL)
    return "\n".join(out)


def rows(items):
    """Рядки «мітка -> речення», розділені волосяною лінією.

    Не список із маркерами: маркери на слайді читаються як перелік справ.
    Мітка ліворуч дає око, за яким ведуть, і слайд сканується за секунду.
    """
    def build(dark, line, fc):
        body = BODY_DARK if dark else BODY_LIGHT
        lab = OLIVE if dark else WARN
        parts = ['<div style="display:flex;flex-direction:column;gap:0">']
        for i, (label, text) in enumerate(items):
            if i:
                parts.append('<hr class="rule" style="background:%s">' % line)
            parts.append(
                '<div style="display:flex;gap:56px;align-items:baseline;'
                'padding:26px 0">'
                '<span class="lab" style="color:%s;min-width:330px;flex:none">'
                '%s</span>'
                '<span style="font-size:31px;line-height:1.35;color:%s;'
                'max-width:1080px">%s</span></div>' % (lab, label, body, text))
        parts.append("</div>")
        return parts
    return build


def pairs(items):
    """«обіцяли -> зміряли»: дві колонки, права акцентом."""
    def build(dark, line, fc):
        lab = DIM_DARK if dark else DIM_LIGHT
        acc = OLIVE if dark else OLIVE_DEEP
        parts = ['<div style="display:flex;flex-direction:column;gap:0">']
        for i, (left, right, warn) in enumerate(items):
            if i:
                parts.append('<hr class="rule" style="background:%s">' % line)
            col = WARN if warn else acc
            parts.append(
                '<div style="display:flex;gap:56px;align-items:baseline;'
                'padding:22px 0">'
                '<span class="lab" style="color:%s;min-width:560px;flex:none">'
                '%s</span>'
                '<span style="font-size:31px;font-weight:600;color:%s">%s'
                '</span></div>' % (lab, left, col, right))
        parts.append("</div>")
        return parts
    return build


def stat(big, under, lines):
    """Одне велике число і кілька коротких рядків під ним."""
    def build(dark, line, fc):
        body = BODY_DARK if dark else BODY_LIGHT
        acc = OLIVE if dark else OLIVE_DEEP
        parts = ['<div style="display:flex;gap:96px;align-items:flex-start">']
        parts.append(
            '<div style="flex:none"><div style="font-size:168px;'
            'font-weight:700;line-height:.9;letter-spacing:-.03em;color:%s">'
            '%s</div><div class="lab" style="color:%s;margin-top:20px">%s'
            '</div></div>' % (acc, big, fc, under))
        inner = ['<div style="display:flex;flex-direction:column;gap:0;'
                 'flex:1">']
        for i, t in enumerate(lines):
            if i:
                inner.append('<hr class="rule" style="background:%s">' % line)
            inner.append('<div style="font-size:31px;line-height:1.35;'
                         'color:%s;padding:22px 0">%s</div>' % (body, t))
        inner.append("</div>")
        parts += inner
        parts.append("</div>")
        return parts
    return build


def chain(steps, notes):
    """Ланцюг кроків і три короткі факти під ним."""
    def build(dark, line, fc):
        body = BODY_DARK if dark else BODY_LIGHT
        acc = OLIVE if dark else OLIVE_DEEP
        parts = ['<div style="display:flex;align-items:center;gap:26px;'
                 'flex-wrap:wrap;margin-bottom:72px">']
        for i, (s, strong) in enumerate(steps):
            if i:
                parts.append('<span style="color:%s;opacity:.45;'
                             'font-size:30px">&#8594;</span>' % acc)
            weight = "700" if strong else "400"
            color = (ON_DARK if dark else ON_LIGHT) if strong else acc
            parts.append(
                '<span class="lab" style="font-size:25px;font-weight:%s;'
                'color:%s;letter-spacing:.04em">%s</span>'
                % (weight, color, s))
        parts.append("</div>")
        parts.append('<div style="display:flex;gap:64px">')
        for t in notes:
            parts.append('<div style="flex:1;font-size:29px;line-height:1.4;'
                         'color:%s">%s</div>' % (body, t))
        parts.append("</div>")
        return parts
    return build


def chips(word, items):
    """Слайд-опора: одне слово й чіпи з назвами наступних слайдів."""
    def build(dark, line, fc):
        acc = OLIVE if dark else OLIVE_DEEP
        parts = ['<div style="font-size:200px;font-weight:700;'
                 'letter-spacing:-.04em;line-height:.9;margin-bottom:72px">'
                 '%s</div>' % word]
        parts.append('<div style="display:flex;gap:20px;flex-wrap:wrap">')
        for t in items:
            parts.append(
                '<span class="lab" style="color:%s;border:1px solid %s;'
                'border-radius:999px;padding:16px 30px;font-size:22px">%s'
                '</span>' % (acc, acc, t))
        parts.append("</div>")
        return parts
    return build


import slides as C


def title_slide():
    """Титульний. Назва проєкту й одне речення -- більше тут нічого не треба."""
    out = [HEAD]
    out.append(
        '<div style="width:1920px;height:1080px;background:%s;color:%s;'
        "font-family:'IBM Plex Sans',system-ui,sans-serif;"
        'padding:104px 120px;display:flex;flex-direction:column;'
        'justify-content:center;box-sizing:border-box">' % (DARK, ON_DARK))
    out.append(
        '<svg viewBox="0 0 32 32" fill="%s" style="width:88px;height:88px;'
        'margin-bottom:52px"><path d="M4.6 17.2a11.4 11.4 0 0 1 22.8 0z"/>'
        '<rect x="2.2" y="17.9" width="27.6" height="3.4" rx="1.7"/>'
        '<path d="M7.4 22.1h2.9l1.5 4.1a1.35 1.35 0 0 1-2.5 1z"/>'
        '<path d="M24.6 22.1h-2.9l-1.5 4.1a1.35 1.35 0 0 0 2.5 1z"/></svg>'
        % OLIVE)
    out.append(
        '<div style="font-size:132px;font-weight:700;letter-spacing:-.03em;'
        'line-height:.95">%s</div>' % C.TITLE["name"])
    out.append(
        '<div style="font-size:38px;color:%s;margin-top:30px;max-width:1300px;'
        "font-family:'IBM Plex Mono',ui-monospace,monospace\">%s</div>"
        % (OLIVE, C.TITLE["sub"]))
    out.append('<div style="margin-top:auto;display:flex;gap:64px">')
    for m in C.TITLE["meta"]:
        out.append('<span class="lab" style="color:%s">%s</span>'
                   % (DIM_DARK, m))
    out.append("</div></div>")
    out.append(TAIL)
    return "\n".join(out)


SLIDES = [
    ("Main", title_slide()),

    ("Limitations", slide(
        False, "01 &middot; Limitations", "What the unit should know first.",
        rows(C.LIMITS),
        "All four are visible on the live system right now.")),

    ("NotYet", slide(
        True, "02 &middot; Not built yet", "Named, with what each one needs.",
        rows(C.NOT_YET),
        "From the project description &#8212; the same list we work from.")),

    ("Promised", slide(
        False, "03 &middot; Promised, and measured",
        "&#8220;Success is not model accuracy. It is what the answer is made "
        "of.&#8221;",
        pairs(C.PROMISED),
        "Live numbers: the system&#39;s own statistics page.", h1_size=52)),

    ("Architecture", slide(
        True, "04 &middot; How an answer is made",
        "31 verified queries. The model never touches the database.",
        rows(C.TIERS),
        "0.07 s for a count &middot; 29 s for a question about a regulation",
        h1_size=56)),

    ("QA", slide(
        False, "05 &middot; Questions", None,
        chips("Q&amp;A", ["How we tested it", "Data access", "Extending it",
                          "Model safety"]),
        "QR to the live system &#8212; statistics, chat, upload.")),

    ("Harness", slide(
        True, "06 &middot; How we tested it", None,
        stat("31", "defects, found by us", C.TESTED),
        "Each number is one command away from being reproduced.")),

    ("DataAccess", slide(
        False, "07 &middot; Data access", "Nothing leaves the network.",
        rows(C.DATA),
        "Our pages make no external requests &#8212; even the fonts are "
        "local.")),

    ("Extending", slide(
        True, "08 &middot; Extending it", None,
        stat("1 day", "for a new document type", C.EXTENDING),
        "Two document types today. A working system, not a platform.")),

    ("ModelSafety", slide(
        False, "09 &middot; Model safety",
        "48 attacks measured. The model never writes the answer.",
        rows(C.MODEL),
        "Open: an instruction hidden inside a document&#39;s own text.",
        h1_size=56)),
]

for name, html in SLIDES:
    path = os.path.join(HERE, name + ".dc.html")
    io.open(path, "w", encoding="utf-8", newline="\n").write(html)
    print("  %-16s %5d байт" % (name + ".dc.html", len(html)))

CANVAS = {
    "artboards": [],
    "launch": {"view": "canvas"},
}
for i, (name, _h) in enumerate(SLIDES):
    CANVAS["artboards"].append({
        "file": name + ".dc.html",
        "x": (i % 3) * 2120,
        "y": (i // 3) * 1340,
        "w": 1920, "h": 1080,
    })
import json
io.open(os.path.join(HERE, "canvas.json"), "w", encoding="utf-8",
        newline="\n").write(json.dumps(CANVAS, ensure_ascii=False, indent=2))
print("canvas.json: %d артбордів, сітка 3x3" % len(SLIDES))
