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
import re

import yaml

from pipeline.build_record import CONSISTENCY_RULES, DERIVE_FUNCS
from pipeline.classification.classify import classify_domain_rules, phrase_in_text
from pipeline.extraction.extract import NAME_PART_ROLES, field_part, name_group_key
from pipeline.normalization.normalize import (
    PLACEHOLDER_TOKENS_EXCEPT_KEY, PLACEHOLDER_TOKENS_KEY)

TITLE_WEIGHT = 5     # заголовок бланка -- найсильніший сигнал
ANCHOR_WEIGHT = 2    # характерні лейбли/номер додатка -- підтверджувальні
DEFAULT_MIN_SCORE = 5

# Нижня межа, ПІД якою LLM навіть не питають, який це бланк. Раніше межі не
# було взагалі: документ з балом 0 (книга обліку техніки, рапорт на відпустку
# -- будь-що, для чого схеми немає) віддавався моделі, а та була обмежена
# grammar до переліку наявних шаблонів + "unknown", тобто мала вибирати серед
# завідомо чужих варіантів. Обраний так шаблон приймався як остаточний, з тим
# самим статусом, що й підтверджений анкорами. Наслідок: чужа схема
# витягувала поля через LLM-фолбек, критичні прогалини закривались, і
# документ ставав confirmed з фактом, якого в ньому немає.
# 2 = рівно один збіглий анкор: хоч один незалежний сигнал, що це той бланк.
DEFAULT_LLM_FLOOR = ANCHOR_WEIGHT


# Режими екстракції, які двигун справді реалізує, і обов'язкові ключі кожного.
# Перевіряється при завантаженні, бо інакше описка в новому YAML має два
# однаково погані наслідки: невідомий режим дає ТИХИЙ no_value (той самий
# вигляд, що "значення в документі немає"), а відсутній ключ -- KeyError уже
# посеред обробки, коли _persist ще не викликався, тобто документ не отримує
# ні запису у сховище, ні рядка в індексі. Обіцянка "новий шаблон = новий
# YAML" без цієї перевірки означає "новий YAML = тихо зламаний прогін".
EXTRACTION_REQUIRED_KEYS = {
    "regex": ("regex_variants",),
    "block_before_label": ("label_before",),
    "first_block_matching": ("starts_with",),
    "derived_from": ("derived_from", "derive"),
    "llm": (),
    # rank_and_name_tokenized свідомо БЕЗ обов'язкових ключів: лейбл несе лише
    # поле rank, а surname/given_name/patronymic беруть результат із кеша того
    # самого розбору рядка. Вимагати label_before на кожному з них -- саме та
    # помилка, яку валідатор видав першою на обох робочих схемах.
    "rank_and_name_tokenized": (),
}
# Групові режими: лейбл потрібен рівно один на групу, і перевіряється окремо.
GROUP_MODES = ("rank_and_name_tokenized",)
KNOWN_DB_TARGETS = {"person", "fact_value", "fact_date_start", "fact_date_end",
                    "additional_info"}
# db_target, для яких схема тримає РІВНО ОДНУ змінну (build_record.py:
# fact_value_code/fact_date_start/fact_date_end) -- друге поле з тим самим
# таргетом мовчки ПЕРЕЗАПИСУЄ перше (на відміну від "person", де дублікат
# явно йде в additional_info). "additional_info" сюди навмисно не входить:
# там ключ -- ім'я поля, а не таргет, тож кілька полів там не конфліктують.
SINGLE_VALUE_DB_TARGETS = {"fact_value", "fact_date_start", "fact_date_end"}
# Типи полів, які генеричний рушій (schema_grammar._field_json_schema)
# розрізняє. Одруківка в type: (напр. "catgory") інакше мовчки отримує ТУ
# САМУ вільну схему {"string","null"}, що й легітимний "text" -- тобто
# категоріальне поле втрачає enum-обмеження LLM без жодного попередження.
KNOWN_FIELD_TYPES = {"category", "text", "date", "number", "object_ref"}
# Ключі, які схема може оголосити, але код їх НЕ читає. Тримаємо перелік явно,
# щоб автор нової схеми дізнався про це з попередження, а не з тихо
# незаповненого поля через тиждень. `note` тут свідомо НЕМА: він і не має
# читатись кодом, це документація для людини, і попередження на нього -- шум.
DECLARED_BUT_UNREAD_KEYS = {"multiple", "registry", "out_of_scope"}
# Типи зв'язку документ->документ, які build_record складає в
# record["document_links"]. Перелік закритий: невідомий тип означав би, що
# зв'язок оголошений у YAML і мовчки нікуди не пішов.
KNOWN_LINK_TYPES = {"supersedes"}


