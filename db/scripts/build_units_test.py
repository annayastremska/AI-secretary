"""Складає одиниці у ТЕСТОВУ схему й рахує для них ембеддинги.

Запуск:
    python db/scripts/build_units_test.py --load          # нарізати й записати
    python db/scripts/build_units_test.py --embed         # порахувати вектори
    python db/scripts/build_units_test.py --drop          # прибрати все

## Чому окрема схема, а не таблиця поруч

База спільна, і з неї читає апка. Усе тут живе в схемі `andriy_test`, тобто
одним `DROP SCHEMA ... CASCADE` прибирається без слідів. Наявних таблиць
(`documents`, `document_chunks`, `facts`) скрипт НЕ торкається -- лише читає
`documents.text_content`.

## Запобіжники на GPU

Сервер спільний, і на карті вже сидить чужий процес. Тому:

* перед стартом перевіряємо вільну пам'ять і відмовляємось, якщо мало;
* `set_per_process_memory_fraction` ставить стелю НА СВІЙ процес -- при
  помилці впаде він, а не сусід;
* модель дрібна (e5-small, 118 млн параметрів, ~0.5 ГБ), тобто це не той
  порядок, що vLLM з його резервуванням 90% карти.
"""
import argparse
import os
import sys
import time

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "db", "scripts"))

import segment_documents as S  # noqa: E402

SCHEMA = "andriy_test"
MODEL_NAME = "intfloat/multilingual-e5-small"
PASSAGE_PREFIX = "passage: "
BATCH = 64
MIN_FREE_GIB = 5.0
MEM_FRACTION = 0.10          # ~8 ГБ зі 80 -- стеля на власний процес

DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};

CREATE TABLE IF NOT EXISTS {SCHEMA}.document_units (
    id                bigserial PRIMARY KEY,
    document_id       bigint NOT NULL,
    ord               integer NOT NULL,
    label             text NOT NULL,
    base_label        text NOT NULL,
    parent_label      text,
    char_start        integer NOT NULL,
    char_end          integer NOT NULL,
    from_length_split boolean NOT NULL DEFAULT false,
    text              text NOT NULL,
    tsv               tsvector,
    embedding         public.vector(384),
    UNIQUE (document_id, ord)
);

CREATE INDEX IF NOT EXISTS units_tsv_idx  ON {SCHEMA}.document_units USING GIN (tsv);
CREATE INDEX IF NOT EXISTS units_doc_idx  ON {SCHEMA}.document_units (document_id);
CREATE INDEX IF NOT EXISTS units_base_idx ON {SCHEMA}.document_units (document_id, base_label);
"""

# HNSW окремо: на порожній таблиці він дешевий, але створювати його треба
# ПІСЛЯ вставки векторів, інакше індексується порожнеча.
HNSW = f"""
CREATE INDEX IF NOT EXISTS units_emb_idx ON {SCHEMA}.document_units
    USING hnsw (embedding public.vector_cosine_ops)
"""


def dsn():
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    return os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")


def do_load(rule):
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute(f"TRUNCATE {SCHEMA}.document_units")
        cur.execute("""SELECT id, text_content FROM documents
                        WHERE domain='normative' AND text_content IS NOT NULL
                        ORDER BY id""")
        docs = cur.fetchall()
        total = 0
        for doc_id, text in docs:
            _, units = S.segment(text, rule)
            for ord_, u in enumerate(units):
                cur.execute(f"""
                    INSERT INTO {SCHEMA}.document_units
                        (document_id, ord, label, base_label, parent_label,
                         char_start, char_end, from_length_split, text, tsv)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            to_tsvector('ukrainian', %s))
                """, (doc_id, ord_, u["label"], u["base_label"], u["parent"],
                      u["char_start"], u["char_end"],
                      S.SPLIT_MARK in u["label"], u["text"], u["text"]))
            total += len(units)
        conn.commit()
    print(f"документів {len(docs)}, одиниць записано {total}")


def load_encoder():
    import torch
    from transformers import AutoModel, AutoTokenizer
    if not torch.cuda.is_available():
        print("GPU немає -- рахую на CPU (повільно, але нікому не шкодить)")
        device = "cpu"
    else:
        free, total = torch.cuda.mem_get_info()
        free_gib = free / 1024**3
        print(f"GPU: вільно {free_gib:.1f} ГіБ із {total/1024**3:.1f}")
        if free_gib < MIN_FREE_GIB:
            raise SystemExit(f"вільно менше за {MIN_FREE_GIB} ГіБ -- не стартую, "
                             "щоб не зачепити чужий процес")
        # Стеля НА СВІЙ процес: при помилці впаде він, а не сусід.
        torch.cuda.set_per_process_memory_fraction(MEM_FRACTION)
        device = "cuda"

    tok = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModel.from_pretrained(MODEL_NAME).eval().to(device)

    def encode(texts):
        enc = tok(texts, padding=True, truncation=True, max_length=512,
                  return_tensors="pt").to(device)
        with torch.no_grad():
            out = model(**enc).last_hidden_state
        mask = enc["attention_mask"].unsqueeze(-1).float()
        emb = (out * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
        return torch.nn.functional.normalize(emb, p=2, dim=1).tolist()

    return encode


def do_embed():
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {SCHEMA}.document_units WHERE embedding IS NULL")
        todo = cur.fetchone()[0]
    if not todo:
        print("усі одиниці вже мають вектори")
        return
    print(f"рахувати вектори для {todo} одиниць")
    encode = load_encoder()

    t0, done = time.time(), 0
    while True:
        with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
            cur.execute(f"""SELECT id, text FROM {SCHEMA}.document_units
                             WHERE embedding IS NULL ORDER BY id LIMIT %s""", (BATCH,))
            rows = cur.fetchall()
            if not rows:
                break
            vecs = encode([PASSAGE_PREFIX + t for _, t in rows])
            for (uid, _), v in zip(rows, vecs):
                cur.execute(f"UPDATE {SCHEMA}.document_units SET embedding = %s::public.vector "
                            f"WHERE id = %s", (str(v), uid))
            conn.commit()
        done += len(rows)
        el = time.time() - t0
        if done % (BATCH * 10) == 0 or done >= todo:
            print(f"  {done}/{todo}  {done/el:.0f} од./с  "
                  f"залишилось ~{(todo-done)/(done/el)/60:.1f} хв")
    print(f"готово за {(time.time()-t0)/60:.1f} хв")

    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        t1 = time.time()
        cur.execute(HNSW)
        conn.commit()
        print(f"HNSW-індекс побудовано за {time.time()-t1:.0f} с")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--embed", action="store_true")
    ap.add_argument("--drop", action="store_true")
    ap.add_argument("--rule", default="nest")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args(argv)

    if args.drop:
        with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
            conn.commit()
        print(f"схему {SCHEMA} прибрано")
        return 0
    if args.load:
        do_load(args.rule)
    if args.embed:
        do_embed()
    if args.stats or not (args.load or args.embed):
        with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
            cur.execute(f"""SELECT count(*), count(embedding),
                                   count(*) FILTER (WHERE from_length_split),
                                   pg_size_pretty(pg_total_relation_size(
                                       '{SCHEMA}.document_units'))
                              FROM {SCHEMA}.document_units""")
            n, emb, spl, size = cur.fetchone()
            print(f"одиниць {n}, з вектором {emb}, порізаних довжиною {spl}, розмір {size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
