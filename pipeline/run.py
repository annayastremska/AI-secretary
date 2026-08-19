"""Оркестрація одного прогону: файл -> запис. Раніше цю роль виконували
клітинки ноутбука; тепер це звичайний модуль, який однаково викликається
з CLI, з тестів і (у майбутньому) з веб-бекенда.

Порядок кроків і гарантії:
1. хеш файлу -> якщо такий вміст уже оброблено, це ДУБЛІКАТ (не другий факт);
2. інжест (docx напряму / зображення через OCR);
3. визначення ШАБЛОНУ (не лише домену) -> сама схема, а не людина в діалозі;
4. якщо шаблон не визначено -> unresolved-запис у чергу, без спроби
   екстракції чужою схемою; вихід існує завжди;
5. екстракція (детермінований прохід + LLM-групи для прогалин);
6. збірка запису + провенанс + 5%-вибірка на аудит;
7. збереження результату в локальне сховище (data/output).
"""
import datetime
import glob
import os
import shutil
import uuid

import yaml

from pipeline.build_record import build_record
from pipeline.classification.classify import load_domain_keyphrases
from pipeline.extraction.extract import extract_document
from pipeline.identification import (
    blank_edition_verdict,
    identify_template,
    load_schemas,
    missing_dictionaries,
    schema_title_phrases,
    validate_schema,
    validate_schema_set,
)
from pipeline.ingestion.ingest import (
    DOCX_EXTS,
    IMAGE_EXTS,
    PDF_EXTS,
    file_sha256,
    load_document_blocks,
)
from pipeline.normalization.normalize import build_alias_lookup
from pipeline.storage.local_store import LocalDocumentStore
from pipeline.subject_kind import (
    UNKNOWN_SUBJECT,
    creates_object,
    domain_subject_kind_problems,
    resolve_subject_kind,
)

SUPPORTED_EXTS = DOCX_EXTS + PDF_EXTS + IMAGE_EXTS


