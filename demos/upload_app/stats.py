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

# Живий лічильник часу відповіді чата. Шлях імпорту мусить бути ОДНАКОВИЙ тут
# і в chat_gradio/app.py: «import livemetrics» там і «from demos.upload_app
# import livemetrics» тут дали б два різні модулі з двома різними буферами --
# чат рахував би в один, сторінка читала б інший і завжди показувала нулі.
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from demos.upload_app import livemetrics  # noqa: E402

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
    # `extracted` -- те, що ставить завантажувач: текст витягнуто, факти
    # записані. Його НЕ БУЛО в цьому словнику, і аудит 27.08 це показав: у
    # базі всі документи мають саме цей статус, тобто розкладка за статусом
    # віддавала б код латиницею, якби її показували. Словник підписів
    # мусить знати кожен код, який СПРАВДІ є в базі, а не лише ті, які ми
    # передбачили.
    "extracted": "витягнуто",
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
    # Штатка. Її не було в словнику, тому в рядку «Типи документів» на екрані
    # друкувалось `staffing 1` -- латиницею, серед українських підписів.
    "staffing": "штатна книжка",
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
        # Живі числа чата: беруться з пам'яті ТОГО САМОГО процесу, у якому
        # працює чат (апка й чат -- один процес, чат змонтований у FastAPI).
        # Ніякого запису на диск і в базу: база -- зона Андрія й для нас
        # тільки на читання.
        "chat_live": livemetrics.snapshot(),
        # Сталі числа внутрішнього заміру: маршрутизація і звірка каталогу.
        "chat_quality": chat_quality(),
        # Замір Андрія (база й нормативний пошук) -- окремим розділом і з
        # окремим підписом: знаменники в нас різні НАВМИСНО, і змішувати їх
        # означало б зробити обидва числа непояснюваними.
        "partner_metrics": partner_metrics(),
        # Перетини відсутностей однієї особи. Окремо від лічильників
        # навмисно: недоступна база тут мусить дати «не зміряно», а
        # не нуль.
        "conflicts": conflicts(),
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

# ── Сталі метрики якості ЧАТА (а не пайплайна) ──────────────────────────────
#
# Пайплайн міряється часткою правильно витягнутих полів. Для чата це не
# годиться: він нічого не витягує, він ВІДПОВІДАЄ. Тому дві окремі мірки, і
# кожна відповідає на своє питання:
#
#   1. чи правильно ЗРОЗУМІЛИ питання -- measure_router по router_testset.yaml
#      («питання -> очікуваний шаблон»). Головне тут не «скільком відповіли»,
#      а скільком узяли ВПЕВНЕНО-НЕПРАВИЛЬНО: впевнена помилка гірша за
#      фолбек у модель, бо фолбек усе одно дасть правильну відповідь;
#   2. чи правильне ЧИСЛО у відповіді -- verify_catalog: для кожного шаблону
#      цифра з бази звіряється з незалежним підрахунком по .md-записах
#      пайплайна. Це і є «повноцінно правильна відповідь» у найсуворішому
#      сенсі: не «схоже», а те саме число.
#
# Третя мірка, про яку просила Аня -- «% підходящих цитат» із нормативних
# актів (ланцюг Андрія: підрядок + перекриття лем). Вона ще НЕ рахується, і
# сторінка каже про це прямо, а не показує прочерк без пояснення: доступ до
# схеми `andriy_test` для `milidoc_readonly` ще не виданий.
#
# Файли звітів лежать у data/eval/ і їдуть у git навмисно: цифра, яка живе
# лише в чиємусь локальному прогоні, нічого не доводить.
ROUTER_REPORT = os.path.join(PROJECT_ROOT, "data", "eval",
                             "router-report.json")
CATALOG_REPORT = os.path.join(PROJECT_ROOT, "data", "eval",
                              "catalog-report.json")

#: Пакет метрик Андрія (зона бази й нормативного пошуку). Формат домовлений:
#: плоский масив із полями `name`, `value`, `of`, `unit`, `as_of`, `how`.
PARTNER_METRICS = os.path.join(PROJECT_ROOT, "data", "eval",
                               "andriy-metrics.json")

#: Метрики, які ДУБЛЮЮТЬ живі числа розділу «База зараз». Показувати їх
#: окремими плитками означало б покласти на один екран два числа про одне й
#: те саме -- і одне з них застаріле. Приклад із пакета 28.08: «охоплення:
#: нормативних актів у корпусі — 41», тоді як у базі вже 44.
#:
#: Тому вони не викидаються, а ЗГОРТАЮТЬСЯ: сторінка каже, скільком метрик
#: пакета відповідають живі числа вище. Ховати замір партнера не можна, але й
#: показувати застарілий знімок поруч із живим — теж.
PARTNER_FOLDED_PREFIX = "охоплення:"

