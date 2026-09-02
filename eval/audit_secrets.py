# -*- coding: utf-8 -*-
"""Секрети в історії -- ПО ВСІХ об'єктах, без переліку розширень.

Перший прилад мав перелік розширень і в ньому не було `.ipynb`. Через це він
проґавив ключ OpenRouter, який знайшло push protection GitHub. Урок той самий,
що вже тричі за день: перевірка, звужена вручну, пропускає саме те, чого не
передбачив автор звуження.

Тепер: беремо КОЖЕН блоб історії, пробуємо прочитати як utf-8, і шукаємо
зразки ключів провайдерів. Значення не друкуємо -- лише де і що.
"""
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAXSIZE = 2_000_000

PATTERNS = [
    ("OpenRouter", re.compile(rb"sk-or-v?1?-?[A-Za-z0-9._-]{20,}")),
    ("OpenAI / сумісний", re.compile(rb"sk-(?!or-)[A-Za-z0-9]{32,}")),
    ("Anthropic", re.compile(rb"sk-ant-[A-Za-z0-9._-]{20,}")),
    ("HuggingFace", re.compile(rb"hf_[A-Za-z0-9]{30,}")),
    ("GitHub token", re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("Google API", re.compile(rb"AIza[A-Za-z0-9_-]{30,}")),
    ("AWS", re.compile(rb"AKIA[A-Z0-9]{16}")),
    ("Bearer у коді", re.compile(rb"Bearer\s+[A-Za-z0-9._~+/-]{30,}")),
    ("приватний ключ", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Slack", re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}")),
]

raw = subprocess.run(["git", "cat-file", "--batch-all-objects",
                      "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
                     cwd=ROOT, capture_output=True).stdout.decode()
blobs = []
for line in raw.split("\n"):
    p = line.split()
    if len(p) == 3 and p[1] == "blob" and int(p[2]) <= MAXSIZE:
        blobs.append(p[0])
print(f"блобів усього (до 2 МБ): {len(blobs)}")

# Мапа sha -> шлях, щоб було видно, де саме.
paths = {}
for line in subprocess.run(["git", "rev-list", "--objects", "--all"],
                           cwd=ROOT, capture_output=True).stdout.decode(
                           "utf-8", "replace").split("\n"):
    q = line.strip().split(" ", 1)
    if len(q) == 2:
        paths.setdefault(q[0], q[1])

hits, CH = [], 300
for i in range(0, len(blobs), CH):
    part = blobs[i:i + CH]
    p = subprocess.run(["git", "cat-file", "--batch"], cwd=ROOT,
                       input=("\n".join(part) + "\n").encode(),
                       capture_output=True)
    out, pos = p.stdout, 0
    for sha in part:
        nl = out.find(b"\n", pos)
        if nl < 0:
            break
        h = out[pos:nl].split()
        if len(h) < 3:
            pos = nl + 1
            continue
        size = int(h[2])
        body = out[nl + 1:nl + 1 + size]
        pos = nl + 1 + size + 1
        if b"\x00" in body[:2000]:      # двійковий
            continue
        for name, rx in PATTERNS:
            if rx.search(body):
                hits.append((name, paths.get(sha, "(без шляху)"), sha[:8]))

print(f"знахідок: {len(hits)}\n")
seen = set()
for name, path, sha in sorted(hits):
    if (name, path) in seen:
        continue
    seen.add((name, path))
    print(f"  {name:<20} {path}  (блоб {sha})")
