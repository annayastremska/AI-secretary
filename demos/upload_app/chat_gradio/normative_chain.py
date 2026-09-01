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

Ланцюгу потрібні: одиниці з векторами в `public` (доступ у ролі вже є)
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

import psycopg

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
#: Скільки символів тексту одиниці кладемо у ворота.
GATE_CHARS = 4000

#: Де живуть одиниці нормативних документів. Відповідь Андрія 27.08: після
#: міграції -- у ПОСТІЙНІЙ схемі, і роль readonly їх уже бачить. Ми просили
#: доступ до його експериментальної `andriy_test`, а потрібна була не зміна
#: прав, а зміна назви. У постійній схемі є міграція, коментарі й тригери; в
#: експериментальній лежала чернетка.
UNITS_SCHEMA = os.environ.get("CHAT_UNITS_SCHEMA", "public").strip()
#: Таблиця груп дублікатів у постійній схемі зветься інакше, ніж в
#: експериментальній (`document_groups` проти `doc_groups`).
GROUPS_TABLE = os.environ.get("CHAT_UNITS_GROUPS", "document_groups").strip()
#: ПОВНЕ ім'я таблиці одиниць -- одне на весь модуль. Модуль пошуку Андрія
#: після 01.09 бере таблиці повними іменами, без схеми, тому й ми тримаємо
#: тут готове ім'я, а не схему окремо від назви: доки їх було двоє, три місця
#: в цьому файлі складали їх по-різному.
_PREFIX = "" if UNITS_SCHEMA in ("", "public") else UNITS_SCHEMA + "."
UNITS_TABLE = _PREFIX + "document_units"
#: Таблиця груп дублікатів -- так само повним іменем.
GROUPS_FULL = _PREFIX + GROUPS_TABLE

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
    except Exception as exc:
        _STATE.update(ready=False,
                      reason=f"немає коду пошуку по одиницях: "
                             f"{type(exc).__name__}: {exc}")
        return False, _STATE["reason"]

    # ДЕ ЛЕЖАТЬ ОДИНИЦІ -- задаємо з НАШОГО боку (його теку не правимо: папка
    # це одна зона відповідальності). Але контракт у його модуля змінився, і
    # підтримуємо обидва:
    #
    #   нова версія (main, 01.09): таблиці беруться ПОВНИМИ іменами --
    #       `UNITS` = document_units, `GROUPS` = document_groups;
    #   стара версія: схема в глобальній `SCHEMA`, а таблиці -- `SCHEMA.х`.
    #
    # Чому це важливо: після мерджу 01.09 присвоєння `su.SCHEMA` стало
    # НІ НА ЩО не впливати -- його функції читали б таблиці за замовчуванням,
    # а наша перевірка доступу дивилась би в іншу схему. Тобто «тихо не те»
    # замість падіння, і на демо це виглядало б як «нормативка нічого не
    # знаходить».
    if hasattr(su, "UNITS"):
        su.UNITS = UNITS_TABLE
        su.GROUPS = GROUPS_FULL
    else:
        su.SCHEMA = UNITS_SCHEMA
    # Таблиця груп дублікатів у постійній схемі зветься інакше. Його
    # `canon_map` на невідомій таблиці глушить виняток і повертає порожній
    # словник -- тобто зведення дублікатів ТИХО не працювало б, а «тихо не
    # працює» гірше за падіння. Тому не покладаємось на глушник, а даємо
    # правильний запит.
    su.canon_map = _canon_map

    try:
        from . import tiers as _t
    except ImportError:
        import tiers as _t
    try:
        # Перевірка доступу мусить дивитись у ТУ САМУ таблицю, з якої читають
        # його функції, -- інакше вона підтверджує доступ не туди.
        _t._run_template_sql(
            f"SELECT 1 FROM {UNITS_TABLE} LIMIT 1", {})
    except Exception as exc:
        _STATE.update(ready=False,
                      reason=f"немає доступу до {UNITS_TABLE}: "
                             f"{type(exc).__name__}")
        return False, _STATE["reason"]

    try:
        _STATE["encode"] = _load_query_encoder()
        _STATE["rescore"] = _load_reranker()
    except Exception as exc:
        _STATE.update(ready=False,
                      reason=f"моделі пошуку не піднялись: "
                             f"{type(exc).__name__}: {exc}")
        return False, _STATE["reason"]
    _STATE.update(ready=True, reason="", su=su)
    return True, ""


