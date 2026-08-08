"""Генерична класифікація домену документа. Не містить нічого про
конкретний домен — усі фрази й описи читаються з
dictionaries/domain_keyphrases.yaml. Новий домен -- новий блок у YAML,
не новий код тут.
"""
import yaml


def load_domain_keyphrases(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["domains"]


def classify_domain_rules(text: str, domains: dict):
    """A+B: заголовок (x3) + фрази/стеми тіла (x1). Повертає (домен_або_None, скори)."""
    low = text.lower()
    scores = {}
    for domain, phrases in domains.items():
        title_hits = sum(1 for p in phrases["title"] if p in low)
        body_hits = sum(1 for p in phrases["body"] if p in low)
        scores[domain] = title_hits * 3 + body_hits
    best_domain = max(scores, key=scores.get)
    if scores[best_domain] == 0:
        return None, scores
    return best_domain, scores


def classify_domain_llm(text: str, domains: dict, llm_classify):
    """C: LLM-fallback -- лише коли A+B не дали жодного збігу.

    llm_classify(prompt, choices) -> str -- grammar-constrained вибір РІВНО
    одного з `choices` (напр. через GBNF-enum у llama.cpp), не вільний
    текст. `choices` ЗАВЖДИ включає "unknown" явним пунктом -- без цього
    модель, обмежена лише реальними доменами, фізично не могла б сказати
    "жоден не підходить" і була б змушена вибрати щось із закритого списку
    навіть коли жоден варіант не годиться."""
    domain_list = "\n".join(f"- {code}: {d['description']}" for code, d in domains.items())
    prompt = (
        "Визнач домен документа з переліку нижче, або 'unknown', якщо жоден "
        f"не підходить.\n\n{domain_list}\n\nТекст документа:\n{text}"
    )
    choices = list(domains) + ["unknown"]
    answer = llm_classify(prompt, choices).strip().lower()
    return answer if answer in domains else None


def classify_domain(text: str, domains: dict, llm_classify=None):
    """Повертає (домен_або_None, скори, джерело). Джерело: "rules"/"llm"/"unresolved"."""
    domain, scores = classify_domain_rules(text, domains)
    if domain is not None:
        return domain, scores, "rules"
    if llm_classify is None:
        return None, scores, "unresolved"
    domain = classify_domain_llm(text, domains, llm_classify)
    return domain, scores, ("llm" if domain else "unresolved")