#: Чому цитати нормативки поки не рахуються. Текст іде на екран як є.
QUOTES_BLOCKED = ("ланцюг нормативки ще не підключений: потрібен доступ "
                  "milidoc_readonly до схеми andriy_test")


def _read_json(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        # Немає звіту -- це не помилка сторінки, а відсутність заміру.
        # Показуємо прочерк і кажемо, чим його заповнити.
        return None


# ── Перетини відсутностей: плитка «Конфлікти» ────────────────────────
#
# Пропозиція Андрія 28.08 («створити на сторінці статистика нову штучку --
# конфлікти, і там писати їхню кількість»), погоджена Анею того ж дня.
#
# ЩО САМЕ ВВАЖАЄТЬСЯ ПЕРЕТИНОМ, і чому це не те саме, що «конфлікт»:
#
# Перетин -- це два ПІДТВЕРДЖЕНІ факти відсутності ОДНІЄЇ особи, чиї періоди
# накладаються. Дві умови тут несуть усю вагу:
#
#   «однієї особи» -- бо різні люди одночасно у відпустці це норма, а без цієї
#                     умови плитка показала б сотні «конфліктів» на штатці зі
#                     153 людей і зробилась би шумом за перший день;
#   «підтверджені» -- бо чернетка ≠ факт: непевний факт у підрахунки не
#                     входить, доки людина його не бачила.
#
# А «конфлікт» -- це вже висновок, і він не завжди правдивий: у базі на 28.08
# рівно дві пари, і одна з них ЗАКОННА (наказ №118 анульований, №131 виданий
# замість нього). Тому плитка називає перше число «пар, що перетинаються», а
# друге -- скільком із них записана підстава заміни. Друге зараз нуль, бо
# `documents.superseded_by_doc_id` не заповнюється, хоча схема витягу має
# `supersedes_document_number`. Тобто плитка заодно робить видимою цю дірку --
# і це навмисно: доки її не видно, законна заміна й справжня суперечність
# виглядають зовні однаково.
#
# ПІБ у переліку є. Це рішення Ані 28.08 дослівно: «чат і так функціонує ДЛЯ
# ТОГО щоб буквально надавати особисту інформацію. це ок -- все синтетичне».
# Обмеження в нас не на сторінку, а на СПРАВЖНІ дані, і воно лишається
# правилом репозиторію.

#: Виміри відсутності беремо з режиму створення документа, а НЕ повторюємо
#: переліком тут. Дві копії означали б, що плитка й фіча колись розійдуться в
#: тому, що вважають конфліктом -- і розійдуться тихо.
def _absence_dims():
    if CHAT_DIR not in sys.path:
        sys.path.insert(0, CHAT_DIR)
    from chat_gradio import docgen  # noqa: PLC0415
    return docgen._ABSENCE_DIMS


try:
    ABSENCE_DIMS = _absence_dims()
except Exception:                                  # noqa: BLE001
    #: Режим створення зламався -> плитка не мусить валити сторінку. Перелік
    #: тут відкатний і навмисно ідентичний тому, що в docgen: якщо вони
    #: розійдуться, це зловить `test_conflicts_tile.py`.
    ABSENCE_DIMS = ["leave", "deployment_location"]

#: Скільком парам показувати подробиці. Перелік розкривається під плиткою (без
#: окремої сторінки -- рішення Ані), і довгий список там нічого не додасть:
#: якщо пар стане більше двадцяти, це вже не «подивитись», а робота в базі.
CONFLICT_ROWS_SHOWN = 20

#: «Підтверджений» тут НЕ вживається навмисно, і це не стилістика:
#: на цій сторінці слово читається як «людина підтвердила», а в базі
#: немає ні confirmed_by, ні confirmed_at. Те саме обмеження вже
#: прибрало з неї три інші твердження 27.08, і воно стосується цієї
#: плитки так само -- зловив `test_removed_claims_stay_removed`.
CONFLICTS_HOW = ("перетин періодів двох фактів відсутності однієї особи "
                 "(відпустка або відрядження), що входять у підрахунки; "
                 "запит по базі в момент відкриття сторінки")


def conflicts(dsn=None):
    """Пари відсутностей однієї особи, що перетинаються. Винятків не кидає.

    -> {"pairs": N|None, "with_supersession": N|None, "rows": [...],
        "how": текст, "error": текст|None}

    `pairs is None` означає «НЕ ЗМІРЯНО», і це не те саме, що нуль. Нуль тут --
    найдорожча брехня на цій сторінці: він читається як «усе чисто», тобто
    рівно навпаки до правди про недоступну базу.

    `dsn` -- лише для тестів (перевірка поведінки при недоступній базі).
    Бойовий шлях свого DSN не має: він той самий, що в чата, read-only.

    ЗАПИТ ТІЛЬКИ ЧИТАЄ. База -- зона Андрія; на це є окремий тест, який
    шукає в тексті цієї функції INSERT/UPDATE/DELETE.
    """
    sql = """
        SELECT p.last_name, p.first_name, p.patronymic, p.service_id,
               da.code AS dim_a, db_.code AS dim_b,
               a.valid_from AS from_a, a.valid_to AS to_a,
               b.valid_from AS from_b, b.valid_to AS to_b,
               a.source_doc_id AS doc_a_id, b.source_doc_id AS doc_b_id,
               (SELECT btrim(n.value) FROM facts n
                  JOIN dimensions nd ON nd.id = n.dimension_id
                 WHERE n.source_doc_id = a.source_doc_id
                   AND nd.code = 'document_number' LIMIT 1) AS number_a,
               (SELECT btrim(n.value) FROM facts n
                  JOIN dimensions nd ON nd.id = n.dimension_id
                 WHERE n.source_doc_id = b.source_doc_id
                   AND nd.code = 'document_number' LIMIT 1) AS number_b,
               (da.id <> db_.id) AS cross_kind,
               (doc_a.superseded_by_doc_id IS NOT NULL
                OR doc_b.superseded_by_doc_id IS NOT NULL) AS supersession
          FROM facts a
          JOIN facts b ON a.object_id = b.object_id AND a.id < b.id
          JOIN dimensions da ON da.id = a.dimension_id
          JOIN dimensions db_ ON db_.id = b.dimension_id
          JOIN people p ON p.object_id = a.object_id
          LEFT JOIN documents doc_a ON doc_a.id = a.source_doc_id
          LEFT JOIN documents doc_b ON doc_b.id = b.source_doc_id
         WHERE da.code = ANY(%(dims)s) AND db_.code = ANY(%(dims)s)
           AND a.status = 'confirmed' AND b.status = 'confirmed'
           AND a.valid_from IS NOT NULL AND b.valid_from IS NOT NULL
           AND a.valid_from <= COALESCE(b.valid_to, b.valid_from)
           AND b.valid_from <= COALESCE(a.valid_to, a.valid_from)
         ORDER BY p.last_name, a.valid_from
    """
    out = {"pairs": None, "with_supersession": None, "rows": [],
           "how": CONFLICTS_HOW, "error": None}
    try:
        if dsn:
            import psycopg                         # noqa: PLC0415
            from psycopg.rows import dict_row      # noqa: PLC0415

            def query(q, params=None):
                with psycopg.connect(dsn + " connect_timeout=1",
                                     row_factory=dict_row,
                                     autocommit=True) as conn:
                    with conn.cursor() as cur:
                        cur.execute(q, params)
                        return cur.fetchall()
        else:
            query = _chat_query()
        rows = query(sql, {"dims": ABSENCE_DIMS})
    except Exception as exc:                        # noqa: BLE001
        out["error"] = str(type(exc).__name__) + ": " + str(exc)
        return out

    labels = {"leave": "відпустка", "deployment_location": "відрядження"}
    out["pairs"] = len(rows)
    out["with_supersession"] = sum(1 for r in rows if r.get("supersession"))
    for r in rows[:CONFLICT_ROWS_SHOWN]:
        name = " ".join(x for x in (r.get("last_name"), r.get("first_name"),
                                    r.get("patronymic")) if x)
        out["rows"].append({
            "person": name or "—",
            #: Готова фраза, а не два слова й сполучник у JS. Причина --
            #: переклад: комбінацій усього три, і кожна перекладається як
            #: ціла фраза, тоді як склейка «X і Y» дала б у словнику ключ
            #: «і», який не є одиницею перекладу.
            "kinds": _kinds_phrase(labels.get(r.get("dim_a"), "відсутність"),
                                   labels.get(r.get("dim_b"), "відсутність")),
            "cross_kind": bool(r.get("cross_kind")),
            "period_a": _period(r.get("from_a"), r.get("to_a")),
            "period_b": _period(r.get("from_b"), r.get("to_b")),
            "number_a": r.get("number_a") or "без номера",
            "number_b": r.get("number_b") or "без номера",
            "supersession": bool(r.get("supersession")),
        })
    if out["pairs"] > CONFLICT_ROWS_SHOWN:
        #: Обрізання НЕ мовчазне: сторінка мусить сказати, скільком парам вона
        #: подробиць не показала. Тихе обрізання читається як «оце все».
        out["truncated"] = out["pairs"] - CONFLICT_ROWS_SHOWN
    return out


def _kinds_phrase(a, b):
    """«відпустка двічі», «відпустка і відрядження». -> рядок.

    Комбінацій рівно три, і всі три перелічені в словнику перекладу:
    сторінка мусить бути англійською цілком, а не наполовину.
    """
    return a + " двічі" if a == b else a + " і " + b


def _period(a, b):
    """Період словами для сторінки. Один день -- одна дата, не «з X по X»."""
    if not a:
        return "—"
    a = a.isoformat() if hasattr(a, "isoformat") else str(a)
    if not b:
        return "з " + a + ", без дати завершення"
    b = b.isoformat() if hasattr(b, "isoformat") else str(b)
    return a if a == b else a + " — " + b


def partner_metrics(path=None):
    """Пакет метрик Андрія для сторінки. Винятків не кидає НІКОЛИ.

    Домовленість про формат була одна й головна: у кожної метрики мусить бути
    **`how`** -- чим саме її зміряно. Без цього поля цифру не можна показати,
    бо правило продукту -- «цифра без джерела не показується», і саме на цьому
    нас уже ловив Денис. Тому метрика без `how` НЕ йде на сторінку, і про це
    сказано числом: тихо викинути замір партнера гірше, ніж показати, що з ним
    не так.

    -> {"shown": [...], "folded": [...], "dropped": N, "error": текст|None}
    """
    raw = _read_json(path or PARTNER_METRICS)
    out = {"shown": [], "folded": [], "dropped": 0, "error": None}
    if raw is None:
        out["error"] = ("пакета метрик немає: очікується "
                        + os.path.relpath(path or PARTNER_METRICS,
                                          PROJECT_ROOT).replace("\\", "/"))
        return out
    if not isinstance(raw, list):
        out["error"] = "пакет метрик має бути плоским масивом"
        return out
    for item in raw:
        if not isinstance(item, dict):
            out["dropped"] += 1
            continue
        name = (item.get("name") or "").strip()
        how = (item.get("how") or "").strip()
        if not name or item.get("value") is None or not how:
            out["dropped"] += 1
            continue
        rec = {"name": name, "value": item.get("value"),
               "of": item.get("of"), "unit": (item.get("unit") or "").strip(),
               "as_of": (item.get("as_of") or "").strip(), "how": how}
        if name.lower().startswith(PARTNER_FOLDED_PREFIX):
            out["folded"].append(rec)
        else:
            out["shown"].append(rec)
    return out


def chat_quality():
    """Сталі числа якості чата. Винятків не кидає: відсутність файла -- поле
    відповіді, а не падіння сторінки."""
    router = _read_json(ROUTER_REPORT)
    catalog = _read_json(CATALOG_REPORT)
    out = {"router": None, "catalog": None,
           "quotes": {"blocked": QUOTES_BLOCKED}}
    if router:
        total = router.get("questions") or 0
        routed = router.get("routed") or 0
        out["router"] = {
            "questions": total,
            "routed": routed,
            "routed_ok": router.get("routed_ok"),
            "confidently_wrong": router.get("confidently_wrong"),
            # Частка ПРАВИЛЬНИХ серед тих, які ярус узяв на себе. Знаменник
            # саме `routed`, а не всі питання: решта свідомо йде в модель, і
            # рахувати її як помилку маршрутизатора нечесно.
            "pct": (round(100.0 * router["routed_ok"] / routed, 1)
                    if routed and router.get("routed_ok") is not None
                    else None),
            # Чесний замір ЦЬОГО ярусу -- «продовий вид»: лише питання, які
            # НЕ ловлять правила, бо вектори стоять після правил і решти
            # питань просто не бачать. Прапорець їде на сторінку, щоб підпис
            # називав правильний знаменник.
            "production_view": bool(router.get("production_view")),
            "encoder": router.get("encoder"),
            "threshold": router.get("threshold"),
            "measured_at": router.get("measured_at"),
        }
    if catalog:
        checks = catalog.get("checks") or 0
        out["catalog"] = {
            "checks": checks,
            # Розкол за видом: скільком перевірок порівнюють ЧИСЛО, а скільком
            # лише виконання запиту чи текст відмови. Без цього підпис
            # обіцяв більше, ніж прилад міряє.
            "checks_compare": catalog.get("checks_compare"),
            "checks_execute": catalog.get("checks_execute"),
            "checks_blocked": catalog.get("checks_blocked"),
            "matched": catalog.get("matched"),
            "failures": catalog.get("failures"),
            "pct": (round(100.0 * catalog["matched"] / checks, 1)
                    if checks and catalog.get("matched") is not None
                    else None),
            "as_of": catalog.get("as_of"),
            "measured_at": catalog.get("measured_at"),
            # Причина відомих розбіжностей, якщо вона відома. Їде разом із
            # числом: 85% без причини читаються як якість продукту.
            "note": catalog.get("note"),
        }
    return out

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

