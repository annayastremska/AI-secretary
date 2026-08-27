"""Цифри для сторінки «Статистика» (задача B2 плану готовності до демо).

Нащо це існує
-------------
П'ята річ, яку демо мусить уміти: «показати цифри якості -- скільком
документам що зробили». Досі відповідь на «де цифри» була «у консолі»:
`run_pipeline.py` пише `run-report.json`, база знає свої лічильники, а в
інтерфейсі не було ні того, ні того.

Два правила, які цей модуль тримає жорстко
------------------------------------------
1. **Чернетка ≠ факт.** Підтверджені факти й чернетки віддаються ОКРЕМИМИ
   числами, і ніде не складаються в одне. `facts.total` -- це «скільки рядків
   у базі», а не «скільки фактів можна рахувати»; саме тому воно окремо
   підписане і саме тому є `facts.confirmed`. Те саме з чергою рев'ю:
   «чекає підтвердження» -- окреме число, не частина «зроблено».
2. **Немає бази -- так і сказати.** Жодного винятку нагору: `collect()`
   завжди повертає словник, а недоступність бази -- це поле
   `db_available: false` + причина. Сторінка мусить показати «база
   недоступна», а не 500.

Звідки читаємо
--------------
Тим самим read-only способом, що чат: `chat_gradio/db.py::_query` (окремий
read-only користувач + `default_transaction_read_only=on` +
`statement_timeout=5000` у DSN). Свого підключення тут немає навмисно --
інакше в проєкті було б два місця, де описано, як ходити в базу.

SQL -- форми, вже звірені з живою базою у `query_catalog.yaml`
(`documents_count`, `unconfirmed_count`, `review_queue_count`,
`failed_docs_count`), тому окремої звірки вони не потребують: та сама
таблиця, та сама умова.
"""
import datetime
import io
import json
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(APP_DIR))
CHAT_DIR = os.path.join(APP_DIR, "chat_gradio")

# Лічильники: ключ -> (SQL, як розібрати рядки). Кожен окремим запитом --
# один недоступний лічильник не мусить забирати з собою решту сторінки.
SQL_DOCS_BY_STATUS = ("SELECT status, COUNT(*) AS n FROM documents "
                      "GROUP BY status ORDER BY status")
SQL_DOCS_BY_DOMAIN = ("SELECT domain, COUNT(*) AS n FROM documents "
                      "GROUP BY domain ORDER BY domain")
SQL_FACTS_BY_STATUS = ("SELECT status, COUNT(*) AS n FROM facts "
                       "GROUP BY status ORDER BY status")
SQL_PEOPLE = ("SELECT COUNT(*) AS n FROM objects o "
              "JOIN object_kinds k ON k.id = o.kind_id "
              "WHERE k.code = 'person'")
SQL_REVIEW_OPEN = ("SELECT rq.queue_type, COUNT(*) AS n FROM review_queue rq "
                   "WHERE rq.resolved_at IS NULL "
                   "GROUP BY rq.queue_type ORDER BY rq.queue_type")

#: Скільки осіб у реєстрі ще НЕ знайдено відповідника у штатці.
#:
#: Навіщо окремо. Аня 26.08: «134 «нова особа» на 300 осіб -- це багато,
#: зʼясуй». Зʼясувалось: 133 із цих завдань створені 24.08, коли ми заливали
#: документи, а штатки в базі ще не було. Це ЖУРНАЛ моменту, коли система
#: вперше побачила прізвище, а не перелік невідомих людей.
#:
#: Після заливки штатки (25.08) майже всі ті особи зійшлися з нею -- без
#: `service_id` лишилось РІВНО ТРИ. Тому на сторінці треба показувати три, а
#: не 134: інакше цифра лякає і не означає нічого.
#:
#: Закрити старі завдання (`resolved_at`) -- запис у базу, тобто зона Андрія.
SQL_PEOPLE_UNMATCHED = ("SELECT COUNT(*) AS n FROM people "
                        "WHERE service_id IS NULL")

