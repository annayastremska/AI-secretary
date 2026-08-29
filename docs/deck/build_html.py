# -*- coding: utf-8 -*-
"""Збирає ту саму колоду одним HTML-файлом, який відкривається двічним клацанням.

Джерело -- ті самі артборди, що й на канві (`docs/deck/canvas/*.dc.html`), тобто
двох версій змісту не існує: правиш артборд, перезбираєш обидва виходи.

Шрифти вкладаються в base64, а посилання на Google Fonts вирізається: файл
відкривають з флешки й з чужого ноутбука, а той, що тягне шрифт із мережі,
ламається МОВЧКИ -- лишається системний шрифт, і розкладка з'їжджає.

Слайд має фіксовані 1920x1080 (так їх намальовано), тому в браузері він
масштабується цілим -- один множник на весь слайд. Так пропорції не поїдуть на
жодному екрані.
"""
import base64
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
CANVAS = os.path.join(HERE, "canvas")
FONTS = (r"C:\Users\Lenovo\Desktop\Agentic AI"
         r"\AI-секретар Design System-handoff\ai-design-system\project"
         r"\assets\fonts")
OUT = r"C:\Users\Lenovo\Desktop\Agentic AI\AI-секретар — Demo Day.html"

WANT = [
    ("IBM Plex Sans", 400, "plexsans-400-latin.woff2"),
    ("IBM Plex Sans", 600, "plexsans-600-latin.woff2"),
    ("IBM Plex Sans", 700, "plexsans-700-latin.woff2"),
    ("IBM Plex Mono", 400, "plexmono-400-latin.woff2"),
]
# Mono-600 у хендофі немає, і підставляти під нього файл 400 не можна: браузер
# повірить оголошенню і намалює звичайну товщину там, де в макеті напівжирна.
# Без оголошення він досинтезує жирність сам -- видно, що це саме напівжирне.

faces = []
for family, weight, fname in WANT:
    raw = io.open(os.path.join(FONTS, fname), "rb").read()
    faces.append('@font-face{font-family:"%s";font-style:normal;'
                 'font-weight:%d;font-display:block;'
                 'src:url("data:font/woff2;base64,%s") format("woff2")}'
                 % (family, weight, base64.b64encode(raw).decode("ascii")))

order = [a["file"] for a in json.load(
    io.open(os.path.join(CANVAS, "canvas.json"), encoding="utf-8"))["artboards"]]

#: Де шукати картинки, на які артборд посилається по імені. На канві вони
#: лежать активами всередині сторінки, а тут мусять стати data-URI.
IMAGE_DIRS = [
    CANVAS,
    (r"C:\Users\Lenovo\AppData\Local\Temp\claude"
     r"\C--Users-Lenovo-Desktop-Agentic-AI-project"
     r"\9b3504e2-1d5c-4293-9968-4b091ff02f95\scratchpad"),
]
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml"}


def inline_images(html, where):
    """Вкладає картинки в сам файл.

    Ненайдена картинка тут -- ПАДІННЯ, не попередження: у браузері вона дає
    просто порожню рамку, тобто збій, який видно лише оком і лише на показі.
    """
    def sub(m):
        quote, name = m.group(1), m.group(2)
        if "//" in name or name.startswith("data:"):
            return m.group(0)
        base = os.path.basename(name)
        for d in IMAGE_DIRS:
            p = os.path.join(d, base)
            if os.path.exists(p):
                mime = MIME.get(os.path.splitext(base)[1].lower())
                if not mime:
                    raise SystemExit("невідомий тип картинки: " + base)
                b64 = base64.b64encode(io.open(p, "rb").read()).decode("ascii")
                return 'src=%s%s%s' % (quote, "data:%s;base64,%s" % (mime, b64),
                                       quote)
        raise SystemExit("картинки %s (з %s) немає в жодній із %s"
                         % (base, where, IMAGE_DIRS))

    return re.sub(r'src=(["\'])([^"\']+)\1', sub, html)

