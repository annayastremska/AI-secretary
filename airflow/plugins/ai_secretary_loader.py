"""Спільна логіка завантаження результату AI-secretary (repo ds-cprgb/AI-secretary,
branch anya) у нашу PostgreSQL. Використовується і з CLI (scripts/load_ai_secretary_output.py,
на хості), і з Airflow DAG (airflow/dags/load_ai_secretary_output_dag.py, у контейнері) --
щоб не тримати логіку мапінгу в двох місцях.

Сам пайплайн AI-secretary навмисно пише лише в локальні MD/YAML-файли (БД --
поза його межами, README того репозиторію). Наша схема (documents/objects/
people/facts/dimensions) уже спроєктована саме під цей вихід (db_target у
schemas/*.yaml).

Підключення до БД: якщо задано env APP_DATABASE_URL (так налаштовано в
docker-compose для контейнерів Airflow) -- береться воно; інакше читається
.env у корені проєкту (для запуску з хосту через CLI-скрипт). Те саме для
теки оригіналів (ORIGINALS_DIR, інакше data/originals у корені).

Оригінали лежать у звичайній теці, не в об'єктному сховищі. MinIO прибрано
свідомо: від сховища потрібно лише покласти файл і видалити його через 30
днів, а адмініструвати S3-сумісний сервіс у підрозділі нікому. Видалення
тепер робить db/scripts/purge_expired_originals.py за documents.expires_at.
"""
import os
import shutil

import psycopg
import yaml
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# template (AI-secretary) -> document_types.code (milidoc). Розширювати тут
# при появі нових шаблонів у schemas/.
# leave_ticket -- "Відпускний квиток" (виданий документ ПІСЛЯ затвердження),
# НЕ те саме, що "Рапорт на відпустку" (vacation_report, заявка ДО
# затвердження) -- дотепер мапилось помилково на один код із заявкою.
TEMPLATE_TO_DOC_TYPE = {
    "deployment_certificate": "business_trip_order",
    "leave_ticket": "leave_ticket",
}

# fact_type -> (назва, validity_model), якщо виміру ще немає в dimensions.
# Синхронізовано 1:1 з dictionaries/fact_type_registry.yaml на боці
# AI-secretary (не з опису в контрактному документі -- той описував
# leave_days/leave_place/unit_to_report з іншим validity_model, ніж у
# реальному реєстрі; звірено grep-ом по її файлу, не по пам'яті).
# rank/position уже насіяні вручну окремою міграцією.
FACT_TYPE_LABELS = {
    "leave": "Відпустка",
    "deployment_location": "Місцезнаходження під час відрядження",
    "attendance": "Присутність (табель)",
    "equipment_status": "Стан техніки",
    "deployment_org": "Організація призначення у відрядженні",
    "deployment_days": "Тривалість відрядження, днів",
    "deployment_purpose": "Мета відрядження",
    "order_date": "Дата наказу-підстави",
    "order_number": "Номер наказу-підстави",
    "leave_days": "Тривалість відпустки, днів",
    "leave_actual_return": "Фактична дата повернення з відпустки",
    "unit_to_report": "Частина, до якої зобов'язаний прибути",
    "document_number": "Номер документа",
    "document_date": "Дата видачі документа",
    "leave_place": "Населений пункт, до якого звільнено",
}

FACT_TYPE_VALIDITY = {
    "leave": "ranged",
    "deployment_location": "ranged",
    "attendance": "ranged",
    "equipment_status": "current_state",
    "deployment_org": "ranged",
    "deployment_days": "ranged",
    "deployment_purpose": "ranged",
    "order_date": "permanent_event",
    "order_number": "permanent_event",
    "leave_days": "ranged",
    "leave_actual_return": "permanent_event",
    "unit_to_report": "ranged",
    "document_number": "permanent_event",
    "document_date": "permanent_event",
    "leave_place": "ranged",
}


def _safe_relpath(path: str, root: str) -> str:
    """os.path.relpath кидає ValueError, коли path і root на різних дисках
    Windows (напр. .md-приклад у Downloads на C:, проєкт на D:). У такому
    разі повертаємо абсолютний шлях замість крашу -- гірше для читабельності,
    краще, ніж збій усього завантаження через некритичне поле."""
    try:
        return os.path.relpath(path, root).replace(os.sep, "/")
    except ValueError:
        return path.replace(os.sep, "/")