def validate_schema(schema: dict, known_fact_types=None) -> list:
    """Повертає список проблем: (severity, message). severity error | warning.
    Не кидає -- вирішує викликач, бо в пакетному режимі краще сказати про всі
    схеми одразу, ніж падати на першій."""
    problems = []
    path = schema.get("_path", schema.get("template", "?"))

    def err(msg):
        problems.append(("error", f"{path}: {msg}"))

    def warn(msg):
        problems.append(("warning", f"{path}: {msg}"))

    fact_type = schema.get("fact_type")
    if not fact_type:
        err("немає fact_type -- факт не отримає виміру в БД")
    # `is not None`, а не просто truthy: викликач МІГ передати завантажений,
    # але порожній реєстр (fact_type_registry.yaml відсутній/побитий/без
    # жодного code -- pipeline/run.py:load_fact_types повертає саме {} у
    # цьому випадку). Порожній словник як "перевірку вимкнено" тихо
    # вимикав саме ту перевірку, яку цей реєстр мав вмикати -- документ зі
    # схемою, що має друкарську помилку у fact_type, проходив без жодної
    # помилки, якщо реєстр з якоїсь причини не завантажився.
    elif known_fact_types is not None and fact_type not in known_fact_types:
        err(f"fact_type '{fact_type}' не зареєстрований у "
            f"dictionaries/fact_type_registry.yaml")

    seen_names = set()
    targets = set()
    single_value_targets_seen = {}
    for field in schema.get("fields") or []:
        name = field.get("name")
        if not name:
            err("поле без name")
            continue
        if name in seen_names:
            err(f"поле '{name}' оголошене двічі")
        seen_names.add(name)

        for key in DECLARED_BUT_UNREAD_KEYS & set(field):
            warn(f"поле '{name}': ключ '{key}' не читається кодом "
                 "(оголошений, але не реалізований)")

        target = field.get("db_target", "additional_info")
        targets.add(target)
        if target not in KNOWN_DB_TARGETS:
            err(f"поле '{name}': невідомий db_target '{target}' -- значення "
                f"мовчки пішло б у additional_info, який БД не читає")
        if target in SINGLE_VALUE_DB_TARGETS:
            single_value_targets_seen.setdefault(target, []).append(name)

        dimension = field.get("dimension")
        # `is not None` -- та сама причина, що й для fact_type вище.
        if dimension and known_fact_types is not None and dimension not in known_fact_types:
            err(f"поле '{name}': dimension '{dimension}' не зареєстрований у "
                "dictionaries/fact_type_registry.yaml")

        field_type = field.get("type")
        if field_type not in KNOWN_FIELD_TYPES:
            err(f"поле '{name}': невідомий type '{field_type}' (відомі: "
                f"{sorted(KNOWN_FIELD_TYPES)}) -- отримає ту саму вільну "
                "схему, що й text (schema_grammar._field_json_schema), тож "
                "LLM зможе повернути що завгодно замість enum-коду")

        part = field.get("part")
        if part is not None and part not in NAME_PART_ROLES:
            err(f"поле '{name}': part '{part}' невідома -- допустимі лише "
                f"{list(NAME_PART_ROLES)} (до кожної прив'язана морфологічна "
                "граммема, тому перелік закритий навмисно)")

        criticality = field.get("criticality")
        if criticality not in (None, "critical", "optional"):
            err(f"поле '{name}': criticality '{criticality}' -- допустимо "
                "лише critical або optional")

        # Опечатка в `consistency:` не має означати "перевірки немає" -- це
        # рівно той клас тихої помилки, проти якого сама перевірка й стоїть.
        rule = field.get("consistency")
        if rule is not None:
            if not isinstance(rule, dict) or not rule.get("rule"):
                err(f"поле '{name}': consistency без ключа 'rule'")
            elif rule["rule"] not in CONSISTENCY_RULES:
                err(f"поле '{name}': consistency.rule '{rule['rule']}' не "
                    f"реалізовано (відомі: {sorted(CONSISTENCY_RULES)}) -- "
                    "перевірка узгодженості мовчки не виконувалась би")
            else:
                for ref in CONSISTENCY_RULES[rule["rule"]][0]:
                    target = rule.get(ref)
                    if not target:
                        err(f"поле '{name}': consistency '{rule['rule']}' "
                            f"вимагає посилання '{ref}'")
                    elif target not in {f.get("name") for f in schema.get("fields") or []}:
                        err(f"поле '{name}': consistency.{ref} посилається на "
                            f"поле '{target}', якого в схемі немає")

        link_type = field.get("link_type")
        if link_type is not None and link_type not in KNOWN_LINK_TYPES:
            err(f"поле '{name}': link_type '{link_type}' невідомий (відомі: "
                f"{sorted(KNOWN_LINK_TYPES)}) -- зв'язок мовчки не потрапив би "
                "у record['document_links']")

        if field.get("llm_fallback") not in (None, True, False):
            err(f"поле '{name}': llm_fallback мусить бути true або false")

        for key in (PLACEHOLDER_TOKENS_KEY, PLACEHOLDER_TOKENS_EXCEPT_KEY):
            value = field.get(key)
            if value is not None and not isinstance(value, list):
                err(f"поле '{name}': {key} мусить бути списком рядків")

        if field.get("type") == "category" and not field.get("category"):
            err(f"поле '{name}': type: category без ключа category")

        mode = field.get("extraction")
        if field.get("priority") == "deferred" or mode is None:
            continue
        if mode not in EXTRACTION_REQUIRED_KEYS:
            err(f"поле '{name}': невідомий режим extraction '{mode}' "
                f"(відомі: {sorted(EXTRACTION_REQUIRED_KEYS)})")
            continue
        for key in EXTRACTION_REQUIRED_KEYS[mode]:
            if not field.get(key):
                err(f"поле '{name}': режим '{mode}' вимагає ключ '{key}'")
        if mode == "derived_from":
            derive_name = field.get("derive")
            if derive_name and derive_name not in DERIVE_FUNCS:
                err(f"поле '{name}': derive '{derive_name}' не реалізовано "
                    f"(відомі: {sorted(DERIVE_FUNCS)}) -- інакше KeyError "
                    "посеред обробки документа, коли значення справді "
                    "знадобиться")
        for variant in field.get("regex_variants") or []:
            pattern = (variant or {}).get("pattern")
            if not pattern:
                err(f"поле '{name}': regex_variants містить запис без pattern")
                continue
            try:
                re.compile(pattern)
            except re.error as exc:
                err(f"поле '{name}': невалідний regex ({exc})")

    # Груповий режим: лейбл рівно один на ГРУПУ (особу), не на поле.
    for mode in GROUP_MODES:
        members = [f for f in (schema.get("fields") or []) if f.get("extraction") == mode]
        by_group = {}
        for f in members:
            by_group.setdefault(name_group_key(f), []).append(f)
        for group, fields_in_group in by_group.items():
            labeled = [f.get("name") for f in fields_in_group if f.get("label_before")]
            if not labeled:
                err(f"режим '{mode}', група '{group}': жодне з полів "
                    f"{[f.get('name') for f in fields_in_group]} не має "
                    "label_before -- рядок для розбору неможливо знайти")
            elif len(labeled) > 1:
                # РІВНО один: resolve_name_groups (extract.py) бере лейбл із
                # ПЕРШОГО поля групи, що його має (`next(...)`) -- друге
                # оголошення тихо ігнорується, а не помічається як помилка,
                # тож копіпаст-помилка (label_before скопійований на два
                # поля однієї групи) досі проходила без жодного сигналу.
                err(f"режим '{mode}', група '{group}': label_before "
                    f"оголошено на кількох полях {labeled} -- має бути рівно "
                    "одне; resolve_name_groups мовчки бере перше за списком")
            parts = [field_part(f) for f in fields_in_group]
            duplicated = {p for p in parts if p in NAME_PART_ROLES and parts.count(p) > 1}
            if duplicated:
                err(f"режим '{mode}', група '{group}': роль(і) {sorted(duplicated)} "
                    "оголошені двічі в одній групі -- друге поле перетерло б "
                    "перше; для другої особи потрібен окремий `group:`")

    if "fact_value" not in targets:
        warn("жодне поле не має db_target: fact_value -- основний факт піде в "
             "БД зі значенням null")
    for target, names in single_value_targets_seen.items():
        if len(names) > 1:
            err(f"db_target '{target}' оголошено на кількох полях {names} -- "
                "build_record тримає ОДНУ змінну на цей таргет, кожне "
                "наступне поле мовчки перезаписує попереднє")
    return problems