def scope(css, name):
    """Приписує кожному селектору артборда його власний слайд.

    Це не косметика. На канві кожен артборд -- окремий iframe, тому `.li`
    у Risk і `.li` у Local ніяк не бачать одне одного. В одному файлі вони
    опиняються в одній голові й ТИХО перебивають одне одного: нічого не
    падає, просто слайд виглядає не так, як на канві.
    """
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sels, body = m.group(1).strip(), m.group(2).strip()
        if not sels or sels.startswith("@") or sels == "body":
            continue
        sels = ",".join('.slide[data-name="%s"] %s' % (name, s.strip())
                        for s in sels.split(","))
        out.append("%s{%s}" % (sels, body))
    return "\n".join(out)


slides, styles = [], []
for fname in order:
    src = io.open(os.path.join(CANVAS, fname), encoding="utf-8").read()
    name = fname.replace(".dc.html", "")
    # стилі артборда (з <helmet>) -- у спільну голову, посилання на шрифти геть
    m = re.search(r"<style>(.*?)</style>", src, re.S)
    if m:
        scoped = scope(m.group(1), name)
        if scoped:
            styles.append("/* %s */\n%s" % (fname, scoped))
    # сам слайд: перший div із фіксованою шириною й усе до кінця x-dc
    body = src.split("</helmet>", 1)[1]
    body = body.rsplit("</x-dc>", 1)[0].strip()
    slides.append('<section class="slide" data-name="%s">%s</section>'
                  % (name, inline_images(body, fname)))

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI-Secretary — Demo Day</title>
<style>
%(fonts)s
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:#0b0d0a;overflow:hidden}
.deck{position:fixed;inset:0}
.slide{position:absolute;top:0;left:0;width:1920px;height:1080px;
  transform-origin:top left;display:none}
.slide.on{display:block}
.counter{position:fixed;right:18px;bottom:14px;z-index:9;
  font:600 13px/1 "IBM Plex Mono",monospace;color:#6d7268;letter-spacing:.1em}
%(styles)s
@media print{
  html,body{background:#fff;overflow:visible}
  .deck{position:static}
  .slide{display:block!important;position:relative;transform:none!important;
    page-break-after:always;margin:0}
  .counter{display:none}
}
</style>
</head>
<body>
<div class="deck" id="deck">
%(slides)s
</div>
<div class="counter" id="counter"></div>
<script>
/* Один слайд на екран, масштабується цілим. Стрілки, пробіл, клац. */
(function () {
  var slides = [].slice.call(document.querySelectorAll(".slide"));
  var counter = document.getElementById("counter");
  var i = 0;

  function fit() {
    var k = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    var dx = (window.innerWidth - 1920 * k) / 2;
    var dy = (window.innerHeight - 1080 * k) / 2;
    slides.forEach(function (s) {
      s.style.transform = "translate(" + dx + "px," + dy + "px) scale(" + k + ")";
    });
  }
  function show(n) {
    i = Math.max(0, Math.min(slides.length - 1, n));
    slides.forEach(function (s, k) { s.classList.toggle("on", k === i); });
    counter.textContent = (i + 1) + " / " + slides.length;
  }
  window.addEventListener("resize", fit);
  document.addEventListener("keydown", function (e) {
    var k = e.key;
    if (k === "ArrowRight" || k === "ArrowDown" || k === " " || k === "PageDown") {
      e.preventDefault(); show(i + 1);
    } else if (k === "ArrowLeft" || k === "ArrowUp" || k === "PageUp") {
      e.preventDefault(); show(i - 1);
    } else if (k === "Home") { show(0); }
    else if (k === "End") { show(slides.length - 1); }
  });
  document.addEventListener("click", function (e) {
    show(i + (e.clientX < window.innerWidth * 0.25 ? -1 : 1));
  });
  fit(); show(0);
})();
</script>
</body>
</html>
""" % {"fonts": "\n".join(faces),
       "styles": "\n".join(styles),
       "slides": "\n".join(slides)}

io.open(OUT, "w", encoding="utf-8").write(HTML)
print("слайдів:", len(slides), "| файл:", OUT, "|",
      os.path.getsize(OUT) // 1024, "КБ")
