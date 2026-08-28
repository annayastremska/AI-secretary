"""Пошук по структурних одиницях зі склеюванням частин у видачі.

Запуск:
    python db/scripts/search_units_test.py "за скільки днів подавати рапорт"
    python db/scripts/search_units_test.py --questions eval/retrieval/probe.txt
    python db/scripts/search_units_test.py "..." --compare   # ще й старі куски

## Що тут нового проти search_hybrid.py

**Гранулярність пошуку і гранулярність відповіді -- різні речі.** Шукаємо по
частинах (дрібні, влазять у 512 токенів моделі, дають точний збіг), а цитуємо
цілу логічну одиницю: беремо `min(char_start)..max(char_end)` по всіх частинах
із тією самою `base_label` і вирізаємо з `documents.text_content`.

Це закриває три різні болячки одним рухом:

* цитата більше не починається з середини переліку (37% частин починаються з
  малої літери -- і користувач їх тепер не бачить);
* частини однієї одиниці ЗЛИВАЮТЬСЯ в один результат, а не займають кілька
  місць у топі, витісняючи інші документи;
* джерело пишеться адресою («Стаття 12»), а не зсувом «@45000».

Обмеження, яке лишається чесним: якщо логічна одиниця величезна, цитувати її
цілком безглуздо. Тому цитата обмежена `QUOTE_CAP`, і в такому разі це видно
у виводі як «фрагмент».
"""
import argparse
import os
import sys

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "db", "scripts"))

import extract_document_identity as E  # noqa: E402
import resolve_identifier as R  # noqa: E402

# Таблиці задаються повними іменами через середовище: після міграції одиниці
# живуть у постійній схемі (`document_units`), а тестова лишається для
# порівняння. Один і той самий скрипт мусить уміти в обидві.
UNITS = os.environ.get("UNITS_TABLE", "document_units")
GROUPS = os.environ.get("GROUPS_TABLE", "document_groups")
RRF_K = 60
CANDIDATES = 70
QUOTE_CAP = 3000
QUERY_PREFIX = "query: "

# Домен як фільтр УСЕРЕДИНІ запиту -- пропозиція Ані
# (docs/contracts/2026-08-22_domain-as-search-filter.md).
#
# Сьогодні цей фільтр не змінює НІ ОДНОГО рядка: одиниці будуються лише з
# `domain='normative'` (див. build_units_test.py), тому весь індекс і так
# процедурний. Заміряно: leave 95 документів, deployment 63, normative 44 --
# але одиниці є тільки в normative, а факти тільки в leave/deployment/staffing.
# Тобто два шляхи відповіді розділені ДАНИМИ, а не запитом.
#
# Фільтр усе одно ставиться, і причина конкретна: інваріант живе в скрипті
# індексації, а не в запиті, і зникає в ту мить, коли хтось проіндексує
# гібридний документ (довідка ВЛК дає І факти, І текст для цитати -- це
# розділ 7 архітектурного документа, тобто планована річ). Тоді пошук почне
# тихо віддавати відпускні квитки на процедурні питання -- рівно та поломка,
# про яку Аня й писала: на «скільком зараз у відпустці» найрелевантнішим за
# текстом буде фрагмент СТАТУТУ, а не квиток.
PROCEDURAL = ("normative",)

# Розширення запиту синонімами. Таблиця будується з КОРПУСУ
# (build_synonyms.py), а не зі списку від руки: нормативні тексти самі
# оголошують свої скорочення -- «(далі - ВМС ЗСУ)», «несанкціонований доступ
# (НСД)».
#
# Розширюється ЛИШЕ лексична гілка. Причина: вектор запиту -- це один вектор,
# і дописування в текст запиту синонімів його РОЗМИВАЄ (середнє по кількох
# формулюваннях схоже на все погано). Лексика ж працює по окремих лемах, тому
# додатковий терм там нічого не псує -- він або збігається, або ні.
SYNONYMS = os.environ.get("SYNONYMS_TABLE", "andriy_test.synonyms")


