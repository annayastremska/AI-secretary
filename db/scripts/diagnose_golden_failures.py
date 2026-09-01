"""Чому саме провалились питання золотого набору -- по стадіях ланцюга.

Запуск:
    python db/scripts/diagnose_golden_failures.py --set ~/andriy/golden/golden_norm.tsv \\
        --only N03,N08,N09,N11,N16,N18

## Навіщо окремий прилад

Замір сказав «10 із 15», але не сказав, ДЕ ламається. Без цього крутити ваги --
навмання: якщо правильна одиниця взагалі не доходить до воріт, винна видача;
якщо доходить, а ворота її не беруть -- винні ворота; якщо ворота беруть, а
цитата не дослівна -- винен вивід моделі. Це три різні роботи.

Для кожного питання друкується позиція правильної одиниці на КОЖНІЙ стадії:

    лексична | векторна | після RRF | після реранкера | скор реранкера

і сирий вердикт воріт по топ-2, включно з полем `why` -- саме воно показує,
чому модель вважає чужий фрагмент відповіддю.

Скор реранкера тут головне число: заміряно окремо, що він розділяє «відповідь
є» від «немає» із зазором +1.82 при порозі 0. Якщо в хибних прийняттях скор
негативний, префільтр за порогом їх зніме, і це буде дешевше за будь-яку
роботу з промптом.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg  # noqa: E402

import quote_with_llm_test as G  # noqa: E402
import resolve_identifier as R  # noqa: E402
import search_units_test as SU  # noqa: E402
from measure_golden_normative import norm, units_containing  # noqa: E402


def rank_of(rows, ids):
    for i, r in enumerate(rows, start=1):
        if r[0] in ids:
            return i
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--pool", type=int, default=50)
    args = ap.parse_args()

    want = {x.strip() for x in args.only.split(",") if x.strip()}
    rows = []
    with open(os.path.expanduser(args.set), encoding="utf-8") as fh:
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 4 and (not want or p[0] in want):
                rows.append(p[:4])

    from build_units_test import dsn, load_encoder
    from measure_rerank_lift import RERANK_CHARS, load_reranker
    encode, rescore = load_encoder(), load_reranker()

    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL hnsw.ef_search = 200")
        for qid, kind, q, proof in rows:
            print(f"\n{'=' * 74}\n[{qid} {kind}] {q}")
            expect, _used = units_containing(cur, proof) if kind != "refusal" \
                else (set(), [])
            if kind != "refusal":
                print(f"  правильних одиниць у корпусі: {len(expect)}")

            res = R.resolve(cur, q)
            docs = ([d["id"] for d in res["documents"]]
                    if res["status"] == "resolved" else None)
            sq = res.get("rest") or q
            vec = str(encode(["query: " + sq])[0])
            lex = SU.lexical(cur, sq, docs=docs)
            sem = SU.semantic(cur, vec, docs=docs)
            fused = SU.dedupe_by_text(cur, SU.rrf_merge(lex, sem), SU.canon_map(cur))

            pool = fused[:args.pool]
            texts = [SU.quote_of(cur, d, b)[0][:RERANK_CHARS] for (d, b), _m in pool]
            scores = rescore(q, texts) if texts else []
            order = sorted(range(len(scores)), key=lambda j: -scores[j])
            reranked = [pool[j] for j in order] + fused[args.pool:]

            if kind != "refusal" and expect:
                def fused_rank(lst):
                    for i, (_key, meta) in enumerate(lst, start=1):
                        if meta["parts"] & expect:
                            return i
                    return None
                print(f"  позиція правильної одиниці: лексична "
                      f"{rank_of(lex, expect)}, векторна {rank_of(sem, expect)}, "
                      f"після RRF {fused_rank(fused)}, "
                      f"після реранкера {fused_rank(reranked)}")

            cache = {}
            for n, ((doc_id, base), _meta) in enumerate(reranked[:2], start=1):
                sc = scores[order[n - 1]] if n - 1 < len(order) else float("nan")
                title, ident = SU.identity(cur, doc_id, cache)
                body, _w, _t = SU.quote_of(cur, doc_id, base)
                data, _u, dt, _raw, trunc = G.ask(q, title[:70], ident, base[:60], body)
                quote = (data.get("quote") or "").strip()
                exact = bool(quote) and norm(quote) in norm(body)
                cur.execute("SELECT id FROM document_units WHERE document_id=%s "
                            "AND base_label=%s", (doc_id, base))
                uids = {r[0] for r in cur.fetchall()}
                mark = "ТУДИ" if (uids & expect) else "не туди"
                print(f"  #{n} скор реранкера {sc:+.2f}  {mark}  "
                      f"{ident[:30]} / {base[:26]}")
                print(f"      ворота: {'ВІДПОВІДАЄ' if data.get('answers') else 'ні'}"
                      f"   дослівна: {'ТАК' if exact else 'НІ'}"
                      f"{'  [ОБРІЗАНО]' if trunc else ''}   {dt:.1f} с")
                print(f"      why: {str(data.get('why') or '')[:150]}")
                if quote and not exact:
                    print(f"      цитата (НЕ дослівна): «{quote[:150]}»")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