def get_dsn() -> str:
    url = os.environ.get("APP_DATABASE_URL")
    if not url:
        load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
        url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+psycopg://", "postgresql://")


def originals_dir() -> str:
    """Тека, де лежать оригінали. ORIGINALS_DIR -- щоб контейнер і хост
    вказували на той самий змонтований шлях."""
    path = os.environ.get("ORIGINALS_DIR")
    if not path:
        load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
        path = os.environ.get("ORIGINALS_DIR") or os.path.join(PROJECT_ROOT, "data", "originals")
    return path


def store_original(local_path: str, file_hash: str) -> str:
    """Копіює оригінал у нашу теку і повертає raw_uri (file://...).

    Раніше тут був MinIO. Прибрали свідомо: єдине, що від сховища потрібно --
    покласти файл і видалити через 30 днів. S3-сумісний сервіс для цього не
    потрібен, а в підрозділі його нікому адмініструвати. Разом із MinIO
    зникає і рут-акаунт, яким ми до нього ходили.

    Ім'я -- за хешем вмісту, тож повторне завантаження того самого файлу
    перезаписує той самий шлях і не плодить дублікатів. Підтека з двох
    перших символів хеша -- щоб не тримати десятки тисяч файлів в одній
    теці (частина ФС на цьому деградує).

    ВАЖЛИВО: 30-денне видалення більше НЕ виконується сховищем саме.
    Раніше це робило ILM-правило MinIO, тепер -- db/scripts/purge_expired_originals.py
    за documents.expires_at. Політика стала видимою в базі, але й вимагає,
    щоб прибирання справді запускалось за розкладом.
    """
    target_dir = os.path.join(originals_dir(), file_hash[:2])
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, f"{file_hash}{os.path.splitext(local_path)[1]}")
    shutil.copy2(local_path, target)
    return f"file:///{os.path.abspath(target).replace(os.sep, '/')}"


def parse_frontmatter(md_path: str) -> dict:
    with open(md_path, encoding="utf-8") as f:
        content = f.read()
    _, fm, body = content.split("---", 2)
    meta = yaml.safe_load(fm)
    # .replace(" "): Postgres text-поля не приймають NUL, а старі .md
    # (до правки пайплайна 24.08) могли принести його з pypdf. Захисний шар:
    # джерело вже чистить, але записи, згенеровані до правки, лишаються.
    meta["_recognized_text"] = (body.split("## Розпізнаний текст", 1)[-1]
                                .strip().replace(" ", ""))
    return meta


def get_or_create_dimension(cur, code: str, validity_model: str = None) -> int:
    """validity_model -- ПЕРЕДАНЕ значення переважає локальну копію реєстру.

    Правка Ані 24.08.2026 (зона db/ -- Андрію на рев'ю): AI-secretary тепер
    віддає validity_model У КОЖНОМУ факті (facts[*].validity_model), тому
    локальний словник FACT_TYPE_VALIDITY більше не мусить встигати за її
    реєстром. Він лишається лише запобіжником на факт без поля (старі .md).
    Причина правки заміряна: у копії немає трьох нових типів
    (travel_document, unrecognized, deployment_actual_return), і без цього
    вони заводили вимір як 'ranged' замість 'permanent_event' -- мовчки."""
    cur.execute("SELECT id FROM dimensions WHERE code = %s", (code,))
    row = cur.fetchone()
    if row:
        return row[0]
    name = FACT_TYPE_LABELS.get(code, code)
    if not validity_model:
        validity_model = FACT_TYPE_VALIDITY.get(code, "ranged")
    cur.execute(
        "INSERT INTO dimensions (code, name, value_type, validity_model) VALUES (%s, %s, 'text', %s) RETURNING id",
        (code, name, validity_model),
    )
    return cur.fetchone()[0]