def only_docs(cur, doc_ids):
    """Обмежує пошук переліком документів -- для запиту з номером.

    Резолвер і пошук мусять СКЛАДАТИСЯ, а не конкурувати: на «що каже
    НД ТЗІ 2.5-004-99 про паролі» номер визначає ДЕ шукати, а решта запиту --
    ЩО шукати. Без цього обмеження пошук іде по всьому корпусу й приводить
    документи, які лише цитують цей номер (саме так стрес-тест і провалився).
    """
    return doc_ids


def expand(cur, query, max_terms=6):
    """-> список додаткових термів для лексичної гілки.

    Двобічно: на «НСД» додає «несанкціонований доступ», на «несанкціонований
    доступ» додає «НСД». Друге не менш важливе -- у документі може стояти саме
    скорочення, а людина пише повністю.
    """
    try:
        # Скорочення шукається ПО МЕЖАХ СЛОВА (`\y`), а не підрядком.
        # Заміряно, чому: з `ILIKE '%%abbr%%'` двобуквенне `ВІ` знаходилось
        # усередині слова «ВІдпустку», і запит «за скільки днів подавати
        # рапорт на відпустку» -- без жодного скорочення -- отримував
        # синоніми «автомобільної техніки» й «відкрита інформація».
        # Повна форма лишається підрядком: це кілька слів, там межа не потрібна.
        cur.execute(rf"""
            SELECT abbr, full_form FROM {SYNONYMS}
             WHERE %(q)s ~* ('\y' || abbr || '\y')
                OR %(q)s ILIKE '%%' || full_form || '%%'
             GROUP BY abbr, full_form
             ORDER BY length(abbr) DESC, max(seen) DESC, abbr
             LIMIT %(lim)s
        """, {"q": query, "lim": max_terms})
    except psycopg.Error as e:
        # Глушник тут був подвійною помилкою: він ховав справжню причину І
        # лишав транзакцію в аварійному стані, після чого падали всі наступні
        # запити з «current transaction is aborted». Тепер відкат і скарга.
        cur.connection.rollback()
        print(f"  [синоніми недоступні: {str(e).splitlines()[0][:90]}]")
        return []
    out = []
    low = query.lower()
    for abbr, full in cur.fetchall():
        # Додаємо ПРОТИЛЕЖНУ форму до тієї, що вже є в запиті.
        out.append(full if abbr.lower() in low else abbr)
    return [t for t in dict.fromkeys(out) if t.lower() not in low]


def lexical(cur, query, limit=CANDIDATES, docs=None, synonyms=True):
    # АБО, не І: на дрібних одиницях вимога «усі слова в одному куску» дає нуль
    # кандидатів -- це вже було виміряно на фрагментах.
    q_text = query
    if synonyms:
        extra = expand(cur, query)
        if extra:
            q_text = query + " " + " ".join(extra)
    tsq = ("replace(websearch_to_tsquery('ukrainian', %(q)s)::text,"
           " ' & ', ' | ')::tsquery")
    # Обмеження по документах збирається ЗМІННОЮ, а не заглушкою в тексті:
    # запит -- f-рядок, тому `{doc_filter}` у ньому Python обчислює одразу.
    dfilter = "AND u.document_id = ANY(%(docs)s)" if docs else ""
    cur.execute(f"""
        SELECT u.id, u.document_id, u.base_label, ts_rank(u.tsv, {tsq}) AS score
          FROM {UNITS} u
          JOIN documents d ON d.id = u.document_id
         WHERE u.tsv @@ {tsq}
           AND d.validity = 'current'
           AND d.domain = ANY(%(domains)s)
           {dfilter}
         ORDER BY score DESC LIMIT %(lim)s
    """, {"q": q_text, "lim": limit, "docs": docs,
          "domains": list(PROCEDURAL)})
    return cur.fetchall()


