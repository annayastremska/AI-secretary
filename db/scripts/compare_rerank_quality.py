"""Показує топ до і після реранкера -- щоб ЛЮДИНА прочитала й судила.

Запуск:
    python db/scripts/compare_rerank_quality.py --questions questions.txt --top 3

Це не метрика. Це матеріал для читання: на кожне питання видно, що ставить
першим гібридний RRF і що ставить реранкер, з назвою документа й текстом
фрагмента. Судити доводиться очима.

Обмеження, яке не зникає від того, що вивід зручний: хто читає цей вивід і
робить висновок, той оцінює систему, яку сам налаштував. Незалежний
розмічений набір потрібен саме тому, і цей скрипт його НЕ заміняє.
"""
import argparse
import os
import sys

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "db", "scripts"))

POOL = 20        # скільком фрагментам реранкер дає оцінку


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--chars", type=int, default=340)
    args = ap.parse_args(argv)

    with open(args.questions, encoding="utf-8") as f:
        questions = [ln.strip() for ln in f if ln.strip()]

    from build_chunk_index import load_encoder, QUERY_PREFIX
    from search_hybrid import lexical, semantic, rrf
    from measure_reranker import load_reranker

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    encode = load_encoder()
    score, _ = load_reranker()

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for q in questions:
            vec = str(encode([QUERY_PREFIX + q])[0])
            fused = rrf(lexical(cur, q), semantic(cur, vec))[:POOL]
            if not fused:
                print(f"\n{'='*78}\n{q}\n  (пошук нічого не дав)")
                continue

            ids = [cid for cid, _ in fused]
            cur.execute("""
                SELECT ch.id, ch.text, ch.char_start,
                       regexp_replace(left(d.text_content, 62), E'[\\n\\r]+', ' ', 'g')
                  FROM document_chunks ch JOIN documents d ON d.id = ch.document_id
                 WHERE ch.id = ANY(%s)
            """, (ids,))
            info = {r[0]: r[1:] for r in cur.fetchall()}

            scores = dict(zip(ids, score(q, [info[i][0] for i in ids])))
            reranked = sorted(ids, key=lambda i: -scores[i])

            print(f"\n{'='*78}\n ПИТАННЯ: {q}")
            for label, order in (("ГІБРИД (RRF)", ids), ("ПІСЛЯ РЕРАНКЕРА", reranked)):
                print(f"\n  ── {label}")
                for pos, cid in enumerate(order[:args.top], 1):
                    text, start, title = info[cid]
                    was = order is not ids and f"  (було #{ids.index(cid)+1})" or ""
                    print(f"   {pos}. [{scores[cid]:+.2f}] {title.strip()[:58]} @{start}{was}")
                    print(f"      {' '.join(text.split())[:args.chars]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
