# -*- coding: utf-8 -*-
"""Git-хеш КОЖНОГО файла на сервері -- щоб перевірити наявність у репозиторії.

Чому саме git-хеш, а не md5: якщо об'єкт із таким sha1 є в репозиторії, то той
самий ВМІСТ у git уже лежить -- байт у байт, у якомусь комміті чи гілці. Це
точна відповідь на «чи не втратимо», а не «схоже на те, що є».

Запускається НА СЕРВЕРІ, друкує рядки «<sha1> <шлях>».
"""
import hashlib
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.expanduser("~/anya/ai-secretary")

SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules",
             ".gradio-cache", ".pytest_cache", ".ruff_cache"}
# Ці теки й файли не забираємо СВІДОМО -- рантайм, секрети, важкі дані.
SKIP_PREFIX = ("logs/", "data/output-demo/", "data/output/", "data/inbox/",
               "data/.gradio-cache/", "models/", "data/eval/embeddings/")
SKIP_EXACT = {".env", "data/jury-guide.html", "data/qr-guest.png",
              "data/qr-jury.png"}


def blob_sha(path):
    data = open(path, "rb").read()
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    for name in filenames:
        full = os.path.join(dirpath, name)
        rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
        if rel in SKIP_EXACT or rel.startswith(SKIP_PREFIX):
            continue
        if ".bak" in name or name.endswith((".pyc", ".log")):
            continue
        try:
            print(blob_sha(full), rel)
        except OSError:
            print("НЕЧИТНИЙ", rel)
