# джерело: answer/chat@andriy-followup-context, адаптація під Postgres.
#
# Сім функцій стику (docs/contracts/2026-08-14_chat-db-interface.md) -- ті самі
# назви, аргументи і ключі, що в SQLite-реалізації Дена/Колі, але всередині --
# наша РЕАЛЬНА Postgres (documents/objects/facts/dimensions). SQL-и звірені з
# demos/upload_app/query_catalog.yaml (вони вже перевірені проти живої бази).
#
# Правила стику, які тримає цей файл (з контракту):
#   - функція повертає дані, не текст для людини
#   - нічого не знайшли → [] (не None, не виняток)
#   - дати — рядки YYYY-MM-DD, як лежать у базі
#   - порожні поля віддаються порожніми, нічого не підставляється
#
# Правило продукту поверх контракту: підрахунки — ЛИШЕ facts.status =
# 'confirmed'; непідтверджені віддаються окремим викликом (confirmed=False),
# щоб чат показав їх окремим числом, а не змішав.
#
# Мапа «рядок відсутності» на нашу схему:
#   один рядок = один факт виміру leave / deployment_location (це і є
#   «документ про відсутність»), збагачений фактами document_number /
#   document_date того ж source_doc_id. Ключі словника — як у контракту.
#
# Що наша схема НЕ покриває (чесно, без обходів):
#   - підрозділи: з 25.08 зв'язок Є (штатка Андрія, вимір `subdivision`);
#     доти його не було (db/README_for_chatbot_team.md
#     п.8). Тепер і find_people(subdivision=...), і
#     count_absent_by_subdivision() рахують по-справжньому;
#   - superseded_by (скасування документом): у схемі немає -- завжди None;
#   - reference_docs: нормативних розділів немає; search_reference шукає FTS по
#     documents.text_content з domain='normative' -- таких документів у базі
#     поки нуль, тож повертається [] і чат чесно відмовляє.

import os
import re

import psycopg
from psycopg.rows import dict_row

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(os.path.dirname(APP_DIR))

# Виміри, які означають «людина поза частиною»
ABSENCE_DIMS = ["leave", "deployment_location"]
DOC_TYPE_BY_DIM = {"leave": "відпустка", "deployment_location": "відрядження"}
# facts.status -> статус документа мовою чату. Це різні осі (у стенді Дена
# «чинний/скасований» -- про документ, у нас confirmed/unconfirmed -- про
# довіру до факту), але для користувача правило одне: у підрахунок входить
# лише підтверджене.
# «ЧИННИЙ» ЗВІДСИ ПРИБРАНО (блок D, 28.08).
#
# п. 10 звіту Дениса: №118 анульований, №131 виданий замість нього -- а чат
# називав ЧИННИМИ обидва, і поточний стан брав із анульованого. Причина не в
# даних і не в підрахунку: `facts.status = 'confirmed'` означає «факт витягнуто
# впевнено», а слово «чинний» читається як «документ не скасовано». Це різні
# осі -- і про це стояв коментар у цьому ж файлі, поруч із мапою, яка їх
# змішувала.
#
# Осі анулювання в базі НЕМА: пайплайн її не витягує (питання від 14.08 без
# структурної відповіді, зона моя). Тому чат не може сказати «чинний» правдиво
# -- і не мусить казати цього взагалі, поки поля немає.
STATUS_LABEL = {"confirmed": "підтверджений",
                "unconfirmed": "не підтверджено (чернетка)",
                "rejected": "відхилений"}


# Умови підключення -- в одному місці (demos/upload_app/dbconn.py). Доти тут
# була власна копія `_read_env`/`_dsn`, і три копії вже розійшлися: у приладі
# звірки бракувало connect_timeout. Імпорт «двома шляхами» -- бо цей модуль
# живе і як частина пакета, і з `chat_gradio/` у sys.path (див.
# test_single_module_instance).
try:
    from .. import dbconn
except ImportError:  # pragma: no cover -- плаский запуск
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import dbconn


def _dsn():
    return dbconn.dsn(dbconn.STATEMENT_TIMEOUT_MS_CHAT)


