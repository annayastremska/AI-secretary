"""Метрика 1 для нормативної половини золотого набору Дениса.

Запуск (модель піднята; набір лежить ПОЗА репозиторієм):
    python db/scripts/measure_golden_normative.py --set ~/andriy/golden/golden_norm.tsv

## Чому саме цей набір закриває метрику 1

Умова, яку я сам поставив: число «питань закрито з першого разу» має право
стояти на сторінці лише якщо список написала людина, яка не крутила ні
промптів, ні каталогу, ні пошуку. Набір Дениса цю умову виконує -- і додає
більше, ніж я просив: до кожного питання є ДОСЛІВНИЙ доказ із джерела, спосіб
перевірки й незалежна відповідь іншої моделі.

## НАБІР У GIT НЕ ЙДЕ

У повному файлі є ПІБ і номери документів (Денис це позначив). Тут
використовується лише нормативна половина -- у ній персональних даних немає, --
і навіть вона лежить поза репозиторієм. У git їде цей скрипт і зведені числа.

## Як зіставляється правильність

Доказ Дениса -- цитата з ЙОГО файлів корпусу (`a21.txt` тощо). Мої одиниці --
з `document_units`. Спільний знаменник -- сам текст: одиниця вважається
правильною, якщо містить доказ. Зіставлення нечутливе до пробілів (у копіях
акту OCR дає подвійні) і має запас на латинські гомоглифи -- на обох я вже
попадався.

Для `multi` доказ склеєний із ДВОХ різних актів, тому правильних одиниць там
кілька, і достатньо влучити в будь-яку: питання перевіряє, чи система знайшла
хоч одну зі складових.

`refusal` не має правильної одиниці за побудовою: PASS -- це коли ланцюг
сказав «не знайдено».

`trap` мітками НЕ оцінюється. N20 вимагає не назвати дубль акта двома різними
документами, N21 -- сказати, що наказ нечинний. Обидва -- про формулювання
відповіді, а не про вибір одиниці, і механічно я їх звести не можу. Тому вони
друкуються сирими для людського ока й у підсумок не входять.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg  # noqa: E402

import quote_with_llm_test as G  # noqa: E402
import resolve_identifier as R  # noqa: E402
import search_units_test as SU  # noqa: E402

# Та сама таблиця, яку читає пошук: інакше зіставлення істини йде по
# public, а видача -- по копії, і id не збігаються.
UNITS = SU.UNITS

FOLD = str.maketrans({"i": "і", "I": "І", "a": "а", "A": "А", "c": "с", "C": "С",
                      "e": "е", "E": "Е", "o": "о", "O": "О", "p": "р", "P": "Р",
                      "x": "х", "X": "Х", "y": "у", "T": "Т", "B": "В", "H": "Н",
                      "M": "М", "K": "К"})


# Варіант критерію воріт проти ДВОХ механізмів, які показала діагностика
# провалів на наборі Дениса. Обидва -- не про пошук, а про те, що ворота
# вважають відповіддю.
#
# N16: одиниця Положення про ВЛК каже «строки розгляду звернень регулюються
# Законом України "Про звернення громадян"». Сам закон у корпусі відсутній,
# але ворота відповіли -- бо текст ЗГАДУЄ потрібне. Це відповідь «за
# посиланням», і вона гірша за відмову: людина отримує впевнене твердження без
# норми під ним.
#
# N18: питання «за законом про неї» (про Нацгвардію), а текст -- зі Закону про
# національну безпеку. Тема збігається, акт інший. Ворота не звірили названий у
# питанні акт із джерелом.
#
# Скор реранкера тут не помічник: у N16 він +0.93, у N18 +7.93 -- обидва вище
# порога 0, тобто префільтр за порогом ці випадки НЕ ловить. Це прямо
# опровергає мою попередню надію.
STRICT_PLUS = (
    G.STRICT
    + " Додатково: ПОСИЛАННЯ НА ІНШИЙ ДОКУМЕНТ -- НЕ відповідь. Якщо текст "
      "лише каже, що це питання регулюється іншим законом, наказом чи "
      "інструкцією, а самої норми не містить -- відповідай, що не відповідає. "
      "І якщо в питанні НАЗВАНО конкретний акт, а показаний текст -- з іншого "
      "акта, це теж не відповідь, навіть коли тема та сама."
)
CRITERIA = {"strict": G.STRICT, "plus": STRICT_PLUS}


def norm(s):
    return " ".join(s.split()).casefold()


def _match(cur, frag):
    """Одиниці, що містять фрагмент. Пробілонечутливо, із запасом на гомоглифи."""
    needle = "%" + " ".join(frag.split()) + "%"
    cur.execute(f"""
        SELECT u.id FROM {UNITS} u
         WHERE regexp_replace(u.text, E'\\\\s+', ' ', 'g') ILIKE %s LIMIT 20
    """, (needle,))
    rows = cur.fetchall()
    if not rows:      # латинські гомоглифи в OCR -- на них я вже попадався
        cur.execute(f"""
            SELECT u.id FROM {UNITS} u
             WHERE translate(regexp_replace(u.text, E'\\\\s+', ' ', 'g'),
                             'iIaAcCeEoOpPxXyTBHMK',
                             'іІаАсСеЕоОрРхХуТВНМК') ILIKE %s LIMIT 20
        """, (needle.translate(FOLD),))
        rows = cur.fetchall()
    return {r[0] for r in rows}


def units_containing(cur, proof, max_per_fragment=5):
    """Усі одиниці корпусу, що містять доказ або його РЕЧЕННЯ.

    Перша версія зупинялась на першому знайденому фрагменті -- і через це
    недорахувала БЛИЗНЮКІВ. Пункт 194 Типової інструкції лежить і в постанові
    КМУ № 55, і в наказі № 40 (ЗСУ переписав його дослівно): правильна
    відповідь із другого акта читалась як «не туди», тобто метрика була
    занижена. Денис це передбачив у примітці й попередив, що номер пункту сам
    по собі адреси не задає.

    Вікна по 70 символів із зіставлення ПРИБРАНІ: речення -- це справжня
    норма, а довільне вікно чіпляє випадкові одиниці й роздуває «правильні»,
    перетворюючи провали на успіхи. Фрагмент, що дає більше за
    `max_per_fragment` збігів, відкидається з тієї самої причини.
    """
    whole = " ".join(proof.split())
    found, used = set(), []
    hit = _match(cur, whole)
    if hit:
        found |= hit
        used.append(whole[:50])
    # Речення беруться НАВІТЬ якщо цілий доказ знайшовся: у `multi` доказ
    # склеєний із двох актів, і цілий збіг у такому разі неможливий, а в
    # `direct` він може знайтись лише в одному з близнюків.
    for sent in re.split(r"(?<=[.;])\s+", whole):
        sent = sent.strip()
        if len(sent) < 40:
            continue
        hit = _match(cur, sent)
        if hit and len(hit) <= max_per_fragment:
            found |= hit
            used.append(sent[:50])
    return found, used


def named_act_absent(cur, question):
    """Питання називає акт у лапках, якого в корпусі немає -> причина відмови.

    Це той самий механізм, що вже є в resolve_identifier для НОМЕРІВ
    («НД ТЗІ 2.5-004-99 -> такого документа немає»), тільки для НАЗВ. Потреба
    видна з провалу N16: питання «за Законом "Про звернення громадян"», акт у
    корпусі відсутній, але одиниця Положення про ВЛК його ЗГАДУЄ -- і ворота
    відповіли за посиланням.

    Правилом промпта це не лікується: варіант критерію з приписом «посилання не
    є відповіддю» N16 не виправив, зате зламав N14. Тут натомість нічого не
    тлумачиться -- перевіряється факт: чи є в корпусі документ із такою назвою.

    Свідомо вузько: лише лапки. Назва без лапок («закон про Нацгвардію») сюди
    не потрапляє -- вгадувати, що в питанні назва акта, я не беруся, бо ціна
    помилки -- відмова на правильне питання.
    """
    for m in re.finditer(r"[«\"]([^«»\"]{8,80})[»\"]", question):
        title = m.group(1).strip()
        if not re.match(r"(?i)^(про|щодо)\s", title):
            continue
        cur.execute("""
            SELECT count(*) FROM documents
             WHERE domain = 'normative'
               AND (coalesce(doc_title,'') ILIKE %s OR coalesce(doc_title,'') ILIKE %s)
        """, (f"%{title}%", f"%{title.rstrip('.')}%"))
        if cur.fetchone()[0] == 0:
            return title
    return None


def chain(cur, encode, rescore, q, top=2, criterion=None, ctx=False,
          vectors=None, guard=False):
    res = R.resolve(cur, q)
    if res["status"] == "absent":
        return [], "за номером документа в корпусі немає"
    docs = ([d["id"] for d in res["documents"]] if res["status"] == "resolved"
            else None)
    sq = res.get("rest") or q
    # vectors=None -> вбудована колонка (e5-small, як у продукті).
    # vectors='bge-m3' -> бічна таблиця, порахована раніше. Її сюди тягну не
    # заради цікавості: провали N09 і N11 -- це саме слабкість ВЕКТОРНОЇ гілки
    # (правильна одиниця на 55 і 26 позиції відповідно, лексична її не бачить
    # узагалі), а на незалежному наборі bge-m3 давала 8 із 10 у топ-2 проти
    # 3 із 10 в e5.
    if vectors:
        import ab_embedding_models as AB
        vec = str(encode([sq], "q")[0])
        sem = AB.semantic_side(cur, AB.MODELS[vectors]["table"], vec)
    else:
        vec = str(encode(["query: " + sq])[0])
        sem = SU.semantic(cur, vec, docs=docs)
    fused = SU.dedupe_by_text(
        cur, SU.rrf_merge(SU.lexical(cur, sq, docs=docs), sem), SU.canon_map(cur))
    out_cache = {}
    if fused and rescore:
        from measure_rerank_lift import RERANK_CHARS
        pool = fused[:50]
        # ctx=True -> реранкерові даємо ШЛЯХ одиниці, не лише її текст.
        #
        # Навіщо: правильна одиниця N11 -- «1.3. Кількість оглянутих за робочий
        # день не повинна перевищувати 50 чоловік». У ній САМІЙ немає слів
        # «військово-лікарська комісія», бо вони в назві акта й у заголовку
        # розділу. Реранкер бачить короткий уривок без предмета й ставить його
        # на 12-те місце -- до воріт воно не доходить ні при top-2, ні при
        # top-5 (заміряно). Ворота контекст мають (назва й адреса передаються
        # окремими полями), а реранкер -- ні.
        texts = []
        for (d, b), _m in pool:
            body = SU.quote_of(cur, d, b)[0]
            if ctx:
                title, ident = SU.identity(cur, d, out_cache)
                head = f"{title} ({ident}), {b}: "
                texts.append((head + body)[:RERANK_CHARS])
            else:
                texts.append(body[:RERANK_CHARS])
        sc = rescore(q, texts)
        order = sorted(range(len(sc)), key=lambda j: -sc[j])
        reranked = [pool[j] for j in order] + fused[50:]
        if guard:
            # Реранкер ЗАМІНЯЄ порядок видачі своїм -- і саме тут губиться N11.
            # Заміряно: з bge-m3 правильна одиниця стоїть ПЕРШОЮ у векторній
            # гілці й першою після склейки при K=10, а до воріт не доходить,
            # бо крос-енкодер дає їй негативний скор: у тексті одиниці
            # («50 чоловік за робочий день») немає слів «військово-лікарська
            # комісія» -- вони в назві акта.
            #
            # Тому воротам віддається по одному кандидату від КОЖНОГО
            # ранжувальника: перший за склейкою і перший за реранкером. Це не
            # компроміс, а страховка від того, що один ранжувальник упевнено
            # помиляється.
            head, seen = [], set()
            for cand in ([fused[0]] if fused else []) + reranked:
                key = cand[0]
                if key not in seen:
                    seen.add(key)
                    head.append(cand)
            fused = head
        else:
            fused = reranked
    out, cache = [], out_cache
    for (doc_id, base), _meta in fused[:top]:
        title, ident = SU.identity(cur, doc_id, cache)
        body, _w, _t = SU.quote_of(cur, doc_id, base)
        data, _u, _dt, _raw, truncated = G.ask(
            q, title[:70], ident, base[:60], body,
            criterion=criterion or G.STRICT)
        if truncated:
            continue
        quote = (data.get("quote") or "").strip()
        cur.execute(f"SELECT id FROM {UNITS} WHERE document_id=%s AND base_label=%s",
                    (doc_id, base))
        uids = {r[0] for r in cur.fetchall()}
        out.append({"doc": doc_id, "label": base, "ident": ident,
                    "answers": bool(data.get("answers")),
                    "exact": bool(quote) and norm(quote) in norm(body),
                    "quote": quote, "unit_ids": uids})
    return out, ""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", required=True)
    ap.add_argument("--top", type=int, default=2)
    ap.add_argument("--criterion", choices=sorted(CRITERIA),
                    default="strict")
    ap.add_argument("--guard-top1", action="store_true",
                    help="віддати воротам топ-1 склейки ПЛЮС топ реранкера")
    ap.add_argument("--rrf-k", type=int, default=0,
                    help="перевизначити RRF_K у склейці (0 -- як у продукті, 60)")
    ap.add_argument("--vectors", default="",
                    help="bge-m3 -- узяти вектори з бічної таблиці")
    ap.add_argument("--title-check", action="store_true",
                    help="відмовляти, якщо названий у лапках акт відсутній")
    ap.add_argument("--rerank-context", action="store_true",
                    help="давати реранкерові назву акта й адресу одиниці")
    args = ap.parse_args()

    # RRF_K -- глобальна стала пошуку. Перевизначаю її тут, а не в
    # search_units_test, бо це ЕКСПЕРИМЕНТ: продукт лишається на 60, доки
    # число не доведе інше.
    #
    # Навіщо: діагностика показала, що правильна одиниця N11 стоїть ПЕРШОЮ у
    # векторній гілці (bge-m3) і все одно не доходить до воріт. При K=60
    # присутність у двох гілках на 60-х місцях (2/120) коштує дорожче за перше
    # місце в одній (1/61) -- тобто унікальна правота вектора топиться. Це
    # заміряно раніше на інших наборах; тут перевіряється на незалежних
    # питаннях Дениса.
    if args.rrf_k:
        SU.RRF_K = args.rrf_k

    rows = []
    with open(os.path.expanduser(args.set), encoding="utf-8") as fh:
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 4:
                rows.append(p[:4])
    print(f"нормативних питань: {len(rows)}, критерій "
          f"{args.criterion}, ворота по top-{args.top}, "
          f"контекст реранкеру: {'так' if args.rerank_context else 'ні'}, "
          f"вектори: {args.vectors or 'e5-small (продукт)'}, "
          f"RRF_K: {SU.RRF_K}, "
          f"страховка топ-1: {'так' if args.guard_top1 else 'ні'}")

    from build_units_test import dsn, load_encoder
    from measure_rerank_lift import load_reranker
    if args.vectors:
        import ab_embedding_models as AB
        encode = AB.load_encoder(args.vectors)
    else:
        encode = load_encoder()
    rescore = load_reranker()

    ok = fail = unscored = 0
    no_proof = []
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL hnsw.ef_search = 200")
        for qid, kind, q, proof in rows:
            if args.title_check:
                missing = named_act_absent(cur, q)
                if missing:
                    good = kind == "refusal"
                    ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
                    print()
                    print(f"[{qid} {kind}] {'PASS' if good else 'FAIL'}  "
                          f"названого акта «{missing[:40]}» у корпусі немає -- "
                          "відмова без виклику моделі")
                    continue
            got, note = chain(cur, encode, rescore, q, args.top,
                              CRITERIA[args.criterion],
                              args.rerank_context,
                              args.vectors or None,
                              args.guard_top1)
            said = [g for g in got if g["answers"]]
            usable = [g for g in said if g["exact"]]

            if kind == "refusal":
                good = not said
                ok, fail = (ok + 1, fail) if good else (ok, fail + 1)
                print(f"\n[{qid} {kind}] {'PASS' if good else 'FAIL'}  {q[:62]}")
                if said:
                    print(f"    відповів із {said[0]['ident'][:40]} / "
                          f"{said[0]['label'][:30]}")
                continue

            if kind == "trap":
                unscored += 1
                print(f"\n[{qid} {kind}] ОКОМ  {q[:62]}")
                for g in got:
                    print(f"    {'ВІДПОВІДАЄ' if g['answers'] else 'ні'} "
                          f"{g['ident'][:34]} / {g['label'][:26]}  "
                          f"дослівна: {'ТАК' if g['exact'] else 'ні'}")
                    if g["quote"]:
                        print(f"      «{g['quote'][:110]}»")
                continue

            expect, used = units_containing(cur, proof)
            if not expect:
                no_proof.append(qid)
                unscored += 1
                print(f"\n[{qid} {kind}] НЕ ОЦІНЕНО  {q[:56]}")
                print("    доказ Дениса не знайдений у моєму корпусі -- "
                      "оцінювати нічим")
                continue
            hit = any(g["unit_ids"] & expect for g in usable)
            ok, fail = (ok + 1, fail) if hit else (ok, fail + 1)
            print(f"\n[{qid} {kind}] {'PASS' if hit else 'FAIL'}  {q[:62]}")
            print(f"    доказ у {len(expect)} одиницях, знайдено фрагментом: "
                  f"«{used[0] if used else '-'}»")
            for g in got:
                mark = "ТУДИ" if g["unit_ids"] & expect else "не туди"
                print(f"    {mark}  {'ВІДПОВІДАЄ' if g['answers'] else 'ні'}  "
                      f"{g['ident'][:32]} / {g['label'][:24]}  "
                      f"дослівна: {'ТАК' if g['exact'] else 'ні'}")

    scored = ok + fail
    print(f"\n{'=' * 74}")
    print(f"МЕТРИКА 1, нормативна половина: {ok} із {scored} оцінюваних "
          f"({len(rows)} питань, {unscored} не оцінено механічно)")
    if no_proof:
        print(f"доказ не знайдений у моєму корпусі: {', '.join(no_proof)} -- "
              "це розходження КОРПУСІВ, не провал пошуку")
    print("Пастки (trap) не оцінені навмисно: вони про формулювання відповіді "
          "(«це один акт, не два», «наказ нечинний»),\nа не про вибір одиниці. "
          "Їх дивиться людина.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
