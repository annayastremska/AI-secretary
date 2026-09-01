"""Чи піднімає реранкер ПРАВИЛЬНУ одиницю в топ -- єдине питання цього заміру.

Запуск:
    python db/scripts/measure_rerank_lift.py --truth eval/retrieval/truth_units.tsv

## Чому саме таке питання, а не «чи краще ранжує»

Прогін воріт показав, що домінує не якість воріт, а якість ВИДАЧІ: у трьох із
п'яти питань правильна одиниця в корпусі є, а в топ-2 не потрапила. Ворота
чесно казали «тут не те» -- і були праві. Отже реранкер потрібен не «щоб
краще», а щоб воротам було ЩО приймати.

Тому метрика тут одна й перевіряється механічно: місце правильної одиниці до
реранкера і після. Правильна одиниця визначається підрядком, який мусить бути
в її тексті -- жодного судження.

## Запобіжники GPU

Реранкер -- bge-reranker-v2-m3, ~568 млн параметрів (~2.2 ГБ fp32). Стеля
0.10 на власний процес, відмова стартувати при малій вільній пам'яті. Сервер
спільний, на карті сидить чужий процес.
"""
import argparse
import os
import sys
import time

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "db", "scripts"))

import search_units_test as SU  # noqa: E402

RERANKER = "BAAI/bge-reranker-v2-m3"
POOL = 20              # скільком логічним одиницям реранкер дає оцінку
RERANK_CHARS = 1800    # більше не має сенсу: вхід реранкера теж 512 токенів
MIN_FREE_GIB = 5.0
MEM_FRACTION = 0.10


def load_reranker():
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    if torch.cuda.is_available():
        free, total = torch.cuda.mem_get_info()
        print(f"GPU: вільно {free/1024**3:.1f} ГіБ із {total/1024**3:.1f}")
        if free / 1024**3 < MIN_FREE_GIB:
            raise SystemExit("вільної пам'яті мало -- не стартую")
        torch.cuda.set_per_process_memory_fraction(MEM_FRACTION)
        device = "cuda"
    else:
        device = "cpu"
        print("GPU немає -- CPU (повільно)")
    tok = AutoTokenizer.from_pretrained(RERANKER)
    model = AutoModelForSequenceClassification.from_pretrained(RERANKER).eval().to(device)

    def score(query, texts, batch=8):
        out = []
        for i in range(0, len(texts), batch):
            chunk = texts[i:i + batch]
            enc = tok([query] * len(chunk), chunk, padding=True, truncation=True,
                      max_length=512, return_tensors="pt").to(device)
            with torch.no_grad():
                out.extend(model(**enc).logits.view(-1).float().cpu().tolist())
        return out

    return score


def load_truth(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            q, doc, sub = line.rstrip("\n").split("\t")
            rows.append((q, int(doc), sub))
    return rows


def rank_of(fused, cur, doc_id, sub):
    """Місце (з 1) логічної одиниці, у тексті якої є підрядок. None -- немає."""
    cur.execute("""SELECT DISTINCT base_label FROM andriy_test.document_units
                    WHERE document_id = %s AND text LIKE %s""", (doc_id, "%" + sub + "%"))
    want = {r[0] for r in cur.fetchall()}
    for i, ((d, base), _m) in enumerate(fused, start=1):
        if d == doc_id and base in want:
            return i, base
    return None, (sorted(want)[0] if want else None)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--pool", type=int, default=POOL)
    args = ap.parse_args(argv)

    truth = load_truth(args.truth)
    from build_units_test import load_encoder, dsn
    encode = load_encoder()
    score = load_reranker()

    tot_before = tot_after = 0
    lat = []
    print(f"\n{'питання':<52} {'до':>6} {'після':>7}  зміна")
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        for q, doc_id, sub in truth:
            vec = str(encode(["query: " + q])[0])
            fused = SU.rrf_merge(SU.lexical(cur, q), SU.semantic(cur, vec))
            before, want_label = rank_of(fused, cur, doc_id, sub)

            pool = fused[:args.pool]
            texts, keys = [], []
            for (d, base), _m in pool:
                body, _s, _t = SU.quote_of(cur, d, base)
                texts.append(body[:RERANK_CHARS])
                keys.append((d, base))
            t0 = time.time()
            scores = score(q, texts) if texts else []
            lat.append(time.time() - t0)
            order = [keys[i] for i in sorted(range(len(scores)),
                                             key=lambda j: -scores[j])]
            after = None
            cur.execute("""SELECT DISTINCT base_label FROM andriy_test.document_units
                            WHERE document_id=%s AND text LIKE %s""",
                        (doc_id, "%" + sub + "%"))
            want = {r[0] for r in cur.fetchall()}
            for i, (d, base) in enumerate(order, start=1):
                if d == doc_id and base in want:
                    after = i
                    break

            def fmt(x):
                return "—" if x is None else str(x)
            arrow = ("не в пулі" if before is None or before > args.pool else
                     "= " if before == after else
                     f"↑ {before - after}" if after and after < before else
                     f"↓ {after - before}" if after else "втратив")
            print(f"{q[:50]:<52} {fmt(before):>6} {fmt(after):>7}  {arrow}")
            tot_before += 1 if before and before <= 2 else 0
            tot_after += 1 if after and after <= 2 else 0

    n = len(truth)
    print(f"\nправильна одиниця в топ-2: до реранкера {tot_before}/{n}, "
          f"після {tot_after}/{n}")
    print(f"реранкер: {sum(lat)/len(lat):.2f} с на питання (пул {args.pool})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
