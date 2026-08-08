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
    identify_template,
    load_schemas,
    missing_dictionaries,
    schema_title_phrases,
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


def build_resources(cfg: dict, force_no_llm=False) -> dict:
    """Важкі об'єкти (схеми, довідники, модель, OCR) створюються ОДИН раз на
    процес, а не на кожен файл -- інакше пакетна обробка папки перечитувала б
    ваги моделі для кожного документа."""
    paths = cfg["paths"]
    res = {
        "schemas": load_schemas(paths["schemas_dir"]),
        "dictionaries": load_dictionaries(paths["dictionaries_dir"]),
        "domains": None,
        "llm": None,
        "ocr": None,
        "store": None,
        "warnings": [],
    }

    keyphrases_path = os.path.join(paths["dictionaries_dir"], "domain_keyphrases.yaml")
    if os.path.exists(keyphrases_path):
        res["domains"] = load_domain_keyphrases(keyphrases_path)

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
            res["ocr"] = make_surya_reader(cfg["ocr"].get("llama_server_path"))
        except Exception as exc:
            res["warnings"].append(f"OCR недоступний ({type(exc).__name__}: {exc}) -- зображення не обробляться")

    res["store"] = LocalDocumentStore(cfg["storage"]["local_root"])
    return res


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


def process_file(path: str, res: dict, cfg: dict, force_template=None) -> dict:
    """Повертає document_meta. Ніколи не кидає виняток через вміст документа
    -- лише через непрацездатне середовище (немає прав на запис тощо)."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    file_hash = file_sha256(path)
    store = res["store"]

    existing_key = store.find_by_hash(file_hash) if store else None
    if existing_key:
        return {
            "id": None, "status": "duplicate", "file_hash": file_hash,
            "source_file": os.path.basename(path), "uploaded_at": now,
            "existing_key": existing_key,
            "reason": "документ з таким самим вмістом уже оброблено",
        }

    document_id = str(uuid.uuid4())
    base_meta = {
        "id": document_id,
        "file_hash": file_hash,
        "source_file": os.path.basename(path),
        "uploaded_at": now,
    }

    try:
        text, blocks = load_document_blocks(path, ocr_fn=res.get("ocr"))
    except Exception as exc:
        meta = dict(base_meta, status="unresolved", domain=None, template=None,
                    reason=f"не вдалося прочитати документ: {type(exc).__name__}: {exc}")
        _persist(meta, "", res)
        return meta

    # Порожній/нечитабельний скан -- окремий випадок, а не "усі поля відсутні":
    # інакше він виглядав би як звичайний needs_review і губився серед них.
    if not text or len(text.strip()) < 20:
        meta = dict(base_meta, status="unresolved", domain=None, template=None,
                    reason="текст не розпізнано (порожній або нечитабельний документ)")
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
                 "runner_up": ident.get("runner_up"), "reason": None}

    if ident["schema"] is None:
        meta = dict(base_meta, status="unresolved", domain=ident.get("domain"), template=None,
                    reason=ident.get("reason") or "шаблон не визначено",
                    identification={"scores": ident.get("scores"), "source": None})
        _persist(meta, text, res)
        return meta

    schema = ident["schema"]
    warnings = []
    missing = missing_dictionaries(schema, res["dictionaries"])
    if missing:
        warnings.append(f"не завантажено довідники категорій: {sorted(missing)}")

    llm_cfg = cfg["llm"]
    raw_extraction = extract_document(
        schema, text, blocks, res["dictionaries"],
        llm_extract_batch=(llm.extract_batch if llm else None),
        title_phrases=schema_title_phrases(schema),
        batch_size=llm_cfg.get("batch_size", 4),
        self_consistency_n=llm_cfg.get("self_consistency_n", 1),
    )
    record = build_record(schema, raw_extraction, res["dictionaries"])
    for fact in record["facts"]:
        fact["source_document_id"] = document_id

    confirmed = all(f.get("confirmed") for f in record["facts"])
    audit_sampled = confirmed and _sampled_for_audit(file_hash, cfg["review"].get("sample_rate", 20))

    meta = dict(
        base_meta,
        status="confirmed" if confirmed else "needs_review",
        domain=ident["domain"],
        template=ident["template"],
        identification={"source": ident["source"], "score": ident.get("score"),
                         "runner_up": ident.get("runner_up")},
        # Позначка для черги ручного аудиту: навіть повністю впевнені записи
        # вибірково перевіряються, інакше рівень помилки системи невідомий
        # (architecture-proposal.md розд. 3).
        review_reason=("random_audit" if audit_sampled else (None if confirmed else "needs_review")),
        subject=record["subject"],
        facts=record["facts"],
        field_provenance=record["field_provenance"],
        unknown_fields=record["unknown_fields"],
        unknown_critical_fields=record["unknown_critical_fields"],
        confirmed_empty_fields=record["confirmed_empty_fields"],
        not_implemented_fields=record["not_implemented_fields"],
        warnings=warnings,
    )
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
    meta["storage_key"] = store.save(key, _to_markdown(meta, text), file_hash=meta["file_hash"])


def scan_target(target: str):
    """Повертає (files, skipped). skipped -- те, що НЕ буде оброблено, і про
    що треба сказати вголос: раніше і підпапки, і файли невідомого типу
    зникали безслідно, і людина, що поклала папку з документами, бачила
    лише "не знайдено файлів" без жодного пояснення."""
    if os.path.isfile(target):
        return [target], {"unsupported": [], "subdirs": []}

    files, unsupported, subdirs = [], [], []
    for path in sorted(glob.glob(os.path.join(target, "*"))):
        name = os.path.basename(path)
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
    day = (meta.get("uploaded_at") or "")[:10] or "unknown-date"
    dest_dir = os.path.join(base, day)
    os.makedirs(dest_dir, exist_ok=True)

    name = os.path.basename(path)
    dest = os.path.join(dest_dir, name)
    if os.path.exists(dest):
        stem, ext = os.path.splitext(name)
        dest = os.path.join(dest_dir, f"{stem}_{(meta.get('file_hash') or '')[:8]}{ext}")
    shutil.move(path, dest)
    return dest


def process_target(target: str, res: dict, cfg: dict, force_template=None):
    """Повертає (results, skipped). Перенесення оброблених файлів працює лише
    в режимі сканування каталогу: файл, переданий явно через --input,
    лишається на місці (інакше прогін на data/samples/ виносив би зразки з
    репозиторію)."""
    files, skipped = scan_target(target)
    is_directory_mode = os.path.isdir(target)
    results = []
    for path in files:
        meta = process_file(path, res, cfg, force_template=force_template)
        if is_directory_mode and res.get("store") is not None:
            try:
                moved_to = archive_input_file(path, meta, cfg)
                if moved_to:
                    meta["archived_to"] = os.path.relpath(moved_to, cfg["project_root"])
            except OSError as exc:
                meta.setdefault("warnings", []).append(
                    f"не вдалося перенести файл з папки-приймача: {exc}")
        results.append(meta)
    return results, skipped
