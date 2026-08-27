"""Порівнює ЗНАЧЕННЯ фактів у .md і в базі для чотирьох документів."""
import os, re, yaml, psycopg
from dotenv import load_dotenv
load_dotenv(".env")
D = os.path.expanduser("~/anya/ai-secretary/data/output-demo/documents")
PAIRS = {9: "deployment/22b01a5b-637d-45cd-a588-f57a9e3b52db.md",
         30: "deployment/6f7ffcc7-cd13-43b7-84d9-52d36bfa743c.md",
         32: "deployment/746d5b0c-5037-4b8f-93b9-1171abe9936e.md",
         147: "leave/e8263c52-8b3c-4a5d-995d-e54e47a30ed1.md"}
with psycopg.connect(os.environ["DATABASE_URL"].replace("postgresql+psycopg://","postgresql://")) as c, c.cursor() as cur:
    for doc_id, rel in PAIRS.items():
        raw = open(os.path.join(D, rel), encoding="utf-8").read()
        m = re.match(r"---\n(.*?)\n---\n", raw, re.S)
        meta = yaml.safe_load(m.group(1))
        md = {}
        for f in meta.get("facts") or []:
            md[f.get("fact_type")] = (str(f.get("value_code")), bool(f.get("confirmed")))
        cur.execute("""SELECT dm.code, f.value, f.status FROM fact_sources s
                         JOIN facts f ON f.id=s.fact_id
                         JOIN dimensions dm ON dm.id=f.dimension_id
                        WHERE s.document_id=%s ORDER BY dm.code""", (doc_id,))
        db = {code: (val, st) for code, val, st in cur.fetchall()}
        val_diff = [(k, md[k][0], db.get(k, ("—",))[0]) for k in md
                    if k in db and md[k][0] != db[k][0]]
        st_diff = [k for k in md if k in db
                   and md[k][1] != (db[k][1] == "confirmed")]
        only_md = sorted(set(md) - set(db))
        only_db = sorted(set(db) - set(md))
        print(f"doc {doc_id}: у .md {len(md)} фактів, у базі {len(db)}")
        print(f"   ЗНАЧЕННЯ розійшлись: {len(val_diff)}  {val_diff[:3]}")
        print(f"   СТАТУС розійшовся:   {len(st_diff)}  {st_diff[:6]}")
        if only_md: print(f"   є лише в .md:  {only_md}")
        if only_db: print(f"   є лише в базі: {only_db}")