def validate_schema_set(schemas: list) -> list:
    """Перевірки, що стосуються НАБОРУ схем разом, а не однієї окремо --
    validate_schema() бачить лише один YAML за раз і не може помітити, що
    ДВІ схеми оголосили однаковий template. identify_template ключує обидва
    scores і by_template за template (identification.py) -- за колізії одна
    з двох схем мовчки випадає з підрахунку без жодної помилки, і документ
    цього типу могли б розпізнавати за ГІРШОЮ (чи взагалі не тою) схемою."""
    problems = []
    seen = {}
    for schema in schemas:
        template = schema.get("template")
        path = schema.get("_path", "?")
        if template in seen:
            problems.append(("warning",
                f"{path}: template '{template}' збігається з {seen[template]} "
                "-- одна зі схем мовчки випадає з ідентифікації (обидві "
                "ключуються за template); перейменуйте одну з них"))
        else:
            seen[template] = path
    return problems


def _spread_schema_defaults(schema: dict) -> None:
    """Розкладає ключі, оголошені на рівні СХЕМИ, по її полях -- один раз, при
    завантаженні. Потрібно тому, що normalize_field бачить лише field_def і не
    має доступу до схеми, а перелік токенів-заповнювачів природно оголошувати
    на бланк цілком ("у книзі обліку техніки «немає» -- значення"), а не
    переписувати в кожне поле. Ключ, оголошений на самому полі, важить більше
    й не перетирається."""
    for key in (PLACEHOLDER_TOKENS_KEY, PLACEHOLDER_TOKENS_EXCEPT_KEY):
        if key not in schema:
            continue
        for field in schema.get("fields") or []:
            field.setdefault(key, schema[key])


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
            _spread_schema_defaults(content)
            schemas.append(content)
    return schemas


