"""A/B ваг лексичної та векторної гілок у RRF.

Запуск (на сервері):
    python db/scripts/ab_fusion_weights.py --model bge-m3 --rerank 50
    python db/scripts/ab_fusion_weights.py --model e5-small

## Звідки взялась задача

На наборі з перефразованими питаннями склейка гілок ЗНИЖУВАЛА якість: векторна
гілка ставила правильну одиницю першою в 6 випадках із 10, а після RRF перших
місць не лишалось узагалі. Причина арифметична: RRF додає 1/(K+позиція) за
кожну гілку, тому при K=60 присутність у ДВОХ гілках на 60-х місцях
(2/120 = 0.0167) коштує дорожче за ПЕРШЕ місце в одній (1/61 = 0.0164). Коли
питання перефразоване, лексичній гілці нема за що чіплятись, вона впевнено
підсовує чуже -- і топить впевнену правоту вектора.

Тому міряємо не «яка модель», а ЯК СКЛАДАТИ дві гілки: вага кожної та K.

## Що НЕ робиться

Лексична гілка не викидається. На питаннях із точним терміном чи номером вона
незамінна -- саме вона знаходить те, чого вектор не бачить. Питання лише в
тому, чи має її голос бути рівним голосу вектора.

## Два зрізи, і другий вирішальний

* після склейки -- власна якість формули;
* після реранкера -- те, що дістається воротам. Реранкер переставляє верхні
  50, тому тут видно вже не порядок, а ПОВНОТУ: чи правильна одиниця взагалі
  доїхала до пулу.
"""
import argparse
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg  # noqa: E402

import ab_embedding_models as AB  # noqa: E402
import search_units_test as SU  # noqa: E402

# (вага лексичної, вага векторної) -- (1, 1) це поточний стан
WEIGHTS = [(1, 0), (1, 1), (1, 2), (1, 3), (1, 5), (0, 1)]
KS = [10, 60]


def weighted_merge(branches, k):
    """RRF із вагами. Склейка частин однієї логічної одиниці -- як у пошуку:
    ключ (документ, мітка), інакше частини конкурували б між собою."""
    fused = {}
    for lst, w in branches:
        if not w:
            continue
        for pos, (uid, doc_id, base, _s) in enumerate(lst, start=1):
            e = fused.setdefault((doc_id, base), {"rrf": 0.0, "parts": set()})
            e["rrf"] += w / (k + pos)
            e["parts"].add(uid)
    return sorted(fused.items(), key=lambda kv: -kv[1]["rrf"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="bge-m3", choices=sorted(AB.MODELS))
    ap.add_argument("--truth", default=os.path.join(PROJECT_ROOT, "eval", "retrieval",
                                                    "truth_units_v2.tsv"))
    ap.add_argument("--rerank", type=int, default=0)
    args = ap.parse_args(argv)

    truth = AB.read_truth(args.truth)
    print(f"набір: {len(truth)} питань, модель {args.model}"
          + (f", реранкер пул {args.rerank}" if args.rerank else ""))
    encode = AB.load_encoder(args.model)
    rescore = None
    if args.rerank:
        from measure_rerank_lift import load_reranker
        rescore = load_reranker()

    cfgs = [(wl, wv, k) for k in KS for (wl, wv) in WEIGHTS]
    # (1,0) і (0,1) від K не залежать -- одна гілка, порядок той самий
    cfgs = [c for c in cfgs if not (c[0] == 0 or c[1] == 0) or c[2] == KS[0]]
    ranks = {c: [] for c in cfgs}
    rr_ranks = {c: [] for c in cfgs}
    t0 = time.time()

    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        for q, doc_id, needle in truth:
            ids = AB.correct_units(cur, doc_id, needle)
            if not ids:
                print(f"  ПРОПУСК (доказ не знайшовся): {q[:56]}")
                continue
            lex = SU.lexical(cur, q)
            vec = str(encode([q], "q")[0])
            sem = (SU.semantic(cur, vec) if AB.MODELS[args.model]["table"] is None
                   else AB.semantic_side(cur, AB.MODELS[args.model]["table"], vec))
            canon = SU.canon_map(cur)
            for c in cfgs:
                wl, wv, k = c
                fused = SU.dedupe_by_text(cur, weighted_merge([(lex, wl), (sem, wv)], k), canon)
                ranks[c].append(AB.rank_fused(fused, ids))
                if rescore and fused:
                    pool = fused[:args.rerank]
                    from measure_rerank_lift import RERANK_CHARS
                    texts = [SU.quote_of(cur, d, b)[0][:RERANK_CHARS] for (d, b), _m in pool]
                    sc = rescore(q, texts)
                    order = sorted(range(len(sc)), key=lambda j: -sc[j])
                    rr_ranks[c].append(
                        AB.rank_fused([pool[j] for j in order] + fused[args.rerank:], ids))
                else:
                    rr_ranks[c].append(None)

    def line(name, rs):
        found = [x for x in rs if x is not None]
        hit = lambda n: sum(1 for x in rs if x is not None and x <= n)  # noqa: E731
        return (f"{name:<16}{hit(1):>5}{hit(2):>5}{hit(5):>6}{hit(10):>6}"
                f"{sum(1 for x in rs if x is None):>9}"
                f"{(sum(found)/len(found) if found else 0):>10.1f}")

    n = len(ranks[cfgs[0]])
    print(f"\n{'='*74}\nПІСЛЯ СКЛЕЙКИ (n={n})")
    print(f"{'лекс:вект K':<16}{'@1':>5}{'@2':>5}{'@5':>6}{'@10':>6}{'немає':>9}{'сер.':>10}")
    for c in cfgs:
        wl, wv, k = c
        name = ("лише вектор" if not wl else "лише лексика" if not wv
                else f"{wl}:{wv} K={k}")
        print(line(name, ranks[c]))

    if rescore:
        print(f"\nПІСЛЯ РЕРАНКЕРА -- це бачать ворота (n={n})")
        print(f"{'лекс:вект K':<16}{'@1':>5}{'@2':>5}{'@5':>6}{'@10':>6}{'немає':>9}{'сер.':>10}")
        for c in cfgs:
            wl, wv, k = c
            name = ("лише вектор" if not wl else "лише лексика" if not wv
                    else f"{wl}:{wv} K={k}")
            print(line(name, rr_ranks[c]))

    print(f"\nусього {time.time()-t0:.0f} с")
    print("Читати так: @2 -- скільком питанням правильна одиниця дісталась у топ-2\n"
          "(саме стільки одиниць бачать ворота). «немає» -- не потрапила у видачу\n"
          "взагалі; це найгірший стовпець, бо його не виправить жоден реранкер.")
    return 0


def _dsn():
    from build_units_test import dsn
    return dsn()


if __name__ == "__main__":
    raise SystemExit(main())
