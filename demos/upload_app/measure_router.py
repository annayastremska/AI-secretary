"""Замір encoder-кандидатів для векторного ярусу (задача 3.2 плану
docs/tasks/2026-08-24_app-chat-plan.md).

Що міряється: для кожного encoder-а з ENCODER_SPECS (vector_route.py)
маршрути будуються з examples query_catalog.yaml, і по тест-сету
router_testset.yaml («питання -> очікуваний шаблон») рахується:

  - точність top-1 загальна і по групах (example/paraphrase/typo/
    colloquial/trap/smalltalk);
  - для сітки порогів: скільки питань нижче порога (-> фолбек у модель) і
    скільки ВПЕВНЕНО-НЕПРАВИЛЬНИХ (бал >= порога, а маршрут не той) --
    найважливіша цифра: впевнено-неправильний маршрут гірший за фолбек
    (критерій 3.3), таких мусить бути 0 або кожен пояснений;
  - латентність кодування одного питання (CPU, батч 1 -- як у проді).

Чесність заміру: приклади каталогу НЕ мають відповідати самі собі --
для групи example рахується leave-one-out (сам приклад виймається з
індексу перед пошуком). Решта груп у прикладах не зустрічається за
побудовою тест-сету.

Правило рішення відтворює semantic-router 0.1.16 дослівно (перевірено
по його коду і хрест-навхрест живим SemanticRouter нижче): косинусна
близькість до всіх utterances -> top_k=5 -> групування по маршруту ->
СЕРЕДНІЙ бал маршруту -> найбільший виграє; поріг порівнюється з цим
середнім. Пряме використання SemanticRouter тут не годиться лише через
leave-one-out: перебудовувати індекс на кожен приклад -- сотні перебудов.

Запуск (локально, інтернет не потрібен, якщо ваги вже в кеші HF):
    python demos/upload_app/measure_router.py
    python demos/upload_app/measure_router.py --encoders intfloat/multilingual-e5-base

Результати заміру 25.08 -- docs/research/2026-08-25_encoder-measurement.md.
"""
import argparse
import os
import sys
import time
from collections import Counter, defaultdict

