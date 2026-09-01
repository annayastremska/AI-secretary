"""Сленг у запиті -> терміни, які насправді є в корпусі.

Запуск (модель піднята):
    python db/scripts/expand_slang.py "як називається наша мережа вайфай"
    python db/scripts/expand_slang.py --questions eval/retrieval/slang.txt

## Що саме тут ламається

Не кількість кандидатів. Заміряно: «як називається наша мережа вайфай» дає 70
лексичних кандидатів -- і всі не ті. Ламається РАНЖУВАННЯ: слово «вайфай» у
корпусі не існує, тому воно не вносить нічого, а решта запиту («як називається
наша мережа») занадто загальна -- вектор іде в НД ТЗІ про захист інформації,
бо там про мережі сотні сторінок.

Тобто зникає рівно те слово, яке одне й відрізняло б потрібний документ від
сорока інших.

## Чому не «відправити запит моделі на переклад»

Дорого й ненадійно. Дорого -- бо 2-3 секунди на КОЖЕН запит, з яких переважна
більшість перекладу не потребує. Ненадійно -- бо модель запропонує «бездротова
локальна мережа», а в документі стоїть «бездротовий сегмент», і лексично це не
допоможе нічим.

## Схема: модель пропонує, механіка перевіряє

Той самий патерн, що дав 100% на реквізитах (регулярка знаходить кандидатів ->
модель обирає) і нуль галюцинацій у цитатах (модель цитує -> перевірка
підрядком). Ненадійний крок завжди обгорнутий у перевірку.

1. **Детектор** -- один SQL-запит: які леми запиту не трапляються в корпусі
   ЖОДНОГО разу. Заміряно, що він точний: на сленгових запитах виділяє рівно
   винне слово (`вайфай`, `підрубити`, `жерти`, `флешка`), на нормальних
   питаннях не спрацьовує ні разу.
2. **Тригер** -- модель викликається ЛИШЕ якщо такі леми є. На звичайних
   запитах вартість нуль.
3. **Модель отримує не запит, а слова** -- вузька задача, короткий вивід.
4. **Перевірка** -- кожен запропонований термін шукається в корпусі. Не
   знайшлось -> відкидаємо. Вигадане не доходить до пошуку.
5. **Розширення** -- підтверджені терміни йдуть у лексичну гілку.

Побічно це не шкодить питанням не по темі: на «Leopard 2» детектор спрацює,
модель запропонує щось про танки, перевірка відкине все як відсутнє, розширення
додасть нуль термінів -- і відмова відбудеться, як і раніше.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "db", "scripts"))

import search_units_test as SU  # noqa: E402

LLM = os.environ.get("LLM_URL", "http://127.0.0.1:8081/v1/chat/completions")

# Термін, що трапляється в надто багатьох одиницях, нічого не РОЗРІЗНЯЄ.
# Заміряно: «жерти» -> «використовувати» підтвердилось 101 збігом, але це
# загальне дієслово -- у лексичній гілці воно додає шум, а не сигнал. Так само
# «забезпечити доступ» (18) і «бойова машина» (68). Сенс розширення саме в
# рідкісному слові: «бездротова мережа» має 1 збіг і вказує точно.
MAX_HITS = 25

# Чому тут таблиця лишається в замовчуванні, а в `search_units_test.SYNONYMS`
# -- ні. Там таблиця вмикала ФІЧУ, зміряно шкідливу, тому замовчування зняли.
# Тут таблиця -- це КЕШ відповідей моделі, який цей самий скрипт і створює
# (`CACHE_DDL` нижче). Порожнє замовчування означало б «питай модель заново
# щоразу», тобто плату за кожен прогін замість вимкненої фічи. Це різні речі.
#
# Спільне в них інше -- ТИХА деградація, і саме її прибрано: `cached()`
# викликається в циклі по кожному невідомому слову, тому зламаний кеш раніше
# не давав ЖОДНОГО повідомлення й тихо перепитував модель кожен прогін.
CACHE_TABLE = os.environ.get("SLANG_TABLE", "andriy_test.slang_terms")

CACHE_DDL = f"""
CREATE TABLE IF NOT EXISTS {CACHE_TABLE} (
    word      text NOT NULL,
    term      text NOT NULL,
    hits      integer NOT NULL,
    kept      boolean NOT NULL,
    added_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (word, term)
);
"""

SYSTEM = (
    "Ти перекладаєш розмовні, сленгові й транслітеровані слова на мову "
    "українських нормативних документів Збройних Сил. Відповідай лише JSON."
)

USER = """Військовослужбовець питає:
{query}

У цьому питанні є слова, яких у корпусі нормативних документів НЕМАЄ ЖОДНОГО
РАЗУ: {words}

Через це пошук їх просто не бачить, і питання втрачає саме те слово, яке його
відрізняє.

