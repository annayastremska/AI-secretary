# -*- coding: utf-8 -*-
"""Чи є кожен серверний файл у git АБО в моєму робочому дереві.

Три можливі відповіді на файл:
  у git        -- об'єкт із таким sha1 у репозиторії є (байт у байт);
  у дереві     -- у git об'єкта немає, але той самий вміст лежить у мене
                  локально (тобто не втратиться, просто ще не закомічений);
  НІДЕ         -- існує лише на сервері. Оце і є відповідь на питання Ані.
"""
import hashlib
import io
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = "C:/Users/Lenovo/Desktop/Agentic AI/project"
SRC = "C:/Users/Lenovo/AppData/Local/Temp/claude/server_blobs.txt"
OUT = "C:/Users/Lenovo/AppData/Local/Temp/claude/blob_report.md"

rows = []
for line in io.open(SRC, encoding="utf-8"):
    parts = line.rstrip("\n").split(" ", 1)
    if len(parts) == 2 and len(parts[0]) == 40:
        rows.append((parts[0], parts[1]))

# 1. Одним викликом питаємо git, які з цих об'єктів у нього є.
inp = "\n".join(sha for sha, _ in rows) + "\n"
res = subprocess.run(["git", "cat-file", "--batch-check"], input=inp,
                     capture_output=True, text=True, cwd=ROOT)
known = set()
for out_line, (sha, _) in zip(res.stdout.strip().split("\n"), rows):
    if " blob " in out_line:
        known.add(sha)


def local_sha(rel):
    p = os.path.join(ROOT, rel.replace("/", os.sep))
    if not os.path.exists(p):
        return None
    data = io.open(p, "rb").read()
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


in_git, in_tree, nowhere = [], [], []
for sha, rel in rows:
    if sha in known:
        in_git.append(rel)
    elif local_sha(rel) == sha:
        in_tree.append(rel)
    else:
        nowhere.append(rel)

with io.open(OUT, "w", encoding="utf-8") as fh:
    fh.write("# Чи все забрали з сервера\n\n")
    fh.write("Файлів перевірено: %d\n\n" % len(rows))
    fh.write("- у git (байт у байт): **%d**\n" % len(in_git))
    fh.write("- у моєму дереві, ще не закомічені: **%d**\n" % len(in_tree))
    fh.write("- НІДЕ, крім сервера: **%d**\n\n" % len(nowhere))
    for title, group in (("Не закомічені (у дереві є)", in_tree),
                         ("ІСНУЮТЬ ЛИШЕ НА СЕРВЕРІ", nowhere)):
        fh.write("## %s — %d\n\n" % (title, len(group)))
        for p in sorted(group):
            fh.write("- `%s`\n" % p)
        fh.write("\n")

print("у git:", len(in_git), "| у дереві:", len(in_tree),
      "| ніде:", len(nowhere))
for p in sorted(nowhere):
    print("   НІДЕ:", p)
for p in sorted(in_tree):
    print("   у дереві:", p)
