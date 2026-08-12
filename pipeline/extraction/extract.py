"""Генеричний двигун екстракції. Читає regex-паттерни й режим екстракції
з самої схеми (schemas/*.yaml) -- жодного per-domain Python-словника тут.
Новий шаблон документа = новий YAML-файл, а не нова функція в цьому модулі.

Гібридний підхід: спершу дешевий детермінований прохід (regex/label-пошук/
токенізація) для всіх полів; те, що він не закрив ("прогалини"), іде групами
по batch_size в LLM-виклик -- компроміс між "один виклик на все" (швидко,
але збій валить усі поля разом) і "один виклик на кожне поле" (ізольовано,
але повільно на CPU).

Прогалини розділяються на два види, бо їм потрібен РІЗНИЙ контекст:
- локалізовані: місце в документі знайдено, не вдався лише розбір рядка
  (напр. звання+ПІБ знайдено, але прізвище не ВЕЛИКИМИ літерами) -- у LLM
  йде лише цей рядок, а не весь документ. На CPU довжина промпту -- головна
  стаття витрат, тож це і швидше, і точніше;
- нелокалізовані: місце не знайдено взагалі (regex не збігся, лейбл
  відсутній, кандидат відхилений denylist-ом) -- тут підказки немає й
  давати її небезпечно (відхилений кандидат -- це якраз НЕПРАВИЛЬНИЙ
  текст), тому в LLM йде повний текст документа.
"""
import json
import re
from collections import Counter

from pipeline.classification.classify import phrase_in_text, normalize_ws
from pipeline.normalization.normalize import is_placeholder, lookup_alias
from pipeline.extraction.schema_grammar import build_json_schema_for_fields, chunk_fields


def majority_vote(values):
    """Self-consistency: кілька семплів одного поля (temperature > 0 на боці
    викликача) -> (значення, чи_був_розкол).

    Значення можуть бути будь-якого JSON-типу (str/int/dict/None, бо LLM
    повертає структуровані значення для date-полів) -- dict/list
    порівнюються за канонічним JSON-рядком, не за ідентичністю об'єкта.

    Другий елемент -- прапорець розколу: коли за лідера й за іншого варіанта
    однакова кількість голосів, переможець визначається порядком у списку,
    тобто фактично випадково. Раніше такий результат виглядав рівно так само
    впевнено, як одноголосний, і рев'юер не мав жодного способу це побачити.
    """
    cleaned = []
    for v in values:
        if v is None:
            continue
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue
        cleaned.append(v)
    if not cleaned:
        return None, False

    def key(v):
        return json.dumps(v, sort_keys=True, ensure_ascii=False) if isinstance(v, (dict, list)) else v

    counts = Counter(key(v) for v in cleaned).most_common()
    best_key, best_count = counts[0]
    split = len(counts) > 1 and counts[1][1] == best_count
    return next(v for v in cleaned if key(v) == best_key), split


def _raw_block_text(block) -> str:
    """block -- str (docx, немає геометрії) або {"text","bbox"} (Surya/PDF,
    з ingest.sort_blocks_by_geometry, яка більше не зрізає bbox)."""
    return block["text"] if isinstance(block, dict) else block


def flatten_blocks(blocks):
    """Плаский список НЕПОРОЖНІХ рядків з усіх блоків, у порядку появи.
    Використовується там, де межі блоків не важливі (напр.
    first_block_starting_with). Для пошуку "значення перед лейблом" межі
    БЛОКІВ важливі -- див. group_blocks_into_lines()."""
    flat = []
    for block in blocks:
        for line in _raw_block_text(block).split("\n"):
            line = line.strip()
            if line:
                flat.append(line)
    return flat


def group_blocks_into_lines(blocks):
    """Розбиває кожен блок на рядки, ЗБЕРІГАЮЧИ межі блоків і bbox блоку
    (якщо є) -- список {"lines": [...], "bbox": (x1,y1,x2,y2) | None}, а не
    єдиний плаский список чи голі рядки. Межі важливі в обидва боки:
    - багаторядкове значення, яке OCR згрупував в ОДИН блок (напр. "Навчальний
      центр Сухопутних військ\\nЗбройних Сил України «Десна»" перед лейблом у
      тому самому блоці), має лишитися РАЗОМ -- підтверджено реальним багом:
      плаский список брав лише ОДИН рядок перед лейблом і губив половину назви;
    - і навпаки, сусідній (інший) блок не повинен випадково приєднатися до
      значення просто тому, що він теж не виглядає як лейбл (підтверджено
      іншим реальним багом: заголовок документа в окремому блоці перед
      блоком зі значенням і лейблом звання/ПІБ).

    bbox зберігається per-блок (Surya не дає геометрії рівня рядка -- лише
    рівня блоку, research-round-2026-08-12.md), і використовується
    find_block_before_label для геометричної прив'язки замість порядку
    списку. Для docx bbox завжди None -- поведінка на цьому шляху НЕ
    змінюється (лишається чисто лінійною, як і була).

    page -- індекс сторінки/кадру (PDF, багатокадровий TIFF), None де
    неприменно (docx, одиночне зображення). bbox рахується ОКРЕМО на
    кожній сторінці, з нуля -- геометричне порівняння між сторінками
    безглузде й дає хибні збіги (виміряний реальний баг: блок зі сторінки 2
    "вирівнювався" з лейблом на сторінці 1, обидві рахують y з нуля).
    _geometric_candidate звіряє це поле, перш ніж порівнювати bbox."""
    result = []
    for block in blocks:
        text = _raw_block_text(block)
        bbox = block.get("bbox") if isinstance(block, dict) else None
        page = block.get("page") if isinstance(block, dict) else None
        result.append({
            "lines": [line.strip() for line in text.split("\n") if line.strip()],
            "bbox": bbox,
            "page": page,
        })
    return result


