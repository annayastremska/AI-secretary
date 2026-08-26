# -*- coding: utf-8 -*-
"""Нормативна дорога чата: пошук по одиницях -> реранкер -> ворота -> цитата.

Рішення Ані 26.08: підключити до чата ланцюг, який Андрій довів у своїй гілці.
Що він дає проти поточного пошуку по фрагментах: цитата стає АДРЕСНОЮ («Стаття
26 / 5»), а не абзацною, і на питання, де відповіді немає, приходить відмова, а
не найсхожіший за темою абзац.

## Ланцюг

1. **пошук по логічних одиницях** (стаття, пункт, додаток) двома гілками --
   словами (український Hunspell) і змістом (e5-small у pgvector), зі злиттям
   за місцями в списках (RRF). Частини однієї одиниці склеюються в один
   результат, документи-дублікати рахуються як один;
2. **реранкер** `bge-reranker-v2-m3` переставляє пул 50: він читає пару
   «питання + текст» разом, тому відрізняє «схоже за темою» від «відповідає»;
3. **ворота**: наша ж MamayLM 27B читає два найкращі тексти й каже, чи там
   справді відповідь, і яку саме цитату брати;
4. **дві перевірки цитати**: дослівний підрядок документа (ловить вигадку) і
   збіг лем питання з цитатою через той самий словник (ловить дослівну, але
   сторонню цитату).

## Чому це не «довіра до моделі»

Модель тут не згадує, а читає наданий текст, і обидва її твердження
перевіряються механічно: цитата мусить бути в документі дослівно, а її
доречність -- лемами. Не пройшло перевірку -> відкидаємо, а не показуємо.

## Деградація замість поломки

Ланцюгу потрібні: схема `andriy_test` (одиниці й вектори), доступ до неї
readonly-користувачу, torch і дві моделі на карті. Якщо чогось із цього немає,
`answer()` повертає None, і чат тихо лишається на поточному пошуку по
фрагментах. Причина пишеться в журнал ОДИН раз -- щоб було видно, чому ланцюг
не працює, і щоб це не заливало лог.

Вимикається явно: `CHAT_NORMATIVE_CHAIN=0`.
"""
import json
import logging
import os
import re
import threading

log = logging.getLogger("chat.normative")

#: Скільком кандидатам дає оцінку реранкер. 50 -- заміряне Андрієм значення:
#: пул 50 підняв правильну одиницю в топ-2 з 2/5 до 4/5 за 0.57 с.
RERANK_POOL = 50
#: Скільком верхнім одиницям задаємо питання воротами. Кожні ворота -- це
#: виклик моделі (~3 с), тому два, не п'ять.
GATE_TOP = 2
#: Нижче цієї частки лем питання цитата позначається як підозріла.
MIN_OVERLAP = 0.5
#: Довжина тексту, яку віддаємо реранкеру: у нього теж вікно 512 токенів.
RERANK_CHARS = 1800
#: Скільком символів тексту одиниці кладемо у ворота.
GATE_CHARS = 4000

ENABLED = os.environ.get("CHAT_NORMATIVE_CHAIN", "1").strip().lower() not in (
    "0", "false", "no", "off")

_LOCK = threading.Lock()
_STATE = {"ready": None, "reason": "", "encode": None, "rescore": None,
          "su": None, "logged": False}

#: Символи, які модель тихо «виправляє», переписуючи цитату: кручений апостроф,
#: різні тире й лапки, нерозривний пробіл. Без цього зведення підрядкова
#: перевірка відкидала цілком доречні цитати (заміряно Андрієм: дві з п'яти).
_CONFUSE = {"’": "'", "‘": "'", "`": "'", "´": "'", "ʼ": "'",
            "–": "-", "—": "-", "‐": "-", "‑": "-", "−": "-", "―": "-",
            "«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "‟": '"',
            " ": " ", " ": " ", " ": " "}
_TRANS = str.maketrans(_CONFUSE)

GATE_SYSTEM = (
    "Ти працюєш із нормативними документами Збройних Сил України. Ти НЕ "
    "переказуєш і НЕ додумуєш: ти або знаходиш у наданому тексті дослівну "
    "відповідь, або кажеш, що її там немає. Відповідай лише JSON."
)