#: Модель, якою побудовані вектори одиниць у базі. НЕ вибір, а факт:
#: `document_units.embedding_model` = саме вона, 384 виміри. Закодувати запит
#: іншою моделью означало б порівнювати непорівнюване, причому МОВЧКИ -- пошук
#: не впав би, а просто перестав знаходити.
UNITS_ENCODER = "intfloat/multilingual-e5-small"
#: Префікс запиту e5. Тексти одиниць кодувались із «passage: », питання
#: кодується з «query: » -- так навчена ця родина моделей.
QUERY_PREFIX = "query: "
#: Реранкер: пара «питання + текст», логіт як бал.
RERANKER = "BAAI/bge-reranker-v2-m3"


def _load_query_encoder():
    """Кодувальник ЗАПИТУ. Свій, а не зі скриптів збірки Андрія.

    Ланцюг брав його з `build_units_test`, а той тягне `segment_documents` --
    різання документів на одиниці, якого на нашій гілці немає. Тобто читання
    готового індексу падало через відсутність коду ЗБІРКИ цього індексу. Читати
    й будувати -- різні задачі, і друга не мусить бути умовою першої.

    Спосіб той самий, що при збірці (перевірено по його коду): усереднення по
    маскованих токенах і L2-нормалізація. Інший спосіб дав би інші вектори при
    тій самій моделі, і пошук зіпсувався б мовчки.

    CPU навмисно: карта на сервері спільна, на ній працює модель чата, а тут
    кодується ОДИН короткий запит. Ціна -- десятки мілісекунд, і вона не варта
    ризику зачепити сусідній процес.
    """
    import torch
    from transformers import AutoModel, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(UNITS_ENCODER)
    model = AutoModel.from_pretrained(UNITS_ENCODER).eval()

    def encode(texts):
        enc = tok(list(texts), padding=True, truncation=True, max_length=512,
                  return_tensors="pt")
        with torch.no_grad():
            out = model(**enc).last_hidden_state
        mask = enc["attention_mask"].unsqueeze(-1).float()
        emb = (out * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
        return torch.nn.functional.normalize(emb, p=2, dim=1).tolist()

    return encode


def _load_reranker():
    """Реранкер. Своя обгортка з тієї ж причини, що й кодувальник."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(RERANKER)
    model = AutoModelForSequenceClassification.from_pretrained(
        RERANKER).eval()

    def score(query, texts, batch=8):
        out = []
        for i in range(0, len(texts), batch):
            chunk = list(texts[i:i + batch])
            enc = tok([query] * len(chunk), chunk, padding=True,
                      truncation=True, max_length=512, return_tensors="pt")
            with torch.no_grad():
                out.extend(model(**enc).logits.view(-1).float().tolist())
        return out

    return score


def _label(cur, su, doc_id, cache):
    """Людська назва документа й його ідентифікатор. -> (назва, ідентифікатор).

    Чому не просто `su.identity`. Та функція витягує назву З ТЕКСТУ документа
    і, коли не знаходить, віддає «documents.id=252» та «—». На екрані виходило
    «**documents.id=252** (—)» -- внутрішній ключ замість назви (видно живим
    прогоном 27.08). Внутрішній ключ у відповіді людині не потрібен: він
    нічого їй не каже й виглядає як поломка.

    Тому спершу питаємо БАЗУ (`doc_title`, `doc_identifier` -- ті самі поля, з
    яких живе решта чата), і лише якщо там порожньо, беремо витяг із тексту.
    Якщо не вийшло ніде -- кажемо «нормативний документ №252»: це чесно
    (номер запису справді єдине, що ми знаємо) і читається як мова, а не як
    рядок з коду.
    """
    if doc_id in cache:
        return cache[doc_id]
    title = ident = None
    try:
        cur.execute("SELECT doc_title, doc_identifier FROM documents "
                    "WHERE id = %s", (doc_id,))
        row = cur.fetchone()
        if row:
            title = (row[0] or "").strip() or None
            ident = (row[1] or "").strip() or None
    except Exception:
        pass
    if not title:
        try:
            got_title, got_ident = su.identity(cur, doc_id, {})
            if got_title and not got_title.startswith("documents.id="):
                title = got_title
            if not ident and got_ident and got_ident != "—":
                ident = got_ident
        except Exception:
            pass
    if not title:
        title = f"нормативний документ №{doc_id}"
    cache[doc_id] = (title, ident)
    return cache[doc_id]


def _canon_map(cur):
    """document_id -> канонічний документ групи дублікатів.

    Наша версія замість тієї, що в модулі Андрія: там ім'я таблиці --
    `doc_groups` з експериментальної схеми, у постійній вона `document_groups`.
    Колонки ті самі (перевірено живою базою 27.08).
    """
    cur.execute(f"SELECT document_id, canonical_id "
                f"FROM {GROUPS_FULL}")
    return {a: b for a, b in cur.fetchall()}


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


def _overlap(cur, question, quote):
    """Частка лем питання, присутніх у цитаті. -> (частка, чого бракує).

    Друга перевірка поверх підрядкової: та ловить ВИГАДКУ, ця -- НЕДОРЕЧНІСТЬ
    (цитата дослівна, але про інше). Леми, яких у корпусі немає жодного разу,
    зі знаменника прибираються: це одруківки самого питання, і карати за них
    цитату неправильно."""
    q = _lexemes(cur, question)
    if q:
        cur.execute(f"""
            SELECT l FROM unnest(%s::text[]) AS l
             WHERE EXISTS (SELECT 1 FROM {UNITS_TABLE} u
                            WHERE u.tsv @@ plainto_tsquery('simple', l))
        """, (sorted(q),))
        q = {r[0] for r in cur.fetchall()}
    if not q:
        return 1.0, set()
    a = _lexemes(cur, quote)
    return len(q & a) / len(q), q - a


#: ПЕРЕХРЕСНЕ ПОСИЛАННЯ ВСЕРЕДИНІ ТІЄЇ САМОЇ СТАТТІ: «відповідно до пункту 1
#: цієї статті», «у порядку, визначеному пунктом 14 цієї статті».
#:
#: Знайдено прогоном Андрія 28.08. На «скільки максмум термін щорічної
#: відпустки?» ворота пропустили цитату зі «Стаття 10-1 / 2»:
#:
#:   «…тривалість щорічної основної відпустки в році початку військової служби
#:    обчислюється з розрахунку 1/12 частини тривалості відпустки, на яку вони
#:    мають право ВІДПОВІДНО ДО ПУНКТУ 1 ЦІЄЇ СТАТТІ…»
#:
#: Тобто цитата ВКАЗУЄ на відповідь, а не містить її. Пункт 1 у базі є
#: (`document_units`, 922 символи, з тими самими строками 30/35/40/45 днів) --
#: його просто не показали, бо це інша одиниця.
#:
#: Ловимо ЛИШЕ посилання в межах тієї самої статті («цієї статті»): сусідню
#: одиницю можна дістати детерміновано, за меткою, без нового пошуку, без
#: моделі й без порогу. Посилання на інший акт («статтею 16-2 Закону України»)
#: сюда не підпадає навмисно -- там потрібен пошук, тобто зона ланцюга.
_CROSS_REF = re.compile(
    r"пункт(?:ом|у|і|ами|ах)?\s+(\d+(?:-\d+)?)"      # пункту 1, пунктом 14
    r"(?:\s+(?:і|та|,)\s*\d+(?:-\d+)?)*"             # «пунктами 17 і 18»
    r"\s+ц(?:ієї|iєї)\s+статт", re.IGNORECASE)

#: Скільком символів сусіднього пункту показуємо. Пункт -- це норма, і рвати її
#: посередині означало б зробити те саме, на чому ми вже спіймались із цитатою:
#: обрізана норма читається як неправда. Тому межа висока, а якщо пункт довший
#: -- він не показується взагалі, і про це сказано.
CROSS_REF_CHARS = 1400


def _cross_ref_label(quote, label):
    """-> метка сусідньої одиниці, на яку посилається цитата, або None.

    `label` -- метка одиниці, з якої взято цитату («Стаття 10-1 / 2»). Батьківська
    частина метки береться з неї ж: посилання «пункту 1 цієї статті» означає
    той самий рівень у тій самій статті.
    """
    if not quote or not label or " / " not in label:
        return None
    m = _CROSS_REF.search(quote)
    if not m:
        return None
    parent = label.rsplit(" / ", 1)[0]
    want = f"{parent} / {m.group(1)}"
    # Посилання на саму себе добирати нічого не треба.
    return None if want == label else want


def _unit_by_label(ident, label):
    """Одиниця документа за ідентифікатором акта й меткою. -> рядок або None.

    Окремий запит, а не додаткова колонка в основному: добір сусіднього пункту
    трапляється рідко, і платити за нього в кожному пошуку не треба.
    """
    try:
        from . import tiers as _t
    except ImportError:
        import tiers as _t
    try:
        with _t._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT u.label, u.text
                          FROM {UNITS_TABLE} u
                          JOIN documents d ON d.id = u.document_id
                         WHERE d.doc_identifier = %s
                           AND (u.label = %s OR u.base_label = %s)
                         ORDER BY u.ord LIMIT 1""",
                    (ident, label, label))
                return cur.fetchone()
    except Exception:
        # Добір -- додаткове знання. Немає його -- цитата лишається цитатою.
        return None