def semantic(cur, vec, limit=CANDIDATES, docs=None):
    # HNSW за замовчуванням обходить лише ~40 вузлів (`hnsw.ef_search`), і при
    # фільтрі по чинності частина з них відпадає ПІСЛЯ індексу -- тому запит на
    # 70 кандидатів віддавав 20-52. Піднімаємо обхід: він мусить бути більшим
    # за потрібну кількість із запасом на фільтр.
    cur.execute("SET LOCAL hnsw.ef_search = 200")
    dfilter = "AND u.document_id = ANY(%(docs)s)" if docs else ""
    cur.execute(f"""
        SELECT u.id, u.document_id, u.base_label,
               1 - (u.embedding <=> %(v)s::public.vector) AS score
          FROM {UNITS} u
          JOIN documents d ON d.id = u.document_id
         WHERE u.embedding IS NOT NULL
           AND d.validity = 'current'
           AND d.domain = ANY(%(domains)s)
           {dfilter}
         ORDER BY u.embedding <=> %(v)s::public.vector LIMIT %(lim)s
    """, {"v": vec, "lim": limit, "docs": docs,
          "domains": list(PROCEDURAL)})
    return cur.fetchall()


def canon_map(cur):
    """document_id -> канонічний документ групи дублікатів."""
    try:
        cur.execute(f"SELECT document_id, canonical_id FROM {GROUPS}")
        return {a: b for a, b in cur.fetchall()}
    except Exception:
        return {}


