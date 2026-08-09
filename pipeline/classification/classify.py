"""Генерична класифікація домену документа. Не містить нічого про
конкретний домен — усі фрази читаються з
dictionaries/domain_keyphrases.yaml. Новий домен -- новий блок у YAML,
не новий код тут.

Домен -- це ГРУБИЙ сигнал (leave / deployment / ...). Вибір конкретної
схеми бланка робить pipeline/identification.py на рівні template.
"""
import re

import yaml

_PHRASE_RE_CACHE = {}


def load_domain_keyphrases(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["domains"]


def phrase_in_text(text_low: str, phrase: str) -> bool:
    """Збіг фрази з межею слова НА ПОЧАТКУ, але БЕЗ межі в кінці.

    Асиметрія навмисна: довідники свідомо містять стеми ("відрядж",
    "особов", "штатн"), які мають ловити всі словоформи, тому вимога межі в
    кінці зламала б їх. А от межа на початку потрібна: без неї коротка фраза
    співпадає всередині непов'язаного слова й накручує бал чужій схемі.
    """
    phrase = (phrase or "").strip().lower()
    if not phrase:
        return False
    pattern = _PHRASE_RE_CACHE.get(phrase)
    if pattern is None:
        pattern = _PHRASE_RE_CACHE[phrase] = re.compile(r"(?<!\w)" + re.escape(phrase))
    return pattern.search(text_low) is not None


def classify_domain_rules(text: str, domains: dict):
    """Заголовок (x3) + фрази/стеми тіла (x1). Повертає (домен_або_None, скори).

    None повертається у двох випадках: жодного збігу, або РІВНИЙ бал у
    кількох доменів. Раніше при нічиї мовчки брався перший за порядком у
    YAML -- тобто результат залежав від порядку рядків у довіднику, а не від
    документа, і жодного сигналу про це не було.
    """
    low = (text or "").lower()
    scores = {}
    for domain, phrases in domains.items():
        # .get(..., []) замість phrases["title"]: один неповний запис у
        # довіднику не має валити обробку всіх документів батчу.
        phrases = phrases or {}
        title_hits = sum(1 for p in phrases.get("title", []) if phrase_in_text(low, p))
        body_hits = sum(1 for p in phrases.get("body", []) if phrase_in_text(low, p))
        scores[domain] = title_hits * 3 + body_hits

    if not scores:
        return None, scores
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_domain, best_score = ranked[0]
    runner_up_score = ranked[1][1] if len(ranked) > 1 else 0
    if best_score == 0 or best_score == runner_up_score:
        return None, scores
    return best_domain, scores