def insert_fact(cur, object_id, dimension_id, value, valid_from, valid_to, source_doc_id, confirmed,
                 additional_info=None, confidence=None, source_field=None) -> int:
    """Вставляє факт. Для вимірів з validity_model='current_state' (rank,
    position -- "чинно, доки не з'явиться новіший факт того самого виміру
    для того самого об'єкта", dictionaries/fact_type_registry.yaml на боці
    AI-secretary) спершу закриває попередній відкритий факт (valid_to IS
    NULL) з ІНШИМ значенням -- інакше на одного об'єкта накопичувались би
    кілька "чинних" фактів того самого виміру одночасно, і жодного способу
    визначити, який саме зараз дійсний."""
    # Захист від CHECK-краху (facts_check: valid_to >= valid_from). AI-secretary
    # СВІДОМО лишає суперечливі дати у виході (date_range_error непорожній),
    # щоб людина це побачила -- завантажувач не має валитись через це: не
    # приховуємо проблему (значення взагалі не пишемо, не вигадуємо
    # виправлення), лише не даємо факту стати confirmed.
    if valid_from and valid_to and str(valid_to) < str(valid_from):
        valid_to = None
        confirmed = False

    cur.execute("SELECT validity_model FROM dimensions WHERE id = %s", (dimension_id,))
    validity_model = cur.fetchone()[0]

    if validity_model == "current_state":
        cur.execute(
            """
            SELECT id, value FROM facts
            WHERE object_id = %s AND dimension_id = %s AND valid_to IS NULL AND status != 'rejected'
            """,
            (object_id, dimension_id),
        )
        open_fact = cur.fetchone()
        if open_fact and open_fact[1] != value:
            old_fact_id, old_value = open_fact
            cur.execute("UPDATE facts SET valid_to = %s WHERE id = %s", (valid_from, old_fact_id))
            cur.execute(
                """
                INSERT INTO review_log (fact_id, changed_by, old_value, new_value, action)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (old_fact_id, "ai_secretary_loader", old_value, value, "superseded_by_newer_fact"),
            )

    cur.execute(
        """
        INSERT INTO facts (object_id, dimension_id, value, valid_from, valid_to,
                            source_doc_id, status, additional_info, confidence, source_field)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (object_id, dimension_id, value, valid_from, valid_to, source_doc_id,
         "confirmed" if confirmed else "unconfirmed",
         Jsonb(additional_info) if additional_info else None,
         confidence, source_field),
    )
    return cur.fetchone()[0]


