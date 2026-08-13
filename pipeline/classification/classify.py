"""Генерична класифікація домену документа. Не містить нічого про
конкретний домен — усі фрази читаються з
dictionaries/domain_keyphrases.yaml. Новий домен -- новий блок у YAML,
не новий код тут.

Домен -- це ГРУБИЙ сигнал (leave / deployment / ...). Вибір конкретної
схеми бланка робить pipeline/identification.py на рівні template.
"""
import re
import unicodedata

import yaml

_PHRASE_RE_CACHE = {}

# Пробіли, які в docx/OCR трапляються замість звичайного: нерозривний,
# вузький нерозривний, тонкий, а також переноси рядків усередині заголовка,
# розбитого OCR на два рядки.
_WS_RE = re.compile(r"[\s   ​]+")


def normalize_ws(text: str) -> str:
    """Зводить будь-які пробіли до одного звичайного й прибирає керуючі
    символи. Без цього фраза не знаходилась узагалі: "Додаток\\u00a030" з
    нерозривним пробілом, подвійний пробіл чи заголовок, розбитий OCR на два
    рядки, давали збіг False -- документ втрачав 5 балів і йшов у unresolved
    (перевірено)."""
    if not text:
        return ""
    cleaned = "".join(ch for ch in str(text)
                      if unicodedata.category(ch)[0] != "C" or ch in "\n\t")
    return _WS_RE.sub(" ", cleaned).strip()


def load_domain_keyphrases(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["domains"]


def phrase_in_text(text_low: str, phrase: str, is_stem: bool = False) -> bool:
    """Збіг фрази з межею слова НА ПОЧАТКУ завжди; межа В КІНЦІ теж вимагається,
    ОКРІМ коли is_stem=True.

    is_stem=True -- лише для довідникових записів, СВІДОМО позначених як
    стем ("відрядж", "особов", "штатн" -- мають ловити всі словоформи).
    За замовчуванням межа потрібна з ОБОХ боків -- виправлений баг: раніше
    відсутність межі в кінці застосовувалась до КОЖНОЇ фрази без розбору,
    тому "додаток 28" (номер, не стем) хибно збігався всередині "додаток
    289"/"додаток 28а", а "діб" (ціле слово) -- всередині "дібрати"/
    "дібраний". Обидва підтверджено як реальний false positive у скорингу
    домену/шаблону (research-round-2026-08-12.md).
    """
    phrase = normalize_ws(phrase).lower()
    if not phrase:
        return False
    cache_key = (phrase, is_stem)
    pattern = _PHRASE_RE_CACHE.get(cache_key)
    if pattern is None:
        # Пробіли у ФРАЗІ теж можуть бути будь-якими в документі, тому кожен
        # пробіл фрази компілюється як "один або більше будь-яких пробілів".
        parts = [re.escape(p) for p in phrase.split(" ") if p]
        body = r"\s+".join(parts)
        suffix = "" if is_stem else r"(?!\w)"
        pattern = _PHRASE_RE_CACHE[cache_key] = re.compile(r"(?<!\w)" + body + suffix)
    return pattern.search(normalize_ws(text_low).lower()) is not None


def _phrase_entry(entry):
    """Запис довідника -- або звичайний рядок (потрібна межа з обох боків),
    або {"stem": "..."} (свідомо позначений стем, межа лише на початку).
    Повертає (текст, is_stem); (None, False) для порожнього/невалідного
    запису -- виклик відсіює None перед phrase_in_text."""
    if isinstance(entry, dict):
        return entry.get("stem"), True
    return entry, False


def _count_phrase_hits(low_text: str, entries) -> int:
    count = 0
    for entry in entries or []:
        text, is_stem = _phrase_entry(entry)
        if text and phrase_in_text(low_text, text, is_stem=is_stem):
            count += 1
    return count


def classify_domain_rules(text: str, domains: dict):
    """Заголовок (x3) + фрази/стеми тіла (x1). Повертає (домен_або_None, скори).

    None повертається у двох випадках: жодного збігу, або РІВНИЙ бал у
    кількох доменів. Раніше при нічиї мовчки брався перший за порядком у
    YAML -- тобто результат залежав від порядку рядків у довіднику, а не від
    документа, і жодного сигналу про це не було.
    """
    low = (text or "").lower()

    # ПРОЦЕДУРНІ домени перевіряються ОКРЕМО і ПЕРШИМИ, а не конкурують за
    # балами з тематичними. Причина категорійна, а не тюнінгова: `leave` /
    # `deployment` / `equipment` / `staffing` відповідають на питання "про ЩО
    # цей документ", а `normative` -- на питання "це запис чи ПРАВИЛА, за
    # якими записи складаються". Це різні осі, і змагання між ними
    # безпідставне.
    #
    # Заміряно, чому це не абстракція: Інструкція з діловодства (402898
    # символів) дає leave 8, equipment 8, staffing 9, deployment 7,
    # normative 6 -- усі п'ять близько, бо вона МІСТИТЬ усі бланки й згадує
    # всі теми. Тематичний переможець там визначається шумом: до додавання
    # `normative` вона класифікувалась як `equipment`, після -- як `staffing`,
    # і жоден із них не є правдою. Підняти вагу `normative`, щоб він переміг,
    # означало б підігнати число під один документ.
    #
    # Це той самий висновок, що вже застосований на рівні шаблонів
    # (`multiple_templates_matched`): документ, який упізнається як багато
    # речей одночасно, не є жодною з них -- він їх ДЖЕРЕЛО.
    #
    # `kind: procedural` оголошується в самому довіднику, не в коді, тому
    # новий процедурний домен -- це рядок у YAML.
    for domain, phrases in (domains or {}).items():
        if (phrases or {}).get("kind") != "procedural":
            continue
        if _count_phrase_hits(low, (phrases or {}).get("title", [])):
            return domain, {domain: "procedural_title_match"}

    scores = {}
    for domain, phrases in domains.items():
        if (phrases or {}).get("kind") == "procedural":
            # Уже перевірено вище; у тематичному змаганні не бере участі.
            continue
        # .get(..., []) замість phrases["title"]: один неповний запис у
        # довіднику не має валити обробку всіх документів батчу.
        phrases = phrases or {}
        title_hits = _count_phrase_hits(low, phrases.get("title", []))
        body_hits = _count_phrase_hits(low, phrases.get("body", []))
        scores[domain] = title_hits * 3 + body_hits

    if not scores:
        return None, scores
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_domain, best_score = ranked[0]
    runner_up_score = ranked[1][1] if len(ranked) > 1 else 0
    if best_score == 0 or best_score == runner_up_score:
        return None, scores
    return best_domain, scores
