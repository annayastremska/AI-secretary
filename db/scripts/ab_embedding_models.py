"""A/B моделей ембедингу на тому самому корпусі одиниць і тому самому наборі.

Запуск (на сервері):
    python db/scripts/ab_embedding_models.py --build bge-m3
    python db/scripts/ab_embedding_models.py --build arctic-l
    python db/scripts/ab_embedding_models.py --measure

## Що перевіряється

Чи виграє видача від сильнішої моделі ембедингу. Поточна -- `multilingual-e5-small`:
384 виміри, найменша в родині. Гіпотеза: для юридичного тексту цього мало.

## Головна засторога, через яку такі A/B зазвичай брешуть

Кожна модель має СВІЙ спосіб застосування, і застосувати до всіх спосіб e5 --
це зміряти власне зловживання, а не моделі:

| модель    | пулінг | префікс питання | префікс тексту |
|-----------|--------|-----------------|----------------|
| e5-small  | mean   | `query: `       | `passage: `    |
| bge-m3    | CLS    | немає           | немає          |
| arctic-l  | CLS    | `query: `       | немає          |

Тому конфіг на кожну модель окремий. `max_length` навмисно однаковий (512) для
всіх: наші одиниці <=2000 символів, тобто в 512 токенів влазять, і різне вікно
додало б у порівняння другу змінну.

## Чому окремі таблиці, а не колонка в document_units

Розмірності різні (384 проти 1024), тобто однією колонкою не обійтись. Але
головне -- `public.document_units` читає апка: додавати туди експериментальні
колонки й індекси означає міняти те, на що хтось уже спирається. Вектори
лежать у `andriy_test.unit_vec_<модель>` і приєднуються за `unit_id`.

## Як рахується правильність

Істина -- `eval/retrieval/truth_units.tsv`: питання, документ і підрядок, який
МУСИТЬ бути в правильній одиниці. Перевірка механічна (підрядок), тому це не
моє судження. Ранг рахується двічі: у чистій векторній гілці (тут видно саму
модель) і після RRF із лексичною (тут видно, чи різниця доживає до видачі).
"""
import argparse
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg  # noqa: E402

import search_units_test as SU  # noqa: E402

MIN_FREE_GIB = 10.0
MEM_FRACTION = 0.1
BATCH = 64
MAXLEN = 512
SCHEMA = "andriy_test"

MODELS = {
    "e5-small": dict(name="intfloat/multilingual-e5-small", dim=384, pool="mean",
                     q="query: ", p="passage: ", table=None),   # table=None -> вбудована колонка
    "bge-m3": dict(name="BAAI/bge-m3", dim=1024, pool="cls",
                   q="", p="", table=f"{SCHEMA}.unit_vec_bge_m3"),
    "arctic-l": dict(name="Snowflake/snowflake-arctic-embed-l-v2.0", dim=1024, pool="cls",
                     q="query: ", p="", table=f"{SCHEMA}.unit_vec_arctic_l"),
    # Та сама модель, але одиниця векторизується РАЗОМ ІЗ ШЛЯХОМ: назва акта й
    # адреса пункту перед текстом.
    #
    # Гіпотеза з діагностики золотого набору: провали N09 і N11 -- це короткі
    # одиниці, у яких немає власного предмета. «1.3. Кількість оглянутих за
    # робочий день не повинна перевищувати 50 чоловік» не містить слів
    # «військово-лікарська комісія» -- вони в назві акта. Тому вектор такої
    # одиниці далекий від питання, і вона стоїть на 26-й позиції.
    #
    # Контекст РЕРАНКЕРУ вже пробував -- нуль. Тут контекст іде в сам вектор.
    "bge-m3-ctx": dict(name="BAAI/bge-m3", dim=1024, pool="cls",
                       q="", p="", table=f"{SCHEMA}.unit_vec_bge_m3_ctx",
                       with_path=True),
}