def handle_reprocess(cur, document_id: int) -> None:
    """Reprocess (--reprocess на боці AI-secretary: виправили схему,
    перепрогнали той самий вхідний файл) -- попередня версія фактів цього
    документа застаріла. НЕ видаляємо (аудит-слід має лишатись: правило
    "не приховувати обмеження"), а позначаємо rejected + review_log,
    і закриваємо старі review_queue-записи -- інакше вони чекають на
    перевірку версії факту, якої вже нема."""
    cur.execute(
        "SELECT id, value FROM facts WHERE source_doc_id = %s AND status != 'rejected'",
        (document_id,),
    )
    for fact_id, old_value in cur.fetchall():
        cur.execute("UPDATE facts SET status = 'rejected' WHERE id = %s", (fact_id,))
        cur.execute(
            """
            INSERT INTO review_log (fact_id, changed_by, old_value, new_value, action)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (fact_id, "ai_secretary_loader:reprocess", old_value, None, "rejected_by_reprocess"),
        )
    cur.execute(
        """
        UPDATE review_queue SET resolved_at = now(), resolution = 'superseded_by_reprocess'
        WHERE document_id = %s AND resolved_at IS NULL
        """,
        (document_id,),
    )


def resolve_queue_type(meta: dict):
    """Довіряємо meta['review_queue'], якщо AI-secretary вже його рахує
    (готове значення з нашого ж CHECK на review_queue.queue_type -- саме
    те, про що вона писала: "ми віддаємо готове значення, ви хардкодите
    один"). Перевіряємо саме НАЯВНІСТЬ ключа, не truthy -- 'review_queue': null
    означає "рецензія не потрібна", а відсутність ключа означає "старий
    .md без цього поля, рахуй сам за нашою старою евристикою"."""
    if "review_queue" in meta:
        return meta.get("review_queue")
    if meta.get("status") == "unresolved":
        return "unknown_type"
    if meta.get("status") == "needs_review":
        return "unconfirmed_fact"
    if meta.get("review_reason") == "random_audit":
        return "qa_sample"
    return None


def get_or_create_person(cur, surname: str, given_name: str, patronymic: str, document_id: int,
                          person_alias: str = None):
    """Повертає (object_id, is_new_alias). Використовує СПРАВЖНЮ SQL-функцію
    resolve_or_create_object (міграція 9337860eac54) замість власного пошуку
    за canonical_name -- саме про це просила команда AI-secretary: без цього
    виклику object_aliases лишається порожньою, а зіставлення особи йде за
    точним збігом рядка (без урахування псевдонімів/варіантів написання).
    Довіряємо person_alias, якщо AI-secretary вже його рахує -- її власна
    нормалізація може відрізнятись від нашого простого join (пробіли,
    порядок), а саме вона й іде в resolve_or_create_object як псевдонім."""
    canonical = person_alias or " ".join(p for p in (surname, given_name, patronymic) if p)

    # is_new_alias перевіряємо ДО виклику функції -- вона сама вирішує
    # "знайдено чи створено" атомарно, але не повертає це нам, а перевірка
    # ПІСЛЯ виклику завжди показала б "уже є" (рядок alias щойно з'явився).
    cur.execute("SELECT 1 FROM object_aliases WHERE alias = %s", (canonical,))
    is_new_alias = cur.fetchone() is None

    cur.execute("SELECT resolve_or_create_object(%s, 'person', %s)", (canonical, document_id))
    object_id = cur.fetchone()[0]

    cur.execute("SELECT 1 FROM people WHERE object_id = %s", (object_id,))
    if cur.fetchone() is not None:
        return object_id, is_new_alias
    cur.execute(
        """
        INSERT INTO people (object_id, last_name, first_name, patronymic)
        VALUES (%s, %s, %s, %s)
        """,
        (object_id, surname, given_name, patronymic),
    )
    return object_id, is_new_alias


def get_or_create_document(cur, meta: dict, md_path: str, original_file_hint: str = None):
    """original_file_hint -- шлях до вихідного docx/скану, якщо він відомий і
    ще існує (CLI-запуск на тому самому зразку). У автоматичному режимі
    (Airflow) оригінал може бути вже архівований чи видалений пайплайном
    Ані -- тоді raw_uri вказує на сам .md-результат, який AI-secretary
    зберігає постійно (на відміну від скану, що видаляється за 30 днів).

    Повертає (document_id, state), де state -- одне з:
    - "new"         -- документ із цим checksum бачимо вперше
    - "unchanged"   -- той самий checksum І той самий ai_secretary_id (той
                        самий .md-файл, просто повторно сканується -- напр.
                        Airflow DAG щоразу перечитує всю теку виводу). Нічого
                        не робимо -- інакше кожне повторне сканування спамило
                        б review_log фальшивими "supersede" на ідентичні дані.
    - "reprocessed" -- той самий checksum, АЛЕ інший ai_secretary_id: Аня
                        виправила схему й перепрогнала той самий вхідний файл
                        (--reprocess). Це і є сценарій, який раніше "тихо
                        ігнорувався" через ON CONFLICT DO NOTHING."""
    cur.execute(
        "SELECT id, pipeline_meta ->> 'ai_secretary_id' FROM documents WHERE checksum = %s",
        (meta["file_hash"],),
    )
    existing = cur.fetchone()
    if existing is not None and existing[1] == meta.get("id"):
        return existing[0], "unchanged"

    doc_type_code = TEMPLATE_TO_DOC_TYPE.get(meta["template"])
    type_id = None
    if doc_type_code:
        cur.execute("SELECT id FROM document_types WHERE code = %s", (doc_type_code,))
        r = cur.fetchone()
        type_id = r[0] if r else None

    # mobile_photo для сканів/фото (пройшли через OCR), unit_export для
    # born-digital docx/pdf з текстовим шаром -- раніше було завжди
    # unit_export, походження документа губилось попри те, що ми його знаємо
    # з meta["source_kind"].
    source_code = "mobile_photo" if meta.get("source_kind") == "photo" else "unit_export"
    cur.execute("SELECT id FROM sources WHERE code = %s", (source_code,))
    source_row = cur.fetchone()
    source_id = source_row[0] if source_row else None

    # documents.status -- стадія КОНВЕЄРА (uploaded/text_ready/classified/
    # extracted/failed), а не статус запису (confirmed/needs_review), який у
    # AI-secretary живе на рівні факту (facts.status) і review_queue.
    doc_status = "failed" if meta["status"] == "unresolved" else "extracted"

    raw_uri = None
    if original_file_hint and os.path.exists(original_file_hint):
        try:
            raw_uri = store_original(original_file_hint, meta["file_hash"])
        except OSError as exc:
            # Немає місця, немає прав, шлях не змонтований -- не валимо весь
            # запис через це. Але й НЕ підставляємо шлях, звідки файл прийшов:
            # пайплайн Ані свій вхід архівує або видаляє, тож таке посилання
            # за кілька днів вказує в нікуди. Краще чесно без raw_uri.
            print(f"Копіювання оригіналу не вдалось ({type(exc).__name__}: {exc}) -- "
                  f"документ пишемо без raw_uri")
    if raw_uri is None:
        raw_uri = f"ai-secretary-output:{_safe_relpath(md_path, PROJECT_ROOT)}"

    # pipeline_meta -- усе, що AI-secretary вже рахує по документу, але що не
    # заслуговує на окрему типізовану колонку (аналіз Ані, розділ "Довіра до
    # запису"/"Класифікація"): identification, field_provenance,
    # unknown_critical_fields, confirmed_empty_fields, warnings,
    # date_range_error, unresolved_values. storage_key -- тепер довіряємо
    # значенню з самого файлу (вона вже рахує його ДО серіалізації), а не
    # рахуємо самі; рахуємо самі лише як fallback для старіших .md без
    # цього поля.
    pipeline_meta = Jsonb({
        "ai_secretary_id": meta.get("id"),
        "ai_secretary_storage_key": meta.get("storage_key") or _safe_relpath(md_path, PROJECT_ROOT),
        "identification": meta.get("identification"),
        "field_provenance": meta.get("field_provenance"),
        "unknown_critical_fields": meta.get("unknown_critical_fields"),
        "confirmed_empty_fields": meta.get("confirmed_empty_fields"),
        "warnings": meta.get("warnings"),
        "date_range_error": meta.get("date_range_error"),
        "unresolved_values": meta.get("unresolved_values"),
    })

    # ON CONFLICT DO UPDATE, не DO NOTHING: checksum під UNIQUE (міграція
    # 2135e0e3ae2a) захищає від дублікатів при паралельних запусках, але
    # DO NOTHING означало, що виправлений reprocess ніколи не потрапляв у
    # базу -- перший, помилковий прохід уже займав checksum назавжди.
    # uploaded_at/expires_at/checksum НЕ оновлюються при конфлікті: годинник
    # retention прив'язаний до першого завантаження, не до кожного
    # reprocess.
    cur.execute(
        """
        INSERT INTO documents
            (type_id, source_id, source_kind, status, raw_uri, text_content,
             uploaded_at, processed_at, expires_at, checksum, domain, pipeline_meta)
        VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s::timestamptz + interval '30 days', %s, %s, %s)
        ON CONFLICT (checksum) DO UPDATE SET
            type_id = EXCLUDED.type_id,
            source_id = EXCLUDED.source_id,
            source_kind = EXCLUDED.source_kind,
            status = EXCLUDED.status,
            raw_uri = EXCLUDED.raw_uri,
            text_content = EXCLUDED.text_content,
            processed_at = now(),
            domain = EXCLUDED.domain,
            pipeline_meta = EXCLUDED.pipeline_meta
        RETURNING id
        """,
        (
            type_id,
            source_id,
            meta.get("source_kind") or "electronic",  # fallback для старіших .md без цього поля
            doc_status,
            raw_uri,
            meta["_recognized_text"],
            meta["uploaded_at"],
            meta["uploaded_at"],
            meta["file_hash"],
            meta.get("domain"),
            pipeline_meta,
        ),
    )
    document_id = cur.fetchone()[0]
    return document_id, ("reprocessed" if existing is not None else "new")


def load(md_path: str, original_file_hint: str = None) -> dict:
    """Повертає короткий звіт: {"document_id", "doc_state", "already_existed",
    "facts_inserted"}. doc_state -- "new" / "unchanged" / "reprocessed"
    (див. get_or_create_document); already_existed лишається для сумісності
    з існуючими викликами (True лише для "unchanged")."""
    meta = parse_frontmatter(md_path)
    subject = meta.get("subject") or {}
    facts_inserted = []

    with psycopg.connect(get_dsn()) as conn:
        with conn.cursor() as cur:
            document_id, doc_state = get_or_create_document(cur, meta, md_path, original_file_hint)
            if doc_state == "unchanged":
                conn.commit()
                return {"document_id": document_id, "doc_state": doc_state,
                        "already_existed": True, "facts_inserted": []}

            if doc_state == "reprocessed":
                handle_reprocess(cur, document_id)

            queue_type = resolve_queue_type(meta)

            # unresolved -- шаблон не впізнано взагалі, build_record ще не
            # викликався на боці AI-secretary, тож subject/facts просто немає
            # (не порожні -- відсутні). person_complete: false -- інший
            # випадок: шаблон впізнано, але особу не вдалось ідентифікувати
            # повністю (напр. відсутнє прізвище). В обох випадках раніше код
            # все одно намагався створити "особу" з трьох None, породжуючи
            # фантомний objects-рядок з canonical_name=''. Тепер документ
            # лишається без жодної особи/факту -- лише документ + review_queue.
            person_incomplete = subject.get("person_complete") is False
            # Документ БЕЗ особи взагалі (правка Ані 24.08, зона db/ -- Андрію
            # на рев'ю). Нормативний акт: status=confirmed, facts=[],
            # create_subject_object=false, суб'єкта немає. Попередній гард
            # ловив лише unresolved/person_incomplete, тому на нормативних
            # get_or_create_person отримувала три None і падала на
            # NOT NULL людей (last_name) -- усі 41 акт не завантажились.
            # Такий документ -- законний запис лише в documents (текст для
            # дороги Б), без особи й без фактів.
            no_person = not ((subject.get("person_alias") or "").strip()
                             or (subject.get("surname") or "").strip())
            if meta.get("status") == "unresolved" or person_incomplete or no_person:
                if queue_type:
                    cur.execute(
                        "INSERT INTO review_queue (document_id, queue_type) VALUES (%s, %s)",
                        (document_id, queue_type),
                    )
                conn.commit()
                return {"document_id": document_id, "doc_state": doc_state,
                        "already_existed": False, "facts_inserted": []}

            person_object_id, is_new_person = get_or_create_person(
                cur, subject.get("surname"), subject.get("given_name"), subject.get("patronymic"),
                document_id, person_alias=subject.get("person_alias"),
            )
            if is_new_person:
                # Новий рядок у реєстрі осіб -- не лишаємо це непомітним:
                # шляху видалення помилково створеної особи в завантажувачі
                # немає, тож людина має підтвердити, що це справді нова
                # людина, а не пропущений псевдонім тієї самої.
                cur.execute(
                    "INSERT INTO review_queue (document_id, queue_type) VALUES (%s, 'new_person')",
                    (document_id,),
                )

            field_provenance = meta.get("field_provenance") or {}

            rank = subject.get("rank")
            if rank:
                rank_dim_id = get_or_create_dimension(cur, "rank")
                rank_provenance = field_provenance.get("rank", {})
                # НЕ дата початку відрядження/відпустки основного факту --
                # звання не почалось у день відрядження, це просто дата
                # найближчого відомого документа. uploaded_at -- єдина
                # завжди наявна точка відліку "станом на коли ми це знаємо".
                # confirmed -- за ВЛАСНИМ provenance звання (детермінований
                # збіг зі довідником, звичайно висока довіра), а не
                # успадковано від основного факту документа, чия впевненість
                # про щось геть інше.
                fact_id = insert_fact(
                    cur, person_object_id, rank_dim_id, rank.get("code"),
                    meta["uploaded_at"], None, document_id,
                    rank_provenance.get("resolved", True),
                    confidence=rank_provenance.get("confidence"),
                )
                facts_inserted.append(("rank", fact_id))

            for fact in meta.get("facts") or []:
                fact_type = fact.get("fact_type")
                if not fact_type:
                    continue
                dim_id = get_or_create_dimension(
                    cur, fact_type, validity_model=fact.get("validity_model"))
                fact_id = insert_fact(
                    cur, person_object_id, dim_id, fact.get("value_code"),
                    fact.get("date_start"), fact.get("date_end"), document_id, fact.get("confirmed"),
                    additional_info=fact.get("additional_info"),
                    confidence=fact.get("confidence"),
                    source_field=fact.get("source_field"),
                )
                facts_inserted.append((fact_type, fact_id))

            if queue_type:
                cur.execute(
                    "INSERT INTO review_queue (document_id, queue_type) VALUES (%s, %s)",
                    (document_id, queue_type),
                )
        conn.commit()

    return {"document_id": document_id, "doc_state": doc_state,
            "already_existed": False, "facts_inserted": facts_inserted}