#: Службові слова, які в хвості обрізаної назви лишаються без свого іменника.
#: Перелік короткий і закритий навмисно: це не «стоп-слова», а рівно ті сполучні
#: слова, після яких обрив читається як недоробка.
_STOP_TAIL = {"та", "і", "й", "а", "але", "або", "чи", "з", "із", "зі", "в",
              "у", "на", "до", "для", "про", "при", "за", "від", "по", "що"}


def _clean_title(title):
    """Назва акта без обрізаного посеред слова хвоста.

    Зауваження Ані 28.08: «три крапки якось не дуже — давай три крапки в кінці
    речення». На екрані було «Про соціальний і правовий захист
    військовослужбовців та чл…» -- половина слова «членів».

    Причина обрізки не в нас: `doc_title` зберігається в базі ВЖЕ обрізаним --
    усі 16 таких назв мають довжину 58-59 символів і закінчуються реальним
    символом «…». Це заливка корпусу (зона Андрія, лежить у переліку до нього).
    Тут ми лише не показуємо огризок слова: відкидаємо останнє неповне слово й
    ставимо «…» після цілого.

    Чому не «1-2 речення», як у першому формулюванні задачі: назва акта -- це
    одне називне словосполучення, речень у ній немає. Тому межа -- слово.
    """
    t = (title or "").strip()
    if not t.endswith(("…", "...")):
        return t
    core = t.rstrip(".…")
    # ЧИ ОСТАННЄ СЛОВО ОБРІЗАНЕ -- видно по тому, що стоїть перед «…».
    #
    # Перша версія завжди відкидала останній токен, і на «Про щось, і ще щось,…»
    # вона з'їдала ціле слово: там «…» стоїть після КОМИ, тобто слово повне, а
    # обірвано речення. Тому:
    #   літера перед «…»  -> слово недописане, відкидаємо його;
    #   кома чи пробіл    -> слово ціле, лишаємо, знімаємо лише розділовий знак.
    if core and core[-1].isalpha():
        core = core.rsplit(" ", 1)[0]
    core = core.rstrip(" ,;:-–—")
    # Службове слово в хвості («та», «і», «або») без свого іменника читається як
    # недоробка -- саме це й вийшло на живому прикладі: «…військовослужбовців
    # та…». Прибираємо і його.
    while True:
        head, _, last = core.rpartition(" ")
        if head and last.lower() in _STOP_TAIL:
            core = head.rstrip(" ,;:-–—")
            continue
        break
    return (core or t.rstrip(".…")) + "…"