#: Скільки ДОКУМЕНТІВ чекає ручної перевірки.
#:
#: Питання Ані 26.08: «цифри не сходяться, або немає каунтера документів на
#: ручну перевірку». Другого не було: сторінка показувала 162 ЗАВДАННЯ, а
#: завдань на один документ буває кілька (нове прізвище + непевне поле), і
#: 133 із них узагалі створені до появи штатки. Тобто з журналу завдань
#: неможливо було зрозуміти головне: скільком папок людині відкрити.
SQL_DOCS_PENDING = ("SELECT COUNT(DISTINCT rq.document_id) AS n "
                    "FROM review_queue rq "
                    "WHERE rq.resolved_at IS NULL "
                    "  AND rq.document_id IS NOT NULL")

#: Те саме, але БЕЗ старих відміток «нове прізвище».
#:
#: Заміряно 26.08: документів у черзі 142, а якщо не рахувати `new_person` --
#: 28. Різниця не косметична: 133 відмітки «нове прізвище» створені 24.08, коли
#: штатки в базі ще не було, і після її заливки ті особи з нею зійшлися. Тобто
#: 142 -- це переважно журнал старого моменту, а 28 -- документи, у яких справді
#: є що дивитися людині. Показуємо обидва числа: одне без другого вводить в
#: оману в обидві сторони.
SQL_DOCS_PENDING_SUBSTANTIVE = ("SELECT COUNT(DISTINCT rq.document_id) AS n "
                                "FROM review_queue rq "
                                "WHERE rq.resolved_at IS NULL "
                                "  AND rq.document_id IS NOT NULL "
                                "  AND rq.queue_type <> 'new_person'")

# Підписи станів і черг українською. Код лишається кодом (він у базі), але на
# сторінці людина мусить читати слова, а не `unknown_type`.
DOC_STATUS_LABELS = {
    "processed": "оброблено",
    "confirmed": "підтверджено",
    "needs_review": "потребує перевірки",
    "unresolved": "не розпізнано",
    "failed": "збій обробки",
}
FACT_STATUS_LABELS = {
    "confirmed": "підтверджені",
    "unconfirmed": "чернетки (у підрахунки не входять)",
    "rejected": "відхилені",
}
QUEUE_LABELS = {
    "unknown_type": "не впізнано шаблон бланка",
    "unconfirmed_fact": "бракує даних у полях",
    "new_person": "нова особа в реєстрі",
    "qa_sample": "вибірковий контроль",
}
DOMAIN_LABELS = {
    "leave": "відпустки",
    "deployment": "відрядження",
    "normative": "нормативні акти",
}


# Скільки чекати САМЕ З'ЄДНАННЯ. Заміряно 25.08 локально (бази немає):
# DSN чата не має connect_timeout, тому psycopg перебирає ::1 і 127.0.0.1 і
# віддає ConnectionTimeout лише через ~4 хвилини -- сторінка статистики за
# цей час виглядає як зависла, а на демо це та сама поломка, яку ми лікуємо
# індикаторами. Сторінка мусить сказати «база недоступна» за секунди.
# Виконання запитів обмежене окремо -- statement_timeout=5000 у DSN чата.
CONNECT_TIMEOUT_S = 3


def _chat_query():
    """Read-only доступ ТИМ САМИМ способом, що чат: DSN бере
    `chat_gradio/db.py::_dsn` (той самий read-only користувач, той самий
    `default_transaction_read_only=on`, той самий `statement_timeout`).
    Свого DSN тут немає навмисно.

    Єдина добавка -- `connect_timeout`: сторінці потрібна ШВИДКА відмова, а
    не чекання. db.py не чіпаємо (чужа зона правок цієї хвилі), тому добавка
    живе тут, у рядку підключення.

    Імпорт лінивий і саме коротким іменем `db`: chat_gradio кладе свою теку в
    sys.path і імпортує сусідів так само, тож ми отримуємо ТОЙ САМИЙ модуль,
    а не другий його екземпляр.
    """
    if CHAT_DIR not in sys.path:
        sys.path.insert(0, CHAT_DIR)
    import db  # noqa: PLC0415 -- лінивий навмисно: psycopg тягнеться при імпорті
    import psycopg
    from psycopg.rows import dict_row

    dsn = f"{db._dsn()} connect_timeout={CONNECT_TIMEOUT_S}"

    def query(sql, params=None):
        with psycopg.connect(dsn, row_factory=dict_row,
                             autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or {})
                return cur.fetchall()

    return query