def schema_title_phrases(schema: dict) -> list:
    return list(schema.get("identification", {}).get("title", []))


def score_schema(text: str, schema: dict) -> int:
    """phrase_in_text, а не `p in low`: підрядковий збіг без межі слова давав
    бал чужій схемі, коли короткий анкор випадково траплявся всередині
    іншого слова або в цитаті документа іншого типу."""
    low = (text or "").lower()
    ident = schema.get("identification") or {}
    title_hits = sum(1 for p in ident.get("title", []) if phrase_in_text(low, p))
    anchor_hits = sum(1 for p in ident.get("anchors", []) if phrase_in_text(low, p))
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
        ident = by_template[best_template].get("identification") or {}
        min_score = ident.get("min_score", DEFAULT_MIN_SCORE)
        # Строга нерівність: рівний бал двох шаблонів -- це неоднозначність,
        # а не перемога того, хто випадково перший у списку.
        if best_score >= min_score and best_score > runner_up_score:
            schema = by_template[best_template]
            return {
                "schema": schema, "template": best_template, "domain": schema.get("domain"),
                "source": "anchors", "score": best_score, "runner_up": runner_up_score,
                "scores": scores, "reason": None,
            }

    # Скільки схем упевнено збіглося. Документ, що містить ДЕКІЛЬКА бланків --
    # це майже завжди не бланк, а джерело, яке їх у собі несе: Інструкція з
    # діловодства має і Додаток 28, і Додаток 30 з їхніми заголовками, тому
    # чесно набирає високий бал за обома схемами. Тобто чим БІЛЬШЕ схем
    # збіглося, тим МЕНШ імовірно, що це якась одна з них.
    # Живе репро від команди БД: інструкція_діловодство.docx, 402898 символів,
    # бал 9 за deployment_certificate і 9 за leave_ticket -- і на LLM це
    # виходило як упевнено визначене "посвідчення про відрядження".
    strong_matches = [t for t, sc in scores.items()
                      if sc >= ((by_template[t].get("identification") or {})
                                .get("min_score", DEFAULT_MIN_SCORE))]

    llm_error = None
    llm_floor = DEFAULT_LLM_FLOOR
    if best_template is not None:
        llm_floor = ((by_template[best_template].get("identification") or {})
                     .get("llm_floor", DEFAULT_LLM_FLOOR))
    # Дві причини НЕ питати модель, обидві -- позитивне свідчення, а не
    # відсутність свідчень:
    #   1. нічия за балом: рівний бал двох бланків означає, що жоден із них не
    #      підтверджений, а не що треба вибрати одного;
    #   2. декілька впевнених збігів: див. strong_matches вище.
    # Додатковий аргумент саме для цього класу документів: інструкція має
    # 400k символів, а в модель іде _trim до max_context_chars (6000) --
    # 60% початку й 40% кінця. Вона фізично не бачить документа, про який
    # її питають, тож її відповідь тут не може бути кращою за здогадку.
    ambiguous_tie = bool(best_score) and best_score == runner_up_score
    container_like = len(strong_matches) > 1
    if llm_choose is not None and schemas and best_score >= llm_floor \
            and not ambiguous_tie and not container_like:
        options = "\n".join(
            f"- {s['template']}: " + ((s.get("identification") or {}).get("description")
                                       or s.get("domain", ""))
            for s in schemas
        )
        prompt = (
            "Визнач, який це бланк документа, з переліку нижче, або 'unknown', "
            f"якщо жоден не підходить.\n\n{options}\n\nТекст документа:\n{text}"
        )
        try:
            answer = llm_choose(prompt, [s["template"] for s in schemas] + ["unknown"]).strip()
        except Exception as exc:
            # Збій LLM на ідентифікації не має валити прогін: документ
            # деградує в unresolved (з причиною), а решта батчу обробляється.
            # Без цього try/except будь-яка помилка моделі тут валила все.
            answer, llm_error = None, f"llm_error:{type(exc).__name__}"
        if answer in by_template:
            schema = by_template[answer]
            return {
                "schema": schema, "template": answer, "domain": schema.get("domain"),
                "source": "llm", "score": scores.get(answer, 0), "runner_up": runner_up_score,
                "scores": scores, "reason": None,
            }

    if llm_error:
        reason = llm_error
    elif container_like:
        # Окрема причина, бо дія людини інша: не "напиши схему для цього
        # бланка", а "це не бланк -- це джерело з додатками".
        reason = f"multiple_templates_matched:{','.join(sorted(strong_matches))}"
    elif ambiguous_tie:
        reason = "ambiguous"
    elif best_score < llm_floor:
        # Окрема причина, а не загальний no_template_match: у черзі рев'ю це
        # означає "жодного сигналу, потрібна нова схема", а не "схема є, але
        # не набрала порога".
        reason = "below_llm_floor"
    else:
        reason = "no_template_match"
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