def _gate(question, title, ident, addr, body):
    """Ворота: наша ж модель читає текст і каже, чи там відповідь.

    Використовуємо той самий екземпляр MamayLM, що й маршрутизатор -- окремий
    llama-server (як у скриптах Андрія) на карті означав би другу копію вагів.
    """
    try:
        from . import tiers as _t
    except ImportError:
        import tiers as _t
    # `_model_json`, а не «mamaylm_json»: другої назви в tiers ніколи не було,
    # і ланцюг падав на AttributeError у ВОРОТАХ -- тобто після пошуку й
    # реранкера, на останньому кроці. Знайдено живим прогоном 27.08, бо
    # раніше ланцюг узагалі не доходив до цього місця.
    data = _t._model_json(
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

    vec = str(_STATE["encode"]([QUERY_PREFIX + question])[0])
    # З'ЄДНАННЯ З КОРТЕЖНИМИ РЯДКАМИ, а не словниковими.
    #
    # Чат усюди читає базу словниками (`row_factory=dict_row`) -- так зручніше
    # в шаблонах. Але функції пошуку по одиницях написані під КОРТЕЖІ:
    # `for a, b in cur.fetchall()` і розпакування `u.id, u.document_id, ...`.
    # На словниках такий цикл перебирає КЛЮЧІ, тобто далі йде рядок
    # «document_id» замість числа -- і база відповідає «invalid input syntax
    # for type bigint». Перевірено живим прогоном 27.08: саме це й сталося.
    # Тому тут окреме з'єднання з типовою фабрикою рядків, і причина названа.
    with psycopg.connect(_t._dsn(), autocommit=True) as conn,             conn.cursor() as cur:
        fused = su.dedupe_by_text(
            cur, su.rrf_merge(su.lexical(cur, question),
                              su.semantic(cur, vec)), su.canon_map(cur))
        if not fused:
            # ЧИСЛО ЖИВЕ, а не літерал. Тут стояло «(41 документ)» -- і воно
            # вже було неправильним: у базі 44 нормативні документи. Тобто
            # відмова, покликана бути честнішою за «немає такої норми», сама
            # називала неправильну цифру. Третє число того самого факту (нуль
            # від старої дороги, 41 тут, 44 в базі) -- саме те, на чому Денис
            # спіймав п. 15 звіту.
            #
            # Читаємо тим же курсором: окремого з'єднання не треба, а падати
            # через лічильник у тексті ВІДМОВИ не можна -- тому except.
            try:
                cur.execute("SELECT COUNT(*) FROM documents "
                            "WHERE domain = 'normative'")
                n_docs = cur.fetchone()[0]
                corpus = f"нашому корпусі ({n_docs} документів)"
            except Exception:
                corpus = "нашому корпусі"
            return (f"Не знайшла в нормативних документах нічого по цьому "
                    f"питанню. Це не «немає такої норми» — це означає, що в "
                    f"{corpus} відповіді немає.",
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
            title, ident = _label(cur, su, doc_id, cache)
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
            share, missing = _overlap(cur, question, quote)
            mark = ("" if share >= MIN_OVERLAP
                    else " ⚠️ цитата слабко перетинається з питанням — "
                         "перечитайте документ")
            # Ідентифікатор у дужках -- лише коли він є. Раніше в дужках
            # стояло «(—)» у кожній відповіді: порожнє місце, оформлене як
            # дані.
            # Назва -- звичайним текстом, не жирним (Аня 28.08): жирним у
            # відповіді виділяється ЦИФРА або головне твердження, а назва
            # документа -- це посилання, не висновок.
            head = _t._esc(_clean_title(title))
            if ident:
                head += f" ({_t._esc(ident)})"
            lines = [f"{head}, {addr}",
                     f"«{quote}»{mark}"]
            # ЦИТАТА, ЯКА ВКАЗУЄ НА ІНШИЙ ПУНКТ, -- НЕПОВНА ВІДПОВІДЬ.
            #
            # Зауваження Андрія 28.08: «тобто у відповідності до пункту 1, який
            # ми не виводимо, бо це інший чанк». Добираємо той пункт за меткою
            # -- у тому самому документі, без нового пошуку й без моделі.
            ref = _cross_ref_label(quote, addr)
            ref_note = None
            if ref and ident:
                sib = _unit_by_label(ident, ref)
                sib_text = ((sib["text"] if sib else "") or "").strip()
                if sib_text and len(sib_text) <= CROSS_REF_CHARS:
                    # Окремої перевірки «є в документі дослівно» тут не треба, і
                    # це не послаблення правила: сусідня одиниця береться з
                    # `document_units` ТОГО САМОГО документа, тобто вона і є
                    # текст документа. Перевірка потрібна для цитати від
                    # МОДЕЛІ -- та могла її вигадати; тут моделі немає взагалі.
                    lines.append(f"Цитата посилається на {_t._esc(ref)} — "
                                 f"наводжу і його:")
                    lines.append(f"«{_t._esc(sib_text)}»")
                    ref_note = f"додано за посиланням із цитати: {ref}"
                elif sib_text:
                    lines.append(
                        f"Цитата посилається на {_t._esc(ref)} — він задовгий, "
                        f"щоб навести тут ({len(sib_text)} символів). "
                        f"Спитайте про нього окремо.")
            if verdict.get("why"):
                lines.append(f"Чому це відповідь: {_t._esc(verdict['why'])}")
            lines.append("Це дослівна цитата з чинного документа; перевірено, "
                         "що вона є в тексті.")
            source = ["нормативний ланцюг: одиниці → реранкер → ворота → "
                      "перевірка цитати",
                      f"документ: запис №{doc_id} у базі, адреса {addr}",
                      f"збіг лем питання й цитати: {share:.2f}"]
            if ref_note:
                source.append(ref_note)
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
