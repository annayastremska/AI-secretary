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

SCHEMA = "andriy_test"
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
          FROM {SCHEMA}.document_units u
          JOIN documents d ON d.id = u.document_id
         WHERE u.tsv @@ {tsq}
           AND d.validity = 'current'
         ORDER BY score DESC LIMIT %(lim)s
    """, {"q": query, "lim": limit})
    return cur.fetchall()


def semantic(cur, vec, limit=CANDIDATES):
    cur.execute(f"""
        SELECT u.id, u.document_id, u.base_label,
               1 - (u.embedding <=> %(v)s::public.vector) AS score
          FROM {SCHEMA}.document_units u
          JOIN documents d ON d.id = u.document_id
         WHERE u.embedding IS NOT NULL
           AND d.validity = 'current'
         ORDER BY u.embedding <=> %(v)s::public.vector LIMIT %(lim)s
    """, {"v": vec, "lim": limit})
    return cur.fetchall()


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
          FROM {SCHEMA}.document_units
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
    fused = rrf_merge(lex, sem)

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
