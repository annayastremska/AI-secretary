# -*- coding: utf-8 -*-
"""Визначення ВИДУ СУБ'ЄКТА документа (`subject_kind`) -- про кого/про що
документ узагалі: людина, техніка, підрозділ, задача -- чи ні про кого.

Окремий модуль, а не частина identification.py, з двох причин:
  1. `identification.py` уже імпортує `build_record`, тому будь-яке звернення
     звідти назад дало б цикл імпортів; тут же немає жодного імпорту з
     pipeline/, і модуль можна читати з обох боків (валідатор схем + run.py);
  2. вид суб'єкта -- це не той самий висновок, що ШАБЛОН: шаблон визначається
     текстом документа, а вид суб'єкта -- ОГОЛОШЕННЯМ (схеми або довідника).

Порядок розв'язання (узгоджено, docs/architecture/extraction-pipeline-prototype.md,
розділ «Визначення суб'єкта документа»), від найнадійнішого до найслабшого:

    є схема               -> subject_kind зі схеми (ОГОЛОШЕННЯ, не висновок)
    немає схеми:
      домен визначений    -> мапінг «домен -> вид» з YAML; значення може
                             бути "none" (суб'єкта в документі НЕМА)
      домен не визначений -> LLM, ЗАКРИТИЙ enum + обов'язковий "unknown"
    вид none / unknown    -> об'єкт у БД НЕ створюється, документ у чергу рев'ю

**Чому схема вище домену.** Схема прямо оголошує, що описує. Домен --
результат підрахунку ключових фраз (classification/classify.py), який може з
оголошенням не збігтися; виводити вид із домену за наявної схеми означає
додати індирекцію, здатну суперечити оголошенню.

**Чому потрібне значення "немає", а не лише person/equipment.** Домен не
тотожний виду суб'єкта, і в репозиторії вже є документ, на якому мапінг без
"none" дав би впевнено неправильну відповідь: Інструкція з діловодства
(домен `normative`, `kind: procedural`) суб'єкта не має взагалі, а мапінг
без "none" мусив би вигадати їй якийсь вид. Окремо `staffing`: вид -- людина,
але БАГАТО людей; вид суб'єкта й кількість суб'єктів -- різні питання, і
мапінг відповідає лише на перше.

**Чому відповідь LLM мусить бути закритим переліком.** Той самий аргумент, що
для `fact_type`: вільна відповідь створює види, яких у `object_kinds` немає, і
`objects.kind_id` (NOT NULL) отримує сміття. Плюс обов'язковий "unknown" --
інакше модель, обмежена лише реальними видами, змушена вибрати щось навіть
коли суб'єкта в документі немає.
"""

# Види, які існують як рядки в `object_kinds` на боці БД
# (docs/contracts/2026-08-11_database-handoff.md, розд. 4 п.16): `person` / `equipment` / `task`
# були від початку, `unit` команда БД додала на нашу пропозицію (рішення
# 13.08.2026) -- саме він потрібен для `registry: military_unit`
# (`destination_org`, `unit_to_report`), яке досі йшло текстом у `facts.value`.
# Перелік ЗАКРИТИЙ навмисно, як NAME_PART_ROLES і KNOWN_DB_TARGETS: кожен
# елемент відповідає рядку в чужій таблиці, тому вигадати новий у YAML не
# можна -- `objects.kind_id` просто не зіставиться.
KNOWN_SUBJECT_KINDS = ("person", "equipment", "task", "unit")

# Суб'єкта в документі НЕМА -- і це ВІДОМО, а не невизначено. Нормативна
# інструкція, методичні рекомендації: правила, за якими складаються інші
# документи. Оголошується у схемі або в мапінгу домену.
NO_SUBJECT = "none"

# Виду визначити НЕ ВДАЛОСЬ. Відрізняється від NO_SUBJECT так само, як
# `below_llm_floor` відрізняється від `no_template_match` в identify_template:
# перше -- відповідь, друге -- її відсутність. Різна дія людини: "none"
# нічого не потребує, "unknown" потребує рев'ю.
UNKNOWN_SUBJECT = "unknown"

# Що можна ОГОЛОСИТИ (у схемі або в мапінгу домену). "unknown" сюди свідомо
# не входить: оголосити "я не знаю" -- це не оголошення, це відсутність
# рядка в YAML, і воно вже має власне представлення.
DECLARABLE_SUBJECT_KINDS = tuple(KNOWN_SUBJECT_KINDS) + (NO_SUBJECT,)

