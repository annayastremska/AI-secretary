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
#   - підрозділи: зв'язку особа→підрозділ немає (db/README_for_chatbot_team.md
#     п.8) → find_people(subdivision=...) і count_absent_by_subdivision()
#     повертають [], чат каже «база цього не знає»;
#   - superseded_by (скасування документом): у схемі немає -- завжди None;
#   - reference_docs: нормативних розділів немає; search_reference шукає FTS по
#     documents.text_content з domain='normative' -- таких документів у базі
#     поки нуль, тож повертається [] і чат чесно відмовляє.

import os

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
STATUS_LABEL = {"confirmed": "чинний",
                "unconfirmed": "не підтверджено (чернетка)",
                "rejected": "відхилений"}


def _read_env():
    vals = {}
    path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip().strip("\"'")
    return vals


def _dsn():
    env = _read_env()
    return (f"host=localhost port={env.get('POSTGRES_PORT', '5433')} "
            f"dbname={env.get('APP_DB_NAME', 'milidoc')} "
            f"user={env.get('READONLY_DB_USER', 'milidoc_readonly')} "
            f"password={env.get('READONLY_DB_PASSWORD', '')} "
            f"options='-c default_transaction_read_only=on "
            f"-c statement_timeout=5000'")


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
       place.value           AS place_val
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
        "date_from": _iso(r["date_from"]),
        "date_to": _iso(r["date_to"]),
        "reason": (r["reason"] or "") if dim == "leave" else "",
        "place": (r["place_val"] or r["reason"] or ""),
        "status": STATUS_LABEL.get(r["fact_status"], r["fact_status"]),
        "fact_status": r["fact_status"],
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

    subdivision: у схемі НЕМАЄ зв'язку особа→підрозділ -- фільтр за
    підрозділом чесно повертає [] (чат озвучує причину сам).
    """
    if subdivision:
        return []
    sql = ("SELECT o.id AS object_id, o.canonical_name, p.service_id, "
           "rank_f.value AS rank_code "
           "FROM objects o "
           "JOIN object_kinds k ON k.id = o.kind_id AND k.code = 'person' "
           "LEFT JOIN people p ON p.object_id = o.id "
           "LEFT JOIN LATERAL ("
           "  SELECT f.value FROM facts f "
           "  JOIN dimensions d ON d.id = f.dimension_id "
           "  WHERE f.object_id = o.id AND d.code = 'rank' "
           "    AND f.status = 'confirmed' "
           "  ORDER BY f.valid_from DESC NULLS LAST LIMIT 1) rank_f ON true "
           "WHERE 1=1")
    params = {}
    if name:
        sql += " AND o.canonical_name ILIKE %(name)s"
        params["name"] = f"%{name}%"
    rows = _query(sql + " ORDER BY o.id", params)
    return [{
        "service_id": r["service_id"] or f"ID-{r['object_id']}",
        "full_name": r["canonical_name"],
        "rank": _rank_label(r["rank_code"]),
        "position_title": "",   # окремого підтвердженого виміру посади чат не тягне
        "subdivision": "",      # зв'язку особа→підрозділ у схемі немає
        "phone": "",            # телефонів у схемі немає
    } for r in rows]


def absences_on_date(date, subdivision=None, doc_type=None, confirmed=True):
    """Хто поза частиною в цей день.

    confirmed=True -- лише facts.status='confirmed' (правило продукту:
    чернетка не входить у підрахунок); confirmed=False -- лише непідтверджені,
    для окремого числа у відповіді. Умови по датах -- як у query_catalog.yaml
    (list_by_state): valid_from <= date <= COALESCE(valid_to, безстроково).
    """
    if subdivision:
        return []  # зв'язку особа→підрозділ немає -- чат відмовляє чесно
    sql = _ABSENCE_SELECT + (
        "  AND f.status = %(status)s "
        "  AND f.valid_from IS NOT NULL "
        "  AND f.valid_from <= %(d)s "
        "  AND (f.valid_to IS NULL OR f.valid_to >= %(d)s) ")
    params = {"dims": ABSENCE_DIMS, "d": date,
              "status": "confirmed" if confirmed else "unconfirmed"}
    if doc_type:
        dim = next((k for k, v in DOC_TYPE_BY_DIM.items() if v == doc_type),
                   doc_type)
        sql += " AND d.code = %(one_dim)s"
        params["one_dim"] = dim
    rows = _query(sql + " ORDER BY num.value NULLS LAST, o.canonical_name",
                  params)
    return [_absence_row(r) for r in rows]


def returning_on_date(date, subdivision=None):
    """У кого в цей день закінчується відсутність (valid_to = дата)."""
    if subdivision:
        return []
    sql = _ABSENCE_SELECT + (
        "  AND f.status = 'confirmed' AND f.valid_to = %(d)s ")
    rows = _query(sql + " ORDER BY num.value NULLS LAST",
                  {"dims": ABSENCE_DIMS, "d": date})
    return [_absence_row(r) for r in rows]


def absences_for_person(name_or_service_id, only_active=True):
    """Всі документи про відсутність людини. only_active=True -> лише
    confirmed (аналог «чинний»); False -> і непідтверджені теж."""
    sql = _ABSENCE_SELECT + (
        "  AND (o.canonical_name ILIKE %(pat)s "
        "       OR p2.service_id = %(exact)s) ")
    sql = sql.replace(
        "JOIN documents dc ON dc.id = f.source_doc_id",
        "JOIN documents dc ON dc.id = f.source_doc_id "
        "LEFT JOIN people p2 ON p2.object_id = o.id")
    if only_active:
        sql += " AND f.status = 'confirmed'"
    rows = _query(sql + " ORDER BY dat.value NULLS LAST",
                  {"dims": ABSENCE_DIMS,
                   "pat": f"%{name_or_service_id}%",
                   "exact": str(name_or_service_id)})
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


def count_absent_by_subdivision(date):
    """Зведення по підрозділах: наша схема НЕ зберігає зв'язок
    особа→підрозділ (db/README_for_chatbot_team.md, п.8) -- повертаємо [],
    чат відповідає «база цього не знає», обхід не вигадуємо."""
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


def unconfirmed_total():
    rows = _query("SELECT COUNT(*) AS n FROM facts "
                  "WHERE status = 'unconfirmed'")
    return rows[0]["n"]


def people_total():
    rows = _query(
        "SELECT COUNT(*) AS n FROM objects o "
        "JOIN object_kinds k ON k.id = o.kind_id WHERE k.code = 'person'")
    return rows[0]["n"]
