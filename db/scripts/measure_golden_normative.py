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

FOLD = str.maketrans({"i": "і", "I": "І", "a": "а", "A": "А", "c": "с", "C": "С",
                      "e": "е", "E": "Е", "o": "о", "O": "О", "p": "р", "P": "Р",
                      "x": "х", "X": "Х", "y": "у", "T": "Т", "B": "В", "H": "Н",
                      "M": "М", "K": "К"})


def norm(s):
    return " ".join(s.split()).casefold()


def fragments(proof):
    """Доказ -> фрагменти, якими його шукати: цілий, потім речення, потім вікна.

    Цілий доказ у `multi` склеєний із двох актів і не знайдеться ніде -- тому
    спуск до дрібніших частин обов'язковий, а не оптимізація."""
    whole = " ".join(proof.split())
    out = [whole]
    out += [s.strip() for s in re.split(r"(?<=[.;])\s+", whole) if len(s.strip()) >= 40]
    out += [whole[i:i + 70] for i in range(0, max(1, len(whole) - 70), 50)]
    return [f for f in out if len(f) >= 40]


def units_containing(cur, proof):
    """Одиниці мого корпусу, що містять доказ (або його частину)."""
    found, used = set(), []
    for frag in fragments(proof):
        cur.execute("""
            SELECT u.id, u.document_id, u.base_label
              FROM document_units u
             WHERE regexp_replace(u.text, E'\\\\s+', ' ', 'g') ILIKE %s
             LIMIT 20
        """, ("%" + " ".join(frag.split()) + "%",))
        rows = cur.fetchall()
        if not rows:      # запас на латинські гомоглифи в OCR
            cur.execute("""
                SELECT u.id, u.document_id, u.base_label
                  FROM document_units u
                 WHERE translate(regexp_replace(u.text, E'\\\\s+', ' ', 'g'),
                                 'iIaAcCeEoOpPxXyTBHMK',
                                 'іІаАсСеЕоОрРхХуТВНМК') ILIKE %s
                 LIMIT 20
            """, ("%" + " ".join(frag.split()).translate(FOLD) + "%",))
            rows = cur.fetchall()
        if rows:
            used.append(frag[:50])
            found |= {r[0] for r in rows}
            if len(found) >= 1 and frag == fragments(proof)[0]:
                break        # цілий доказ знайшовся -- дрібніші не потрібні
    return found, used


def chain(cur, encode, rescore, q, top=2):
    res = R.resolve(cur, q)
    if res["status"] == "absent":
        return [], "за номером документа в корпусі немає"
    docs = ([d["id"] for d in res["documents"]] if res["status"] == "resolved"
            else None)
    sq = res.get("rest") or q
    vec = str(encode(["query: " + sq])[0])
    fused = SU.dedupe_by_text(
        cur, SU.rrf_merge(SU.lexical(cur, sq, docs=docs),
                          SU.semantic(cur, vec, docs=docs)), SU.canon_map(cur))
    if fused and rescore:
        from measure_rerank_lift import RERANK_CHARS
        pool = fused[:50]
        texts = [SU.quote_of(cur, d, b)[0][:RERANK_CHARS] for (d, b), _m in pool]
        sc = rescore(q, texts)
        order = sorted(range(len(sc)), key=lambda j: -sc[j])
        fused = [pool[j] for j in order] + fused[50:]
    out, cache = [], {}
    for (doc_id, base), _meta in fused[:top]:
        title, ident = SU.identity(cur, doc_id, cache)
        body, _w, _t = SU.quote_of(cur, doc_id, base)
        data, _u, _dt, _raw, truncated = G.ask(q, title[:70], ident, base[:60], body)
        if truncated:
            continue
        quote = (data.get("quote") or "").strip()
        cur.execute("SELECT id FROM document_units WHERE document_id=%s AND base_label=%s",
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
    args = ap.parse_args()

    rows = []
    with open(os.path.expanduser(args.set), encoding="utf-8") as fh:
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 4:
                rows.append(p[:4])
    print(f"нормативних питань: {len(rows)}")

    from build_units_test import dsn, load_encoder
    from measure_rerank_lift import load_reranker
    encode, rescore = load_encoder(), load_reranker()

    ok = fail = unscored = 0
    no_proof = []
    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL hnsw.ef_search = 200")
        for qid, kind, q, proof in rows:
            got, note = chain(cur, encode, rescore, q, args.top)
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
