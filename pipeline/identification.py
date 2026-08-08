"""Визначення ШАБЛОНУ документа (не лише домену) -- вибір схеми стає
частиною пайплайна, а не ручною дією людини.

Чому не домен: схема прив'язана до template (`deployment_certificate`), а
один домен містить багато різних бланків. Перевірка "домен схеми == домен
класифікації" не відрізнить "правильний домен, неправильний бланк", і в
пакетному режимі (папка-приймач з context/project-expectations.md) немає
людини, яка вручну підсуне потрібний YAML на кожен файл.

Кожна схема сама описує, як її впізнати (блок `identification`), тому
"новий шаблон = новий YAML" стає правдою end-to-end: не потрібно ні правити
код, ні синхронізувати окремий довідник ключових фраз.
"""
import glob
import os

import yaml

from pipeline.classification.classify import classify_domain_rules

TITLE_WEIGHT = 5     # заголовок бланка -- найсильніший сигнал
ANCHOR_WEIGHT = 2    # характерні лейбли/номер додатка -- підтверджувальні
DEFAULT_MIN_SCORE = 5


def load_schemas(schemas_dir: str) -> list:
    """Усі *.yaml, що є схемами (мають template + fields). Довідники й
    domain_keyphrases сюди не потрапляють -- визначається за ВМІСТОМ, не за
    назвою файлу (порядок glob не алфавітний, а назва довідника не містить
    жодного маркера "це не схема")."""
    schemas = []
    for path in sorted(glob.glob(os.path.join(schemas_dir, "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            content = yaml.safe_load(f)
        if isinstance(content, dict) and "template" in content and "fields" in content:
            content["_path"] = path
            schemas.append(content)
    return schemas


def schema_title_phrases(schema: dict) -> list:
    return list(schema.get("identification", {}).get("title", []))


def score_schema(text: str, schema: dict) -> int:
    low = text.lower()
    ident = schema.get("identification", {})
    title_hits = sum(1 for p in ident.get("title", []) if p.lower() in low)
    anchor_hits = sum(1 for p in ident.get("anchors", []) if p.lower() in low)
    return title_hits * TITLE_WEIGHT + anchor_hits * ANCHOR_WEIGHT


def identify_template(text: str, schemas: list, domains: dict = None, llm_choose=None) -> dict:
    """Повертає словник-результат, ніколи не кидає виняток:
      schema     -- обрана схема або None
      template   -- її template або None
      domain     -- домен обраної схеми, або грубий домен із domain_keyphrases,
                    якщо шаблон не визначено (корисно для черги unresolved)
      source     -- anchors | llm | None
      score, runner_up, scores, reason

    llm_choose(prompt, choices) -> str -- grammar-constrained вибір рівно
    одного з choices; "unknown" ЗАВЖДИ серед choices, інакше модель,
    обмежена лише реальними шаблонами, змушена вибрати щось навіть коли
    жоден варіант не підходить.
    """
    scores = {s["template"]: score_schema(text, s) for s in schemas}
    by_template = {s["template"]: s for s in schemas}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

    best_template, best_score = ranked[0] if ranked else (None, 0)
    runner_up_score = ranked[1][1] if len(ranked) > 1 else 0
    coarse_domain = None
    if domains:
        coarse_domain, _ = classify_domain_rules(text, domains)

    if best_template is not None:
        min_score = by_template[best_template].get("identification", {}).get("min_score", DEFAULT_MIN_SCORE)
        # Строга нерівність: рівний бал двох шаблонів -- це неоднозначність,
        # а не перемога того, хто випадково перший у списку.
        if best_score >= min_score and best_score > runner_up_score:
            schema = by_template[best_template]
            return {
                "schema": schema, "template": best_template, "domain": schema.get("domain"),
                "source": "anchors", "score": best_score, "runner_up": runner_up_score,
                "scores": scores, "reason": None,
            }

    if llm_choose is not None and schemas:
        options = "\n".join(
            f"- {s['template']}: " + (s.get("identification", {}).get("description")
                                       or s.get("domain", ""))
            for s in schemas
        )
        prompt = (
            "Визнач, який це бланк документа, з переліку нижче, або 'unknown', "
            f"якщо жоден не підходить.\n\n{options}\n\nТекст документа:\n{text}"
        )
        answer = llm_choose(prompt, [s["template"] for s in schemas] + ["unknown"]).strip()
        if answer in by_template:
            schema = by_template[answer]
            return {
                "schema": schema, "template": answer, "domain": schema.get("domain"),
                "source": "llm", "score": scores.get(answer, 0), "runner_up": runner_up_score,
                "scores": scores, "reason": None,
            }

    reason = "ambiguous" if best_score and best_score == runner_up_score else "no_template_match"
    return {
        "schema": None, "template": None, "domain": coarse_domain,
        "source": None, "score": best_score, "runner_up": runner_up_score,
        "scores": scores, "reason": reason,
    }


def missing_dictionaries(schema: dict, dictionaries: dict) -> set:
    """Категорії, на які схема посилається, але довідник не завантажено --
    інакше поле мовчки лишається "unknown" без жодного пояснення чому."""
    required = {f["category"] for f in schema["fields"]
                if f.get("type") == "category" and f.get("category")}
    return required - set(dictionaries)
