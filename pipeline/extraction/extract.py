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

from pipeline.normalization.normalize import is_placeholder
from pipeline.extraction.schema_grammar import build_json_schema_for_fields, chunk_fields


def majority_vote(values):
    """Self-consistency: кілька семплів одного поля (temperature > 0 на боці
    викликача) -> найчастіше НЕ-порожнє значення. Значення можуть бути будь-
    якого JSON-типу (str/int/dict/None, бо LLM повертає структуровані
    значення для date-полів) -- dict/list порівнюються за канонічним JSON-
    рядком, не за ідентичністю об'єкта."""
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
        return None

    def key(v):
        return json.dumps(v, sort_keys=True, ensure_ascii=False) if isinstance(v, (dict, list)) else v

    counts = Counter(key(v) for v in cleaned)
    best_key = counts.most_common(1)[0][0]
    return next(v for v in cleaned if key(v) == best_key)


def flatten_blocks(blocks):
    """Плаский список НЕПОРОЖНІХ рядків з усіх блоків, у порядку появи.
    Використовується там, де межі блоків не важливі (напр.
    first_block_starting_with). Для пошуку "значення перед лейблом" межі
    БЛОКІВ важливі -- див. group_blocks_into_lines()."""
    flat = []
    for block in blocks:
        for line in block.split("\n"):
            line = line.strip()
            if line:
                flat.append(line)
    return flat


def group_blocks_into_lines(blocks):
    """Розбиває кожен блок на рядки, ЗБЕРІГАЮЧИ межі блоків (список списків),
    а не єдиний плаский список. Межі важливі в обидва боки:
    - багаторядкове значення, яке OCR згрупував в ОДИН блок (напр. "Навчальний
      центр Сухопутних військ\\nЗбройних Сил України «Десна»" перед лейблом у
      тому самому блоці), має лишитися РАЗОМ -- підтверджено реальним багом:
      плаский список брав лише ОДИН рядок перед лейблом і губив половину назви;
    - і навпаки, сусідній (інший) блок не повинен випадково приєднатися до
      значення просто тому, що він теж не виглядає як лейбл (підтверджено
      іншим реальним багом: заголовок документа в окремому блоці перед
      блоком зі значенням і лейблом звання/ПІБ)."""
    return [[line.strip() for line in block.split("\n") if line.strip()] for block in blocks]


def find_block_before_label(blocks, label_substring, denylist=None):
    """blocks: результат group_blocks_into_lines(). Шукає лейбл усередині
    КОЖНОГО блоку окремо:
    - якщо лейбл не перший рядок свого блоку -- значення це ВСІ рядки того ж
      блоку до лейбла (може бути кілька -- багаторядкове значення);
    - якщо лейбл перший рядок свого блоку -- значення це весь попередній блок.
    Ніколи не змішує рядки з двох різних блоків в одну "лінійну" відстань.

    denylist: фрази (заголовок бланка), які НЕ можуть бути справжнім
    значенням -- захист від випадку, коли лейбл лежить у тому самому блоці,
    що й значення сусіднього поля, і "попередній блок" -- це насправді
    заголовок документа."""
    low_label = label_substring.lower()
    low_denylist = denylist or set()
    for i, lines in enumerate(blocks):
        for j, line in enumerate(lines):
            if low_label in line.lower():
                if j > 0:
                    candidate = "\n".join(lines[:j])
                elif i > 0 and blocks[i - 1]:
                    candidate = "\n".join(blocks[i - 1])
                else:
                    return None
                if candidate.strip().lower() in low_denylist:
                    return None
                return candidate
    return None


def first_block_starting_with(blocks, prefix):
    return next((b for b in blocks if b.strip().startswith(prefix)), None)


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
        candidate = " ".join(tokens[:n]).lower()
        if candidate in rank_alias_lookup:
            rank_value, rank_len = rank_alias_lookup[candidate], n
            break
    rank_result = {"code": rank_value[0], "label": rank_value[1]} if rank_value else None
    name_tokens = tokens[rank_len:]
    surname = next((t for t in name_tokens if t.isupper() and len(t) > 1), None)
    if surname is None:
        return rank_result, {"surname": None, "given_name": None, "patronymic": None}
    rest = [t for t in name_tokens if t != surname]
    return rank_result, {
        "surname": surname,
        "given_name": rest[0] if rest else None,
        "patronymic": rest[1] if len(rest) > 1 else None,
    }


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
    rank_and_name_cache = None   # рахуємо один раз на документ, не на кожне поле
    rank_raw_line = None         # знайдений рядок "звання ПІБ" -- підказка для LLM
    localized_gaps, global_gaps, hints = [], [], {}

    rank_field = next((f for f in schema["fields"] if f.get("name") == "rank"), None)
    rank_alias_lookup = dictionaries.get(rank_field["category"], {}) if rank_field else {}

    for field in schema["fields"]:
        name = field["name"]
        mode = field.get("extraction")

        if mode == "rank_and_name_tokenized":
            if rank_and_name_cache is None:
                rank_raw_line = find_block_before_label(grouped_blocks, field["label_before"], denylist)
                if rank_raw_line and field.get("strip_prefix"):
                    rank_raw_line = strip_literal_prefix(rank_raw_line, field["strip_prefix"])
                rank_and_name_cache = parse_rank_and_name(rank_raw_line, rank_alias_lookup)
            rank_result, name_parts = rank_and_name_cache
            value = rank_result if name == "rank" else name_parts.get(name)
            if value:
                results[name] = (value, "matched")
            else:
                results[name] = (None, "no_value")
                # рядок знайдено, не вдався лише розбір -> локалізована прогалина
                if rank_raw_line:
                    hints[name] = rank_raw_line
                    localized_gaps.append(name)
                else:
                    global_gaps.append(name)

        elif mode == "block_before_label":
            raw = find_block_before_label(grouped_blocks, field["label_before"], denylist)
            if raw is not None and field.get("strip_prefix"):
                raw = strip_literal_prefix(raw, field["strip_prefix"])
            if raw is None:
                # Місце не локалізоване (лейбл не знайдено АБО кандидат
                # відхилений denylist-ом). Підказку не даємо навмисно:
                # відхилений кандидат -- це саме неправильний текст, і
                # передавати його в LLM означало б підштовхувати до тієї
                # самої помилки.
                results[name] = (None, "no_value")
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
            voted = majority_vote([s.get(name) for s in samples if isinstance(s, dict)])
            results[name] = (voted, "llm") if voted is not None else (None, "no_value")

    # локалізовані прогалини: контекст -- лише знайдений фрагмент
    for batch_names in chunk_fields(localized_gaps, batch_size):
        local_context = "\n".join(dict.fromkeys(hints[n] for n in batch_names if n in hints))
        run_group(batch_names, local_context or ocr_text)

    # нелокалізовані: контекст -- увесь документ, іншого немає
    for batch_names in chunk_fields(global_gaps, batch_size):
        run_group(batch_names, ocr_text)

    return results
