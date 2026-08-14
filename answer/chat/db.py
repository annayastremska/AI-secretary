# Сім функцій стику — реалізація на SQLite для тестового стенду.
# Контракт — docs/contracts/2026-08-14_chat-db-interface.md. Ті самі назви, аргументи і ключі дає db_postgres.py Андрія.
#
# Правила стику, які тримає цей файл:
#   - функція повертає дані, не текст для людини
#   - нічого не знайшли → [] (не None, не виняток)
#   - дати — рядки YYYY-MM-DD, як лежать у базі
#   - порожні поля віддаються порожніми, нічого не підставляється
#   - коли документів два — віддаються обидва, вибір чинного за чатом

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stand.sqlite")

PEOPLE_COLS = ("service_id", "full_name", "rank", "position_title",
               "subdivision", "phone")
ABSENCE_COLS = ("doc_number", "doc_date", "doc_type", "service_id",
                "person_name_raw", "date_from", "date_to", "reason",
                "place", "status", "superseded_by", "source_file")


def _query(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def find_people(subdivision=None, name=None):
    sql = f"SELECT {', '.join(PEOPLE_COLS)} FROM people WHERE 1=1"
    params = []
    if subdivision:
        sql += " AND subdivision = ?"
        params.append(subdivision)
    if name:
        sql += " AND full_name LIKE ?"
        params.append(f"%{name}%")
    return _query(sql + " ORDER BY service_id", params)


def absences_on_date(date, subdivision=None, doc_type=None):
    """Хто поза частиною в цей день. Тільки status = чинний.

    Документ із порожніми чи переплутаними датами сюди не потрапляє:
    умова date_from <= date <= date_to на ньому не виконується.
    """
    sql = (f"SELECT {', '.join('a.' + c for c in ABSENCE_COLS)} "
           "FROM absences a LEFT JOIN people p ON p.service_id = a.service_id "
           "WHERE a.status = 'чинний' "
           "AND a.date_from != '' AND a.date_to != '' "
           "AND a.date_from <= ? AND ? <= a.date_to")
    params = [date, date]
    if subdivision:
        sql += " AND p.subdivision = ?"
        params.append(subdivision)
    if doc_type:
        sql += " AND a.doc_type = ?"
        params.append(doc_type)
    return _query(sql + " ORDER BY a.doc_number", params)


def returning_on_date(date, subdivision=None):
    sql = (f"SELECT {', '.join('a.' + c for c in ABSENCE_COLS)} "
           "FROM absences a LEFT JOIN people p ON p.service_id = a.service_id "
           "WHERE a.status = 'чинний' AND a.date_to = ?")
    params = [date]
    if subdivision:
        sql += " AND p.subdivision = ?"
        params.append(subdivision)
    return _query(sql + " ORDER BY a.doc_number", params)


def absences_for_person(name_or_service_id, only_active=True):
    sql = (f"SELECT {', '.join('a.' + c for c in ABSENCE_COLS)} "
           "FROM absences a LEFT JOIN people p ON p.service_id = a.service_id "
           "WHERE (a.service_id = ? OR a.person_name_raw LIKE ? "
           "OR p.full_name LIKE ?)")
    like = f"%{name_or_service_id}%"
    params = [name_or_service_id, like, like]
    if only_active:
        sql += " AND a.status = 'чинний'"
    return _query(sql + " ORDER BY a.doc_date", params)


def document_by_number(doc_number):
    """Список, не один запис: буває два документи з тим самим номером."""
    return _query(
        f"SELECT {', '.join(ABSENCE_COLS)} FROM absences "
        "WHERE doc_number = ? ORDER BY doc_date", [doc_number])


def count_absent_by_subdivision(date):
    """Рядок добової довідки: {subdivision, absent, total} по всіх підрозділах.

    absent рахує людей, підтверджених реєстром (service_id непорожній).
    Документи без service_id не мають підрозділу — чат називає їх окремо
    як непідтверджені.
    """
    return _query(
        "SELECT p.subdivision AS subdivision, "
        "COUNT(DISTINCT CASE WHEN a.status = 'чинний' "
        "  AND a.date_from != '' AND a.date_to != '' "
        "  AND a.date_from <= ? AND ? <= a.date_to "
        "  THEN a.service_id END) AS absent, "
        "COUNT(DISTINCT p.service_id) AS total "
        "FROM people p LEFT JOIN absences a ON a.service_id = p.service_id "
        "GROUP BY p.subdivision ORDER BY p.subdivision", [date, date])


def search_reference(query, limit=3):
    """Простий пошук за словами. У бойовій версії — Postgres FTS.

    score — скільки слів запиту знайшлось у тексті розділу (назва документа,
    назва розділу і текст разом). Розділи без жодного збігу не повертаються.
    """
    words = [w.lower() for w in query.split() if len(w) >= 3]
    if not words:
        return []
    rows = _query("SELECT doc_title, section_number, section_title, text, "
                  "source_note FROM reference_docs")
    scored = []
    for r in rows:
        haystack = f"{r['doc_title']} {r['section_title']} {r['text']}".lower()
        score = sum(1 for w in words if w in haystack)
        if score > 0:
            r["score"] = score
            scored.append(r)
    scored.sort(key=lambda r: -r["score"])
    return scored[:limit]
