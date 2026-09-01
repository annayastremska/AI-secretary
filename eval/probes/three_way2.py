# -*- coding: utf-8 -*-
"""Звірка трьох версій -- лише КОД і лише два важливі випадки.

Перша версія показала 398 файлів, з яких 297 -- синтетичні зразки .docx, які
Андрій просто не тримає у своїй гілці. Це шум: він приховує сигнал.

Сигнал тут рівно два:
  А) у нього == на сервері, у мене ІНШЕ -> я застаріла, треба забрати;
  Б) сервер відрізняється від обох -> на сервері є щось унікальне.
"""
import hashlib
import io
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = "C:/Users/Lenovo/Desktop/Agentic AI/project"
OUT = "C:/Users/Lenovo/AppData/Local/Temp/claude/three_way2.md"
SSH = ["ssh", "-p", "7301", "-i", os.path.expanduser("~/.ssh/kse-h100"),
       "ubuntu@185.9.41.1"]
CODE = (".py", ".html", ".js", ".css", ".yaml", ".yml")
SKIP = ("data/eval/samples/", "data/eval/", "docs/deck/")


def md5(b):
    return hashlib.md5(b).hexdigest() if b is not None else None


files = [l for l in subprocess.run(
    ["git", "diff", "--name-only", "origin/anya-pipeline", "origin/andriy-db"],
    capture_output=True, text=True, encoding="utf-8", cwd=ROOT).stdout.split("\n")
    if l.strip() and l.endswith(CODE) and not l.startswith(SKIP)]

srv = {}
for i in range(0, len(files), 25):
    cmd = ("cd ~/anya/ai-secretary && md5sum "
           + " ".join("'%s'" % p for p in files[i:i + 25]) + " 2>/dev/null")
    for line in subprocess.run(SSH + [cmd], capture_output=True, text=True,
                               encoding="utf-8", errors="replace").stdout.split("\n"):
        if "  " in line:
            h, p = line.split("  ", 1)
            srv[p.strip()] = h.strip()

stale, unique, mine_newer, same = [], [], [], []
for p in files:
    lp = os.path.join(ROOT, p)
    lh = md5(io.open(lp, "rb").read()) if os.path.exists(lp) else None
    r = subprocess.run(["git", "show", "origin/andriy-db:" + p],
                       capture_output=True, cwd=ROOT)
    hh = md5(r.stdout) if r.returncode == 0 else None
    sh = srv.get(p)
    if lh == hh == sh and lh:
        same.append(p)
    elif sh and hh and sh == hh and lh != hh:
        stale.append(p)
    elif sh and sh != lh and sh != hh:
        unique.append(p)
    elif lh and sh == lh and hh != lh:
        mine_newer.append(p)

with io.open(OUT, "w", encoding="utf-8") as fh:
    fh.write("# Звірка коду: я / Андрій / сервер\n\n")
    fh.write("Файлів коду на спільній поверхні: %d\n\n" % len(files))
    for title, group, what in [
        ("Я ЗАСТАРІЛА (у нього == на сервері, у мене інше)", stale,
         "забрати з його гілки"),
        ("СЕРВЕР УНІКАЛЬНИЙ (відрізняється від обох)", unique,
         "перенести в git або визнати непотрібним"),
        ("У мене == на сервері, у нього інше", mine_newer,
         "нормально: це наша зона або він її не тримає"),
        ("Однакові в усіх трьох", same, "нічого робити"),
    ]:
        fh.write("## %s — %d\n_%s_\n\n" % (title, len(group), what))
        for p in group:
            fh.write("- `%s`\n" % p)
        fh.write("\n")
print(io.open(OUT, encoding="utf-8").read())