def dedupe_by_text(cur, fused, canon, prefix=80):
    """Прибирає повтори того самого тексту в межах групи дублікатів.

    Ключа `(канонічний документ, мітка)` НЕ достатньо: у документах-дублікатах
    мітки різні (у 205 пункт зветься `20/20.3`, у 222 інакше), бо нумерація
    вивантажена по-різному. Тому склеюємо ще й за початком тексту.

    Навіщо: на питанні про рапорт ОБА місця топ-2 пішли на ту саму цитату з
    пари 237/238. Дублікат не просто повторюється -- він витісняє з видачі
    документи, які могли б відповісти, і ворота двічі платять за той самий
    текст.
    """
    seen, out = set(), []
    for (doc_id, base), meta in fused:
        body, _s, _t = quote_of(cur, doc_id, base)
        key = (canon.get(doc_id, doc_id), " ".join(body.split())[:prefix].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(((doc_id, base), meta))
    return out


def rrf_merge(*lists):
    """RRF + СКЛЕЮВАННЯ: ключ -- логічна одиниця, а не частина.

    Саме тут частини перестають конкурувати між собою: усі частини однієї
    `base_label` складають свої внески в один запис.
    """
    fused = {}
    for lst in lists:
        for pos, (uid, doc_id, base, _s) in enumerate(lst, start=1):
            key = (doc_id, base)
            e = fused.setdefault(key, {"rrf": 0.0, "parts": set(), "branches": 0})
            e["rrf"] += 1.0 / (RRF_K + pos)
            e["parts"].add(uid)
        for key in {(d, b) for _, d, b, _ in lst}:
            if key in fused:
                fused[key]["branches"] += 1
    return sorted(fused.items(), key=lambda kv: -kv[1]["rrf"])


def quote_of(cur, doc_id, base_label):
    """Цитата -- ціла логічна одиниця з ОРИГІНАЛУ, а не текст частини."""
    cur.execute(f"""
        SELECT min(char_start), max(char_end), bool_or(from_length_split)
          FROM {UNITS}
         WHERE document_id = %s AND base_label = %s
    """, (doc_id, base_label))
    lo, hi, was_split = cur.fetchone()
    cur.execute("SELECT text_content FROM documents WHERE id = %s", (doc_id,))
    full = cur.fetchone()[0]
    body = full[lo:hi]
    trimmed = False
    if len(body) > QUOTE_CAP:
        body, trimmed = body[:QUOTE_CAP], True
    return " ".join(body.split()), was_split, trimmed


def identity(cur, doc_id, cache):
    """Реквізити для підвалу відповіді -- зі СТОВПЦІВ documents, і лише потім
    повторним розбором тексту.

    Було навпаки: розбір щоразу заново, а `doc_identifier` не читався взагалі.
    Знайдено на золотому наборі Дениса: правильна відповідь із наказу № 606
    (документ 216) підписувалась як «—», хоч номер у базі стоїть. Тобто
    міграція b5f1c7a92d63 додала ці стовпці саме для підвалу, а підвал їх
    ігнорував -- і цитата виходила без джерела, що для цього продукту гірше за
    відсутність цитати.

    Розбір лишається запасним шляхом: у трьох внутрішніх інструкцій номера
    немає взагалі, і для них «—» -- правда, а не збій.
    """
    if doc_id in cache:
        return cache[doc_id]
    cur.execute("SELECT doc_title, doc_identifier, text_content "
                "FROM documents WHERE id = %s", (doc_id,))
    title, ident, text = cur.fetchone()
    if not (title and ident):
        info = E.extract(text or "")
        title = title or info.get("title")
        ident = ident or info.get("identifier")
    cache[doc_id] = (title or f"documents.id={doc_id}", ident or "—")
    return cache[doc_id]


def answer(cur, encode, question, top=3, chars=420):
    print(f"\n{'='*78}\nПИТАННЯ: {question}")

    # Крок 0: номер у запиті вирішується ДО пошуку. Резолвер і пошук
    # складаються: номер каже ДЕ шукати, решта запиту -- ЩО шукати. Без цього
    # запит за номером іде по всьому корпусу й приводить документи, які лише
    # ЦИТУЮТЬ цей номер -- саме так стрес-тест і провалився.
    res = R.resolve(cur, question)
    docs, search_q = None, question
    if res["status"] == "absent":
        print(f"  ЗА НОМЕРОМ {res['missing']} документа в корпусі НЕМА.")
        print("  (це окрема відповідь, не «не знайшлось»: документ не завантажений)")
        return []
    if res["status"] == "resolved":
        docs = [d["id"] for d in res["documents"]]
        for d in res["documents"]:
            print(f"  ЗА НОМЕРОМ: {d['identifier']} -> "
                  f"{(d['title'] or '')[:52]} [{d['validity']}] id={d['id']}")
        if not res["rest"]:
            print("  (у запиті лише номер -- це картка документа, шукати нічого)")
            return [(d["id"], None, 1.0) for d in res["documents"]]
        search_q = res["rest"]
        print(f"  шукаю ВСЕРЕДИНІ нього: {search_q!r}")

    vec = str(encode([QUERY_PREFIX + search_q])[0])
    lex = lexical(cur, search_q, docs=docs)
    sem = semantic(cur, vec, docs=docs)
    fused = dedupe_by_text(cur, rrf_merge(lex, sem), canon_map(cur))

    print(f"  кандидатів: лексика {len(lex)}, семантика {len(sem)}, "
          f"логічних одиниць після склеювання {len(fused)}")
    if not fused:
        print("  НІЧОГО НЕ ЗНАЙДЕНО")
        return []
    cache = {}
    shown = []
    for (doc_id, base), meta in fused[:top]:
        title, ident = identity(cur, doc_id, cache)
        body, was_split, trimmed = quote_of(cur, doc_id, base)
        both = "  [обидві гілки]" if meta["branches"] >= 2 else ""
        addr = base + (" (фрагмент)" if was_split or trimmed else "")
        print(f"\n  rrf={meta['rrf']:.4f}  частин {len(meta['parts'])}{both}")
        print(f"  Джерело: {title[:60]} | {ident} | {addr}")
        print(f"  {body[:chars]}")
        shown.append((doc_id, base, meta["rrf"]))
    return shown


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query", nargs="*")
    ap.add_argument("--questions")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--chars", type=int, default=420)
    args = ap.parse_args(argv)

    questions = []
    if args.questions:
        with open(args.questions, encoding="utf-8") as f:
            questions = [l.strip() for l in f
                         if l.strip() and not l.startswith("#")]
    if args.query:
        questions.append(" ".join(args.query))

    from build_units_test import load_encoder, dsn
    encode = load_encoder()
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        for q in questions:
            answer(cur, encode, q, args.top, args.chars)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