def _query(sql, params=None):
    with psycopg.connect(_dsn(), row_factory=dict_row, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchall()


def _iso(d):
    return d.isoformat() if d is not None else ""


# Один запит збирає «рядок відсутності»: факт leave/deployment_location +
# номер і дата документа того ж source_doc_id (LATERAL, бо це окремі факти).
_ABSENCE_SELECT = """
SELECT o.canonical_name      AS person_name_raw,
       f.object_id           AS object_id,
       d.code                AS dim_code,
       f.value               AS reason,
       f.valid_from          AS date_from,
       f.valid_to            AS date_to,
       f.status              AS fact_status,
       f.source_doc_id       AS source_doc_id,
       dc.source_kind        AS source_kind,
       num.value             AS doc_number_val,
       dat.value             AS doc_date_val,
       place.value           AS place_val,
       ldays.value           AS leave_days_val,
       unit_rep.value        AS unit_to_report_val,
       act_ret.value         AS actual_return_val,
       dep_org.value         AS deployment_org_val,
       dep_purp.value        AS deployment_purpose_val,
       dep_days.value        AS deployment_days_val,
       ord_num.value         AS order_number_val,
       ord_date.value        AS order_date_val,
       trav_doc.value        AS travel_document_val
FROM facts f
JOIN dimensions d ON d.id = f.dimension_id
JOIN objects o ON o.id = f.object_id
JOIN documents dc ON dc.id = f.source_doc_id
LEFT JOIN LATERAL (
    SELECT f2.value FROM facts f2
    JOIN dimensions d2 ON d2.id = f2.dimension_id
    WHERE f2.source_doc_id = f.source_doc_id
      AND d2.code = 'document_number' LIMIT 1) num ON true
LEFT JOIN LATERAL (
    SELECT f2.value FROM facts f2
    JOIN dimensions d2 ON d2.id = f2.dimension_id
    WHERE f2.source_doc_id = f.source_doc_id
      AND d2.code = 'document_date' LIMIT 1) dat ON true
LEFT JOIN LATERAL (
    SELECT f2.value FROM facts f2
    JOIN dimensions d2 ON d2.id = f2.dimension_id
    WHERE f2.source_doc_id = f.source_doc_id
      AND d2.code IN ('leave_place', 'deployment_location')
      AND d2.code <> 'leave' LIMIT 1) place ON true
""" + "".join(
    # Решта вимірів документа, яких у відповіді не було НІКОЛИ (аудит 26.08).
    # Кожен -- окремий LATERAL із тим самим шаблоном, щоб не плодити руками
    # дев'ять однакових блоків і не забути один із них.
    f"""
LEFT JOIN LATERAL (
    SELECT f2.value FROM facts f2
    JOIN dimensions d2 ON d2.id = f2.dimension_id
    WHERE f2.source_doc_id = f.source_doc_id
      AND d2.code = '{code}' LIMIT 1) {alias} ON true"""
    for code, alias in (("leave_days", "ldays"),
                        ("unit_to_report", "unit_rep"),
                        ("leave_actual_return", "act_ret"),
                        ("deployment_org", "dep_org"),
                        ("deployment_purpose", "dep_purp"),
                        ("deployment_days", "dep_days"),
                        ("order_number", "ord_num"),
                        ("order_date", "ord_date"),
                        ("travel_document", "trav_doc"))
) + """
WHERE d.code = ANY(%(dims)s)
"""


def _absence_row(r):
    """Сирий рядок запиту -> словник за ключами контракту."""
    dim = r["dim_code"]
    return {
        "doc_number": f"№{r['doc_number_val'].strip()}" if r["doc_number_val"] else "",
        "doc_date": (r["doc_date_val"] or "").strip(),
        "doc_type": DOC_TYPE_BY_DIM.get(dim, dim),
        # service_id у стенді значив «підтверджено реєстром»; наш аналог
        # довіри -- facts.status. Порожній service_id => чат покаже позначку.
        "service_id": (f"ID-{r['object_id']}"
                       if r["fact_status"] == "confirmed" else ""),
        "person_name_raw": r["person_name_raw"] or "",
        # ЧИЯ це відсутність -- ключем, а не рядком.
        #
        # `object_id` у запиті був завжди, але назовні не виходив, тому «чий це
        # документ» доводилось з'ясовувати порівнянням ПІБ. А саме порівняння
        # ПІБ і є причиною п. 6 звіту Дениса: у картці однієї людини лежали
        # документи двох інших, бо всі троє мали спільний шматок імені.
        # Прилад, який міряє чистоту картки, мусить питати КЛЮЧ.
        "object_id": r["object_id"],
        "date_from": _iso(r["date_from"]),
        "date_to": _iso(r["date_to"]),
        "reason": (r["reason"] or "") if dim == "leave" else "",
        "place": (r["place_val"] or r["reason"] or ""),
        "status": STATUS_LABEL.get(r["fact_status"], r["fact_status"]),
        "fact_status": r["fact_status"],
        # Решта вимірів документа. Доти чат їх не показував НІДЕ, хоч вони в
        # базі є: тривалість, куди прибути, фактичне повернення, організація й
        # мета відрядження, наказ-підстава, проїзний документ.
        "leave_days": r["leave_days_val"] or "",
        "unit_to_report": r["unit_to_report_val"] or "",
        "actual_return": r["actual_return_val"] or "",
        "deployment_org": r["deployment_org_val"] or "",
        "deployment_purpose": r["deployment_purpose_val"] or "",
        "deployment_days": r["deployment_days_val"] or "",
        "order_number": r["order_number_val"] or "",
        "order_date": r["order_date_val"] or "",
        "travel_document": r["travel_document_val"] or "",
        "superseded_by": "",  # у схемі такого зв'язку немає
        "source_file": (f"запис №{r['source_doc_id']} у базі "
                        f"({r['source_kind']})"),
    }


def _rank_label(code):
    if not code:
        return ""
    rows = _query(
        "SELECT dv.label FROM dimension_values dv "
        "JOIN dimensions d ON d.id = dv.dimension_id "
        "WHERE d.code = 'rank' AND dv.value = %(v)s", {"v": code})
    return rows[0]["label"] if rows else code


def find_people(subdivision=None, name=None):
    """Люди з реєстру (objects kind=person + розширення people).

    subdivision: фільтр за підрозділом працює зі штатки (25.08) -- через
    вимір `subdivision`. Доти повертав [] і чат казав «база цього не знає».
    """
    sql = ("SELECT o.id AS object_id, o.canonical_name, p.service_id, "
           "rank_f.value AS rank_code, pos_f.value AS position_val, "
           "sub_f.value AS subdivision_val "
           "FROM objects o "
           "JOIN object_kinds k ON k.id = o.kind_id AND k.code = 'person' "
           "LEFT JOIN people p ON p.object_id = o.id "
           "LEFT JOIN LATERAL ("
           "  SELECT f.value FROM facts f "
           "  JOIN dimensions d ON d.id = f.dimension_id "
           "  WHERE f.object_id = o.id AND d.code = 'rank' "
           "    AND f.status = 'confirmed' "
           "  ORDER BY f.valid_from DESC NULLS LAST LIMIT 1) rank_f ON true "
           # Посада й підрозділ -- окремі виміри зі штатки. Доти вони не
           # витягались, і картка особи складалася з ПІБ і звання: на питання
           # «що відомо про Усика» це майже нічого (питання Ані 26.08).
           "LEFT JOIN LATERAL ("
           "  SELECT f.value FROM facts f "
           "  JOIN dimensions d ON d.id = f.dimension_id "
           "  WHERE f.object_id = o.id AND d.code = 'position' "
           "    AND f.status = 'confirmed' "
           "  ORDER BY f.valid_from DESC NULLS LAST LIMIT 1) pos_f ON true "
           "LEFT JOIN LATERAL ("
           "  SELECT f.value FROM facts f "
           "  JOIN dimensions d ON d.id = f.dimension_id "
           "  WHERE f.object_id = o.id AND d.code = 'subdivision' "
           "    AND f.status = 'confirmed' "
           "  ORDER BY f.valid_from DESC NULLS LAST LIMIT 1) sub_f ON true "
           "WHERE 1=1")
    params = {}
    if name:
        # МЕЖА СЛОВА, а не підрядок (див. `name_word_regex`). Якщо слово
        # закоротке для надійного пошуку -- НЕ шукаємо взагалі: порожній
        # результат честніший за пів реєстру.
        rx = name_word_regex(name)
        if rx is None:
            return []
        sql += " AND o.canonical_name ~* %(name)s"
        params["name"] = rx
    if subdivision:
        # той самий вимір, що й у фільтрі відсутностей, лише від o.id
        sql += (" AND EXISTS (SELECT 1 FROM facts sf "
                "  JOIN dimensions sd ON sd.id = sf.dimension_id "
                "                    AND sd.code = 'subdivision' "
                "  WHERE sf.object_id = o.id "
                "    AND sf.value ILIKE %(subdivision)s)")
        params["subdivision"] = _subdivision_pattern(subdivision)
    rows = _query(sql + " ORDER BY o.id", params)
    return [{
        # Ключ особи. Доти картка складалась і порівнювалась по ПІБ, тобто по
        # тому самому, що й ламається (п. 6-7 звіту). Прилад ідентифікації
        # порівнює `object_id`, а не рядок.
        "object_id": r["object_id"],
        "service_id": r["service_id"] or f"ID-{r['object_id']}",
        # service_id вище має підміну на ID-<object_id>, тому по ньому не
        # видно, чи людина зійшлася зі штаткою. Окремий прапорець -- щоб
        # картка особи могла це сказати (див. answer_person).
        "in_roster": bool(r["service_id"]),
        "full_name": r["canonical_name"],
        "rank": _rank_label(r["rank_code"]),
        "position_title": r["position_val"] or "",
        "subdivision": r["subdivision_val"] or "",
        "phone": "",            # телефонів у схемі немає
    } for r in rows]


#: Умова «особа з цього підрозділу». Штатку залив Андрій 25.08, і вимір
#: `subdivision` у базі є -- тому стара дорога більше НЕ мусить відмовляти.
#:
#: Знайдено глибоким аналізом 26.08: у цьому файлі три функції повертали [] із
#: коментарем «зв'язку особа->підрозділ немає», а чат казав «база цього не
#: знає». База знає. Такий текст -- не обережність, а неправда про власні дані:
#: він заперечує 300 рядків штатки. Шаблони каталогу ці питання зазвичай
#: перехоплюють, але «зазвичай» -- не гарантія: досить іншого формулювання, і
#: людина почує заперечення того, що система має.
_SUBDIVISION_FILTER = """
  AND EXISTS (SELECT 1 FROM facts sf
              JOIN dimensions sd ON sd.id = sf.dimension_id
                                AND sd.code = 'subdivision'
              WHERE sf.object_id = f.object_id
                AND sf.value ILIKE %(subdivision)s)
"""


#: Скільком літер дозволяємо дописати після основи слова. Відмінок української
#: забирає до трьох («Крижанівськ-ого»), тому чотири -- запас на один символ.
_INFLECTION = 4

#: Мінімальна довжина основи, за якою шукаємо. Коротше -- і «Бог» почне ловити
#: пів реєстру.
_MIN_STEM = 5


def name_word_regex(word):
    """Слово з питання -> регулярка Postgres, що ловить це слово в ПІБ ЦІЛИМ.

    НАВІЩО ЦЕ ВЗАГАЛІ ІСНУЄ. Доти пошук особи був `canonical_name ILIKE
    '%слово%'` -- неприв'язаний підрядок. Наслідок -- п. 6 звіту Дениса
    27.08: «Богодар» як підрядок є і в «Ґоляш **Богодар** Святославович», і в
    «Дашкевич Едуард **Богодар**ович», тому три різні людини склеювались в
    одну картку. Помилку такого роду збоку не видно: картка виглядає
    нормальною.

    ЯК. Слово мусить починатись на межі (початок рядка або пробіл) і
    закінчуватись на межі -- з допуском на відмінок в обидві сторони:
    основа береться коротшою на три літери, а після неї дозволяється до
    чотирьох. Тобто «Богодар» ловить «Богодар» і «Богодара», але НЕ
    «Богодарович» (там шість зайвих). А «Крижанівського» з питання ловить
    «Крижанівський» у базі -- саме той випадок, через який обрізання й
    з'явилось (без нього чат упевнено заперечував власні дані).

    Метасимволи екрануються: текст приходить від людини й у регулярку
    підставляти його як є не можна.
    """
    w = str(word or "").strip()
    if len(w) < 3:
        return None
    stem = w[:max(_MIN_STEM, len(w) - 3)]
    # ДОПУСК ПРОПОРЦІЙНИЙ ДОВЖИНІ, а не однаковий.
    #
    # Зловив власний тест: при постійному допуску чотири літери коротке слово
    # «Бог» ловило «Богодар», «Богуслав» і решту -- тобто пів реєстру. Для
    # трилітерного слова допуск нуль (лише точний збіг), для довгого -- до
    # чотирьох, бо саме там живе відмінок. Короткі прізвища при цьому не
    # заборонені: «Дяк» знайдеться, просто не притягне «Дякович».
    room = min(_INFLECTION, max(0, len(w) - 3))
    return (r"(^|\s)" + re.escape(stem)
            + r"[а-яіїєґА-ЯІЇЄҐ'ʼ-]{0,%d}(\s|$)" % room)


def _subdivision_pattern(text):
    """Текст із питання -> шаблон ILIKE («2 рота» -> «%2 рота%»)."""
    want = str(text or "").strip()
    if not want:
        return None
    return "%" + want.strip("%") + "%"


def subdivision_values():
    """-> назви підрозділів зі штатки (щоб перевірити, чи такий існує)."""
    try:
        rows = _query("SELECT DISTINCT f.value AS v FROM facts f "
                      "JOIN dimensions d ON d.id = f.dimension_id "
                      "WHERE d.code = 'subdivision' AND f.value <> '' "
                      "ORDER BY f.value", {})
        return [r["v"] for r in rows]
    except psycopg.Error:
        return []


def absences_on_date(date, subdivision=None, doc_type=None, confirmed=True,
                     dim=None):
    """Хто поза частиною в цей день.

    confirmed=True -- лише facts.status='confirmed' (правило продукту:
    чернетка не входить у підрахунок); confirmed=False -- лише непідтверджені,
    для окремого числа у відповіді. Умови по датах -- як у query_catalog.yaml
    (list_by_state): valid_from <= date <= COALESCE(valid_to, безстроково).
    """
    sql = _ABSENCE_SELECT + (
        "  AND f.status = %(status)s "
        "  AND f.valid_from IS NOT NULL "
        "  AND f.valid_from <= %(d)s "
        "  AND (f.valid_to IS NULL OR f.valid_to >= %(d)s) ")
    params = {"dims": ABSENCE_DIMS, "d": date,
              "status": "confirmed" if confirmed else "unconfirmed"}
    if subdivision:
        sql += _SUBDIVISION_FILTER
        params["subdivision"] = _subdivision_pattern(subdivision)
    # ОДИН вимір замість обох. `dim` -- явний код виміру ('leave' /
    # 'deployment_location'); `doc_type` лишається для старих викликів.
    #
    # Навіщо: п. 13 і 19 звіту Дениса. На питання ПРО ВІДПУСТКУ чат відповідав
    # числом «поза частиною» -- тобто відпустка ПЛЮС відрядження. Звідси 12 і
    # 15 на одну дату: обидві цифри правдиві, метрика різна, і ніде не сказано
    # яка. Фільтр у цій функції був від початку і не передавався ЖОДНОГО разу
    # (знахідка Андрія в контексті для дослідження).
    one = dim or (next((k for k, v in DOC_TYPE_BY_DIM.items() if v == doc_type),
                       doc_type) if doc_type else None)
    if one:
        sql += " AND d.code = %(one_dim)s"
        params["one_dim"] = one
    rows = _query(sql + " ORDER BY num.value NULLS LAST, o.canonical_name",
                  params)
    return [_absence_row(r) for r in rows]


def returning_on_date(date, subdivision=None):
    """У кого в цей день закінчується відсутність (valid_to = дата)."""
    sql = _ABSENCE_SELECT + (
        "  AND f.status = 'confirmed' AND f.valid_to = %(d)s ")
    params = {"dims": ABSENCE_DIMS, "d": date}
    if subdivision:
        sql += _SUBDIVISION_FILTER
        params["subdivision"] = _subdivision_pattern(subdivision)
    rows = _query(sql + " ORDER BY num.value NULLS LAST", params)
    return [_absence_row(r) for r in rows]


def absences_for_person(name_or_service_id, only_active=True):
    """Всі документи про відсутність людини. only_active=True -> лише
    confirmed (аналог «чинний»); False -> і непідтверджені теж."""
    # МЕЖА СЛОВА замість підрядка -- та сама причина, що у `find_people`.
    # Ця функція лишається потрібною для одного випадку: особи в реєстрі
    # немає, а документи з її ПІБ є (тоді `object_id` брати нізвідки).
    rx = name_word_regex(name_or_service_id)
    sql = _ABSENCE_SELECT + (
        "  AND (o.canonical_name ~* %(pat)s "
        "       OR p2.service_id = %(exact)s) ")
    sql = sql.replace(
        "JOIN documents dc ON dc.id = f.source_doc_id",
        "JOIN documents dc ON dc.id = f.source_doc_id "
        "LEFT JOIN people p2 ON p2.object_id = o.id")
    if only_active:
        sql += " AND f.status = 'confirmed'"
    rows = _query(sql + " ORDER BY dat.value NULLS LAST",
                  {"dims": ABSENCE_DIMS,
                   # Регулярка, яка не збігається ні з чим, замість None: так
                   # лишається робочою гілка пошуку за службовим номером.
                   "pat": rx or r"(?!)",
                   "exact": str(name_or_service_id)})
    return [_absence_row(r) for r in rows]


def absences_for_object(object_id, only_active=True):
    """Документи про відсутність ОДНІЄЇ особи -- за ключем, не за ПІБ.

    ЦЕ ГОЛОВНА ПРАВКА БЛОКУ C. Доти картка особи складалася з ДВОХ незалежних
    пошуків по одному рядку: `find_people(name)` давав заголовок, а
    `absences_for_person(name)` -- документи, і ніде не було сказано, що вони
    мусять стосуватись однієї людини. Тому «Богодар Святославович» давав
    картку Дашкевича з документами Ґоляша й Ващенка (п. 6 звіту), а навіть
    повне однозначне ПІБ «Дашкевич Едуард Богодарович» тягло документ другого
    Дашкевича -- зміряно приладом 28.08.

    Тут зв'язок один: спершу з'ясовуємо, ХТО це (`object_id`), потім беремо
    ЙОГО документи. Чужий документ у картці стає неможливим за побудовою, а не
    менш імовірним.
    """
    if not object_id:
        return []
    sql = _ABSENCE_SELECT + "  AND f.object_id = %(oid)s "
    if only_active:
        sql += " AND f.status = 'confirmed'"
    rows = _query(sql + " ORDER BY dat.value NULLS LAST",
                  {"dims": ABSENCE_DIMS, "oid": int(object_id)})
    return [_absence_row(r) for r in rows]


def document_by_number(doc_number):
    """Список (буває кілька документів з одним номером). Номер шукається серед
    фактів document_number БЕЗ фільтра статусу: документ із непідтвердженим
    номером теж має знаходитись -- його статус чат покаже чесно."""
    num = str(doc_number or "").lstrip("№").strip()
    if not num:
        return []
    sql = _ABSENCE_SELECT + (
        "  AND f.source_doc_id IN ("
        "    SELECT f3.source_doc_id FROM facts f3 "
        "    JOIN dimensions d3 ON d3.id = f3.dimension_id "
        "    WHERE d3.code = 'document_number' "
        "      AND btrim(f3.value) = %(num)s) ")
    rows = _query(sql + " ORDER BY dat.value NULLS LAST",
                  {"dims": ABSENCE_DIMS, "num": num})
    return [_absence_row(r) for r in rows]


def document_by_record_id(record_id):
    """Картка документа за НОМЕРОМ ЗАПИСУ В БАЗІ (`documents.id`).

    Нащо. Під кожною відповіддю ми самі показуємо «документ №207 (запис №33 у
    базі)», а спитати про запис було НЕМОЖЛИВО -- система показувала
    ідентифікатор, яким не можна скористатись. Найгірше це там, де номера на
    папері немає взагалі (58 документів із 205): запис -- єдиний спосіб на
    такий документ послатись.

    Та сама вибірка, що й за номером, тому картка виходить однакова.
    """
    try:
        rid = int(str(record_id).lstrip("№").strip())
    except (TypeError, ValueError):
        return []
    sql = _ABSENCE_SELECT + "  AND f.source_doc_id = %(rid)s "
    rows = _query(sql + " ORDER BY dat.value NULLS LAST",
                  {"dims": ABSENCE_DIMS, "rid": rid})
    return [_absence_row(r) for r in rows]


def count_absent_by_subdivision(date):
    """Зведення по підрозділах -> [{subdivision, total, absent}, ...].

    Доти повертала [] із поясненням «схема не зберігає зв'язок
    особа->підрозділ». Зі штаткою (25.08) це перестало бути правдою: вимір
    `subdivision` у базі є, і зведення -- рівно та таблиця, яку секретар
    зводить руками. `total` -- склад ЦЬОГО підрозділу за штаткою, тобто його
    власний знаменник, а не 303 по частині."""
    try:
        return _query(
            "WITH sub AS ("
            "  SELECT sf.object_id, sf.value AS subdivision "
            "  FROM facts sf JOIN dimensions sd ON sd.id = sf.dimension_id "
            "  WHERE sd.code = 'subdivision' AND sf.value <> ''), "
            "absent AS ("
            "  SELECT DISTINCT f.object_id FROM facts f "
            "  JOIN dimensions d ON d.id = f.dimension_id "
            "  WHERE d.code = ANY(%(dims)s) AND f.status = 'confirmed' "
            "    AND f.valid_from IS NOT NULL AND f.valid_from <= %(d)s "
            "    AND (f.valid_to IS NULL OR f.valid_to >= %(d)s)) "
            "SELECT sub.subdivision, "
            "       COUNT(*) AS total, "
            "       COUNT(absent.object_id) AS absent "
            "FROM sub LEFT JOIN absent ON absent.object_id = sub.object_id "
            "GROUP BY sub.subdivision ORDER BY sub.subdivision",
            {"dims": ABSENCE_DIMS, "d": date})
    except psycopg.Error:
        return []


def search_reference(query, limit=3):
    """Пошук нормативки: Ukrainian FTS по documents.text_content з
    domain='normative'. Зараз таких документів у базі немає -- функція чесно
    віддає [], а чат каже, що довідника в базі поки нуль документів."""
    q = " ".join(w for w in str(query or "").split() if len(w) >= 3)
    if not q:
        return []
    try:
        rows = _query(
            "SELECT dc.id, dc.text_content, "
            "  ts_rank(to_tsvector('ukrainian', dc.text_content), "
            "          websearch_to_tsquery('ukrainian', %(q)s)) AS score "
            "FROM documents dc "
            "WHERE dc.domain = 'normative' AND dc.text_content IS NOT NULL "
            "  AND to_tsvector('ukrainian', dc.text_content) @@ "
            "      websearch_to_tsquery('ukrainian', %(q)s) "
            "ORDER BY score DESC LIMIT %(lim)s", {"q": q, "lim": limit})
    except psycopg.Error:
        return []
    return [{
        "doc_title": f"нормативний документ №{r['id']} у базі",
        "section_number": "",
        "section_title": f"нормативний документ №{r['id']}",
        "text": (r["text_content"] or "")[:800],
        "source_note": f"документ №{r['id']} у базі (розпізнаний текст)",
        "score": float(r["score"]),
    } for r in rows]


# ── Додаткові виклики поверх контракту (для складу відповіді) ────────────────


def unconfirmed_absences_on_date(date):
    """Скільки НЕпідтверджених записів про відсутність накривають дату --
    окреме число у відповіді (правило продукту: чернетка ≠ факт)."""
    return len(absences_on_date(date, confirmed=False))


def people_total():
    rows = _query(
        "SELECT COUNT(*) AS n FROM objects o "
        "JOIN object_kinds k ON k.id = o.kind_id WHERE k.code = 'person'")
    return rows[0]["n"]


# ── Покриття даних: за який період у базі взагалі щось є ─────────────────────

_COVERAGE = None


def data_coverage():
    """-> (перша дата, остання дата) серед фактів із періодом, або (None, None).

    Навіщо. 25.08 Аня питала «хто був у відпустці 5 травня» і чат казав «0 --
    чинних документів немає». Формально правда: у базі даних за травень НЕМА
    ЖОДНОГО (покриття 02.06-10.10). Але «нуль» і «за цю дату в нас нічого
    немає» -- різні твердження, і перше читається як «ніхто не був у
    відпустці». Гірше: підказка чата казала «дані стенду -- травень 2026»,
    тобто сама відправляла в порожній період.

    Тому покриття беремо З БАЗИ, а не з коментаря в коді: набір документів
    перегенерують -- відповідь поїде за ним.

    Кешується на процес: це властивість набору даних, а не запиту.
    """
    global _COVERAGE
    if _COVERAGE is None:
        try:
            # ЛИШЕ виміри відсутностей. Раніше бралися всі факти з періодом --
            # і коли 25.08 залили штатку, покриття «поїхало» на 2022-03-12
            # (там дати служби, призначень, контрактів). Підказка почала знову
            # відправляти людину в порожній період, тільки з іншого боку.
            # Покриття мусить бути про ТЕ, про що людина питає: відпустки й
            # відрядження.
            rows = _query("SELECT min(f.valid_from) AS d_from, "
                          "max(f.valid_to) AS d_to FROM facts f "
                          "JOIN dimensions d ON d.id = f.dimension_id "
                          "WHERE f.valid_from IS NOT NULL "
                          "AND d.code = ANY(%(dims)s)",
                          {"dims": ABSENCE_DIMS})
            # Порожній результат ловимо явно. Доти тут стояло `[...][0]` під
            # `except Exception`, і IndexError глушився разом із помилками бази.
            # Коли except звузили до помилок бази (це було потрібно, щоб
            # «база недоступна» не перетворювалась у тиху неправду), виняток
            # виліз -- і його зловив тест. Саме для цього звуження й робилось.
            row = rows[0] if rows else {"d_from": None, "d_to": None}
            if row["d_from"] and row["d_to"]:
                _COVERAGE = (row["d_from"], row["d_to"])
            else:
                return (None, None)          # порожня база -- кешувати нічого
        except psycopg.Error:
            # НЕ кешуємо неуспіх (знайдено блоком 8, 26.08): інакше один збій
            # бази на старті процесу назавжди прибирав би межі покриття з усіх
            # відповідей -- тобто нуль знову читався б як «нікого не було».
            return (None, None)
    return _COVERAGE


def coverage_note(date=None):
    """Рядок про покриття -- або порожній, якщо покриття невідоме.

    Якщо дату передано і вона ПОЗА покриттям, це сказано прямо: саме той
    випадок, коли нуль означає «даних немає», а не «нікого немає».

    ТИПИ. `date` приходить із чата РЯДКОМ ('2026-05-05'), а з бази -- обʼєктом
    `datetime.date`. Порівняння рядка з датою в Python кидає TypeError, і на
    сервері це давало НЕОБРОБЛЕНЕ падіння на питаннях «хто не повернувся 5
    травня?» -- тобто моя ж правка про чесний нуль ламала відповідь. Знайдено
    другим адверсарним проходом 25.08. Тому все зводиться до рядків ISO: у них
    лексикографічне порівняння збігається з хронологічним.
    """
    d_from, d_to = data_coverage()
    if not d_from or not d_to:
        return ""

    def _iso_str(value):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    lo, hi = _iso_str(d_from), _iso_str(d_to)
    if date:
        asked = _iso_str(date)
        if asked < lo or asked > hi:
            return (f"За цю дату в базі даних НЕМАЄ: документи покривають "
                    f"{lo} — {hi}. Нуль тут означає «немає даних», а не "
                    f"«нікого не було».")
    return f"Покриття даних у базі: {lo} — {hi}."