def load_dictionaries(dictionaries_dir: str) -> dict:
    """Довідники визначаються за ВМІСТОМ (category + values), не за назвою
    файлу: назва "military_rank.yaml" не містить жодного маркера типу, а
    порядок glob не гарантовано алфавітний."""
    dictionaries = {}
    for path in sorted(glob.glob(os.path.join(dictionaries_dir, "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            content = yaml.safe_load(f)
        if isinstance(content, dict) and "category" in content and "values" in content:
            dictionaries[content["category"]] = build_alias_lookup(content)
    return dictionaries


def load_fact_types(dictionaries_dir: str) -> dict:
    """{code: {label, validity_model}} з fact_type_registry.yaml.

    Раніше цей файл не завантажувався ЖОДНИМ рядком коду: load_dictionaries
    вимагає ключі category + values, а в реєстрі верхній ключ -- fact_types.
    Тому validity_model (ranged / current_state / permanent_event) ніде не
    читався, і схема могла оголосити fact_type: vacation замість leave без
    жодної помилки -- факт просто ніколи не потрапив би в підрахунок, який
    шукає код leave.
    """
    path = os.path.join(dictionaries_dir, "fact_type_registry.yaml")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        content = yaml.safe_load(f) or {}
    return {entry["code"]: entry
            for entry in (content.get("fact_types") or [])
            if isinstance(entry, dict) and entry.get("code")}


def build_resources(cfg: dict, force_no_llm=False) -> dict:
    """Важкі об'єкти (схеми, довідники, модель, OCR) створюються ОДИН раз на
    процес, а не на кожен файл -- інакше пакетна обробка папки перечитувала б
    ваги моделі для кожного документа."""
    paths = cfg["paths"]
    res = {
        "schemas": load_schemas(paths["schemas_dir"]),
        "dictionaries": load_dictionaries(paths["dictionaries_dir"]),
        "fact_types": load_fact_types(paths["dictionaries_dir"]),
        "domains": None,
        "llm": None,
        "ocr": None,
        "store": None,
        "warnings": [],
    }

    # Валідація схем ДО обробки першого документа: помилка в YAML інакше
    # проявляється або тихим no_value, або KeyError посеред process_file, коли
    # _persist ще не викликався -- документ не отримує ні запису у сховище, ні
    # рядка в індексі, хоч файл уже перенесений у data/failed.
    # Дублікат template між ДВОМА схемами -- per-schema validate_schema
    # нижче не бачить: кожен виклик знає лише про одну схему.
    for severity, message in validate_schema_set(res["schemas"]):
        res["warnings"].append(f"схема: {message}")

    invalid = set()
    for schema in res["schemas"]:
        for severity, message in validate_schema(schema, res["fact_types"]):
            res["warnings"].append(f"схема: {message}")
            if severity == "error":
                invalid.add(schema["template"])
    if invalid:
        # Невалідна схема виключається, а не валить прогін: решта шаблонів
        # мусить обробитись. Але документ її типу тепер піде в unresolved --
        # це видно у попередженні, а не тихо.
        res["schemas"] = [s for s in res["schemas"] if s["template"] not in invalid]
        res["warnings"].append(
            f"схеми виключені через помилки: {sorted(invalid)} -- документи цих "
            "типів підуть у unresolved, доки YAML не виправлено")

    keyphrases_path = os.path.join(paths["dictionaries_dir"], "domain_keyphrases.yaml")
    if os.path.exists(keyphrases_path):
        res["domains"] = load_domain_keyphrases(keyphrases_path)
        # Мапінг «домен -> вид суб'єкта» перевіряється ТУТ, разом зі схемами, і
        # з тієї самої причини: невідоме значення в YAML інакше не проявляється
        # ніде на нашому боці -- вид пройшов би у вихід рядком і осів у
        # `objects.kind_id` (NOT NULL, зіставляється з чужою таблицею).
        # На відміну від схеми, домен виключити з набору неможливо (він не
        # об'єкт, а рядок довідника), тому сміттєве значення відсікається на
        # виході: resolve_subject_kind повертає замість нього 'unknown'.
        for severity, message in domain_subject_kind_problems(res["domains"]):
            res["warnings"].append(f"довідник домену: {message}")

    llm_cfg = cfg["llm"]
    if llm_cfg.get("enabled") and not force_no_llm:
        # Перевіряємо наявність пакета ДО створення клієнта: сам llama_cpp
        # імпортується ліниво (лише при першій генерації), тому без цієї
        # перевірки відсутній пакет проявився б аж на етапі екстракції --
        # кожне LLM-поле окремо отримало б криптичний llm_error:
        # ModuleNotFoundError замість одного зрозумілого попередження.
        import importlib.util
        if importlib.util.find_spec("llama_cpp") is None:
            res["warnings"].append(
                "llm.enabled: true, але пакет llama-cpp-python не встановлено "
                "(pip install llama-cpp-python) -- працюємо без LLM")
            llm_cfg = dict(llm_cfg, enabled=False)
    if llm_cfg.get("enabled") and not force_no_llm:
        from pipeline.llm.client import LlamaClient, load_system_prompt
        try:
            res["llm"] = LlamaClient(
                model_path=llm_cfg.get("model_path"),
                n_ctx=llm_cfg.get("n_ctx", 4096),
                n_gpu_layers=llm_cfg.get("n_gpu_layers", 0),
                n_threads=llm_cfg.get("n_threads"),
                chat_format=llm_cfg.get("chat_format", "gemma"),
                system_prompt=load_system_prompt(paths.get("llm_context")),
                max_context_chars=llm_cfg.get("max_context_chars", 6000),
                # >1 семпл має сенс лише при temperature > 0, інакше всі
                # семпли однакові й голосування нічого не дає
                temperature=0.0 if llm_cfg.get("self_consistency_n", 1) <= 1 else 0.7,
                verbose=llm_cfg.get("verbose", False),
            )
        except Exception as exc:
            # Відсутня модель не має валити прогін: детермінований прохід
            # усе одно дасть результат, а прогалини будуть чесно позначені.
            res["warnings"].append(f"LLM недоступна ({type(exc).__name__}: {exc}) -- працюємо без неї")

    if cfg["ocr"].get("engine") == "surya":
        from pipeline.ocr.surya_reader import make_surya_reader
        try:
            # УСІ чотири ключі ocr.* з конфіга, не два: n_gpu_layers і
            # hub_offline були оголошені в pipeline/config.py (з коментарями,
            # що описують їхню дію), але сюди не передавались -- тобто
            # `ocr.n_gpu_layers: 0` у конфізі мовчки не діяв, і внутрішній
            # llama-server Surya все одно йшов на Vulkan (99 шарів), де на
            # цій машині вмирає з vk::ErrorDeviceLost
            # (docs/research/2026-08-14_ocr-ngl0-control-run.md).
            res["ocr"] = make_surya_reader(cfg["ocr"].get("llama_server_path"),
                                           cfg["ocr"].get("inference_parallel"),
                                           n_gpu_layers=cfg["ocr"].get("n_gpu_layers"),
                                           hub_offline=cfg["ocr"].get("hub_offline", False),
                                           cache_ram_mb=cfg["ocr"].get("cache_ram_mb"),
                                           max_tokens_full_page=cfg["ocr"].get("max_tokens_full_page"),
                                           guided_layout=cfg["ocr"].get("guided_layout"),
                                           recognition_max_retries=cfg["ocr"].get("recognition_max_retries"))
        except Exception as exc:
            res["warnings"].append(f"OCR недоступний ({type(exc).__name__}: {exc}) -- зображення не обробляться")

    res["store"] = LocalDocumentStore(cfg["storage"]["local_root"])
    return res


def blank_meta(**overrides) -> dict:
    """Єдина форма запису для ВСІХ статусів.

    Раніше форм було шість: у duplicate не було ні template, ні facts; у
    записі про необроблену помилку пакетного прогону -- ні file_hash, ні
    subject. Споживач читає meta["template"] і meta["facts"] напряму, тож
    перший же дублікат у папці валив завантаження KeyError. Відсутнє значення
    тепер None або порожня колекція, але КЛЮЧ є завжди.
    """
    meta = {
        "id": None,
        "status": None,
        "file_hash": None,
        "source_file": None,
        # electronic | photo -- те саме, що documents.source_kind у БД-споживача
        # (там CHECK на ці два значення). None лише коли інжест не відбувся.
        "source_kind": None,
        "uploaded_at": None,
        "domain": None,
        "template": None,
        # ВИД СУБ'ЄКТА документа -> object_kinds.code у БД-споживача.
        # person | equipment | task | unit -- створюємо об'єкт;
        # none    -- суб'єкта в документі НЕМА (нормативний документ);
        # unknown -- визначити не вдалося;
        # null    -- до питання не дійшли взагалі (дублікат, нечитабельний
        #            файл). null і "unknown" -- РІЗНІ речі: перше означає, що
        #            питання не ставилось, друге -- що ставилось і без відповіді.
        "subject_kind": None,
        # schema | domain_map | llm | null -- ЧИМ визначено вид. Та сама
        # цінність, що в `identification.source`: оголошення схеми й здогадка
        # моделі не однакові за надійністю, і в базі це має бути видно.
        "subject_kind_source": None,
        # Причина, чому вид не вийшло визначити надійніше (напр.
        # domain_without_subject_kind:staffing). null = вид оголошено.
        "subject_kind_reason": None,
        # ГЕЙТ: чи створювати об'єкт у реєстрі `objects`. false для
        # none/unknown/null. Окрема вісь від `subject.person_complete`
        # (див. pipeline/subject_kind.py:creates_object) -- завантажувач
        # мусить перевіряти обидва ключі, а не один.
        "create_subject_object": False,
        "identification": None,
        "storage_key": None,
        "reason": None,
        "review_reason": None,
        # queue_type для review_queue у БД-споживача: unknown_type |
        # unconfirmed_fact | handwritten | qa_sample. None = у чергу не треба.
        "review_queue": None,
        "subject": {},
        "facts": [],
        "field_provenance": {},
        "unknown_fields": [],
        "unknown_critical_fields": [],
        "confirmed_empty_fields": [],
        "not_implemented_fields": [],
        "date_range_error": None,
        # {поле: причина} -- значення суперечить іншим полям документа або не
        # має чим підтвердитись (тривалість без дат). Ключ є завжди, як і
        # решта: споживач читає його напряму.
        "consistency_problems": {},
        # Ознака, що ЦЕЙ документ скасовує/змінює інший (див.
        # build_record["document_links"]). Порожній список -- норма.
        "document_links": [],
        # Ключ ПОПЕРЕДНЬОГО запису того самого вмісту, який цей запис
        # замінив при --reprocess (R-A1-04). None = першопрохід. Сам старий
        # запис при цьому переїжджає в superseded/ (store.retire).
        "supersedes_storage_key": None,
        # Сирий текст полів, значення яких у документі Є, але не
        # зіставилось із довідником (напр. звання поза словником).
        # Вихід OCR як ФАКТ у кожному записі: скільки блоків і символів
        # повернуло розпізнавання. Без цього деградацію OCR неможливо
        # відрізнити від "документ невідомого типу" -- обидва давали
        # unresolved без жодної різниці (заміряно: 7 з 16 фото, п. 2.18).
        "ocr_blocks": None,
        "ocr_chars": None,
        "unresolved_values": {},
        "warnings": [],
    }
    meta.update(overrides)
    return meta


def _subject_kind_llm(res: dict, cfg: dict):
    """ТОЧКА РОЗШИРЕННЯ під рівень 3 визначення виду суб'єкта (LLM із закритим
    enum). Повертає `llm.choose` або None.

    Прикріплена до ОКРЕМОГО прапорця `llm.subject_kind`, вимкненого за
    замовчуванням (pipeline/config.py), а не до загального `llm.enabled` --
    свідомо. Причина: рівень 3 запускається саме на документах, для яких немає
    ні схеми, ні впізнаного домену, тобто на найдовших і найчужіших текстах
    батчу (Інструкція з діловодства -- 402898 символів), і кожен такий документ
    додає повний виклик моделі до прогону, який зараз обходиться без неї
    (посвідчення -- 0 викликів, відпускні -- 2 на 16). Увімкнути це має бути
    окремим свідомим рішенням із власним заміром, а не побічним наслідком
    `--llm`. Сам виклик у subject_kind.resolve_subject_kind уже реалізований і
    обмежений LLM_SUBJECT_CHOICES.
    """
    llm = res.get("llm")
    if llm is None or not cfg["llm"].get("subject_kind", False):
        return None
    return llm.choose


def _person_identity(subject: dict) -> dict:
    """Доповнює subject тим, що потрібно саме БД-споживачу:

    - person_alias: рядок для resolve_or_create_object / object_aliases; той
      самий порядок токенів, що й у канонічному імені, інакше та сама людина
      двічі створиться як два різні об'єкти й підрахунок роздується;
    - person_complete: чи є прізвище Й ім'я -- у них people.last_name і
      people.first_name оголошені NOT NULL, тож неповний ПІБ це не "запис із
      прогалиною", а падіння вставки. Краще сказати заздалегідь.
    """
    parts = [subject.get(k) for k in ("surname", "given_name", "patronymic")]
    present = [str(p).strip() for p in parts if p and str(p).strip()]
    subject = dict(subject)
    subject["person_alias"] = " ".join(present) if present else None
    subject["person_complete"] = bool(subject.get("surname") and subject.get("given_name"))
    return subject


def _review_queue_type(status: str, source_kind: str, audit_sampled: bool,
                       ocr_used: bool = False):
    """Одне значення queue_type, бо в них review_queue приймає рівно одне.
    Порядок = порядок пріоритету: непізнаний тип документа гірший за
    непідтверджений факт, а вибірка аудиту -- найслабша причина з трьох."""
    if status == "unresolved":
        return "unknown_type"
    if status == "needs_review":
        # handwritten окремим типом -- ЛИШЕ коли вміст справді читався з
        # пікселів (OCR), а не з текстового шару. Раніше вистачало самого
        # source_kind == "photo" без жодної перевірки вмісту (R-A1-10 +
        # R-A2-12): docx під іменем .pdf (порожня остання сторінка без
        # текстового шару -> scan_pages_detected -> photo, R-B1-05) потрапляв
        # би в чергу «рукописне», хоча кожне його значення прийшло з
        # born-digital тексту. OCR -- це не доказ рукопису, але це доказ, що
        # людині треба дивитись саме зображення; текстовий шар -- доказ
        # протилежного. Рукопис як такий пайплайн не розпізнає (свідомо,
        # docs/open-questions.md), тож handwritten тут означає «читалось
        # оком з картинки», як і погоджено в контракті з БД.
        return "handwritten" if (source_kind == "photo" and ocr_used) \
            else "unconfirmed_fact"
    return "qa_sample" if audit_sampled else None


def _sampled_for_audit(file_hash: str, sample_rate: int) -> bool:
    """Детерміністична 5%-вибірка від хешу, а не random: те саме рішення при
    повторному прогоні того самого документа (відтворюваність аудиту), і
    жодного стану генератора між запусками."""
    if not sample_rate or sample_rate <= 1:
        return bool(sample_rate)
    return int(file_hash[:8], 16) % sample_rate == 0


def _to_markdown(document_meta: dict, text: str) -> str:
    """YAML-шапка містить УСЕ витягнуте (subject, facts, provenance); у тілі
    -- лише розпізнаний текст, без дублювання полів окремим JSON-блоком."""
    return (
        "---\n"
        + yaml.safe_dump(document_meta, allow_unicode=True, sort_keys=False)
        + "---\n\n## Розпізнаний текст\n\n"
        + (text or "")
        + "\n"
    )


def process_file(path: str, res: dict, cfg: dict, force_template=None,
                 reprocess=False) -> dict:
    """Повертає document_meta. Ніколи не кидає виняток через вміст документа
    -- лише через непрацездатне середовище (немає прав на запис тощо).

    reprocess=True вимикає перевірку дедуплікації: документ, який раніше пішов
    в unresolved (немає схеми) чи needs_review, інакше назавжди повертав
    "duplicate", бо його хеш уже в індексі -- і після додавання схеми
    перепрогнати його було неможливо без ручного чищення індексу."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    file_hash = file_sha256(path)
    store = res["store"]

    existing_key = store.find_by_hash(file_hash) if store else None
    if existing_key and not reprocess:
        return blank_meta(
            status="duplicate", file_hash=file_hash,
            source_file=os.path.basename(path), uploaded_at=now,
            storage_key=existing_key,
            reason="документ з таким самим вмістом уже оброблено "
                   "(--reprocess, щоб обробити повторно)",
        )

    document_id = str(uuid.uuid4())
    base_meta = blank_meta(
        id=document_id,
        file_hash=file_hash,
        source_file=os.path.basename(path),
        uploaded_at=now,
        # При --reprocess старий запис того самого вмісту буде replaced, а не
        # здубльований (R-A1-04): ключ їде в meta (видно, ЩО замінено), а сам
        # файл _persist переносить у superseded/.
        supersedes_storage_key=(existing_key if reprocess else None),
    )

    ingest_warnings = []
    ingest_info = {}
    try:
        text, blocks = load_document_blocks(path, ocr_fn=res.get("ocr"),
                                            warnings=ingest_warnings,
                                            info=ingest_info)
    except Exception as exc:
        meta = dict(base_meta, status="unresolved",
                    source_kind=ingest_info.get("source_kind"),
                    review_queue="unknown_type", review_reason="unresolved",
                    warnings=list(ingest_warnings),
                    reason=f"не вдалося прочитати документ: {type(exc).__name__}: {exc}")
        _persist(meta, "", res)
        return meta

    source_kind = ingest_info.get("source_kind")
    # У base_meta, а не в кінцевому meta: інакше запис про нерозпізнаний текст
    # (гілка нижче) саме цієї інформації й не мав би -- а він її потребує
    # найбільше.
    base_meta["ocr_blocks"] = ingest_info.get("ocr_blocks")
    base_meta["ocr_chars"] = ingest_info.get("ocr_chars")

    # Порожній/нечитабельний скан -- окремий випадок, а не "усі поля відсутні":
    # інакше він виглядав би як звичайний needs_review і губився серед них.
    if not text or len(text.strip()) < 20:
        meta = dict(base_meta, status="unresolved", source_kind=source_kind,
                    review_queue="unknown_type", review_reason="unresolved",
                    warnings=list(ingest_warnings),
                    reason=("OCR не дав тексту: "
                            f"{ingest_info.get('ocr_blocks')} блоків, "
                            f"{ingest_info.get('ocr_chars')} символів -- "
                            "збій розпізнавання, а не невідомий тип документа"
                            if ingest_info.get("ocr_blocks") is not None
                            else "текст не розпізнано (порожній або нечитабельний документ)"))
        _persist(meta, text, res)
        return meta

    llm = res.get("llm")
    ident = identify_template(
        text, res["schemas"], domains=res.get("domains"),
        llm_choose=(llm.choose if llm else None),
    )
    if force_template:
        forced = next((s for s in res["schemas"] if s["template"] == force_template), None)
        if forced is None:
            raise ValueError(f"Шаблон '{force_template}' не знайдено серед {[s['template'] for s in res['schemas']]}")
        ident = {"schema": forced, "template": forced["template"], "domain": forced.get("domain"),
                 "source": "forced", "score": ident.get("score"), "scores": ident.get("scores"),
                 "runner_up": ident.get("runner_up"), "reason": None,
                 # Вердикт про РЕДАКЦІЮ рахується й для примусового шаблону.
                 # `--force-template` каже "це той бланк", а не "цей документ
                 # дослівно збігається з бланком": підмінити перше другим
                 # означало б, що ручне вказання шаблону мовчки вимикає рівно
                 # ту перевірку, заради якої вона й потрібна.
                 "blank_edition": blank_edition_verdict(text, forced)}

    if ident["schema"] is None:
        # Схеми немає -> рівні 2 і 3: мапінг «домен -> вид», далі (якщо ввімкнено
        # окремим прапорцем) модель. Саме тут значення `none` окупається:
        # нормативна інструкція отримує домен `normative`, вид `none` і
        # `create_subject_object: false` -- тобто фантомний об'єкт у реєстрі не
        # створюється, і його не доведеться видаляти (шляху видалення в
        # завантажувачі БД немає).
        kind_info = resolve_subject_kind(
            schema=None, domain=ident.get("domain"), domains=res.get("domains"),
            llm_choose=_subject_kind_llm(res, cfg), text=text)
        meta = dict(base_meta, status="unresolved", domain=ident.get("domain"),
                    source_kind=source_kind,
                    review_queue="unknown_type", review_reason="unresolved",
                    warnings=list(ingest_warnings),
                    reason=ident.get("reason") or "шаблон не визначено",
                    subject_kind=kind_info["kind"],
                    subject_kind_source=kind_info["source"],
                    subject_kind_reason=kind_info["reason"],
                    create_subject_object=creates_object(kind_info["kind"]),
                    identification={"scores": ident.get("scores"),
                                    # Скори доменів -- щоб вирок про домен був
                                    # перевірним, а не лише шаблонні (R-B1-01).
                                    "domain_scores": ident.get("domain_scores"),
                                    "source": None})
        _persist(meta, text, res)
        return meta

    schema = ident["schema"]
    # РЕДАКЦІЯ бланка -- ГОТОВИЙ вердикт ідентифікації, а не власний підрахунок:
    # `identify_template` єдиний відповідає на питання «це наша форма», а решта
    # пайплайна його читає (identification.blank_edition_verdict). Два джерела
    # цієї відповіді розійшлися б, і ніхто не помітив би, яке з них право.
    blank_edition = ident.get("blank_edition") or {}
    # Відсутній вердикт -> форма вважається впізнаною. Схема без
    # `blank_template:` перевірки не отримує взагалі (та сама межа, що в
    # blank_form.py): мовчки не довіряти власним полям вона не має.
    form_recognized = blank_edition.get("recognized", True)
    warnings = list(ingest_warnings)
    missing = missing_dictionaries(schema, res["dictionaries"])
    if missing:
        warnings.append(f"не завантажено довідники категорій: {sorted(missing)}")

    # try/except НАВКОЛО самої екстракції -- докстрінг функції обіцяє "ніколи
    # не кидає виняток через ВМІСТ документа", але раніше цю гарантію
    # реалізовував лише ЗОВНІШНІЙ виклик (process_target), не сама функція.
    # Докстрінг run.py прямо каже, що process_file планується викликати "у
    # майбутньому з веб-бекенда" -- без process_target-обгортки виняток
    # контенту (напр. OverflowError з нормалізації дати на аномальному
    # LLM-виводі) пробився б назовні необробленим. force_template нижче
    # свідомо ЗАЛИШЕНИЙ поза цим try: невідома назва шаблону -- помилка
    # виклику/конфігурації, не вмісту документа, і має падати явно.
    try:
        llm_cfg = cfg["llm"]
        raw_extraction = extract_document(
            schema, text, blocks, res["dictionaries"],
            llm_extract_batch=(llm.extract_batch if llm else None),
            title_phrases=schema_title_phrases(schema),
            batch_size=llm_cfg.get("batch_size", 4),
            self_consistency_n=llm_cfg.get("self_consistency_n", 1),
            form_recognized=form_recognized,
        )
        record = build_record(schema, raw_extraction, res["dictionaries"])
        for fact in record["facts"]:
            fact["source_document_id"] = document_id

        # ОСНОВНИЙ факт визначає статус документа. Похідні факти (поля з
        # `dimension:`) додаються найкраще-як-вийде: відсутня посада не має
        # відправляти в ручний розбір документ, у якому дата й особа зчитані.
        confirmed = bool(record["facts"]) and record["facts"][0].get("confirmed")

        # Шаблон, обраний МОДЕЛЛЮ, а не анкорами, не може дати confirmed сам.
        # Анкорний збіг -- це детермінований доказ, що бланк той самий; вибір
        # моделі -- здогадка з обмеженого переліку, і вона за конструкцією не
        # може відповісти "це щось четверте". Далі чужа схема витягує поля через
        # LLM-фолбек, критичні прогалини закриваються, і документ виходив
        # confirmed з фактом, якого в ньому немає. Значення полів зберігаються
        # повністю -- ми нічого не викидаємо, лише не пускаємо це в підрахунки
        # до підтвердження людиною.
        template_by_llm = ident.get("source") == "llm"
        if template_by_llm:
            confirmed = False
            warnings.append("шаблон визначено моделлю, не анкорами -- потрібне "
                            "підтвердження людиною")

        # ІНША РЕДАКЦІЯ БЛАНКА -- той самий клас, що template_by_llm, і той
        # самий висновок: значення зберігаються повністю, але в підрахунки без
        # людини не йдуть. Різниця в питанні до рев'юера: там "що це за бланк",
        # тут "чи це той бланк, тільки іншої редакції". Причина пишеться
        # словами з цифрами -- інакше рев'юер бачив би набір порожніх і
        # непідтверджених полів без жодного пояснення, чому саме цей документ
        # виглядає гірше за решту.
        if not form_recognized and blank_edition.get("reason") == "blank_template_missing_or_empty":
            # Оголошений бланк не читається (файл зник ПІСЛЯ завантаження
            # схем -- на завантаженні це ловить валідатор). Це не «інша
            # редакція», а відсутність самої опори перевірки (R-A1-02).
            confirmed = False
            warnings.append(
                "оголошений blank_template не читається (файл відсутній або "
                "порожній) -- резегментація фото, перевірка друкованого "
                "тексту й вердикт редакції НЕ працюють; підтвердження без "
                "людини неможливе")
        elif not form_recognized:
            confirmed = False
            warnings.append(
                "друкований текст документа збігається з оголошеним бланком "
                f"лише на {blank_edition.get('found')} з "
                f"{blank_edition.get('total')} рядків "
                f"({blank_edition.get('coverage'):.2f} проти порога "
                f"{blank_edition.get('threshold'):.2f}) -- ЙМОВІРНО ІНША "
                "РЕДАКЦІЯ БЛАНКА: межі полів за бланком не відновлюються, тому "
                "значення, знайдені без опори на друкований підпис поля, "
                "перевірено моделлю й не підтверджено автоматично")

        subject = _person_identity(record["subject"])
        if not subject["person_complete"]:
            # Не warning заради warning: у них people.last_name/first_name NOT NULL,
            # тобто такий запис не вставиться, а не "вставиться неповним".
            warnings.append("неповний ПІБ (немає прізвища або імені) -- "
                            "вставка в people у БД-споживача не пройде")

        # ВИД СУБ'ЄКТА, рівень 1: оголошення самої схеми. Домен передається як
        # фолбек для схеми, що `subject_kind:` не оголосила (валідатор про це
        # попереджає на завантаженні). Модель тут не потрібна за визначенням --
        # схема вже є.
        kind_info = resolve_subject_kind(schema=schema, domain=ident.get("domain"),
                                         domains=res.get("domains"))
        subject_kind = kind_info["kind"]
        create_object = creates_object(subject_kind)
        # ГЕЙТ «немає виду -> немає об'єкта». Два випадки, і дія людини різна:
        #   none    -- ВІДПОВІДЬ: суб'єкта немає. Об'єкт не створюється, але
        #              статус документа не псується: примусове рев'ю тут
        #              означало б, що кожен нормативний документ назавжди
        #              висить у черзі без жодної дії, яку людина може зробити.
        #   unknown -- відповіді НЕМА (схема без оголошення + домен без
        #              мапінгу). Об'єкт не створюється І документ не може бути
        #              confirmed: ми не знаємо, ЩО описує документ, тобто до
        #              полів дивитись ще рано. Це той самий клас, що
        #              template_by_llm, тому й черга та сама -- unknown_type.
        unknown_kind = subject_kind == UNKNOWN_SUBJECT
        if not create_object:
            warnings.append(
                f"вид суб'єкта '{subject_kind}' -- об'єкт у реєстрі БД НЕ "
                f"створюється (create_subject_object: false)"
                + (f"; причина: {kind_info['reason']}" if kind_info["reason"] else ""))
        if unknown_kind:
            confirmed = False

        # ФІНАЛЬНИЙ вердикт confirmed їде НАЗАД у facts (R-A1-01 + R-A2-01).
        # Гейти вище (template_by_llm, чужа редакція бланка, unknown_kind)
        # гасили лише ЛОКАЛЬНУ змінну, а record["facts"][*].confirmed лишався
        # таким, яким його зібрав build_record за критичними полями. Споживач
        # фільтрує підрахунки за facts.confirmed, а не за meta.status -- тобто
        # needs_review-документ ішов у підрахунки як підтверджений факт.
        # Заміряно (b2-verdicts, repro_01): ident.source="llm" давав
        # status=needs_review при facts=[True]*8.
        if not confirmed:
            for fact in record["facts"]:
                fact["confirmed"] = False

        audit_sampled = confirmed and _sampled_for_audit(file_hash, cfg["review"].get("sample_rate", 20))
        status = "confirmed" if confirmed else "needs_review"

        meta = dict(
            base_meta,
            status=status,
            source_kind=source_kind,
            domain=ident["domain"],
            template=ident["template"],
            identification={"source": ident["source"], "score": ident.get("score"),
                             "runner_up": ident.get("runner_up"),
                             # Дрібніша міра тієї самої схожості -- у мету
                             # цілком, разом із порогом: без цифр "ймовірно інша
                             # редакція" неможливо ні перевірити, ні оскаржити.
                             "blank_edition": ident.get("blank_edition")},
            # Позначка для черги ручного аудиту: навіть повністю впевнені записи
            # вибірково перевіряються, інакше рівень помилки системи невідомий
            # (architecture-proposal.md розд. 3).
            # Порядок причин = порядок первинності: невпізнаний ШАБЛОН старший
            # за чужу РЕДАКЦІЮ того самого шаблону (якщо під питанням сама
            # схема, редакція під питанням похідно); редакція, у свою чергу,
            # старша за невідомий вид суб'єкта (вид беруть зі схеми, тож коли
            # під питанням бланк, вид під питанням похідно) і за загальний
            # needs_review, бо вона їх ПОЯСНЮЄ: поля виглядають гірше саме
            # через бланк.
            review_reason=("template_by_llm" if template_by_llm else
                           ("foreign_form_edition" if not form_recognized else
                            ("unknown_subject_kind" if unknown_kind else
                             ("random_audit" if audit_sampled else
                              (None if confirmed else "needs_review"))))),
            # Шаблон від моделі -> у черзі це саме "тип документа під питанням"
            # (unknown_type), а не "факт непідтверджений": рев'юер має спершу
            # сказати, ЩО це за бланк, і лише потім дивитись на поля. Невідомий
            # вид суб'єкта -- те саме питання іншими словами ("про КОГО це"),
            # тому та сама черга, а не unconfirmed_fact.
            # Чужа редакція -- теж питання "ЩО це за бланк" (а не "чи вірний
            # факт"), тому та сама черга unknown_type: рев'юер має спершу
            # сказати, чи це справді наша форма іншої редакції, і лише потім
            # дивитись на поля.
            review_queue=("unknown_type" if (template_by_llm or unknown_kind
                                             or not form_recognized)
                          else _review_queue_type(
                              status, source_kind, audit_sampled,
                              ocr_used=bool(ingest_info.get("ocr_pages")))),
            subject=subject,
            subject_kind=subject_kind,
            subject_kind_source=kind_info["source"],
            subject_kind_reason=kind_info["reason"],
            create_subject_object=create_object,
            facts=record["facts"],
            field_provenance=record["field_provenance"],
            unknown_fields=record["unknown_fields"],
            unknown_critical_fields=record["unknown_critical_fields"],
            confirmed_empty_fields=record["confirmed_empty_fields"],
            not_implemented_fields=record["not_implemented_fields"],
            date_range_error=record["date_range_error"],
            consistency_problems=record["consistency_problems"],
            document_links=record["document_links"],
            unresolved_values=record["unresolved_values"],
            warnings=warnings,
        )
    except Exception as exc:
        meta = dict(base_meta, status="unresolved", domain=ident.get("domain"),
                    source_kind=source_kind,
                    review_queue="unknown_type", review_reason="unresolved",
                    warnings=list(warnings),
                    reason=f"не вдалося обробити вміст документа: {type(exc).__name__}: {exc}")
    _persist(meta, text, res)
    return meta


def _persist(meta: dict, text: str, res: dict) -> None:
    store = res.get("store")
    if store is None:
        return
    # unresolved завжди в одну теку, навіть коли грубий домен вгадався: інакше
    # непізнані документи розсипались би по доменних теках (нормативна
    # інструкція, напр., впізнається як "equipment"), і черга ручного
    # розгляду перестала б бути одним місцем, куди дивиться людина.
    folder = "unresolved" if meta.get("status") == "unresolved" else (meta.get("domain") or "unresolved")
    key = store.key_for(folder, meta["id"])
    # У meta лишається КЛЮЧ, не абсолютний шлях: саме ключ повертає
    # find_by_hash, і саме він переносний між сховищами. Раніше тут був
    # абсолютний шлях, тож storage_key у дублікаті й у звичайному записі
    # означали різні речі й порівнювати їх було неможливо.
    meta["storage_key"] = key
    store.save(key, _to_markdown(meta, text), file_hash=meta["file_hash"])
    # Старий запис того самого вмісту (--reprocess) прибирається з живих ПІСЛЯ
    # успішного збереження нового: якщо save впав, старий запис лишається
    # єдиним і чинним (R-A1-04).
    previous_key = meta.get("supersedes_storage_key")
    if previous_key and previous_key != key:
        store.retire(previous_key)


def _update_persisted_meta(meta: dict, res: dict) -> None:
    """Перезаписує ВЖЕ збережений запис оновленими метаданими (R-A1-13:
    archived_to з'являється в process_target ПІСЛЯ _persist, тому в .md він
    не потрапляв жодним шляхом -- збережений запис не знав, куди переїхав
    вхідний файл). Рядок індексу не дописується: він уже є, ключ той самий."""
    store = res.get("store")
    key = meta.get("storage_key")
    if store is None or not key:
        return
    path = os.path.join(store.root, key.replace("/", os.sep))
    marker = "---\n\n## Розпізнаний текст\n\n"
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        text = content.split(marker, 1)[1] if marker in content else ""
        if text.endswith("\n"):
            text = text[:-1]
        store.save(key, _to_markdown(meta, text))
    except OSError as exc:
        meta.setdefault("warnings", []).append(
            f"не вдалося оновити збережений запис (archived_to): {exc}")


def scan_target(target: str):
    """Повертає (files, skipped). skipped -- те, що НЕ буде оброблено, і про
    що треба сказати вголос: раніше і підпапки, і файли невідомого типу
    зникали безслідно, і людина, що поклала папку з документами, бачила
    лише "не знайдено файлів" без жодного пояснення.

    os.listdir, а не glob.glob(target/"*"): glob трактує "[" і "]" у ШЛЯХУ
    як символьний клас, тому назва файлу чи папки з квадратними дужками
    (реальний випадок: "Наказ №123 [копія].docx") могла мовчки НЕ потрапити
    в жодну гілку -- документ зникав з папки-приймача без обробки й без
    попередження."""
    if os.path.isfile(target):
        return [target], {"unsupported": [], "subdirs": []}

    files, unsupported, subdirs = [], [], []
    for name in sorted(os.listdir(target)):
        path = os.path.join(target, name)
        if os.path.isdir(path):
            subdirs.append(name)
        elif os.path.splitext(path)[1].lower() in SUPPORTED_EXTS:
            files.append(path)
        elif name != ".gitkeep":
            unsupported.append(name)
    return files, {"unsupported": unsupported, "subdirs": subdirs}


def archive_input_file(path: str, meta: dict, cfg: dict):
    """Переносить оброблений файл з папки-приймача, щоб наступний запуск не
    перечитував те саме. unresolved -- окремо: його має подивитись людина.
    Підкаталог за датою, щоб великий архів лишався навігабельним; при збігу
    імен додається префікс хеша, а не перезапис."""
    intake = cfg.get("intake", {})
    if not intake.get("archive", True):
        return None
    base = intake["failed_dir"] if meta.get("status") == "unresolved" else intake["processed_dir"]
    # Локальний день, НЕ UTC-дата з meta["uploaded_at"]: документ, оброблений
    # вночі за київським часом, і так потрапляв у теку "вчорашнього" (UTC)
    # дня -- людина, що шукає архів за календарним днем на власному годиннику,
    # не знайшла б файл там, де очікувала. uploaded_at у самому записі
    # лишається UTC навмисно (стандарт для збереженого таймстампа) -- міняється
    # лише назва директорії архіву.
    day = datetime.datetime.now().strftime("%Y-%m-%d")
    dest_dir = os.path.join(base, day)
    os.makedirs(dest_dir, exist_ok=True)

    name = os.path.basename(path)
    dest = os.path.join(dest_dir, name)
    if os.path.exists(dest):
        stem, ext = os.path.splitext(name)
        dest = os.path.join(dest_dir, f"{stem}_{(meta.get('file_hash') or '')[:8]}{ext}")
    shutil.move(path, dest)
    return dest


def process_target(target: str, res: dict, cfg: dict, force_template=None,
                   reprocess=False):
    """Повертає (results, skipped). Перенесення оброблених файлів працює лише
    в режимі сканування каталогу: файл, переданий явно через --input,
    лишається на місці (інакше прогін на data/eval/samples/ виносив би зразки з
    репозиторію)."""
    files, skipped = scan_target(target)
    # Переносимо оброблене ЛИШЕ з налаштованої папки-приймача. Раніше умовою
    # було просто "target -- це папка", тому прогін на data/eval/samples/ фізично
    # виносив зразки з репозиторію в data/processed/. Комментар нижче обіцяв
    # протилежне, але захист працював тільки для одного файла, переданого
    # через --input, не для папки.
    configured_inbox = os.path.abspath(cfg["paths"]["input_dir"])
    is_directory_mode = (os.path.isdir(target)
                         and os.path.abspath(target) == configured_inbox)
    if os.path.isdir(target) and not is_directory_mode:
        skipped["not_archived"] = True
    results = []
    for path in files:
        try:
            meta = process_file(path, res, cfg, force_template=force_template,
                                reprocess=reprocess)
        except Exception as exc:
            # Ізоляція збою по документу: без цього одна несподівана помилка на
            # одному файлі валила ВЕСЬ пакетний прогін, і решта папки лишалась
            # необробленою. process_file і сам намагається не кидати винятків,
            # але це остання лінія -- вона мусить бути, бо гарантія "вихід
            # існує завжди" не може залежати від того, що ми передбачили всі
            # можливі помилки всередині.
            #
            # Запис МУСИТЬ існувати й у сховищі (R-A2-06): раніше тут було лише
            # results.append + continue, тобто документ, що впав, не мав ні id,
            # ні файла у сховищі, ні рядка індексу -- сліду ніде, крім консолі.
            # А continue минав archive_input_file, тож файл лишався в
            # папці-приймачі й падав так само на КОЖНОМУ наступному запуску.
            meta = blank_meta(
                id=str(uuid.uuid4()),
                status="unresolved",
                source_file=os.path.basename(path),
                uploaded_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                review_queue="unknown_type", review_reason="unresolved",
                reason=f"необроблена помилка: {type(exc).__name__}: {exc}",
            )
            try:
                meta["file_hash"] = file_sha256(path)
            except OSError:
                meta["file_hash"] = None
            try:
                _persist(meta, "", res)
            except Exception as persist_exc:
                # Збій самого сховища не має валити решту батчу, але й не має
                # бути німим: причина йде в запис, який побачить викликач.
                meta["warnings"] = list(meta.get("warnings") or []) + [
                    f"не вдалося зберегти запис про збій: "
                    f"{type(persist_exc).__name__}: {persist_exc}"]
        if is_directory_mode and res.get("store") is not None:
            try:
                moved_to = archive_input_file(path, meta, cfg)
                if moved_to:
                    meta["archived_to"] = os.path.relpath(moved_to, cfg["project_root"])
                    # Ключ мусить дожити й до збереженого .md (R-A1-13).
                    _update_persisted_meta(meta, res)
            except OSError as exc:
                meta.setdefault("warnings", []).append(
                    f"не вдалося перенести файл з папки-приймача: {exc}")
        results.append(meta)
    return results, skipped
