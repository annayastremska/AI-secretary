"""Чому «джерело сходиться» не 40/40: класифікує кожен провал за причиною.

Запуск (модель піднята):
    /home/ubuntu/anya/ai-secretary/.venv/bin/python \\
        db/scripts/diagnose_source_failures.py

## Що це доводить і чого НЕ доводить

Метрика `source_checks_out` сувора: зараховує, лише коли ворота процитували
ДОСЛІВНО саме ту одиницю, яку призначила істина. Провал != «без джерела»:
правило харнесу «завжди джерело» тримається за побудовою (ворота не
підтверджують без дослівного підрядка). Провал означає одне з чотирьох:

* порожня_істина -- у рядку набору немає доку/уривка: зіставляти нема з чим,
  дефект НАБОРУ, не системи;
* ворота_відмовили -- ланцюг нічого не підтвердив (answers=false скрізь):
  правильний уривок не піднявся або ворота його відкинули;
* не_дослівно -- ворота відповіли, але цитата не є підрядком (OCR/перефраз);
* інша_одиниця -- ворота відповіли дослівно, але з ІНШОГО пункту (двійник,
  сусідній пункт, сторонній акт). Часто це теж правильна по суті відповідь,
  просто не та адреса, яку зафіксувала істина.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg  # noqa: E402

import quote_with_llm_test as G          # noqa: E402
import resolve_identifier as R           # noqa: E402
import search_units_test as SU           # noqa: E402
import ab_embedding_models as AB         # noqa: E402
from build_units_test import dsn, load_encoder  # noqa: E402
from measure_rerank_lift import RERANK_CHARS, load_reranker  # noqa: E402

EVAL = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "eval", "retrieval")


def read_set(name):
    out = []
    with open(os.path.join(EVAL, name), encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            out.append(line.rstrip("\n").split("\t"))
    return out


def unit_ids(cur, doc_id, base):
    cur.execute("SELECT id FROM document_units WHERE document_id = %s "
                "AND base_label = %s", (doc_id, base))
    return {r[0] for r in cur.fetchall()}


def main():
    encode, rescore = load_encoder(), load_reranker()
    truth = []
    for name in ("truth_units_v2.tsv", "truth_units_terms.tsv"):
        if os.path.exists(os.path.join(EVAL, name)):
            truth += [(p[0], int(p[1]), p[2]) for p in read_set(name)
                      if len(p) >= 3 and p[1].strip()]
    # рядки з порожнім/відсутнім джерелом -- окремо, це дефект набору
    empty = [p[0] for name in ("truth_units_v2.tsv", "truth_units_terms.tsv")
             if os.path.exists(os.path.join(EVAL, name))
             for p in read_set(name) if len(p) < 3 or not p[1].strip()]

    def chain(cur, q, top=2):
        res = R.resolve(cur, q)
        if res["status"] == "absent":
            return []
        docs = ([d["id"] for d in res["documents"]]
                if res["status"] == "resolved" else None)
        sq = res.get("rest") or q
        vec = str(encode(["query: " + sq])[0])
        fused = SU.dedupe_by_text(
            cur, SU.rrf_merge(SU.lexical(cur, sq, docs=docs),
                              SU.semantic(cur, vec, docs=docs)),
            SU.canon_map(cur))
        if fused:
            pool = fused[:50]
            texts = [SU.quote_of(cur, d, b)[0][:RERANK_CHARS]
                     for (d, b), _m in pool]
            sc = rescore(q, texts)
            order = sorted(range(len(sc)), key=lambda j: -sc[j])
            fused = [pool[j] for j in order] + fused[50:]
        out = []
        cache = {}
        for (doc_id, base), _meta in fused[:top]:
            title, ident = SU.identity(cur, doc_id, cache)
            body, _w, _t = SU.quote_of(cur, doc_id, base)
            data, _u, _dt, _raw, truncated = G.ask(
                q, title[:70], ident, base[:60], body)
            if truncated:
                continue
            quote = (data.get("quote") or "").strip()
            exact = bool(quote) and G.norm(" ".join(quote.split())) in G.norm(body)
            out.append(((doc_id, base), exact, bool(data.get("answers")),
                        (data.get("why") or "")[:80]))
        return out

    counts = {"OK": 0, "інша_одиниця": 0, "ворота_відмовили": 0,
              "не_дослівно": 0, "порожня_істина": len(empty)}
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL hnsw.ef_search = 200")
        for q, doc_id, needle in truth:
            ids = AB.correct_units(cur, doc_id, needle)
            got = chain(cur, q)
            hit = any(e and a and (unit_ids(cur, d, b) & ids)
                      for (d, b), e, a, _w in got)
            if hit:
                counts["OK"] += 1
                continue
            # класифікація провалу
            answered = [(d, b, e, w) for (d, b), e, a, w in got if a]
            if not ids:
                reason = "порожня_істина"
            elif not answered:
                reason = "ворота_відмовили"
            elif not any(e for _d, _b, e, _w in answered):
                reason = "не_дослівно"
            else:
                reason = "інша_одиниця"
            counts[reason] = counts.get(reason, 0) + 1
            landed = "; ".join(f"док{d}/{b} exact={e}" for d, b, e, _w in answered) \
                or "(нічого не підтверджено)"
            print(f"[{reason}] {q[:58]}")
            print(f"    істина: док{doc_id}/{needle[:40]}  (одиниць {len(ids)})")
            print(f"    ворота: {landed}")

    for q in empty:
        print(f"[порожня_істина] {q[:58]}   (у наборі немає доку -- дефект набору)")

    print("\n" + "=" * 60)
    tot = sum(counts.values())
    print(f"усього {tot}: OK {counts['OK']}, "
          f"інша одиниця {counts['інша_одиниця']}, "
          f"ворота відмовили {counts['ворота_відмовили']}, "
          f"не дослівно {counts['не_дослівно']}, "
          f"порожня істина {counts['порожня_істина']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
