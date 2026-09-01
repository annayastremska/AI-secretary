"""Замір реранкера: скільки коштує і що змінює в порядку.

Запуск (потрібен GPU або терпіння):
    python db/scripts/measure_reranker.py "за скільки днів подавати рапорт"
    python db/scripts/measure_reranker.py --questions questions.txt

## Що саме тут міряється -- і чого НЕ міряється

Міряється **латентність** і **зміна порядку**. Це об'єктивні числа, для них
розмітка не потрібна.

**Якість НЕ міряється, і назвати її цим скриптом не можна.** Щоб сказати
«реранкер піднімає правильні відповіді», треба знати, яка відповідь
правильна -- тобто розмічений набір (банківський GRD-05: поріг калібрований
на розмічених запитах, метод і вибірка задокументовані). Його немає.

Тому висновок цього скрипта звучить лише так: «реранкер коштує N секунд і
переставляє K% верхівки». Чи переставляє він **на краще** -- окреме питання,
на яке відповідає розмічений набір, а не цей замір.

Проксі «обидві гілки погодились» тут навмисно не використовується як мірило
якості: на питанні «хто зараз у відпустці» обидві гілки впевнено погодились
на Указі Президента, і обидві були неправі. Згода двох гілок корелює з
формулюванням питання, не з правильністю.

Модель -- `BAAI/bge-reranker-v2-m3`, як у командному дослідженні
(`docs/research/2026-08-08_embedding-model-selection/`).
"""
import argparse
import os
import sys
import time

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "db", "scripts"))

RERANKER = "BAAI/bge-reranker-v2-m3"
TOP_K = 20          # скільком фрагментам віддаємо остаточний порядок
RERANK_BATCH = 16


def load_reranker():
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(RERANKER)
    model = AutoModelForSequenceClassification.from_pretrained(RERANKER)
    model.eval().to(device)

    def score(query, texts):
        out = []
        for i in range(0, len(texts), RERANK_BATCH):
            batch = texts[i:i + RERANK_BATCH]
            enc = tok([query] * len(batch), batch, padding=True, truncation=True,
                      max_length=512, return_tensors="pt").to(device)
            with torch.no_grad():
                logits = model(**enc).logits.view(-1)
            out.extend(logits.float().cpu().tolist())
        return out

    return score, device


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query", nargs="*")
    ap.add_argument("--questions", help="файл із питаннями, одне на рядок")
    ap.add_argument("--top-k", type=int, default=TOP_K)
    args = ap.parse_args(argv)

    questions = []
    if args.questions:
        with open(args.questions, encoding="utf-8") as f:
            questions = [ln.strip() for ln in f if ln.strip()]
    if args.query:
        questions.append(" ".join(args.query))
    if not questions:
        ap.error("дай питання аргументом або --questions файл")

    from build_chunk_index import load_encoder, QUERY_PREFIX
    from search_hybrid import lexical, semantic, rrf

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

    print(f"Завантаження {RERANKER}...")
    t0 = time.time()
    score, device = load_reranker()
    print(f"  готово за {time.time()-t0:.1f}с, пристрій: {device}\n")

    encode = load_encoder()
    rows = []

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for q in questions:
            t_search = time.time()
            vec = str(encode([QUERY_PREFIX + q])[0])
            lex = lexical(cur, q)
            sem = semantic(cur, vec)
            fused = rrf(lex, sem)[:args.top_k]
            search_s = time.time() - t_search

            if not fused:
                print(f"«{q[:56]}» -- пошук нічого не дав, пропускаємо")
                continue

            ids = [cid for cid, _ in fused]
            cur.execute("SELECT id, text FROM document_chunks WHERE id = ANY(%s)", (ids,))
            texts = dict(cur.fetchall())

            t_rr = time.time()
            scores = score(q, [texts[cid] for cid in ids])
            rerank_s = time.time() - t_rr

            reranked = [cid for cid, _ in sorted(zip(ids, scores), key=lambda kv: -kv[1])]

            # Наскільько переставилась верхівка. Це міра ВПЛИВУ, не якості:
            # якщо реранкер нічого не змінює, він не потрібен; якщо змінює --
            # питання «на краще чи ні» лишається відкритим.
            top5_before, top5_after = ids[:5], reranked[:5]
            moved = len(set(top5_before) ^ set(top5_after)) // 2
            same_first = ids[0] == reranked[0]

            rows.append((q, search_s, rerank_s, len(ids), moved, same_first))
            print(f"«{q[:54]}»")
            print(f"   пошук {search_s:5.2f}с · реранк {rerank_s:5.2f}с "
                  f"({len(ids)} фрагм.) · у топ-5 змінилось {moved} · "
                  f"перший {'той самий' if same_first else 'ІНШИЙ'}")

    if len(rows) > 1:
        n = len(rows)
        print(f"\n── разом по {n} питаннях")
        print(f"   пошук:  {sum(r[1] for r in rows)/n:.2f}с у середньому")
        print(f"   реранк: {sum(r[2] for r in rows)/n:.2f}с у середньому")
        print(f"   перше місце змінилось у {sum(1 for r in rows if not r[5])} з {n}")
        print(f"   у топ-5 змінилось у середньому {sum(r[4] for r in rows)/n:.1f} позицій")
    print("\nЦе замір ВПЛИВУ й ЦІНИ, не якості. Чи краще -- показав би "
          "розмічений набір, якого немає.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