GATE_USER = """Питання: {question}

Нижче — фрагмент нормативного документа, знайдений пошуком. Він МОЖЕ бути не
про те: пошук помиляється.

--- ДОКУМЕНТ: {title} ({ident}), {addr} ---
{body}
--- КІНЕЦЬ ФРАГМЕНТА ---

1. Чи цей фрагмент справді відповідає на питання? Критерій строгий: відповідає
   лише якщо в тексті є САМЕ те, що запитали (строк, число, хто саме, який
   порядок). Сусідня тема, інший вид відпустки, інший орган — це НЕ відповідь.
2. Якщо відповідає — вибери з тексту не більше двох речень із самою відповіддю
   і скопіюй їх ДОСЛІВНО, символ за символом. Ми перевіряємо, що цитата є
   точним підрядком документа, і відкидаємо її, якщо це не так. Довгий перелік
   не переписуй: візьми рядок, який його вводить. Цитата довша за 400 символів
   не приймається.

Поверни рівно такий JSON:
{{"answers": true|false, "why": "<коротко чому>", "quote": "<дослівна цитата або порожньо>"}}"""

GATE_SCHEMA = {
    "type": "object",
    "properties": {"answers": {"type": "boolean"},
                   "why": {"type": "string"},
                   "quote": {"type": "string"}},
    "required": ["answers"],
}


def _norm(s):
    """Зведення ДЛЯ ПОРІВНЯННЯ: пробіли, апострофи, тире, лапки, регістр."""
    return re.sub(r"\s+", " ", (s or "").translate(_TRANS)).strip().casefold()