def _is_denylisted(candidate: str, denylist) -> bool:
    """Кандидат відхиляється, якщо ХОЧ ОДИН його рядок є денай-лист фразою.

    Раніше порівнювалось точним рівнянням усього кандидата, тому
    багаторядковий кандидат (заголовок бланка + номер наступним рядком) не
    збігався з фразою денай-листа й мовчки приймався як значення поля --
    саме той випадок, від якого денай-лист і мав захищати."""
    if not denylist or not candidate:
        return False
    lines = [line.strip().lower() for line in candidate.split("\n") if line.strip()]
    return any(line in denylist for line in lines)


# Довжина, за якою кандидат перестає бути "значенням поля" і стає ознакою,
# що OCR злила кілька логічних полів в один блок без внутрішньої межі --
# виміряний провал: LEAVE-001, `unit_to_report` віддав ~340 символів (майже
# все тіло бланка) замість сусіднього значення (known-weak-spots.md, 2.11a).
# Легітимні багаторядкові значення бланків (повна назва частини у 2 рядки)
# лишаються в межах з великим запасом.
OVERSIZED_CANDIDATE_CHARS = 200


# Блок, вищий за медіанну висоту блоку в стільки разів, найпевніше містить
# КІЛЬКА логічних рядків/полів, злитих Surya в один bbox, а не одне значення
# -- виміряно: LEAVE-001, блок з "вид відпустки"/"дата повернення"/
# "найменування військової частини" висотою ~908px проти медіани ~84px
# (10.8x). bbox такого блоку не описує позицію ЖОДНОГО окремого рядка
# всередині нього -- ні для геометричного вирівнювання, ні для "рядки в
# тому самому блоці до лейбла", ні для "попередній блок за списком": усі
# три мовчки візьмуть щось під/над/перед УСІМ блоком, а не рядком, де
# насправді стоїть лейбл. Легітимний багаторядковий блок (напр. заголовок
# бланка у 4 рядки, ~2.1x медіани) лишається під порогом з запасом.
MEGA_BLOCK_HEIGHT_RATIO = 2.5


