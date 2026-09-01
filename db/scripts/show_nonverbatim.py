"""Показати, ЩО саме модель змінила в цитаті на трьох «не дослівних».

Для кожного питання: цитата воріт, найближчий фрагмент документа й
посимвольна різниця. Доводить, що провал -- не «нема джерела», а модель
відредагувала текст, і перевірка це впіймала.

Запуск (модель піднята):
    /home/ubuntu/anya/ai-secretary/.venv/bin/python \\
        db/scripts/show_nonverbatim.py
"""
import difflib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg  # noqa: E402

import quote_with_llm_test as G          # noqa: E402
import resolve_identifier as R           # noqa: E402
import search_units_test as SU           # noqa: E402
from build_units_test import dsn, load_encoder  # noqa: E402
from measure_rerank_lift import RERANK_CHARS, load_reranker  # noqa: E402

QUESTIONS = [
    "у якому віці юнаків уперше ставлять на військовий облік і в які місяці це роблять",
    "яку форму носить офіцер після переведення до органу військового управління",
    "чи можна знімати зі зберігання машини недоторканого запасу для навчань",
]


def closest_window(nq, nbody):
    """Найкраще вирівняне вікно тіла завдовжки з цитату."""
    sm = difflib.SequenceMatcher(None, nq, nbody, autojunk=False)
    m = sm.find_longest_match(0, len(nq), 0, len(nbody))
    start = max(0, m.b - m.a)
    return nbody[start:start + len(nq) + 10]


def main():
    encode, rescore = load_encoder(), load_reranker()

    def chain(cur, q, top=2):
        res = R.resolve(cur, q)
        docs = ([d["id"] for d in res["documents"]]
                if res["status"] == "resolved" else None)
        if res["status"] == "absent":
            return []
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
            data, _u, _dt, _raw, trunc = G.ask(q, title[:70], ident, base[:60], body)
            if trunc:
                continue
            out.append((doc_id, base, body, (data.get("quote") or "").strip(),
                        bool(data.get("answers"))))
        return out

    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL hnsw.ef_search = 200")
        for q in QUESTIONS:
            print("=" * 70)
            print("ПИТАННЯ:", q)
            for doc_id, base, body, quote, answers in chain(cur, q):
                if not (answers and quote):
                    continue
                nq = G.norm(" ".join(quote.split()))
                nb = G.norm(body)
                exact = nq in nb
                print(f"\n  док{doc_id} / {base}   answers={answers}  дослівно={exact}")
                if exact:
                    print("    (ця цитата дослівна)")
                    continue
                print("    ЦИТАТА МОДЕЛІ:")
                print("      " + quote[:300])
                win = closest_window(nq, nb)
                print("    НАЙБЛИЖЧЕ В ДОКУМЕНТІ (нормалізовано):")
                print("      " + win[:300])
                print("    РІЗНИЦЯ (- документ, + модель), нормалізовано:")
                diff = difflib.ndiff([win], [nq])
                for line in diff:
                    if line and line[0] in "-+":
                        print("      " + line[:300])
                # покажемо перші розбіжні символи явно
                for i, (a, b) in enumerate(zip(win, nq)):
                    if a != b:
                        print(f"    перша розбіжність на позиції {i}: "
                              f"документ={a!r} vs модель={b!r} "
                              f"…{win[max(0,i-15):i+15]!r}")
                        break
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