import numpy as np
import yaml

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(APP_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from demos.upload_app.chat_gradio.vector_route import (  # noqa: E402
    ENCODER_SPECS, catalog_routes, _make_encoder)

TESTSET_PATH = os.path.join(APP_DIR, "router_testset.yaml")
TOP_K = 5          # дефолт semantic-router 0.1.16
THRESHOLDS = ([round(0.30 + 0.05 * i, 2) for i in range(11)]      # 0.30..0.80
              + [round(0.82 + 0.02 * i, 2) for i in range(8)])    # 0.82..0.96


def rules_covered(questions):
    """Продовий пре-фільтр: які питання тест-сету ловлять ПРАВИЛА
    (rules_route у tiers.py) ще до векторного ярусу. Реєстр осіб
    заглушується «прізвище завжди знайдеться» -- на сервері з живою базою
    extract_name так і працює для прізвищ демо-набору, а локально бази
    немає. -> {q: template_id | None}."""
    import demos.upload_app.chat_gradio.tiers as tiers
    orig = tiers._run_template_sql
    tiers._run_template_sql = lambda sql, params: [{"ok": 1}]
    covered = {}
    try:
        for q in questions:
            try:
                routed = tiers.rules_route(q["q"])
            except Exception:
                routed = None
            covered[q["q"]] = routed[0] if routed else None
    finally:
        tiers._run_template_sql = orig
    return covered


def load_testset():
    with open(TESTSET_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)["questions"]


AGG_FN = {"mean": np.mean, "max": np.max, "sum": np.sum}


def classify(sims, route_of, top_k=TOP_K, mask_idx=None, agg="mean"):
    """Правило semantic-router: top-k близькостей -> агрегат по маршруту ->
    максимум. -> (route, score). agg -- аргумент aggregation SemanticRouter
    (дефолт бібліотеки "mean"; "max" = правило найближчого прикладу).
    Перший замір показав, що mean КАРАЄ маршрути з кількома схожими
    прикладами: їхній бал розбавляється власними слабшими сусідами, і
    самотній буквено-близький приклад ЧУЖОГО маршруту виграє -- тому
    агрегат міряється як окрема вісь, а не приймається дефолтом."""
    sims = sims.copy()
    if mask_idx is not None:
        sims[mask_idx] = -np.inf          # leave-one-out для групи example
    top = np.argsort(sims)[::-1][:top_k]
    by_route = defaultdict(list)
    for i in top:
        by_route[route_of[i]].append(sims[i])
    fn = AGG_FN[agg]
    scored = [(r, float(fn(v))) for r, v in by_route.items()]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[0]


def crosscheck(encoder_name, encoder, utter_texts, route_of, questions,
               agg="mean", n=12):
    """Живий SemanticRouter проти нашого відтворення правила: на n питаннях
    без leave-one-out маршрути мусять збігтися. Розбіжність -- зупинка
    заміру: означає, що ми міряємо не те, що поїде в прод."""
    from semantic_router import Route
    from semantic_router.routers import SemanticRouter
    spec = ENCODER_SPECS[encoder_name]
    by_route = defaultdict(list)
    for t, r in zip(utter_texts, route_of):
        by_route[r].append(t)
    routes = [Route(name=r, utterances=u, score_threshold=0.0)
              for r, u in by_route.items()]
    router = SemanticRouter(encoder=encoder, routes=routes,
                            aggregation=agg, auto_sync="local")
    U = np.array(encoder(utter_texts))
    picked = [q for q in questions if q["group"] != "example"][:n]
    for q in picked:
        qv = np.array(encoder([spec["query_prefix"] + q["q"]]))[0]
        mine, my_score = classify(U @ qv, route_of, agg=agg)
        choice = router(spec["query_prefix"] + q["q"])
        live = getattr(choice, "name", None)
        live_score = float(getattr(choice, "similarity_score", 0.0) or 0.0)
        if live != mine or abs(live_score - my_score) > 1e-4:
            raise SystemExit(
                f"ХРЕСТ-ПЕРЕВІРКА НЕ ЗІЙШЛАСЬ ({encoder_name}): "
                f"«{q['q']}» -> наше {mine}@{my_score:.4f}, "
                f"живий {live}@{live_score:.4f}")
    print(f"    хрест-перевірка з живим SemanticRouter (aggregation={agg}): "
          f"{len(picked)} питань, збіг маршруту і бала (до 1e-4)")


def measure(encoder_name, questions):
    spec = ENCODER_SPECS[encoder_name]
    print(f"\n=== {encoder_name} (pooling={spec['pooling']}, "
          f"префікси: query='{spec['query_prefix']}' "
          f"passage='{spec['passage_prefix']}') ===")
    t0 = time.perf_counter()
    encoder = _make_encoder(encoder_name)
    print(f"  завантаження encoder-а: {time.perf_counter() - t0:.1f} с")

    route_of, utter_texts, raw_utts = [], [], []
    for tid, examples in catalog_routes():
        for u in examples:
            route_of.append(tid)
            raw_utts.append(u)
            utter_texts.append(spec["passage_prefix"] + u)

    t0 = time.perf_counter()
    U = np.array(encoder(utter_texts))
    print(f"  індекс: {len(utter_texts)} прикладів, "
          f"{time.perf_counter() - t0:.1f} с")

    # латентність одного питання (як у проді: батч 1)
    lat = []
    for q in questions[:10]:
        t0 = time.perf_counter()
        encoder([spec["query_prefix"] + q["q"]])
        lat.append((time.perf_counter() - t0) * 1000)
    print(f"  латентність кодування питання: медіана "
          f"{np.median(lat):.0f} мс (CPU, батч 1)")

    Q = np.array(encoder([spec["query_prefix"] + q["q"]
                          for q in questions]))
    S = Q @ U.T

    for agg in ("mean", "max"):
        print(f"  --- aggregation={agg}"
              f"{' (дефолт semantic-router)' if agg == 'mean' else ''} ---")
        results = []
        for i, q in enumerate(questions):
            mask = None
            if q["group"] == "example":
                # приклад не має відповідати сам собі: виймаємо з індексу
                mask = raw_utts.index(q["q"])
            route, score = classify(S[i], route_of, mask_idx=mask, agg=agg)
            results.append(dict(q=q["q"], expected=q["expected"],
                                group=q["group"], got=route, score=score,
                                ok=route == q["expected"]))

        crosscheck(encoder_name, encoder, utter_texts, route_of, questions,
                   agg=agg)

        by_group = defaultdict(list)
        for r in results:
            by_group[r["group"]].append(r)
        print("    точність top-1 (без порога):")
        for g in ["example", "paraphrase", "typo", "colloquial", "trap",
                  "smalltalk"]:
            rs = by_group[g]
            n_ok = sum(r["ok"] for r in rs)
            miss_list = [r for r in rs if not r["ok"]]
            misses = "; ".join(
                "«{q}» -> {got}@{score:.3f} (чекали {expected})".format(**r)
                for r in miss_list[:8])
            if len(miss_list) > 8:
                misses += f"; ... і ще {len(miss_list) - 8}"
            print(f"      {g:10} {n_ok:3}/{len(rs):3}"
                  + (f"  промахи: {misses}" if misses else ""))
        total_ok = sum(r["ok"] for r in results)
        print(f"      {'разом':10} {total_ok:3}/{len(results):3} "
              f"= {total_ok / len(results):.1%}")

        wrong = [r for r in results if not r["ok"]]
        max_wrong = max((r["score"] for r in wrong), default=0.0)
        min_ok = min((r["score"] for r in results if r["ok"]), default=0.0)
        print(f"    бали: max серед НЕПРАВИЛЬНИХ {max_wrong:.4f}; "
              f"min серед правильних {min_ok:.4f}")

        print("    поріг -> [точних серед відповілих | фолбеків (нижче "
              "порога) | ВПЕВНЕНО-НЕПРАВИЛЬНИХ]:")
        for t in THRESHOLDS:
            routed = [r for r in results if r["score"] >= t]
            conf_wrong = [r for r in routed if not r["ok"]]
            print(f"      {t:.2f} -> {sum(r['ok'] for r in routed):3} | "
                  f"{len(results) - len(routed):3} | {len(conf_wrong):3}"
                  + ("" if not conf_wrong or len(conf_wrong) > 6 else
                     "   " + "; ".join(
                         f"«{r['q']}» -> {r['got']}@{r['score']:.3f}"
                         for r in conf_wrong)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--encoders", default=",".join(ENCODER_SPECS),
                    help="кома-розділений перелік імен з ENCODER_SPECS")
    ap.add_argument("--production-view", action="store_true",
                    help="міряти лише питання, які НЕ ловлять правила "
                         "(rules_route) -- справжнє навантаження векторного "
                         "ярусу, бо він стоїть ПІСЛЯ правил")
    args = ap.parse_args()
    questions = load_testset()
    groups = Counter(q["group"] for q in questions)
    print(f"тест-сет: {len(questions)} питань {dict(groups)}")
    if args.production_view:
        covered = rules_covered(questions)
        caught = [q for q in questions if covered[q["q"]] is not None]
        n_rules_ok = sum(covered[q["q"]] == q["expected"] for q in caught)
        print(f"продовий вид: правила ловлять {len(caught)}/{len(questions)} "
              f"(з них правильно {n_rules_ok}); векторному ярусу лишається "
              f"{len(questions) - len(caught)}")
        wrong_rules = [(q["q"], covered[q["q"]], q["expected"])
                       for q in caught if covered[q["q"]] != q["expected"]]
        for qq, got, exp in wrong_rules:
            print(f"  УВАГА, правила неправильно: «{qq}» -> {got} "
                  f"(чекали {exp})")
        questions = [q for q in questions if covered[q["q"]] is None]
    for name in args.encoders.split(","):
        name = name.strip()
        try:
            measure(name, questions)
        except SystemExit:
            raise
        except Exception as exc:
            # encoder не завівся (немає ваг / несумісний код моделі) --
            # це РЕЗУЛЬТАТ заміру, а не падіння скрипта: фіксуємо і йдемо далі
            print(f"  НЕ ЗАМІРЯНО: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