def _median_block_height(blocks):
    heights = [b["bbox"][3] - b["bbox"][1] for b in blocks if b.get("bbox")]
    if not heights:
        return None
    heights.sort()
    return heights[len(heights) // 2] or 1.0


def _is_mega_block(block, h_med) -> bool:
    bbox = block.get("bbox")
    if not bbox or not h_med:
        return False
    return (bbox[3] - bbox[1]) > MEGA_BLOCK_HEIGHT_RATIO * h_med


def _geometric_candidate(blocks, label_i, h_med):
    """Шукає значення за вирівнюванням bbox з блоком лейбла, а не за
    порядком блоків у списку -- research-round-2026-08-12.md.

    Викликається лише коли лейбл ПЕРШИЙ рядок свого блоку (тобто раніше
    значення бралось як "попередній блок за списком" -- саме тут стався
    виміряний збій: сусідній за списком блок опинявся не тим, що сусідній
    ГЕОМЕТРИЧНО). Коли лейбл НЕ перший рядок -- значення вже надійно в
    ТОМУ Ж блоці, геометрія там не потрібна й не викликається (виклик --
    у find_block_before_label). label_i уже перевірений викликом як НЕ
    мега-блок.

    Пріоритет напрямку: (1) той самий рядок, значення ПРАВОРУЧ від лейбла;
    (2) той самий стовпець, значення НАД лейблом; (3) той самий стовпець,
    значення ПІД лейблом. "Над" ПЕРЕД "під" навмисно, не за модулем
    відстані: у цій родині бланків лейбл-примітка в дужках СИСТЕМАТИЧНО
    стоїть ПІД значенням, якому належить (`unit_to_report`,
    "дата повернення", "звання, прізвище" -- усі виміряні приклади),
    ніколи навпаки. Виміряний реальний провал версії "усе за модулем
    відстані": лейбл "(військове звання, прізвище...)" мав правильного
    кандидата НАД собою (ім'я, дист. 7px) і невірного ПІД собою (опис
    посади наступного поля, дист. 0px, впритул) -- ближчий за модулем
    переміг, хоч належав ІНШОМУ полю. "Ліворуч"/лінійний порядок навмисно
    НЕ покривається тут -- це фолбек виклику (попередній блок за списком),
    незмінний відносно попередньої поведінки.

    Кандидати-мега-блоки виключаються так само, як і лейбл-мега-блоки:
    bbox, що охоплює кілька рядків, вирівняється з чим завгодно поруч
    суто випадково (підтверджено реальним багом -- блок ПІБ заявника
    "вирівнювався" зі СУСІДНІМ мега-блоком лише тому, що впритул сидів
    НАД ним усім, а не над конкретним рядком-значенням).

    Повертає "\\n".join(lines) знайденого блоку або None -- коли жоден блок
    не має bbox (docx -- геометрії немає, попередня лінійна поведінка
    незмінна) або жоден кандидат не пройшов фільтр вирівнювання.

    "Той самий рядок/стовпець" визначається ПЕРЕТИНОМ діапазонів bbox по
    перпендикулярній осі, а не відстанню між ЦЕНТРАМИ блоків. Виміряний
    реальний провал попередньої версії (центр + поріг 0.5*h_med): короткий
    лейбл "(дата повернення)" і довша дата над ним "23" травня 2026 р."
    мають РІЗНУ ширину bbox, тому їхні центри розходяться на ~40px --
    більше за поріг, і геометрично правильний, майже впритул сусід
    відкидався, а випадковий далекий блок (~118px) проходив поріг і
    вигравав. Перетин діапазонів переживає різницю ширини тексту, лишаючись
    чутливим до напрямку (той самий рядок/стовпець, не будь-де)."""
    label_bbox = blocks[label_i].get("bbox")
    if not label_bbox or not h_med:
        return None
    label_page = blocks[label_i].get("page")
    lx1, ly1, lx2, ly2 = label_bbox

    same_row, same_above, same_below = [], [], []
    for i, block in enumerate(blocks):
        if i == label_i or not block.get("bbox") or not block["lines"]:
            continue
        # Кандидат з ІНШОЇ сторінки/кадру виключається завжди: кожна
        # сторінка PDF (і кожен кадр TIFF) рахує bbox з нуля, тому
        # "близькість" між сторінками -- геометричний нуль-сенс, не збіг.
        # Виміряний реальний баг: LEAVE-003.pdf (2 сторінки) -- блок
        # "Командир" зі сторінки 2 опинився "поруч" із лейблом на сторінці 1
        # лише тому, що обидві сторінки рахують y з нуля.
        if block.get("page") != label_page:
            continue
        if _is_mega_block(block, h_med):
            continue
        bx1, by1, bx2, by2 = block["bbox"]
        y_overlap = by1 < ly2 and ly1 < by2
        x_overlap = bx1 < lx2 and lx1 < bx2
        if y_overlap and bx1 > lx2:
            same_row.append((bx1 - lx2, block))
        elif x_overlap and by2 <= ly1:
            same_above.append((ly1 - by2, block))
        elif x_overlap and by1 >= ly2:
            same_below.append((by1 - ly2, block))

    for group in (same_row, same_above, same_below):
        if group:
            group.sort(key=lambda t: t[0])
            return "\n".join(group[0][1]["lines"])
    return None


def _sandwich_value(block_text: str, label_substring: str):
    """Значення, приклеєне до лейбла в ОДНОМУ суцільному OCR-тексті без
    роздільника -- бланк друкує "ЗНАЧЕННЯ(лейбл-примітка)" одним потоком, а
    Surya не завжди ставить `\\n` на межі поля -- research-round-2026-08-12.md,
    варіант C. Шукає лейбл у ПОВНОМУ тексті мега-блоку (не в одному
    `\\n`-розрізаному "рядку" -- сам `\\n` тут не межа поля, лише випадковий
    артефакт того, де Surya вставила `<br>`: підтверджено реальним випадком,
    де значення для лейбла з одного "рядка" фізично лежить у ПОПЕРЕДНЬОМУ),
    бере текст між НАЙБЛИЖЧОЮ попередньою ")" і початком лейбла.

    Навмисно консервативний -- None означає "не застосовно", НЕ "значення
    відсутнє": якщо немає попередньої ")" (лейбл, найпевніше, перший у
    блоці -- значення в ПОПЕРЕДНЬОМУ окремому блоці, сендвіч тут не
    працює), або кандидат містить ВКЛАДЕНУ "(" (ознака, що OCR десь РАНІШЕ
    в блоці загубила закриваючу дужку, і межа "з'їхала" на поле раніше --
    виміряний реальний випадок: одна втрачена дужка захопила текст трьох
    чужих полів), або кандидат аномально довгий -- відмовляється, а не
    вгадує.

    normalize_ws на ОБОХ аргументах -- ОБОВ'ЯЗКОВО: `hits` (виклик знаходить
    лейбл через find_block_before_label) знаходяться через `phrase_in_text`,
    яка вже нормалізує пробіли (nbsp, подвійний пробіл, перенос рядка
    всередині фрази). Без цього тут `.find()` шукав би лейбл буквально й
    міг НЕ знайти те, що `phrase_in_text` щойно знайшла -- сендвіч мовчки
    відмовлявся б на тих самих OCR-документах, де межа лейбла найчастіше і
    ламається пробілами."""
    text = normalize_ws(block_text).lower()
    label = normalize_ws(label_substring).lower()
    idx = text.find(label)
    if idx == -1:
        return None
    prev_close = text.rfind(")", 0, idx)
    if prev_close == -1:
        return None
    value = text[prev_close + 1:idx].rstrip("(").strip()
    if not value or len(value) > OVERSIZED_CANDIDATE_CHARS or "(" in value:
        return None
    return value


def _has_unmatched_close_paren(text: str) -> bool:
    """True, якщо в тексті є ")" без відповідної "(" ПЕРЕД нею в ЦЬОМУ Ж
    тексті -- ознака, що справжній початок значення лежить у ПОПЕРЕДНЬОМУ
    блоці. Виміряний реальний випадок: PyMuPDF (текстовий шар PDF) розбив
    "Центральна база зберігання майна (в/ч" / "Т3011)" на ДВА окремі блоки
    рівно на переносі рядка всередині дужки -- значення не мега-блок і не
    склеєне без пробілу, воно просто фізично в іншому блоці, ніж лейбл."""
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                return True
            depth -= 1
    return False


def _extend_across_block_boundary(blocks, label_block_i, candidate):
    """Якщо candidate має незакриту ")" -- добирає рядки з КІНЦЯ
    попереднього блоку, по одному, поки дужки не збалансуються. Мінімально
    необхідне розширення, а не весь попередній блок цілком: інакше в
    кандидат потрапив би й сторонній текст (напр. "посада, місце роботи"
    перед "Центральна база зберігання майна (в/ч" в тому самому блоці).
    Якщо баланс не досягається навіть з усім попереднім блоком -- повертає
    candidate без змін (безпечний відкат, а не вигаданий текст)."""
    if not _has_unmatched_close_paren(candidate) or label_block_i == 0:
        return candidate
    prev_lines = blocks[label_block_i - 1]["lines"]
    prefix = []
    for line in reversed(prev_lines):
        prefix.insert(0, line)
        merged = " ".join(prefix) + " " + candidate.replace("\n", " ")
        if not _has_unmatched_close_paren(merged):
            # Пробіл, не "\n", на межі злиття: це перенос рядка ВСЕРЕДИНІ
            # одного значення (де опинилась дужка), не межа двох різних
            # значень -- на відміну від "\n".join(prefix) для рядків
            # УСЕРЕДИНІ самого prefix, де перенос може бути змістовним.
            return "\n".join(prefix) + " " + candidate
    return candidate


def find_block_before_label(blocks, label_substring, denylist=None):
    """blocks: результат group_blocks_into_lines() -- список
    {"lines": [...], "bbox": (...)|None}.
    Повертає (значення_або_None, причина), причина:
    matched | no_label | ambiguous_label | denylisted |
    oversized_block_suspect.

    Логіка пошуку:
    - лейбл усередині МЕГА-блоку (_is_mega_block) -- ні лінійна, ні
      геометрична позиція всередині нього не надійна (bbox описує ввесь
      блок, не конкретний рядок). Спершу пробується детермінований
      "сендвіч"-парсер (_sandwich_value) -- значення й лейбл нерідко
      склеєні в ОДИН OCR-текст без розриву; якщо він відмовився (сам по
      собі консервативний) -- `oversized_block_suspect` БЕЗ підказки, а не
      з обрізаним текстом: обрізка сама вгадувала межу поля іншим способом
      і так само помилялась (виміряно емпірично -- research-round-2026-08-12.md,
      варіант A: повний текст документа відновив 5 з 7 полів мега-блоку,
      обрізана підказка -- 1 з 5). Виклик направляє таке поле в LLM-фолбек
      з ПОВНИМ текстом документа;
    - інакше, якщо лейбл не перший рядок свого блоку -- значення це ВСІ
      рядки того ж блоку до лейбла (може бути кілька -- багаторядкове
      значення). Ніколи не змішує рядки з двох різних блоків в одну
      "лінійну" відстань;
    - інакше (лейбл перший рядок свого блоку) -- спершу геометрична
      прив'язка за bbox (_geometric_candidate); коли bbox немає (docx) або
      вирівняного кандидата не знайдено -- лінійний фолбек, як і раніше:
      весь попередній блок за порядком списку.

    Якщо лейбл трапляється в КІЛЬКОХ місцях документа -- це `ambiguous_label`,
    а не "беремо перше входження". Раніше перше входження вигравало мовчки, і
    документ із двома схожими лейблами (звання заявника й звання командира)
    міг дати чуже значення з виглядом повного успіху.

    Кандидат зі ЗВИЧАЙНОГО блоку, аномально довший за очікуване значення
    поля (OVERSIZED_CANDIDATE_CHARS), так само не приймається мовчки як
    "matched" -- та сама проблема (кілька полів без межі), лише не в лейблі,
    а у сусідньому блоці. Повертається `oversized_block_suspect` без
    підказки, з тієї самої причини, що й для мега-блоку.
    """
    low_label = label_substring.lower()
    low_denylist = denylist or set()

    hits = []
    for i, block in enumerate(blocks):
        for j, line in enumerate(block["lines"]):
            # Межа слова на початку (як у phrase_in_text): лейбл не має
            # співпадати всередині іншого слова.
            if phrase_in_text(line.lower(), low_label):
                hits.append((i, j))

    if not hits:
        return None, "no_label"

    h_med = _median_block_height(blocks)

    # Перевірка мега-блоку -- ДО побудови кандидатів і ДО ambiguous_label:
    # інакше та сама плутанина рядків усередині мега-блоку могла б випадково
    # дати кілька РІЗНИХ "кандидатів" і замаскувати проблему під "лейбл
    # неоднозначний" замість чесного "не можна довіряти позиції".
    #
    # Розділяємо ВСІ хіти на мега- і звичайні, а не дивимось лише на
    # hits[0]: якщо лейбл трапляється і в мега-, і в звичайному блоці
    # одночасно -- це справжня неоднозначність (виправлений баг: раніше
    # `any(...)` перевіряла всі хіти, а брався беззастережно hits[0], тому
    # чистий звичайний блок міг "загубитись" за спиною мега-блоку в іншому
    # місці документа, чи навпаки).
    mega_hits = [(i, j) for i, j in hits if _is_mega_block(blocks[i], h_med)]
    normal_hits = [(i, j) for i, j in hits if not _is_mega_block(blocks[i], h_med)]

    if mega_hits and normal_hits:
        return None, "ambiguous_label"

    if mega_hits:
        sandwich_values = []
        for i, _ in mega_hits:
            block_text = "\n".join(blocks[i]["lines"])
            value = _sandwich_value(block_text, label_substring)
            if value is not None:
                sandwich_values.append(value)
        distinct_sandwich = {v.strip() for v in sandwich_values}
        if len(distinct_sandwich) > 1:
            return None, "ambiguous_label"
        if len(distinct_sandwich) == 1:
            value = sandwich_values[0]
            if _is_denylisted(value, low_denylist):
                return None, "denylisted"
            return value, "matched"
        return None, "oversized_block_suspect"

    candidates = []
    unresolved_hit = False
    for i, j in hits:
        lines = blocks[i]["lines"]
        if j > 0:
            candidate = "\n".join(lines[:j])
            candidates.append(_extend_across_block_boundary(blocks, i, candidate))
            continue
        geo = _geometric_candidate(blocks, i, h_med)
        if geo is not None:
            candidates.append(geo)
        elif i > 0 and blocks[i - 1]["lines"]:
            candidates.append("\n".join(blocks[i - 1]["lines"]))
        else:
            # Цей хіт не дав ЖОДНОГО кандидата (лейбл -- перший рядок
            # першого блоку документа, і геометрія теж не спрацювала).
            # Раніше хіт просто мовчки випадав зі списку candidates -- якщо
            # лейбл трапляється кілька разів, а цей конкретний хіт міг би
            # дати ІНШЕ значення, ми цього не дізнаємось і ризикуємо
            # видати "matched" за збігом решти хітів, хоча справжньої
            # одностайності не перевірено.
            unresolved_hit = True

    if not candidates:
        return None, "no_label"
    if unresolved_hit and len(hits) > 1:
        return None, "ambiguous_label"
    distinct = {c.strip() for c in candidates}
    if len(distinct) > 1:
        return None, "ambiguous_label"

    candidate = candidates[0]
    if _is_denylisted(candidate, low_denylist):
        return None, "denylisted"
    if len(candidate) > OVERSIZED_CANDIDATE_CHARS:
        return None, "oversized_block_suspect"
    return candidate, "matched"


def first_block_starting_with(blocks, prefix):
    """Регістронезалежно: OCR/автор бланка не гарантує той самий регістр,
    що схема написала в `starts_with` (напр. "Видано" проти "видано" на
    початку речення чи навпаки)."""
    low_prefix = prefix.lower()
    return next((b for b in blocks if b.strip().lower().startswith(low_prefix)), None)


def strip_literal_prefix(text, prefix):
    """Знімає ЛІТЕРАЛЬНИЙ префікс-рядок, якщо він є на початку (регістр
    ігнорується). НЕ str.lstrip(prefix) -- lstrip прибирає будь-які символи
    з НАБОРУ символів префікса, а не сам рядок."""
    if text and text.strip().lower().startswith(prefix.lower()):
        return text.strip()[len(prefix):].strip()
    return text


def parse_rank_and_name(raw_line, rank_alias_lookup):
    """Токенізація замість одного regex на все: найдовший префікс токенів, що
    є відомим званням, визначає межу; решта токенів -- прізвище (ВЕЛИКІ
    ЛІТЕРИ) / ім'я / по батькові за регістром.

    Якщо жоден токен не у ВЕЛИКОМУ регістрі -- повертає всі три поля як
    None, а НЕ "решту токенів за позицією": позиційний фолбек тут давав
    ЗСУНУТІ (не порожні, а тихо неправильні) given_name/patronymic, які
    виглядали як успішний розбір, тому LLM-фолбек для них ніколи не
    спрацьовував (підтверджено тестом). rank при цьому лишається -- його
    визначення не залежить від регістру прізвища."""
    if not raw_line or is_placeholder(raw_line):
        return None, {"surname": None, "given_name": None, "patronymic": None}
    tokens = raw_line.split()
    rank_value, rank_len = None, 0
    for n in range(len(tokens), 0, -1):
        candidate = " ".join(tokens[:n])
        # lookup_alias, а не пряме `in`: у документі звання стоїть у тому
        # відмінку, якого вимагає речення бланка ("Видано старшому сержанту"),
        # а довідник містить називний. Точний збіг через це не знаходив навіть
        # "підполковника" -- перевірено.
        hit = lookup_alias(candidate, rank_alias_lookup)
        if hit is not None:
            rank_value, rank_len = hit, n
            break
    rank_result = {"code": rank_value[0], "label": rank_value[1]} if rank_value else None
    name_tokens = tokens[rank_len:]
    surname_index = next((k for k, t in enumerate(name_tokens)
                          if t.isupper() and len(t) > 1), None)
    if surname_index is None:
        return rank_result, {"surname": None, "given_name": None, "patronymic": None}

    # Ім'я та по батькові беруться ЛИШЕ з токенів ПІСЛЯ прізвища. Раніше
    # бралася "решта токенів" з обох боків, і будь-який неспожитий токен
    # ЛІВОРУЧ від прізвища зсував поля: звання, відсутнє в довіднику
    # ("старшина ІВАНЕНКО Іван Іванович" -> given_name='старшина',
    # patronymic='Іван'), скорочення поза aliases ("мол. с-т"), або залишок
    # префікса ("Видано:" -> given_name='Видано:'). Підтверджено тестом.
    # У бланках ЗСУ порядок завжди "звання ПРІЗВИЩЕ Ім'я По-батькові", тож
    # усе ліворуч від прізвища -- це звання або сміття, не частина ПІБ.
    after = name_tokens[surname_index + 1:]
    leftover_before = name_tokens[:surname_index]
    return rank_result, {
        "surname": name_tokens[surname_index],
        "given_name": after[0] if after else None,
        "patronymic": after[1] if len(after) > 1 else None,
        # Неспожиті токени ліворуч -- сигнал, що звання не розпізнане або
        # префікс знято неповністю. Значення полів при цьому правильні, але
        # rank, найпевніше, втрачений -- і це має бути видно.
        "_leftover_before_surname": leftover_before or None,
    }


# Роль поля в розборі рядка "звання ПІБ". Перелік ЗАКРИТИЙ навмисно, і це не
# недоробка: до кожної ролі прив'язана морфологічна граммема
# (normalize._ROLE_GRAMMEME), яка й не дає відмінити прізвище як звичайний
# іменник. Якби схема могла оголосити довільну роль, розбір втратив би
# обмежувач і повернувся баг "ПЕТРОВА -> ПЕТРОВ зі статусом normalized" --
# найдорожча тиха помилка в системі, бо в базу йде ІНША людина з виглядом
# успіху. Вільними стають ІМЕНА полів, не роль.
NAME_PART_ROLES = ("rank", "surname", "given_name", "patronymic")
DEFAULT_NAME_GROUP = "__subject__"


def field_part(field: dict) -> str:
    """Роль поля: `part:` зі схеми, інакше саме ім'я поля.

    Фолбек на ім'я лишається свідомо -- дві наявні схеми називають поля
    рівно `rank`/`surname`/`given_name`/`patronymic`, і змушувати їх дописати
    `part:` заради того самого значення означало б зламати їх без користі.
    """
    return field.get("part") or field.get("name")


def name_group_key(field: dict) -> str:
    """До якої ОСОБИ належить поле. Один документ може описувати кількох
    (заявник і командир, що затверджує -- обидва є в наявних бланках), і без
    цього ключа розбір другої особи брав значення першої."""
    return field.get("group") or DEFAULT_NAME_GROUP


def primary_name_group(schema: dict) -> str:
    """Група ОСНОВНОЇ особи документа -- та, чиє поле стоїть у схемі першим.

    Порядок, а не спеціальна назва: якщо схема оголошує групи явно
    (`group: applicant`, `group: commander`), жодна з них не називається
    "__subject__", і перевірка на літерал лишала subject порожнім -- тобто
    основна особа взагалі не доходила до БД. Перше поле = основний суб'єкт,
    решта = додаткові.
    """
    for field in schema.get("fields") or []:
        if field.get("extraction") == "rank_and_name_tokenized":
            return name_group_key(field)
    return DEFAULT_NAME_GROUP


def resolve_name_groups(schema, grouped_blocks, denylist, dictionaries):
    """{group: (rank_result, name_parts, raw_line, label_reason)} -- по одному
    розбору на ОСОБУ, не один на документ.

    Було: єдиний кеш на весь документ, не ключований нічим. Схема з двома
    людьми віддала б у поля командира ПІБ заявника -- не null, а тихо чуже
    значення з провенансом `matched`, і навіть підказка для LLM була б від
    чужого лейбла. Плюс лейбл читався як field["label_before"] з ПЕРШОГО
    такого поля: варто було переставити поля в YAML місцями, і генеричний
    двигун кидав KeyError.
    """
    groups = {}
    for field in schema["fields"]:
        if field.get("extraction") != "rank_and_name_tokenized":
            continue
        groups.setdefault(name_group_key(field), []).append(field)

    resolved = {}
    for group, fields in groups.items():
        # Лейбл несе будь-яке поле групи -- зазвичай те, що описує звання.
        label = next((f["label_before"] for f in fields if f.get("label_before")), None)
        strip = next((f["strip_prefix"] for f in fields if f.get("strip_prefix")), None)
        # Довідник звань -- з поля цієї групи, що має роль rank; .get, а не
        # ["category"], бо схема може оголосити звання не категоріальним.
        rank_field = next((f for f in fields if field_part(f) == "rank"), None)
        rank_lookup = dictionaries.get((rank_field or {}).get("category"), {})

        if not label:
            resolved[group] = (None, {"surname": None, "given_name": None,
                                      "patronymic": None}, None, "no_label")
            continue
        raw_line, label_reason = find_block_before_label(grouped_blocks, label, denylist)
        if raw_line and strip:
            raw_line = strip_literal_prefix(raw_line, strip)
        rank_result, name_parts = parse_rank_and_name(raw_line, rank_lookup)
        resolved[group] = (rank_result, name_parts, raw_line, label_reason)
    return resolved


def _compile_variants(field_def):
    return [re.compile(v["pattern"]) for v in field_def.get("regex_variants", [])]


def extract_field_regex(field_def, text: str):
    """Пробує всі відомі варіанти по черзі. Групи стандартизовані за типом
    поля: date -> (day, month, year); інші -> (value)."""
    for pattern in _compile_variants(field_def):
        m = pattern.search(text)
        if m:
            groups = m.groupdict()
            if field_def.get("type") == "date":
                return {"day": groups.get("day"), "month": groups.get("month"), "year": groups.get("year")}, "matched"
            return groups.get("value"), "matched"
    return None, "no_value"


def extract_document(schema: dict, ocr_text: str, ocr_blocks: list, dictionaries: dict,
                      llm_extract_batch=None, title_phrases=None,
                      batch_size=4, self_consistency_n=1):
    """Повертає {field_name: (сире_значення, reason)}.
    reason: matched | llm | no_value | derived | llm_error:<Тип>.

    dictionaries: {category: alias_lookup} -- ПОВНИЙ набір довідників (не
    лише rank), бо build_json_schema_for_fields потребує їх для enum-ів
    категоріальних полів у LLM-групах.

    llm_extract_batch(field_defs, context_text, json_schema) -> {ім'я: значення}
    -- один виклик на ГРУПУ полів. Кожна група у власному try/except: збій
    однієї групи позначає лише ЇЇ поля як llm_error, не чіпає інші групи й
    не втрачає вже отримані детерміновані значення.
    """
    grouped_blocks = group_blocks_into_lines(ocr_blocks)
    flat_blocks = flatten_blocks(ocr_blocks)
    denylist = {p.strip().lower() for p in (title_phrases or [])}
    results = {}
    localized_gaps, global_gaps, hints = [], [], {}

    # Розбір "звання ПІБ" -- один на ОСОБУ, до головного циклу. Раніше це був
    # один лінивий кеш на весь документ (див. resolve_name_groups).
    name_groups = resolve_name_groups(schema, grouped_blocks, denylist, dictionaries)

    for field in schema["fields"]:
        name = field["name"]
        mode = field.get("extraction")

        if mode == "rank_and_name_tokenized":
            # part, а не ім'я поля: схема може називати поля як завгодно
            # (applicant_surname, commander_rank) -- роль оголошується явно.
            part = field_part(field)
            rank_result, name_parts, rank_raw_line, rank_label_reason = \
                name_groups[name_group_key(field)]
            value = rank_result if part == "rank" else name_parts.get(part)
            if value:
                results[name] = (value, "matched")
            elif part == "rank" and name_parts.get("_leftover_before_surname"):
                # Прізвище знайдене, але ліворуч від нього лишились неспожиті
                # токени -- значить там звання, якого немає в довіднику
                # (або скорочення поза aliases). Це інша причина, ніж
                # "рядка не знайдено", і вона підказує, що саме дописати.
                leftover = " ".join(name_parts["_leftover_before_surname"])
                results[name] = (None, f"rank_not_in_dictionary:{leftover}")
                hints[name] = rank_raw_line or ""
                localized_gaps.append(name)
            else:
                results[name] = (None, rank_label_reason if rank_label_reason != "matched" else "no_value")
                # рядок знайдено, не вдався лише розбір -> локалізована прогалина
                if rank_raw_line:
                    hints[name] = rank_raw_line
                    localized_gaps.append(name)
                else:
                    global_gaps.append(name)

        elif mode == "block_before_label":
            raw, label_reason = find_block_before_label(
                grouped_blocks, field["label_before"], denylist)
            if raw is not None and field.get("strip_prefix"):
                raw = strip_literal_prefix(raw, field["strip_prefix"])
            if raw is None:
                # Місце не локалізоване -- лейбл відсутній / неоднозначний /
                # кандидат відхилений denylist-ом / мега-блок чи аномально
                # довгий кандидат (`oversized_block_suspect`, коли
                # детермінований "сендвіч"-парсер у find_block_before_label
                # теж відмовився). Підказку не даємо НАВМИСНО в усіх цих
                # випадках: обрізана "підказка" сама вгадувала б межу поля
                # іншим способом і так само помилялась би (виміряно
                # емпірично -- research-round-2026-08-12.md, варіант A:
                # повний текст документа відновив 5 з 7 полів мега-блоку,
                # обрізана підказка -- 1 з 5). Причина зберігається в
                # provenance, щоб "лейбл неоднозначний" не виглядало як
                # "поля просто немає".
                results[name] = (None, label_reason)
                global_gaps.append(name)
            else:
                results[name] = (raw, "matched")

        elif mode == "first_block_matching":
            raw = first_block_starting_with(flat_blocks, field["starts_with"])
            if raw and field.get("strip_prefix"):
                raw = strip_literal_prefix(raw, field["strip_prefix"])
            if raw:
                results[name] = (raw, "matched")
            else:
                results[name] = (None, "no_value")
                global_gaps.append(name)

        elif mode == "regex":
            value, reason = extract_field_regex(field, ocr_text)
            results[name] = (value, reason)
            if value is None:
                # Regex -- найкрихкіший режим (паттерн пишеться під конкретну
                # верстку бланка), тож саме він найбільше потребує фолбеку.
                # Раніше regex-поля в прогалини не додавались узагалі --
                # найкрихкіший шлях був єдиним без страховки.
                global_gaps.append(name)

        elif mode == "derived_from":
            # обробляється в build_record-етапі, тут лише позначка
            results[name] = (None, "derived")

        elif mode == "llm":
            results[name] = (None, "no_value")
            global_gaps.append(name)

        else:
            results[name] = (None, "no_value")

    if llm_extract_batch is None:
        return results

    def run_group(batch_names, context_text):
        json_schema, field_defs = build_json_schema_for_fields(schema, dictionaries, batch_names)
        try:
            samples = [llm_extract_batch(field_defs, context_text, json_schema)
                       for _ in range(max(1, self_consistency_n))]
        except Exception as exc:
            for name in batch_names:
                results[name] = (None, f"llm_error:{type(exc).__name__}")
            return
        for name in batch_names:
            voted, split = majority_vote([s.get(name) for s in samples if isinstance(s, dict)])
            if voted is None:
                results[name] = (None, "no_value")
            else:
                # llm_split_vote -- голоси розділились навпіл, переможець
                # обраний фактично випадково; для рев'юера це має виглядати
                # інакше, ніж одноголосний результат.
                results[name] = (voted, "llm_split_vote" if split else "llm")

    # Локалізовані прогалини: контекст -- лише знайдений фрагмент. Групуємо за
    # САМОЮ підказкою, а не просто по batch_size: інакше поля з РІЗНИМИ
    # підказками потрапляли в один виклик, усі підказки склеювались в один
    # контекст без прив'язки "яка кому належить", і LLM могла приписати
    # підказку одного поля іншому.
    hint_groups = {}
    for name in localized_gaps:
        hint_groups.setdefault(hints.get(name, ""), []).append(name)
    for hint, names in hint_groups.items():
        for batch_names in chunk_fields(names, batch_size):
            run_group(batch_names, hint or ocr_text)

    # нелокалізовані: контекст -- увесь документ, іншого немає
    for batch_names in chunk_fields(global_gaps, batch_size):
        run_group(batch_names, ocr_text)

    return results
