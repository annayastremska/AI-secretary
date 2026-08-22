"""Гібридний пошук: лексика (FTS) + семантика (pgvector), злиття через RRF.

Запуск (потрібен .venv-ml -- там torch для ембеддингу запиту):
    .venv-ml/Scripts/python db/scripts/search_hybrid.py "за скільки днів подавати рапорт"
    .venv-ml/Scripts/python db/scripts/search_hybrid.py "НД ТЗІ 2.5-004-99" --compare
    .venv-ml/Scripts/python db/scripts/search_hybrid.py "як захистити мережу" --limit 5

`--compare` показує три списки поруч -- лексичний, векторний і злитий. Це не
косметика: без нього неможливо сказати, чи гібрид узагалі щось додає, а саме
це питання ми й перевіряємо.

## Чому RRF, а не сума нормалізованих скорів

`ts_rank` і косинусна відстань живуть у різних шкалах, і немає осмисленого
способу привести їх до спільної: 0.9 у одного і 0.9 у іншого означають різні
речі. RRF складає **місця в списках**, не бали, тому шкали взагалі не
потрібні. `RRF_K=60` -- значення з банківського документа, там же й
обґрунтування: ранги стійкіші за бали при різномовних запитах.

## Що тут навмисно НЕ зроблено

Реранкера немає. CrossEncoder оцінює пару «запит + фрагмент» разом і
відрізняє «схоже за темою» від «відповідає на питання» -- але він рахує
~140 пар на кожне питання, і на CPU це десятки секунд. Спершу міряємо, чи
гібрид без нього достатній.

Правило на майбутнє закладено вже зараз: фрагмент без оцінки реранкера НЕ
вважається таким, що прошов поріг. Тому поріг тут застосовується до
злитого рангу, а не до сирих скорів окремих гілок -- інакше при додаванні
реранкера логіку доведеться переписувати.
"""
import argparse
import os
import sys

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "db", "scripts"))

RRF_K = 60
CANDIDATES = 70          # з кожної гілки, як у банківському документі
FUSED_MIN_SOURCES = 1    # 2 = показувати лише те, що знайшли ОБИДВІ гілки


def lexical(cur, query, limit=CANDIDATES, mode="or"):
    """mode='or' -- як BM25: слова об'єднані АБО, впорядковує ts_rank.
    mode='and' -- як websearch_to_tsquery за замовчуванням.

    Чому за замовчуванням АБО. websearch_to_tsquery зліплює слова через `&`,
    тобто вимагає ВСІ слова в ОДНОМУ фрагменті. На документах цілком це
    працювало, а на фрагментах по 1200 символів -- ні: виміряно, запит «за
    скільки днів подавати рапорт на відпустку» давав лексикою РІВНО НУЛЬ
    кандидатів, і гібрид ставав звичайним векторним пошуком.

    АБО дає повноту, а порядок усе одно визначає ts_rank (він враховує, скільки
    слів збіглося і як часто). Для точного пошуку за номером пункту `and`
    лишається доступним.
    """
    tsq = ("websearch_to_tsquery('ukrainian', %(q)s)" if mode == "and" else
           "replace(websearch_to_tsquery('ukrainian', %(q)s)::text, ' & ', ' | ')::tsquery")
    cur.execute(f"""
        SELECT ch.id, ch.document_id,
               ts_rank(to_tsvector('ukrainian', ch.text), {tsq}) AS score
          FROM document_chunks ch
          JOIN documents d ON d.id = ch.document_id
         WHERE d.domain = 'normative' AND d.validity = 'current'
           AND to_tsvector('ukrainian', ch.text) @@ {tsq}
         ORDER BY score DESC
         LIMIT %(lim)s
    """, {"q": query, "lim": limit})
    return cur.fetchall()


def semantic(cur, vec, limit=CANDIDATES):
    # Фільтр чинності -- у самому запиті, не пост-фільтром: інакше кількість
    # результатів і латентність самі розкажуть про існування недоступного.
    cur.execute("""
        SELECT ch.id, ch.document_id,
               1 - (ch.embedding <=> %(v)s::vector) AS score
          FROM document_chunks ch
          JOIN documents d ON d.id = ch.document_id
         WHERE d.domain = 'normative' AND d.validity = 'current'
           AND ch.embedding IS NOT NULL
         ORDER BY ch.embedding <=> %(v)s::vector
         LIMIT %(lim)s
    """, {"v": vec, "lim": limit})
    return cur.fetchall()


def rrf(*ranked_lists):
    """Reciprocal Rank Fusion: складаємо 1/(k+місце), а не бали."""
    fused = {}
    for lst in ranked_lists:
        for pos, (chunk_id, doc_id, _score) in enumerate(lst, start=1):
            entry = fused.setdefault(chunk_id, {"doc_id": doc_id, "rrf": 0.0, "sources": 0})
            entry["rrf"] += 1.0 / (RRF_K + pos)
            entry["sources"] += 1
    return sorted(fused.items(), key=lambda kv: -kv[1]["rrf"])


def show(cur, title, items, limit):
    print(f"\n── {title}")
    if not items:
        print("   (порожньо)")
        return
    for chunk_id, meta in items[:limit]:
        cur.execute("""
            SELECT d.pipeline_meta ->> 'title', ch.text, ch.char_start
              FROM document_chunks ch JOIN documents d ON d.id = ch.document_id
             WHERE ch.id = %s
        """, (chunk_id,))
        doc_title, text, char_start = cur.fetchone()
        mark = "" if meta["sources"] < 2 else "  [обидві гілки]"
        print(f"   {meta['rrf']:.4f}{mark}  {doc_title[:56]}  @{char_start}")
        print(f"           {' '.join(text.split())[:170]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query", nargs="+")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--compare", action="store_true", help="три списки поруч")
    ap.add_argument("--both-only", action="store_true",
                    help="лише фрагменти, знайдені обома гілками")
    ap.add_argument("--lexical-and", action="store_true",
                    help="вимагати ВСІ слова в одному фрагменті (для точного пошуку)")
    args = ap.parse_args(argv)
    query = " ".join(args.query)

    from build_chunk_index import load_encoder, QUERY_PREFIX
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

    encode = load_encoder()
    vec = str(encode([QUERY_PREFIX + query])[0])

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        lex = lexical(cur, query, mode="and" if args.lexical_and else "or")
        sem = semantic(cur, vec)
        fused = rrf(lex, sem)
        if args.both_only:
            fused = [(k, v) for k, v in fused if v["sources"] >= 2]

        print(f"Запит: {query!r}")
        print(f"лексика: {len(lex)} кандидатів · семантика: {len(sem)} · злито: {len(fused)}")

        if args.compare:
            show(cur, "ЛЕКСИКА (FTS)", rrf(lex), args.limit)
            show(cur, "СЕМАНТИКА (вектори)", rrf(sem), args.limit)
        show(cur, "ГІБРИД (RRF)", fused, args.limit)

        both = sum(1 for _, v in fused if v["sources"] >= 2)
        print(f"\nзнайдено обома гілками: {both} з {len(fused)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