def default_report_path(config_path=None):
    """Де лежить `run-report.json`. Тека виходу -- з профілю пайплайна, який
    читає апка (`paths.output_dir`), бо профілів кілька і в них РІЗНІ теки
    (data/output проти data/output-demo). Не читається -- дефолт data/output."""
    config_path = config_path or os.environ.get(
        "APP_PIPELINE_CONFIG", os.path.join(APP_DIR, "config-app.yaml"))
    if not os.path.isabs(config_path):
        config_path = os.path.join(PROJECT_ROOT, config_path)
    output_dir = "data/output"
    try:
        import yaml
        with open(config_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        output_dir = (cfg.get("paths") or {}).get("output_dir") or output_dir
    except Exception:
        pass
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(PROJECT_ROOT, output_dir)
    return os.path.join(output_dir, "run-report.json")


def _rows_to_map(rows, key):
    """[{key: 'leave', 'n': 3}] -> {'leave': 3}. None-ключ -> '(не вказано)':
    порожній домен -- це теж стан, і ховати його не можна."""
    out = {}
    for row in rows:
        name = row.get(key)
        out["(не вказано)" if name in (None, "") else str(name)] = int(row["n"])
    return out


def db_counters(query=None):
    """Лічильники бази. Повертає (дані, помилка): помилка -- рядок для людини,
    не виняток. Один непрочитаний лічильник не валить сторінку -- він просто
    приходить як None (сторінка покаже «—»)."""
    try:
        query = query or _chat_query()
    except Exception as exc:                       # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"

    def ask(sql, key=None):
        rows = query(sql)
        if key is None:
            return int(rows[0]["n"]) if rows else 0
        return _rows_to_map(rows, key)

    try:
        docs_by_status = ask(SQL_DOCS_BY_STATUS, "status")
        docs_by_domain = ask(SQL_DOCS_BY_DOMAIN, "domain")
        facts_by_status = ask(SQL_FACTS_BY_STATUS, "status")
        people = ask(SQL_PEOPLE)
        review = ask(SQL_REVIEW_OPEN, "queue_type")
        unmatched = ask(SQL_PEOPLE_UNMATCHED)
        docs_pending = ask(SQL_DOCS_PENDING)
        docs_pending_sub = ask(SQL_DOCS_PENDING_SUBSTANTIVE)
    except Exception as exc:                       # noqa: BLE001
        # Найчастіша причина -- база просто не піднята (ConnectionTimeout) або
        # немає read-only пароля в .env. Обидві -- «недоступна», не «нуль».
        return None, f"{type(exc).__name__}: {exc}"

    return {
        "documents": {
            "total": sum(docs_by_status.values()),
            "by_status": docs_by_status,
            "by_domain": docs_by_domain,
            "failed": docs_by_status.get("failed", 0),
        },
        "facts": {
            # rows_total -- саме «рядків у таблиці», НЕ «фактів для
            # підрахунків»: у ньому лежать і чернетки, і відхилені. Назва
            # навмисно не «total», щоб її не поставили в UI як підсумок.
            "rows_total": sum(facts_by_status.values()),
            "confirmed": facts_by_status.get("confirmed", 0),
            "unconfirmed": facts_by_status.get("unconfirmed", 0),
            "rejected": facts_by_status.get("rejected", 0),
            "by_status": facts_by_status,
        },
        "people": people,
        "review_queue": {
            "open_total": sum(review.values()),
            "by_type": review,
            # Скільки осіб ще немає відповідника у штатці. Саме це число
            # означає «справді невідомі», а `open_total` -- журнал завдань,
            # більшість яких створена до появи штатки.
            "people_unmatched": unmatched,
            # Скільки ДОКУМЕНТІВ чекає людини. Це не те саме, що кількість
            # завдань: на один документ їх буває кілька.
            "documents_pending": docs_pending,
            "documents_pending_substantive": docs_pending_sub,
        },
    }, None


def read_run_report(path=None):
    """Останній звіт прогону -- як його написав pipeline/run_report.py. Файлу
    немає (прогону ще не було) -- це не помилка, а стан: (None, None)."""
    path = path or default_report_path()
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except Exception as exc:                       # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def collect(query=None, report_path=None):
    """Усе, що показує сторінка. Винятків не кидає НІКОЛИ: недоступність бази
    або звіту -- це поля відповіді."""
    counters, db_error = db_counters(query)
    report, report_error = read_run_report(report_path)
    path = report_path or default_report_path()
    out = {
        "collected_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "db_available": counters is not None,
        "db_error": db_error,
        "labels": {
            "doc_status": DOC_STATUS_LABELS,
            "fact_status": FACT_STATUS_LABELS,
            "queue": QUEUE_LABELS,
            "domain": DOMAIN_LABELS,
        },
        "quality": quality_metrics()[0],
        "quality_error": quality_metrics()[1],
        "run_report": report,
        "run_report_error": report_error,
        "run_report_path": os.path.relpath(path, PROJECT_ROOT).replace("\\", "/"),
        "run_report_mtime": (
            datetime.datetime.fromtimestamp(os.path.getmtime(path))
            .isoformat(timespec="seconds") if os.path.exists(path) else None),
    }
    out.update(counters or {})
    return out

# ── Виміряна якість пайплайна ────────────────────────────────────────────────
#
# Джерело -- `eval/baseline.json`, той самий файл, проти якого щодня йде
# перевірка «не ламає». Він У РЕПО навмисно: цифра, яка лежить у чиємусь
# локальному звіті, нічого не доводить, а ця видна в історії коду.
#
# Чому саме ці числа. Аня 25.08: на сторінці має бути «якась виміряна
# успішність пайплайну -- метрики». Найчесніша така метрика в нас одна: частка
# полів, витягнутих ПРАВИЛЬНО проти заздалегідь відомої відповіді, на корпусах,
# де ця відповідь є. Усе інше (скільки документів, скільки фактів) -- це обсяг,
# не якість.

BASELINE_PATH = os.path.join(PROJECT_ROOT, "eval", "baseline.json")

#: Корпуси з еталоном -> як їх називати людині.
_CORPORA = {
    "leave": "відпустки (docx)",
    "leave_pdf": "відпустки (pdf)",
    "deployment": "відрядження (docx)",
    "deployment_pdf": "відрядження (pdf)",
    "story": "демо-історія (docx)",
    "story_pdf": "демо-історія (pdf)",
}


def quality_metrics(path=None):
    """-> (метрики, помилка). Помилка -- рядок для людини, не виняток."""
    path = path or BASELINE_PATH
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"

    m = data.get("measurements") or {}
    fields_ok = fields_total = docs = 0
    per_corpus = {}
    for key, label in _CORPORA.items():
        entry = m.get(key) or {}
        ok, total = entry.get("fields_ok"), entry.get("fields_total")
        if ok is None or not total:
            continue
        fields_ok += ok
        fields_total += total
        docs += entry.get("documents") or 0
        per_corpus[label] = {"ok": ok, "total": total}

    normative = m.get("normative") or {}
    tests = m.get("tests") or {}
    return {
        # ГОЛОВНА цифра сторінки: частка полів, витягнутих правильно проти
        # заздалегідь відомої відповіді.
        "fields_ok": fields_ok,
        "fields_total": fields_total,
        "fields_pct": (round(100.0 * fields_ok / fields_total, 1)
                       if fields_total else None),
        "documents": docs,
        "per_corpus": per_corpus,
        "normative_ok": normative.get("confirmed_normative"),
        "normative_total": normative.get("documents"),
        # Тести пайплайна й приладу: з БАЗОВОЇ ЛІНІЇ, тобто рівно ті
        # цифри, проти яких щодня йде перевірка «не ламає». Знаменник
        # обовʼязковий: «296 проходить» без «із 297» -- це число без
        # сенсу (Аня 26.08 спитала саме це).
        # xfail -- очікуваний провал: тест, який навмисно фіксує ще не
        # зроблене. Він НЕ падіння, але й не успіх, тому окремо.
        "tests_passed": tests.get("passed"),
        "tests_failed": tests.get("failed"),
        "tests_xfailed": tests.get("xfailed"),
        "tests_total": (sum(v for v in (tests.get("passed"),
                                        tests.get("failed"),
                                        tests.get("xfailed"))
                            if v) or None),
        "measured_at": (datetime.datetime.fromtimestamp(os.path.getmtime(path))
                        .replace(microsecond=0).isoformat()
                        if os.path.exists(path) else None),
    }, None