def _prepare():
    """Один раз на процес: чи можемо працювати, і чим. -> (ready, reason)."""
    if _STATE["ready"] is not None:
        return _STATE["ready"], _STATE["reason"]
    if not ENABLED:
        _STATE.update(ready=False, reason="вимкнено CHAT_NORMATIVE_CHAIN=0")
        return False, _STATE["reason"]
    try:
        import sys
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        scripts = os.path.join(root, "db", "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import search_units_test as su          # noqa: F401
        from build_units_test import load_encoder
        from measure_rerank_lift import load_reranker
    except Exception as exc:
        _STATE.update(ready=False,
                      reason=f"немає коду пошуку по одиницях: "
                             f"{type(exc).__name__}: {exc}")
        return False, _STATE["reason"]

    # Доступ до схеми одиниць -- окремо від коду: readonly-користувачу права
    # видає Андрій, і без них ланцюг просто не наш.
    try:
        from . import tiers as _t
    except ImportError:
        import tiers as _t
    try:
        _t._run_template_sql(
            f"SELECT 1 FROM {su.SCHEMA}.document_units LIMIT 1", {})
    except Exception as exc:
        _STATE.update(ready=False,
                      reason=f"немає доступу до схеми {su.SCHEMA}: "
                             f"{type(exc).__name__}")
        return False, _STATE["reason"]

    try:
        _STATE["encode"] = load_encoder()
        _STATE["rescore"] = load_reranker()
    except Exception as exc:
        _STATE.update(ready=False,
                      reason=f"моделі пошуку не піднялись: "
                             f"{type(exc).__name__}: {exc}")
        return False, _STATE["reason"]
    _STATE.update(ready=True, reason="", su=su)
    return True, ""


def available():
    """-> (готовий, причина). Для приладів і сторінки діагностики."""
    with _LOCK:
        ready, reason = _prepare()
    if not ready and not _STATE["logged"]:
        log.info("нормативний ланцюг не працює: %s (лишаюсь на пошуку по "
                 "фрагментах)", reason)
        _STATE["logged"] = True
    return ready, reason


def _lexemes(cur, text):
    cur.execute("SELECT unnest(tsvector_to_array(to_tsvector('ukrainian', %s)))",
                (text,))
    return {r[0] for r in cur.fetchall()}


def _overlap(cur, schema, question, quote):
    """Частка лем питання, присутніх у цитаті. -> (частка, чого бракує).

    Друга перевірка поверх підрядкової: та ловить ВИГАДКУ, ця -- НЕДОРЕЧНІСТЬ
    (цитата дослівна, але про інше). Леми, яких у корпусі немає жодного разу,
    зі знаменника прибираються: це одруківки самого питання, і карати за них
    цитату неправильно."""
    q = _lexemes(cur, question)
    if q:
        cur.execute(f"""
            SELECT l FROM unnest(%s::text[]) AS l
             WHERE EXISTS (SELECT 1 FROM {schema}.document_units u
                            WHERE u.tsv @@ plainto_tsquery('simple', l))
        """, (sorted(q),))
        q = {r[0] for r in cur.fetchall()}
    if not q:
        return 1.0, set()
    a = _lexemes(cur, quote)
    return len(q & a) / len(q), q - a


def _gate(question, title, ident, addr, body):
    """Ворота: наша ж модель читає текст і каже, чи там відповідь.

    Використовуємо той самий екземпляр MamayLM, що й маршрутизатор -- окремий
    llama-server (як у скриптах Андрія) на карті означав би другу копію вагів.
    """
    try:
        from . import tiers as _t
    except ImportError:
        import tiers as _t
    data = _t.mamaylm_json(
        GATE_SYSTEM,
        GATE_USER.format(question=question, title=title, ident=ident,
                         addr=addr, body=body[:GATE_CHARS]),
        GATE_SCHEMA)
    if not isinstance(data, dict):
        return None
    return data


def answer(question):
    """-> (текст, рядки джерела) або None, якщо ланцюг недоступний.

    None означає «цю дорогу не пройшли» -- чат тоді працює як раніше. Якщо ж
    ланцюг пройдено, а відповіді немає, повертається саме ВІДМОВА, а не None:
    це вже знання, а не брак можливості."""
    ready, reason = available()
    if not ready:
        return None
    su = _STATE["su"]
    try:
        from . import tiers as _t
    except ImportError:
        import tiers as _t

    vec = str(_STATE["encode"](["query: " + question])[0])
    with _t._connect() as conn, conn.cursor() as cur:
        fused = su.dedupe_by_text(
            cur, su.rrf_merge(su.lexical(cur, question),
                              su.semantic(cur, vec)), su.canon_map(cur))
        if not fused:
            return ("Не знайшла в нормативних документах нічого по цьому "
                    "питанню. Це не «немає такої норми» — це означає, що в "
                    "нашому корпусі (41 документ) відповіді немає.",
                    ["нормативний ланцюг: пошук по одиницях",
                     "кандидатів: 0"])

        # Реранкер: пул 50 переставляється за оцінкою пари «питання + текст».
        pool = fused[:RERANK_POOL]
        texts = [su.quote_of(cur, d, b)[0][:RERANK_CHARS] for (d, b), _m in pool]
        scores = _STATE["rescore"](question, texts)
        order = sorted(range(len(scores)), key=lambda j: -scores[j])
        ranked = [pool[j] for j in order] + fused[RERANK_POOL:]

        cache = {}
        rejected = []
        for (doc_id, base), meta in ranked[:GATE_TOP]:
            title, ident = su.identity(cur, doc_id, cache)
            body, was_split, trimmed = su.quote_of(cur, doc_id, base)
            addr = base + (" (фрагмент)" if was_split or trimmed else "")
            verdict = _gate(question, title, ident, addr, body)
            if not verdict:
                rejected.append(f"{addr}: ворота не дали розбірливої відповіді")
                continue
            if not verdict.get("answers"):
                rejected.append(f"{addr}: {(verdict.get('why') or '')[:90]}")
                continue
            quote = (verdict.get("quote") or "").strip()
            if not quote:
                rejected.append(f"{addr}: ворота сказали «відповідає», але "
                                f"цитати не дали")
                continue
            # Перевірка 1: цитата мусить бути в документі дослівно.
            if _norm(quote) not in _norm(body):
                rejected.append(f"{addr}: цитати немає в документі дослівно — "
                                f"відкинуто")
                continue
            # Перевірка 2: чи цитата про те саме, що питання.
            share, missing = _overlap(cur, su.SCHEMA, question, quote)
            mark = ("" if share >= MIN_OVERLAP
                    else " ⚠️ цитата слабко перетинається з питанням — "
                         "перечитайте документ")
            lines = [f"**{title}** ({ident}), {addr}",
                     f"«{quote}»{mark}"]
            if verdict.get("why"):
                lines.append(f"Чому це відповідь: {_t._esc(verdict['why'])}")
            lines.append("Це дослівна цитата з чинного документа; перевірено, "
                         "що вона є в тексті.")
            source = ["нормативний ланцюг: одиниці → реранкер → ворота → "
                      "перевірка цитати",
                      f"документ: запис №{doc_id} у базі, адреса {addr}",
                      f"збіг лем питання й цитати: {share:.2f}"]
            return "\n".join(lines), source

        # Ворота нікого не пропустили -- це ЗНАННЯ, не збій.
        text = ["Знайшла схожі за темою місця, але жодне не відповідає на "
                "питання прямо. Показувати їх як відповідь не буду."]
        if rejected:
            text.append("Що саме відкинуто:")
            text += [f"- {r}" for r in rejected]
        return "\n".join(text), [
            "нормативний ланцюг: ворота не пропустили жодного кандидата",
            f"перевірено найкращих: {min(GATE_TOP, len(ranked))}"]
