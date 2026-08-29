# -*- coding: utf-8 -*-
"""Міряє текст слайдів проти docs/contracts/2026-08-29_slide-text-pravyla.md.

Ловить П1 (художні формулювання), П2 (завуальовані фрази), П3 (заголовок >4
слів), П4 (довге речення) і П8 (згадка інших слайдів). П5, П7, П9 приладом не
міряються -- це око.

Прилад міряє САМ ТЕКСТ артборда, не HTML: теги знімаються, сутності
розкриваються. Інакше він рахував би слова в назвах кольорів -- один раз мій
аудит docx уже так збрехав.

    python eval/audit_slides.py [--dir docs/deck/canvas]
"""
import argparse
import glob
import html
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

HEAD_MAX_WORDS = 4      # П3
SENT_MAX_WORDS = 14     # П4: довше речення на слайді не читають

# П1: сліди художнього. Лапки навколо цілої думки, «не X, а Y» як гасло,
# двокрапка-афоризм, риторичні «інакше/бо тоді».
ART = [
    # лапки караються лише навколо ЦІЛОЇ думки. Коротка цитата в лапках --
    # це рядок, який система показує на екрані («no data»), тобто факт.
    (r"[«“\"][^»”\"]*(?:\s+\S+){3,}[»”\"]", "лапки -- цитата або гасло"),
    (r"\bis not\b.{0,40}\bit is\b", "конструкція «не X, а Y» -- гасло"),
    (r"\bgoes back to\b|\bwould rather\b|\bat the end of the day\b",
     "риторичний зворот"),
    (r"\bbelongs on\b|\bis the price\b|\bcalls nobody\b|\bunknown territory\b",
     "метафора"),
    (r"\bThat is the whole\b|\bnot a feature we added\b|\bin other words\b",
     "обрамлення факту, не факт"),
]

# П2: слова, які звучать як оцінка, але не називають ні що, ні скільки.
VEIL = [
    "sharper", "better", "robust", "seamless", "powerful", "smart",
    "leverage", "cutting-edge", "state of the art", "next-generation",
    "can be improved", "room to grow", "solid", "meaningful",
]

# П8: слайд не називає інші слайди.
NEXT = [r"\bnext slide\b", r"\bthe following slide\b", r"\bsee slide\b",
        r"\bas we will show\b", r"\blater in this deck\b"]


def text_of(path):
    """Тільки видимий текст: без <helmet>, тегів, svg і сутностей."""
    src = io.open(path, encoding="utf-8").read()
    src = src.split("</helmet>", 1)[-1]
    src = re.sub(r"<svg\b.*?</svg>", " ", src, flags=re.S | re.I)
    src = re.sub(r"<style\b.*?</style>", " ", src, flags=re.S | re.I)
    # межа блоку -- це межа речення, інакше сусідні рядки склеюються в одне
    src = re.sub(r"<(br|/div|/section|/li|/p)\b[^>]*>", "\n", src, flags=re.I)
    src = re.sub(r"<[^>]+>", "", src)
    lines = [html.unescape(l).strip() for l in src.split("\n")]
    return [l for l in lines if l]


def words(s):
    return [w for w in re.split(r"[\s‒-―-]+", s) if re.search(r"\w", w)]


def sentences(line):
    return [s.strip() for s in re.split(r"(?<=[.!?:])\s+", line) if s.strip()]


def audit(path):
    lines = text_of(path)
    bad = []
    if not lines:
        return [("П0", "у файлі немає видимого тексту")]

    # заголовок -- саме ПЕРШИЙ рядок. Спершу я брав перший рядок довший за
    # одне слово -- і прилад міряв підзаголовок QA, де заголовок «Questions».
    head = lines[0]
    if len(words(head)) > HEAD_MAX_WORDS:
        bad.append(("П3", "заголовок %d слів: %s" % (len(words(head)), head)))

    for line in lines:
        low = line.lower()
        for rx, why in ART:
            if re.search(rx, line, re.I):
                bad.append(("П1", "%s: %s" % (why, line)))
        for v in VEIL:
            if re.search(r"\b%s\b" % re.escape(v), low):
                bad.append(("П2", "завуальоване «%s»: %s" % (v, line)))
        for rx in NEXT:
            if re.search(rx, low):
                bad.append(("П8", "згадка іншого слайда: %s" % line))
        for s in sentences(line):
            n = len(words(s))
            if n > SENT_MAX_WORDS:
                bad.append(("П4", "речення %d слів: %s" % (n, s)))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join("docs", "deck", "canvas"))
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(a.dir, "*.dc.html")))
    if not files:
        print("артбордів не знайдено в", a.dir)
        return 2

    total = 0
    for f in files:
        bad = audit(f)
        total += len(bad)
        name = os.path.basename(f)
        if bad:
            print("\n%s -- %d" % (name, len(bad)))
            for rule, why in bad:
                print("  %s  %s" % (rule, why))
        else:
            print("%s -- чисто" % name)

    print("\nартбордів: %d | порушень: %d" % (len(files), total))
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