# Закритий перелік для grammar-обмеженого вибору LLM. "unknown" ОБОВ'ЯЗКОВО
# останнім елементом і обов'язково присутній -- див. докстрінг модуля.
LLM_SUBJECT_CHOICES = tuple(DECLARABLE_SUBJECT_KINDS) + (UNKNOWN_SUBJECT,)


def creates_object(subject_kind) -> bool:
    """Чи має завантажувач БД створювати об'єкт у реєстрі (`objects`).

    False для `none` (суб'єкта немає), `unknown` (не визначили) і None (до
    питання взагалі не дійшли -- дублікат, нечитабельний файл).

    Це НЕ дублікат `subject.person_complete` (run.py:_person_identity), а
    інша вісь, і плутати їх не можна:
      - `subject_kind`     -- ЧИ ТРЕБА об'єкт і В ЯКОМУ реєстрі;
      - `person_complete`  -- чи вставиться конкретний рядок `people`
                              (їхні `last_name`/`first_name` -- NOT NULL).
    Тому це не кон'юнкція: у техніки прізвища немає за визначенням, і
    `person_complete: false` для неї -- норма, а не перешкода створенню
    об'єкта. Завантажувач мусить перевіряти обидва ключі окремо.

    ЗАМІРЯНИЙ КРАЙНІЙ ВИПАДОК (рев'ю 22.08.2026, C-10). ПОРОЖНІЙ бланк
    (`відпускний_шаблон.docx`) виходить `create_subject_object: true` при
    повністю порожньому `subject` (усі частини ПІБ null,
    `person_complete: false`). Це послідовно з написаним вище -- ключ
    відповідає на питання «якого ВИДУ суб'єкт у цього шаблону», а не «чи є
    він у цьому файлі», -- але читається як інструкція «створи об'єкт», і саме
    тому тут стоїть цей абзац. Шляху в базу такий запис не має (status
    `needs_review`), тож фантомний об'єкт із порожнього бланка не з'явиться.
    Перейменувати ключ (`subject_kind_creates_object`) було б чесніше, але це
    ЗМІНА КОНТРАКТУ: завантажувач читає саме `create_subject_object`
    (ai_secretary_loader.py) -- див. «потребує рішення» в
    docs/review-2026-08-22/fixes-pipeline.md.
    """
    return subject_kind in KNOWN_SUBJECT_KINDS


def domain_subject_kind_problems(domains: dict) -> list:
    """Перевірка МАПІНГУ «домен -> вид» у dictionaries/domain_keyphrases.yaml.
    Повертає [(severity, message)], як validate_schema -- не кидає.

    Навіщо: невідоме значення в цьому мапінгу інакше проявилось би тихо. Воно
    не породжує помилки ні на завантаженні (це просто рядок у YAML), ні на
    обробці (вид пішов би у вихід як є) -- і сміттєвий вид дійшов би до
    `objects.kind_id`, який NOT NULL і зіставляється з чужою таблицею. Це
    рівно той клас помилки, проти якого стоїть валідатор схем.

    Відсутність мапінгу -- ПОПЕРЕДЖЕННЯ, не помилка: домен без оголошеного
    виду дає `unknown` (тобто рев'ю), а не сміття в базі. Але сказати про це
    треба на завантаженні, а не через тиждень у черзі рев'ю.
    """
    problems = []
    for domain, spec in (domains or {}).items():
        spec = spec or {}
        if "subject_kind" not in spec:
            problems.append(("warning",
                f"домен '{domain}': немає subject_kind -- документ цього домену "
                f"без схеми отримає вид '{UNKNOWN_SUBJECT}' і піде в чергу рев'ю "
                f"(допустимі: {list(DECLARABLE_SUBJECT_KINDS)})"))
            continue
        value = spec["subject_kind"]
        if value not in DECLARABLE_SUBJECT_KINDS:
            problems.append(("error",
                f"домен '{domain}': невідомий subject_kind '{value}' (допустимі: "
                f"{list(DECLARABLE_SUBJECT_KINDS)}) -- вид мовчки пішов би у вихід "
                "і далі в objects.kind_id, якого в object_kinds немає"))
    return problems


def _sanitized(value, source, bad_reason):
    """Оголошене значення поза закритим переліком НЕ проходить у вихід.
    Валідатори (validate_schema / domain_subject_kind_problems) ловлять це
    раніше й голосніше, але resolve_subject_kind мусить бути безпечним і
    окремо: він викликається й на схемах, завантажених в обхід валідації
    (тести, --force-template), а ціна пропуску -- сміття в NOT NULL-колонці
    чужої таблиці."""
    if value in DECLARABLE_SUBJECT_KINDS:
        return {"kind": value, "source": source, "reason": None}
    return {"kind": UNKNOWN_SUBJECT, "source": source,
            "reason": f"{bad_reason}:{value}"}


