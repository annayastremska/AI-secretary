"""Строгий проти пом'якшеного критерію воріт -- на всіх класах питань разом.

Запуск (на сервері, потрібна піднята модель):
    python db/scripts/measure_gate_criterion.py --set eval/retrieval/abstain_set.tsv --rerank 50

## Що саме порівнюється

Ворота -- виклик моделі, який дивиться на найкращі одиниці й вирішує, чи є в
них відповідь. Критерій -- абзац у промпті. Строгий вимагає САМЕ того, що
запитали; пом'якшений -- щоб військовослужбовець отримав те, що хотів
дізнатися, навіть якщо питання поставлене розмовно.

Пом'якшений з'явився, бо строгий відмовляв ПРАВИЛЬНИМ відповідям на розмовне
формулювання. Але заміряно це було на п'яти позитивних питаннях, тобто рівно
з того боку, де пом'якшення не може зашкодити. Небезпека -- з іншого: чи не
почав він приймати те, що приймати не можна.

## Чому видача робиться один раз, а ворота питаються двічі

Пошук і реранкер для обох критеріїв ІДЕНТИЧНІ -- критерій живе лише в промпті.
Якби видача виконувалась окремо для кожного, у порівняння зайшла б її власна
нестабільність, і різницю не можна було б віднести до критерію. Заодно це
вдвічі дешевше.

## Критерій рішення, названий ДО прогону

Пом'якшений стає дефолтом лише якщо ОБИДВІ умови:

1. на негативних питаннях (no_answer + off_topic) відмова лишається 100% --
   жодної впевненої відповіді на «яка швидкість танка Leopard 2»;
2. на answerable він приймає не менше, ніж строгий.

Якщо перша умова порушена -- не беремо, і це нормальний результат: хибна
впевнена відповідь коштує дорожче за хибну відмову. Друга умова без першої
нічого не варта.

## Що вважається прийняттям

Записуються дві різні речі, бо це не те саме:

* `answers` -- модель сказала «тут є відповідь»;
* `usable`  -- модель сказала «є» І цитата виявилась дослівним підрядком
  оригіналу. Недослівна цитата відкидається незалежно від вердикту, тому
  саме `usable` -- те, що побачила б людина.
"""
import argparse
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg  # noqa: E402

import quote_with_llm_test as G  # noqa: E402
import resolve_identifier as R  # noqa: E402
import search_units_test as SU  # noqa: E402

NEGATIVE = ("no_answer", "off_topic")


def read_set(path):
    """Приймає і розмічений tsv (клас, питання), і простий txt із питаннями.

    Нерозмічений набір -- не другий сорт: на ньому не можна казати «правильно
    чи ні», зате можна знайти, ДЕ критерії розходяться, а це і є те, що варто
    дивитись очима. Клас 'unlabelled' саме про це, і зведення по класах для
    нього навмисно не рахується.
    """
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].strip() in ("answerable",) + NEGATIVE:
                rows.append((parts[0].strip(), parts[1].strip()))
            else:
                rows.append(("unlabelled", parts[0].strip()))
    return rows


def retrieve(cur, encode, rescore, rerank, question, top):
    """Повертає (units, absent) -- одиниці для воріт і чи документ за номером
    у корпусі відсутній. absent -- це відповідь, а не порожня видача."""
    res = R.resolve(cur, question)
    if res["status"] == "absent":
        return [], True
    docs, sq = None, question
    if res["status"] == "resolved":
        docs = [d["id"] for d in res["documents"]]
        sq = res["rest"] or question
    vec = str(encode(["query: " + sq])[0])
    fused = SU.dedupe_by_text(
        cur,
        SU.rrf_merge(SU.lexical(cur, sq, docs=docs), SU.semantic(cur, vec, docs=docs)),
        SU.canon_map(cur),
    )
    if rescore and fused and rerank:
        from measure_rerank_lift import RERANK_CHARS
        pool = fused[:rerank]
        texts = [SU.quote_of(cur, d, b)[0][:RERANK_CHARS] for (d, b), _m in pool]
        sc = rescore(question, texts)
        order = sorted(range(len(sc)), key=lambda j: -sc[j])
        fused = [pool[j] for j in order] + fused[rerank:]
    return fused[:top], False


