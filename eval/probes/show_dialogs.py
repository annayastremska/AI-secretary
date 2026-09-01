# -*- coding: utf-8 -*-
"""Показати другі ходи розмов: текст відповіді + SQL, якщо був."""
import io
import json
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

for line in io.open("/tmp/free-sql-dialogs.jsonl", encoding="utf-8"):
    r = json.loads(line)
    if r["turn"] != 2:
        continue
    body = re.sub(r"<details.*?</details>", "", r["reply"], flags=re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    body = " ".join(body.split())
    print("=" * 76)
    print("%s | %s | %.1f с" % (r["dialog"],
                                "ВІЛЬНИЙ SQL" if r["free"] else "шаблон",
                                r["sec"]))
    print("   питання:", r["q"])
    print("   ->", body[:300])
    if r["sql"]:
        print("   SQL:", " ".join(r["sql"].split())[:240])