def resolve_subject_kind(schema=None, domain=None, domains=None,
                         llm_choose=None, text=None) -> dict:
    """Повертає {"kind", "source", "reason"}; ніколи не кидає виняток.

      kind    -- один із KNOWN_SUBJECT_KINDS | NO_SUBJECT | UNKNOWN_SUBJECT
      source  -- schema | domain_map | llm | None
      reason  -- чому НЕ вийшло краще (None, якщо вид оголошено)

    `llm_choose(prompt, choices) -> str` -- grammar-обмежений вибір рівно
    одного з `choices`, той самий контракт, що в identify_template. Це ТОЧКА
    РОЗШИРЕННЯ: за замовчуванням None, тобто модель не викликається взагалі
    (див. run.py: підключення прикріплене до окремого прапорця конфігу
    `llm.subject_kind`, вимкненого за замовчуванням).
    """
    # --- Рівень 1: схема. ОГОЛОШЕННЯ, а не висновок. --------------------
    if schema is not None:
        declared = schema.get("subject_kind")
        if declared is not None:
            return _sanitized(declared, "schema", "invalid_subject_kind_in_schema")
        # Схема є, але виду не оголосила. Не помилка (валідатор дає
        # попередження), тому падаємо на наступний рівень: `domain:` схема
        # оголошує завжди, і мапінг домену -- краще, ніж нічого.
        domain = domain or schema.get("domain")

    # --- Рівень 2: мапінг «домен -> вид» з YAML, не з коду. -------------
    if domain:
        spec = (domains or {}).get(domain)
        if spec is None:
            # Домен визначено, але його опису в довіднику немає. Реально
            # можливо лише при --force-template зі схемою, чий `domain:` не
            # заведений у domain_keyphrases.yaml.
            return {"kind": UNKNOWN_SUBJECT, "source": None,
                    "reason": f"domain_not_in_dictionary:{domain}"}
        if "subject_kind" in (spec or {}):
            return _sanitized((spec or {})["subject_kind"], "domain_map",
                              "invalid_subject_kind_in_dictionary")
        # Домен визначено, мапінгу для нього НЕМА. LLM тут свідомо НЕ
        # питають: домен уже відомий, тобто на питання мусить відповідати
        # рядок у YAML, а відповідь моделі лише замаскувала б прогалину в
        # довіднику -- і замаскувала б тихо, бо вид виглядав би визначеним.
        return {"kind": UNKNOWN_SUBJECT, "source": None,
                "reason": f"domain_without_subject_kind:{domain}"}

    # --- Рівень 3: LLM. Закритий enum + обов'язковий "unknown". ---------
    if llm_choose is not None and text:
        prompt = build_subject_kind_prompt(text)
        try:
            answer = llm_choose(prompt, list(LLM_SUBJECT_CHOICES)).strip()
        except Exception as exc:
            # Той самий принцип, що в identify_template: збій моделі не валить
            # прогін, документ деградує до "unknown" із причиною.
            return {"kind": UNKNOWN_SUBJECT, "source": None,
                    "reason": f"llm_error:{type(exc).__name__}"}
        return _sanitized(answer, "llm", "invalid_subject_kind_from_llm")

    return {"kind": UNKNOWN_SUBJECT, "source": None,
            "reason": "no_schema_no_domain"}


# Описи видів для промпту -- у КОДІ поруч із закритим переліком, а не в
# окремому YAML: перелік і його пояснення мусять змінюватись одним рухом,
# інакше модель отримує опис виду, якого в enum уже немає (або навпаки).
_SUBJECT_KIND_DESCRIPTIONS = {
    "person": "конкретна особа (військовослужбовець, працівник)",
    "equipment": "одиниця озброєння, техніки, транспортний засіб",
    "task": "завдання, робота, доручення",
    "unit": "військова частина, підрозділ, орган",
    NO_SUBJECT: ("суб'єкта немає: документ не про когось конкретного, а "
                 "правила/інструкція/нормативний текст"),
    UNKNOWN_SUBJECT: "визначити неможливо",
}


def build_subject_kind_prompt(text: str) -> str:
    """Промпт рівня 3. Виділений окремо, щоб його можна було прочитати й
    перевірити без запуску моделі."""
    options = "\n".join(f"- {kind}: {_SUBJECT_KIND_DESCRIPTIONS[kind]}"
                        for kind in LLM_SUBJECT_CHOICES)
    return (
        "Визнач ВИД СУБ'ЄКТА документа -- про кого або про що цей документ "
        "робить запис. Обери рівно один варіант з переліку нижче.\n\n"
        f"{options}\n\nТекст документа:\n{text}"
    )
