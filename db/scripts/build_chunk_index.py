"""Нарізає нормативні документи на фрагменти й рахує ембеддинги.

Запуск (потрібен .venv-ml -- там torch):
    .venv-ml/Scripts/python db/scripts/build_chunk_index.py
    .venv-ml/Scripts/python db/scripts/build_chunk_index.py --chunks-only
    .venv-ml/Scripts/python db/scripts/build_chunk_index.py --rebuild

Нарізка -- по межах абзаців із перекриттям. Не «кожні N символів»: розрив
посеред речення псує і лексичний, і векторний збіг, а перекриття закриває
випадок, коли відповідь лежить на межі двох фрагментів.

Розмір фрагмента -- компроміс, а не оптимум. Занадто дрібний губить контекст
(«крок 3 без кроку 1» -- заперечення з нашого дизайну, §5.2), занадто
великий розмиває вектор: ембеддинг довгого тексту -- це середнє по кількох
темах, і він однаково погано схожий на все. 1200 символів приблизно
відповідає 300-400 токенам, тобто влазить у вікно e5 (512) із запасом на
префікс.

**Про модель.** e5-small обрано, щоб конвеєр працював і був вимірюваний:
118 млн параметрів, 384 виміри, індексація корпусу -- хвилини на CPU.
Командне дослідження (`docs/research/2026-08-08_embedding-model-selection/`)
рекомендує сильнішу `snowflake-arctic-embed-l-v2.0`; перехід -- це зміна
MODEL_NAME, нова міграція під 1024 виміри і переіндексація. Свідомо
відкладено, а не забуто.

**Префікси e5 обов'язкові.** Модель тренована з `query:` та `passage:`, і
без них якість падає без жодної помилки в логах -- найгірший вид поломки.
Тому вони тут в константах, а не в рядку виклику.
"""
import argparse
import os
import re
import sys
import time

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MODEL_NAME = "intfloat/multilingual-e5-small"
EMBEDDING_DIM = 384          # мусить збігатися з vector(N) у міграції
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200
PASSAGE_PREFIX = "passage: "
QUERY_PREFIX = "query: "     # для пошуку -- у search_hybrid.py
BATCH = 32


def split_paragraphs(text: str):
    """Абзаци з їхніми зсувами в оригіналі -- зсуви потрібні, щоб цитату
    можна було показати в документі, а не переказати."""
    out, pos = [], 0
    for part in re.split(r"(\n\s*\n)", text):
        if part and not part.isspace():
            out.append((pos, part))
        pos += len(part)
    return out


def chunk_text(text: str, size=CHUNK_CHARS, overlap=CHUNK_OVERLAP):
    """Склеює абзаци до `size`, наступний фрагмент починається з хвоста
    попереднього (`overlap`). Абзац, довший за size, ріжеться сам -- інакше
    один довгий блок з'їдав би цілий фрагмент."""
    chunks = []
    buf, buf_start = "", None
    for start, para in split_paragraphs(text):
        para = para.strip()
        if not para:
            continue
        if len(para) > size:
            if buf:
                chunks.append((buf_start, buf))
                buf, buf_start = "", None
            for i in range(0, len(para), size - overlap):
                piece = para[i:i + size]
                if piece.strip():
                    chunks.append((start + i, piece))
            continue
        if buf and len(buf) + len(para) + 1 > size:
            chunks.append((buf_start, buf))
            tail = buf[-overlap:] if overlap else ""
            buf = (tail + "\n" + para) if tail else para
            buf_start = buf_start + len(buf) - len(tail) - len(para) - 1 if tail else start
            buf_start = max(0, buf_start)
        else:
            if not buf:
                buf, buf_start = para, start
            else:
                buf += "\n" + para
    if buf:
        chunks.append((buf_start, buf))
    return [(s, c) for s, c in chunks if len(c.strip()) >= 80]


def load_encoder():
    import torch
    from transformers import AutoModel, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModel.from_pretrained(MODEL_NAME)
    model.eval()
    # GPU, якщо є. Без цього ембеддинг ЗАПИТУ рахувався на CPU і давав ~4с на
    # пошук при 0.9с на реранкер -- тобто вузьким місцем був не реранкер, як
    # я очікував, а власний енкодер.
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    if device == "cpu":
        torch.set_num_threads(max(1, (os.cpu_count() or 4) - 1))

    def encode(texts):
        # mean pooling з урахуванням маски -- саме так тренована e5.
        # CLS-пулінг тут дав би інші вектори, і збіг із запитом просів би
        # без жодної помилки.
        enc = tok(texts, padding=True, truncation=True, max_length=512,
                  return_tensors="pt").to(device)
        with torch.no_grad():
            out = model(**enc).last_hidden_state
        mask = enc["attention_mask"].unsqueeze(-1).float()
        emb = (out * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
        emb = torch.nn.functional.normalize(emb, p=2, dim=1)   # косинус
        return emb.tolist()

    return encode


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chunks-only", action="store_true", help="нарізати, без ембеддингів")
    ap.add_argument("--rebuild", action="store_true", help="видалити наявні фрагменти")
    ap.add_argument("--limit-docs", type=int, default=None)
    args = ap.parse_args(argv)

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            if args.rebuild:
                cur.execute("DELETE FROM document_chunks")
                print(f"видалено наявних фрагментів: {cur.rowcount}")

            cur.execute("""
                SELECT d.id, d.pipeline_meta ->> 'title', d.text_content
                  FROM documents d
                 WHERE d.domain = 'normative' AND d.text_content IS NOT NULL
                   AND NOT EXISTS (SELECT 1 FROM document_chunks ch
                                    WHERE ch.document_id = d.id)
                 ORDER BY d.id
                 LIMIT %s
            """, (args.limit_docs,))
            docs = cur.fetchall()
            print(f"документів до нарізки: {len(docs)}")

            total = 0
            for doc_id, title, text in docs:
                pieces = chunk_text(text)
                for ord_, (start, piece) in enumerate(pieces):
                    cur.execute("""
                        INSERT INTO document_chunks
                            (document_id, ord, text, char_start, char_end)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (document_id, ord) DO NOTHING
                    """, (doc_id, ord_, piece, start, start + len(piece)))
                total += len(pieces)
                print(f"  {len(pieces):>5} фрагм.  {(title or '')[:58]}")
        conn.commit()
    print(f"\nусього фрагментів: {total}")

    if args.chunks_only:
        return 0

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM document_chunks WHERE embedding IS NULL")
        todo = cur.fetchone()[0]
    if not todo:
        print("Усі фрагменти вже мають ембеддинги.")
        return 0

    print(f"\nЗавантаження {MODEL_NAME}...")
    encode = load_encoder()
    print(f"Рахуємо ембеддинги для {todo} фрагментів (batch={BATCH})")

    t0, done = time.time(), 0
    while True:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, text FROM document_chunks
                     WHERE embedding IS NULL ORDER BY id LIMIT %s
                """, (BATCH,))
                rows = cur.fetchall()
                if not rows:
                    break
                vecs = encode([PASSAGE_PREFIX + t for _, t in rows])
                for (chunk_id, _), vec in zip(rows, vecs):
                    cur.execute(
                        "UPDATE document_chunks SET embedding = %s::vector, "
                        "embedding_model = %s WHERE id = %s",
                        (str(vec), MODEL_NAME, chunk_id),
                    )
            conn.commit()
        done += len(rows)
        el = time.time() - t0
        print(f"  {done}/{todo}  {done/el:.1f} фрагм./с  залишилось ~{(todo-done)/(done/el)/60:.1f} хв")

    print(f"\nГотово за {(time.time()-t0)/60:.1f} хв")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