def run_gate(cur, question, units, criterion, cost, cache):
    """Вердикт воріт по набору одиниць. Обрізаний вивід -- НЕ вердикт моделі,
    а збій розбору, і мовчки перетворювати його на відмову не можна."""
    answers = usable = False
    truncated_any = False
    for (doc_id, base), _meta in units:
        title, ident = G.SU.identity(cur, doc_id, cache)
        body, _was_split, _trimmed = SU.quote_of(cur, doc_id, base)
        data, usage, dt, _raw, truncated = G.ask(
            question, title[:70], ident, base[:60], body, criterion=criterion)
        cost["calls"] += 1
        cost["s"] += dt
        if truncated:
            truncated_any = True
            continue
        a = bool(data.get("answers"))
        quote = (data.get("quote") or "").strip()
        exact = bool(quote) and G.norm(" ".join(quote.split())) in G.norm(body)
        answers = answers or a
        usable = usable or (a and exact)
    return answers, usable, truncated_any


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", default=os.path.join(PROJECT_ROOT, "eval", "retrieval",
                                                  "abstain_set.tsv"))
    ap.add_argument("--top", type=int, default=2)
    ap.add_argument("--rerank", type=int, default=50)
    args = ap.parse_args(argv)

    rows = read_set(args.set)
    print(f"набір: {len(rows)} питань, "
          + ", ".join(f"{c}={sum(1 for k, _ in rows if k == c)}"
                      for c in sorted({k for k, _ in rows})))
    print(f"видача: top-{args.top}, реранкер пул {args.rerank}\n")

    from build_units_test import load_encoder, dsn
    encode = load_encoder()
    rescore = None
    if args.rerank:
        from measure_rerank_lift import load_reranker
        rescore = load_reranker()

    cost = {"calls": 0, "s": 0.0}
    out = []
    t_all = time.time()
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL hnsw.ef_search = 200")
        cache = {}
        for klass, q in rows:
            units, absent = retrieve(cur, encode, rescore, args.rerank, q, args.top)
            if absent:
                out.append((klass, q, False, False, False, False, "за номером немає"))
                print(f"  [{klass:<10}] {q[:56]:<56} документа за номером немає")
                continue
            if not units:
                out.append((klass, q, False, False, False, False, "видача порожня"))
                print(f"  [{klass:<10}] {q[:56]:<56} видача порожня")
                continue
            s_a, s_u, s_t = run_gate(cur, q, units, G.STRICT, cost, cache)
            l_a, l_u, l_t = run_gate(cur, q, units, G.LENIENT, cost, cache)
            note = "обрізано" if (s_t or l_t) else ""
            out.append((klass, q, s_a, s_u, l_a, l_u, note))
            print(f"  [{klass:<10}] {q[:56]:<56} "
                  f"строго {'ПРИЙ' if s_u else 'відм'}  "
                  f"м'яко {'ПРИЙ' if l_u else 'відм'}  {note}")

    # ── зведення ────────────────────────────────────────────────────────
    def tally(pred):
        sel = [r for r in out if pred(r[0])]
        return (len(sel),
                sum(1 for r in sel if r[3]),   # строго usable
                sum(1 for r in sel if r[5]))   # м'яко usable

    n_pos, s_pos, l_pos = tally(lambda k: k == "answerable")
    n_neg, s_neg, l_neg = tally(lambda k: k in NEGATIVE)

    print(f"\n{'='*78}")
    print(f"{'клас':<24}{'усього':>8}{'строго прийнято':>18}{'м''яко прийнято':>18}")
    print(f"{'answerable':<24}{n_pos:>8}{s_pos:>18}{l_pos:>18}")
    print(f"{'no_answer + off_topic':<24}{n_neg:>8}{s_neg:>18}{l_neg:>18}")

    # Порівнюємо пом'якшений зі СТРОГИМ, а не з ідеалом. Перша версія питала
    # «чи відмовляє пом'якшений на всіх негативних» -- і при спільній для обох
    # критеріїв хибній прийнятності виносила вирок пом'якшеному за те, чого
    # він не робив. Хибна прийнятність, спільна для обох, -- це властивість
    # воріт, і вона друкується нижче окремим пунктом.
    disagree = [r for r in out if r[3] != r[5]]
    print("\nумови рішення (пом'якшений проти строгого):")
    c1 = l_neg <= s_neg
    c2 = l_pos >= s_pos
    print(f"  1. на негативних приймає не більше за строгий: "
          f"{'ТАК' if c1 else 'НІ'} ({l_neg} проти {s_neg})")
    print(f"  2. на answerable приймає не менше за строгий: "
          f"{'ТАК' if c2 else 'НІ'} ({l_pos} проти {s_pos})")
    print(f"  розійшлись вердикти: {len(disagree)} із {len(out)}")
    if not disagree:
        print("\nВИСНОВОК: на цьому наборі критерій не змінює НІЧОГО -- "
              "вибрати ним не можна.\n"
              "Пом'якшення писалось під розмовне формулювання; якщо в наборі "
              "таких питань немає,\nвін і не може показати різницю. Потрібен "
              "набір із розмовними питаннями.")
    else:
        print(f"\nВИСНОВОК: пом'якшений критерій "
              + ("не гірший за строгий" if (c1 and c2) else "ГІРШИЙ за строгий"))

    if s_neg or l_neg:
        print(f"\nхибна прийнятність на негативних -- ВЛАСТИВІСТЬ ВОРІТ, "
              f"не критерію (строго {s_neg}, м'яко {l_neg}):")
        for k, q, _sa, su, _la, lu, _n in out:
            if k in NEGATIVE and (su or lu):
                print(f"  [{k}] {q}")
                print(f"      строго: {'прийняв' if su else 'відмовив'}, "
                      f"м'яко: {'прийняв' if lu else 'відмовив'}")
    if disagree:
        print("\nде критерії РОЗІЙШЛИСЬ -- саме це варто дивитись очима:")
        for k, q, _sa, su, _la, lu, _n in disagree:
            print(f"  [{k}] {q}")
            print(f"      строго: {'прийняв' if su else 'відмовив'}, "
                  f"м'яко: {'прийняв' if lu else 'відмовив'}")

    gained = [(k, q) for k, q, _sa, su, _la, lu, _n in out
              if k == "answerable" and lu and not su]
    if gained:
        print("\nвиправлені хибні відмови (чого пом'якшення й мало дати):")
        for k, q in gained:
            print(f"  {q}")
    lost = [(k, q) for k, q, _sa, su, _la, lu, _n in out
            if k == "answerable" and su and not lu]
    if lost:
        print("\nвтрачені (строгий приймав, пом'якшений ні -- нестабільність моделі):")
        for k, q in lost:
            print(f"  {q}")

    print(f"\nвартість: {cost['calls']} викликів, {cost['s']:.1f} с "
          f"({cost['s']/max(1,cost['calls']):.1f} с/виклик), "
          f"усього {time.time()-t_all:.0f} с")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
