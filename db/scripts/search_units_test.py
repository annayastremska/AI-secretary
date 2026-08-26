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

# Таблиці задаються повними іменами через середовище: після міграції одиниці
# живуть у постійній схемі (`document_units`), а тестова лишається для
# порівняння. Один і той самий скрипт мусить уміти в обидві.
UNITS = os.environ.get("UNITS_TABLE", "document_units")
GROUPS = os.environ.get("GROUPS_TABLE", "document_groups")
RRF_K = 60
CANDIDATES = 70
QUOTE_CAP = 3000
QUERY_PREFIX = "query: "


def lexical(cur, query, limit=CANDIDATES):
    # АБО, не І: на дрібних одиницях вимога «усі слова в одному куску» дає нуль
    # кандидатів -- це вже було виміряно на фрагментах.
    tsq = ("replace(websearch_to_tsquery('ukrainian', %(q)s)::text,"
           " ' & ', ' | ')::tsquery")
    cur.execute(f"""
        SELECT u.id, u.document_id, u.base_label, ts_rank(u.tsv, {tsq}) AS score
          FROM {UNITS} u
          JOIN documents d ON d.id = u.document_id
         WHERE u.tsv @@ {tsq}
           AND d.validity = 'current'
         ORDER BY score DESC LIMIT %(lim)s
    """, {"q": query, "lim": limit})
    return cur.fetchall()


def semantic(cur, vec, limit=CANDIDATES):
    # HNSW за замовчуванням обходить лише ~40 вузлів (`hnsw.ef_search`), і при
    # фільтрі по чинності частина з них відпадає ПІСЛЯ індексу -- тому запит на
    # 70 кандидатів віддавав 20-52. Піднімаємо обхід: він мусить бути більшим
    # за потрібну кількість із запасом на фільтр.
    cur.execute("SET LOCAL hnsw.ef_search = 200")
    cur.execute(f"""
        SELECT u.id, u.document_id, u.base_label,
               1 - (u.embedding <=> %(v)s::public.vector) AS score
          FROM {UNITS} u
          JOIN documents d ON d.id = u.document_id
         WHERE u.embedding IS NOT NULL
           AND d.validity = 'current'
         ORDER BY u.embedding <=> %(v)s::public.vector LIMIT %(lim)s
    """, {"v": vec, "lim": limit})
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
    if doc_id in cache:
        return cache[doc_id]
    cur.execute("SELECT text_content FROM documents WHERE id = %s", (doc_id,))
    info = E.extract(cur.fetchone()[0])
    cache[doc_id] = (info.get("title") or f"documents.id={doc_id}",
                     info.get("identifier") or "—")
    return cache[doc_id]


def answer(cur, encode, question, top=3, chars=420):
    vec = str(encode([QUERY_PREFIX + question])[0])
    lex = lexical(cur, question)
    sem = semantic(cur, vec)
    fused = dedupe_by_text(cur, rrf_merge(lex, sem), canon_map(cur))

    print(f"\n{'='*78}\nПИТАННЯ: {question}")
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
