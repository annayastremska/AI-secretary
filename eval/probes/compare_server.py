# -*- coding: utf-8 -*-
"""Чи є на сервері ХОЧ ОДИН файл, змінений не мною локально.

Головне питання перед переносом: сервер -- це копія мого дерева (тоді
переносити нічого) або там є правки, зроблені прямо на ньому (тоді вони
єдині в світі й можуть загубитись 31.08).

Порівнюємо md5 змінених файлів: сервер проти мого РОБОЧОГО дерева.
"""
import hashlib
import io
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = "C:/Users/Lenovo/Desktop/Agentic AI/project"
SSH = ["ssh", "-p", "7301", "-i",
       os.path.expanduser("~/.ssh/kse-h100"), "ubuntu@185.9.41.1"]


def sh(cmd):
    return subprocess.run(SSH + [cmd], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


modified = [l[3:].strip() for l in sh(
    "cd ~/anya/ai-secretary && git status --porcelain").split("\n")
    if l.startswith(" M") or l.startswith("M ")]
print("змінених на сервері:", len(modified))

remote = {}
for chunk in [modified[i:i + 20] for i in range(0, len(modified), 20)]:
    out = sh("cd ~/anya/ai-secretary && md5sum " +
             " ".join("'%s'" % p for p in chunk))
    for line in out.split("\n"):
        if "  " in line:
            h, p = line.split("  ", 1)
            remote[p.strip()] = h.strip()

same, diff, missing = [], [], []
for p in modified:
    local = os.path.join(ROOT, p)
    if not os.path.exists(local):
        missing.append(p)
        continue
    h = hashlib.md5(io.open(local, "rb").read()).hexdigest()
    (same if h == remote.get(p) else diff).append(p)

print("однакові з моїм робочим деревом:", len(same))
print("ВІДРІЗНЯЮТЬСЯ:", len(diff))
for p in diff:
    print("   ", p)
if missing:
    print("немає локально:", missing)