def load_encoder(key):
    import torch
    from transformers import AutoModel, AutoTokenizer
    cfg = MODELS[key]
    if not torch.cuda.is_available():
        device = "cpu"
        print("GPU немає -- CPU (повільно, але нікому не шкодить)")
    else:
        free, total = torch.cuda.mem_get_info()
        print(f"GPU: вільно {free/1024**3:.1f} ГіБ із {total/1024**3:.1f}")
        if free / 1024**3 < MIN_FREE_GIB:
            raise SystemExit(f"вільно менше за {MIN_FREE_GIB} ГіБ -- не стартую")
        torch.cuda.set_per_process_memory_fraction(MEM_FRACTION)
        device = "cuda"
    tok = AutoTokenizer.from_pretrained(cfg["name"])
    model = AutoModel.from_pretrained(cfg["name"], trust_remote_code=False).eval().to(device)

    def encode(texts, kind):
        pref = cfg["q"] if kind == "q" else cfg["p"]
        enc = tok([pref + t for t in texts], padding=True, truncation=True,
                  max_length=MAXLEN, return_tensors="pt").to(device)
        with torch.no_grad():
            out = model(**enc).last_hidden_state
        if cfg["pool"] == "cls":
            emb = out[:, 0]
        else:
            mask = enc["attention_mask"].unsqueeze(-1).float()
            emb = (out * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
        return torch.nn.functional.normalize(emb, p=2, dim=1).tolist()

    return encode


def build(key, dsn):
    cfg = MODELS[key]
    table = cfg["table"]
    if table is None:
        print(f"{key} -- вбудована колонка document_units.embedding, будувати нічого")
        return
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                unit_id bigint PRIMARY KEY
                    REFERENCES public.document_units(id) ON DELETE CASCADE,
                vec public.vector({cfg['dim']})
            )""")
        conn.commit()
        cur.execute(f"""SELECT count(*) FROM public.document_units u
                         WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE t.unit_id = u.id)""")
        todo = cur.fetchone()[0]
    if not todo:
        print(f"{key}: усі одиниці вже мають вектори")
        return
    print(f"{key} ({cfg['name']}, {cfg['dim']} вимірів, пулінг {cfg['pool']}): "
          f"рахувати {todo} одиниць")
    encode = load_encoder(key)
    t0, done = time.time(), 0
    while True:
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(f"""SELECT u.id, u.text, d.doc_title, u.base_label
                              FROM public.document_units u
                              JOIN public.documents d ON d.id = u.document_id
                             WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE t.unit_id = u.id)
                             ORDER BY u.id LIMIT %s""", (BATCH,))
            rows = cur.fetchall()
            if not rows:
                break
            if cfg.get("with_path"):
                vecs = encode([f"{ttl or ''}, {lbl}: {t}" for _, t, ttl, lbl in rows], "p")
            else:
                vecs = encode([t for _, t, _ttl, _lbl in rows], "p")
            for (uid, _t, _ttl, _lbl), v in zip(rows, vecs):
                cur.execute(f"INSERT INTO {table} (unit_id, vec) VALUES (%s, %s::public.vector) "
                            f"ON CONFLICT (unit_id) DO UPDATE SET vec = EXCLUDED.vec",
                            (uid, str(v)))
            conn.commit()
        done += len(rows)
        el = time.time() - t0
        if done % (BATCH * 20) == 0 or done >= todo:
            print(f"  {done}/{todo}  {done/el:.0f} од./с  "
                  f"ще ~{(todo-done)/max(done/el, 1e-9)/60:.1f} хв")
    print(f"вектори за {(time.time()-t0)/60:.1f} хв")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        t1 = time.time()
        cur.execute(f"CREATE INDEX IF NOT EXISTS {table.split('.')[-1]}_hnsw ON {table} "
                    f"USING hnsw (vec public.vector_cosine_ops)")
        conn.commit()
        print(f"HNSW за {time.time()-t1:.0f} с")


def semantic_side(cur, table, vec, limit=SU.CANDIDATES):
    """Та сама векторна гілка, що в пошуку, але вектор із бічної таблиці.
    Фільтри чинності й домену -- ідентичні, інакше порівняння не про модель."""
    cur.execute("SET LOCAL hnsw.ef_search = 200")
    cur.execute(f"""
        SELECT u.id, u.document_id, u.base_label, 1 - (t.vec <=> %(v)s::public.vector) AS score
          FROM {table} t
          JOIN public.document_units u ON u.id = t.unit_id
          JOIN documents d ON d.id = u.document_id
         WHERE d.validity = 'current' AND d.domain = ANY(%(domains)s)
         ORDER BY t.vec <=> %(v)s::public.vector LIMIT %(lim)s
    """, {"v": vec, "lim": limit, "domains": list(SU.PROCEDURAL)})
    return cur.fetchall()


def read_truth(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) >= 3:
                rows.append((p[0].strip(), int(p[1]), p[2].strip()))
    return rows


def correct_units(cur, doc_id, needle):
    """Правильні одиниці шукаються по ВСІЙ ГРУПІ ДУБЛІКАТІВ і БЕЗ огляду на
    пробіли.

    Друга умова -- не дрібниця: у копіях того самого закону OCR дає подвійні
    пробіли («залізничних  станціях»), тому дослівний підрядок, узятий з однієї
    копії, у другій не знаходиться. Я на цьому вже попався: вирішив, що зведення
    дублікатів ГУБИТЬ статтю, тоді як стаття була на місці, а не збігався мій
    власний підрядок.

    Про групу дублікатів: не в одному
    документі. У корпусі той самий закон трапляється двічі (наприклад
    № 550-XIV -- документи 201 і 224), а видача зводить дублікати до
    канонічного. Якби істина була прив'язана до одного document_id, правильна
    одиниця, знайдена в копії, читалась би як «не знайдено» -- і прилад
    показував би поломку пошуку там, де поломка в ньому самому.
    """
    # rf"""...""" -- рядок СИРИЙ навмисно: усередині є регулярка \s+ для
    # Postgres, і у звичайному рядку Python це попередження про невідому
    # escape-послідовність, яке в наступних версіях стане помилкою.
    cur.execute(rf"""
        WITH мій AS (
            SELECT coalesce((SELECT canonical_id FROM {SU.GROUPS}
                              WHERE document_id = %(d)s), %(d)s) AS canon
        ),
        родина AS (
            SELECT %(d)s AS id
            UNION
            SELECT g.document_id FROM {SU.GROUPS} g, мій
             WHERE g.canonical_id = мій.canon
            UNION
            SELECT мій.canon FROM мій
        )
        SELECT u.id FROM public.document_units u
          JOIN родина r ON r.id = u.document_id
         WHERE regexp_replace(u.text, E'\s+', ' ', 'g') ILIKE %(n)s
    """, {"d": doc_id, "n": "%" + " ".join(needle.split()) + "%"})
    return {r[0] for r in cur.fetchall()}


def rank_of(lst, ids):
    for i, (uid, _d, _b, _s) in enumerate(lst, start=1):
        if uid in ids:
            return i
    return None


def rank_fused(fused, ids):
    for i, (_key, meta) in enumerate(fused, start=1):
        if meta["parts"] & ids:
            return i
    return None


def measure(dsn, truth_path, keys, rerank=0):
    """Три зрізи, і третій вирішальний.

    Векторна гілка показує саму модель. Після RRF видно, чи різниця доживає до
    склейки. Після РЕРАНКЕРА видно те, що дістається воротам, -- і рішення про
    заміну моделі має ухвалюватись саме тут: виграш, який гине до воріт,
    продуктом не є, а програш, який реранкер виправляє, не є підставою
    відмовлятись.
    """
    truth = read_truth(truth_path)
    print(f"набір істини: {len(truth)} питань"
          + (f", реранкер пул {rerank}" if rerank else ""))
    encoders = {k: load_encoder(k) for k in keys}
    rescore = None
    if rerank:
        from measure_rerank_lift import load_reranker
        rescore = load_reranker()
    stats = {k: {"sem": [], "rrf": [], "rr": [], "s": 0.0} for k in keys}

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for q, doc_id, needle in truth:
            ids = correct_units(cur, doc_id, needle)
            if not ids:
                print(f"  ПРОПУСК (істина не знайшлась у корпусі): {q[:60]}")
                continue
            lex = SU.lexical(cur, q)
            print(f"\n  {q[:66]}")
            for k in keys:
                t0 = time.time()
                vec = str(encoders[k]([q], "q")[0])
                sem = (SU.semantic(cur, vec) if MODELS[k]["table"] is None
                       else semantic_side(cur, MODELS[k]["table"], vec))
                stats[k]["s"] += time.time() - t0
                fused = SU.dedupe_by_text(cur, SU.rrf_merge(lex, sem), SU.canon_map(cur))
                r_s, r_f = rank_of(sem, ids), rank_fused(fused, ids)
                stats[k]["sem"].append(r_s)
                stats[k]["rrf"].append(r_f)
                r_r = None
                if rescore and fused:
                    from measure_rerank_lift import RERANK_CHARS
                    pool = fused[:rerank]
                    texts = [SU.quote_of(cur, d, b)[0][:RERANK_CHARS]
                             for (d, b), _m in pool]
                    sc = rescore(q, texts)
                    order = sorted(range(len(sc)), key=lambda j: -sc[j])
                    r_r = rank_fused([pool[j] for j in order] + fused[rerank:], ids)
                stats[k]["rr"].append(r_r)
                print(f"     {k:<10} вектор {str(r_s or '-'):>4}   "
                      f"RRF {str(r_f or '-'):>4}"
                      + (f"   реранкер {str(r_r or '-'):>4}" if rescore else ""))

    def hit(ranks, n):
        return sum(1 for r in ranks if r is not None and r <= n)

    print(f"\n{'='*78}")
    print(f"{'модель':<12}{'@1':>6}{'@2':>6}{'@5':>6}{'не знайшла':>12}"
          f"{'сер. ранг':>11}{'с/питання':>11}   (векторна гілка)")
    total = len(stats[keys[0]]["sem"]) if keys else 0
    for k in keys:
        r = stats[k]["sem"]
        found = [x for x in r if x is not None]
        print(f"{k:<12}{hit(r,1):>6}{hit(r,2):>6}{hit(r,5):>6}"
              f"{sum(1 for x in r if x is None):>12}"
              f"{(sum(found)/len(found) if found else 0):>11.1f}"
              f"{stats[k]['s']/max(1,total):>11.2f}")
    print(f"\n{'модель':<12}{'@1':>6}{'@2':>6}{'@5':>6}{'не знайшла':>12}"
          f"{'сер. ранг':>11}   (після RRF із лексичною)")
    for k in keys:
        r = stats[k]["rrf"]
        found = [x for x in r if x is not None]
        print(f"{k:<12}{hit(r,1):>6}{hit(r,2):>6}{hit(r,5):>6}"
              f"{sum(1 for x in r if x is None):>12}"
              f"{(sum(found)/len(found) if found else 0):>11.1f}")
    if any(any(x is not None for x in stats[k]["rr"]) for k in keys):
        print()
        print(f"{'модель':<12}{'@1':>6}{'@2':>6}{'@5':>6}{'не знайшла':>12}"
              f"{'сер. ранг':>11}   (ПІСЛЯ РЕРАНКЕРА -- це бачать ворота)")
        for k in keys:
            r = stats[k]["rr"]
            found = [x for x in r if x is not None]
            print(f"{k:<12}{hit(r,1):>6}{hit(r,2):>6}{hit(r,5):>6}"
                  f"{sum(1 for x in r if x is None):>12}"
                  f"{(sum(found)/len(found) if found else 0):>11.1f}")
    print(f"\nусього питань у заміру: {total}")
    print("Рішення про заміну -- лише якщо виграш доживає до колонки «після RRF»:\n"
          "видача віддає в реранкер саме її, тому виграш у чистій векторній\n"
          "гілці, що зникає після склейки, продуктом не є.")



def sweep_k(dsn, truth_path, keys, ks):
    """Чи виправляється потоплення унікальної правоти вектора параметром K.

    RRF додає 1/(K+позиція) за кожну гілку, тому при великому K присутність у
    ДВОХ гілках коштує дорожче за перше місце в одній: при K=60 одиниця на
    60-х місцях обох гілок дає 2/120=0.0167 проти 1/61=0.0164 у переможця
    однієї. Саме це й побачив замір: вектор ставить правильну одиницю першою,
    а склейка -- на 26-ту. Малий K повертає вагу верхнім місцям.

    Модель не викликається: вектори вже в базі, тому прогін коштує секунди.
    """
    truth = read_truth(truth_path)
    encoders = {k: load_encoder(k) for k in keys}
    orig = SU.RRF_K
    print()
    print("=" * 78)
    print(f"RRF_K: як позиція правильної одиниці залежить від K "
          f"(n={len(truth)}, це мало)")
    print()
    header = "модель       гілка" + "".join(f"{('K='+str(k)):>8}" for k in ks)
    print(header)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for k in keys:
            vec_ranks, per_k = [], {kk: [] for kk in ks}
            for q, doc_id, needle in truth:
                ids = correct_units(cur, doc_id, needle)
                if not ids:
                    continue
                lex = SU.lexical(cur, q)
                vec = str(encoders[k]([q], "q")[0])
                sem = (SU.semantic(cur, vec) if MODELS[k]["table"] is None
                       else semantic_side(cur, MODELS[k]["table"], vec))
                vec_ranks.append(rank_of(sem, ids))
                for kk in ks:
                    SU.RRF_K = kk
                    fused = SU.dedupe_by_text(cur, SU.rrf_merge(lex, sem),
                                              SU.canon_map(cur))
                    per_k[kk].append(rank_fused(fused, ids))
            SU.RRF_K = orig

            def summarize(rs):
                found = [x for x in rs if x is not None]
                return (sum(1 for x in rs if x is not None and x <= 2),
                        sum(found)/len(found) if found else 0)

            h2, m2 = summarize(vec_ranks)
            print(f"{k:<13}{'вектор':<6}" + f"  @2={h2} сер={m2:.1f}")
            row1 = f"{'':<13}{'@2':<6}"
            row2 = f"{'':<13}{'сер':<6}"
            for kk in ks:
                h, m = summarize(per_k[kk])
                row1 += f"{h:>8}"
                row2 += f"{m:>8.1f}"
            print(row1)
            print(row2)
    print()
    print("@2 -- скільком питанням правильна одиниця дісталась у топ-2 "
          "(це те, що бачать ворота);")
    print("сер -- середня позиція серед знайдених. Менше -- краще.")

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build", choices=sorted(MODELS))
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--rerank", type=int, default=0,
                    help="пул реранкера в --measure; 0 -- без нього")
    ap.add_argument("--sweep-k", default="",
                    help="перебрати RRF_K, напр. 1,5,10,20,40,60")
    ap.add_argument("--models", default="e5-small,bge-m3",
                    help="які моделі порівнювати в --measure")
    ap.add_argument("--truth", default=os.path.join(PROJECT_ROOT, "eval", "retrieval",
                                                    "truth_units.tsv"))
    args = ap.parse_args(argv)

    from build_units_test import dsn as _dsn
    dsn = _dsn()
    if args.build:
        build(args.build, dsn)
    if args.measure:
        keys = [k.strip() for k in args.models.split(",") if k.strip()]
        bad = [k for k in keys if k not in MODELS]
        if bad:
            raise SystemExit(f"невідомі моделі: {bad}")
        measure(dsn, args.truth, keys, args.rerank)
    if args.sweep_k:
        keys = [k.strip() for k in args.models.split(',') if k.strip()]
        ks = [int(x) for x in args.sweep_k.split(',') if x.strip()]
        sweep_k(dsn, args.truth, keys, ks)
    if not args.build and not args.measure and not args.sweep_k:
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