Для КАЖНОГО такого слова дай 1-4 варіанти, якими це саме поняття називають у
службових документах: офіційний термін, поширене скорочення, англійське
написання, описова назва. Пиши варіанти в НАЗИВНОМУ відмінку однини або так, як
вони стояли б у тексті документа.

Приклади напряму думки (не копіюй їх, це лише зразок формату):
  «флешка» -> ["носій інформації", "USB-накопичувач", "змінний носій"]
  «підрубити» -> ["підключити", "підключення"]

Якщо слово не має відповідника в службовій мові (власна назва, іноземна
техніка, побутове поняття поза предметом) -- дай для нього порожній список.
Не вигадуй терміни, яких не буває: ми перевіряємо кожен варіант на наявність у
корпусі й відкидаємо відсутні.

Поверни рівно такий JSON:
{{"варіанти": {{"<слово>": ["<термін>", "..."]}}}}"""


def unknown_lemmas(cur, query):
    """Леми запиту, яких у корпусі немає ЖОДНОГО разу."""
    cur.execute("SELECT unnest(tsvector_to_array(to_tsvector('ukrainian', %s)))",
                (query,))
    lemmas = sorted({r[0] for r in cur.fetchall() if len(r[0]) > 2})
    if not lemmas:
        return []
    cur.execute(f"""
        SELECT l FROM unnest(%s::text[]) AS l
         WHERE NOT EXISTS (SELECT 1 FROM {SU.UNITS} u
                            WHERE u.tsv @@ plainto_tsquery('simple', l))
    """, (lemmas,))
    return sorted(r[0] for r in cur.fetchall())


def found_in_corpus(cur, term):
    """Чи знайшло б щось пошук за цим терміном. Пряма перевірка, не за лемами.

    Саме так формулюється потрібне питання: не «чи існує таке слово», а «чи
    знайде його пошук». Термін, за яким нічого не знаходиться, у розширенні
    не має сенсу.
    """
    cur.execute(f"""SELECT count(*) FROM {SU.UNITS}
                     WHERE tsv @@ plainto_tsquery('ukrainian', %s)""", (term,))
    return cur.fetchone()[0]


def ask_model(query, words, max_tokens=400):
    body = json.dumps({
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER.format(
                query=query, words=", ".join(f"«{w}»" for w in words))},
        ],
        "temperature": 0, "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(LLM, data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    r = json.load(urllib.request.urlopen(req, timeout=180))
    txt = r["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", txt, re.S)
    try:
        data = json.loads(m.group()) if m else {}
    except Exception:
        data = {}
    return data.get("варіанти") or data, r.get("usage", {}), time.time() - t0


def check_cache(cur):
    """Перевірка готовності кешу -> текст стану. Кидає на помилці КОНФІГУРАЦІЇ.

    Розрізняє два випадки, які раніше зливались в одне тихе `return None`:

    * таблиці ще НЕМА -- нормально, це перший прогін, `remember()` її створить;
    * таблиця є, але не читається (немає прав, не та схема) -- помилка
      конфігурації. Вона мусить бути видна ОДИН РАЗ на старті, а не глушитись
      на кожному слові в циклі.
    """
    try:
        cur.execute(f"SELECT count(*) FROM {CACHE_TABLE}")
        (n,) = cur.fetchone()
    except psycopg.errors.UndefinedTable:
        cur.connection.rollback()
        return f"кеш {CACHE_TABLE}: таблиці ще немає, буде створена"
    except psycopg.Error as e:
        cur.connection.rollback()
        raise RuntimeError(
            f"кеш {CACHE_TABLE} існує, але не читається: "
            f"{str(e).splitlines()[0][:120]}. Це помилка конфігурації: або "
            f"вкажіть іншу таблицю через SLANG_TABLE, або дайте доступ. Без "
            f"кешу скрипт перепитував би модель на кожному прогоні."
        ) from e
    return f"кеш {CACHE_TABLE}: {n} рядків"


def cached(cur, word):
    """Уже питали про це слово? -> список (термін, збіги, залишено) або None.

    Кеш по СЛОВУ, а не по запиту. Заміряно, чому: те саме «вайфай» у двох
    різних питаннях дало різні варіанти, і в одному з них не підтвердився
    жоден. Але «вайфай -> бездротова мережа» -- властивість слова, а не
    питання, тому питати модель треба один раз за весь час, і відповідь
    мусить бути та сама.
    """
    try:
        cur.execute(f"SELECT term, hits, kept FROM {CACHE_TABLE} WHERE word = %s",
                    (word,))
        rows = cur.fetchall()
    except psycopg.errors.UndefinedTable:
        # Перший прогін: таблиці ще немає, її створить `remember()`. Це єдиний
        # випадок, у якому «немає кешу» -- нормальна відповідь, а не поломка.
        cur.connection.rollback()
        return None
    except psycopg.Error:
        # Решту не глушимо: доступ перевірено `check_cache()` на старті, тому
        # помилка ТУТ означає щось неочікуване. Тихе `return None` тут коштувало
        # виклику моделі на кожне слово кожного прогону -- і не залишало слідів.
        cur.connection.rollback()
        raise
    return rows or None


def remember(cur, word, rows):
    try:
        cur.execute(CACHE_DDL)
        for term, hits, kept in rows:
            cur.execute(f"""INSERT INTO {CACHE_TABLE} (word, term, hits, kept)
                            VALUES (%s,%s,%s,%s)
                            ON CONFLICT (word, term) DO NOTHING""",
                        (word, term, hits, kept))
        cur.connection.commit()
    except psycopg.Error as e:
        # Раніше тут була скарга в лог і рух далі. Наслідок тихий і дорогий:
        # кеш НЕ наповнюється, і наступний прогін питає модель про ті самі
        # слова знову. Скрипт існує саме щоб цей кеш побудувати, тому невдалий
        # запис -- це відмова, а не дрібниця.
        cur.connection.rollback()
        raise RuntimeError(
            f"не вдалось записати кеш у {CACHE_TABLE}: "
            f"{str(e).splitlines()[0][:120]}. Потрібні права на CREATE/INSERT "
            f"або інша таблиця через SLANG_TABLE."
        ) from e


def expand(cur, query, verbose=False):
    """-> (підтверджені терміни, діагностика)."""
    unknown = unknown_lemmas(cur, query)
    diag = {"unknown": unknown, "proposed": {}, "kept": [], "dropped": [],
            "seconds": 0.0, "tokens_in": 0, "tokens_out": 0}
    if not unknown:
        return [], diag

    # Спершу кеш: слова, про які вже питали, модель більше не бачить.
    from_cache, to_ask = {}, []
    for w in unknown:
        rows = cached(cur, w)
        if rows is None:
            to_ask.append(w)
        else:
            from_cache[w] = rows
    diag["from_cache"] = sorted(from_cache)
    diag["asked"] = to_ask

    proposed = {}
    if to_ask:
        proposed, usage, dt = ask_model(query, to_ask)
        diag["seconds"] = dt
        diag["tokens_in"] = usage.get("prompt_tokens", 0)
        diag["tokens_out"] = usage.get("completion_tokens", 0)
        if not isinstance(proposed, dict):
            proposed = {}
    diag["proposed"] = proposed

    kept = []
    for word, variants in proposed.items():
        if not isinstance(variants, list):
            continue
        rows = []
        for term in variants:
            term = " ".join(str(term).split())
            if not term or len(term) < 3:
                continue
            n = found_in_corpus(cur, term)
            keep = 0 < n <= MAX_HITS
            rows.append((term, n, keep))
            if keep:
                kept.append((term, n))
            elif n == 0:
                diag["dropped"].append(term)
            else:
                diag["dropped"].append(f"{term} (надто часте: {n})")
        if rows:
            remember(cur, word, rows)
    for word, rows in from_cache.items():
        for term, n, keep in rows:
            if keep:
                kept.append((term, n))
    # Найінформативніші перші: рідкісний термін розрізняє краще за частий.
    kept.sort(key=lambda t: t[1])
    diag["kept"] = kept
    return [t for t, _n in kept], diag


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query", nargs="*")
    ap.add_argument("--questions")
    args = ap.parse_args(argv)

    questions = []
    if args.questions:
        with open(args.questions, encoding="utf-8") as f:
            questions = [l.strip() for l in f
                         if l.strip() and not l.startswith("#")]
    if args.query:
        questions.append(" ".join(args.query))

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    total = {"calls": 0, "s": 0.0}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # Стан кешу -- один рядок на старті. Без нього «модель викликалась 40
        # разів» не відрізнити від «кеш не працює, і це ті самі 12 слів».
        print(f"[{check_cache(cur)}]")
        for q in questions:
            terms, d = expand(cur, q)
            print(f"\n{'='*78}\n{q}")
            if not d["unknown"]:
                print("  невідомих корпусу лем немає -- модель не викликалась")
                continue
            total["calls"] += 1
            total["s"] += d["seconds"]
            print(f"  невідомі корпусу: {d['unknown']}")
            print(f"  модель запропонувала: "
                  f"{json.dumps(d['proposed'], ensure_ascii=False)[:200]}")
            print(f"  ПІДТВЕРДЖЕНО в корпусі: "
                  f"{[(t, n) for t, n in d['kept']][:6]}")
            if d["dropped"]:
                print(f"  відкинуто (у корпусі немає): {d['dropped'][:6]}")
            print(f"  {d['seconds']:.1f} с, токенів у {d['tokens_in']} / з {d['tokens_out']}")
    if total["calls"]:
        print(f"\nвикликів моделі {total['calls']}, "
              f"{total['s']:.1f} с ({total['s']/total['calls']:.1f} с/виклик)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
