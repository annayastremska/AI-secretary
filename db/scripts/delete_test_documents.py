"""Видаляє пробні документи 248-250 і завдання черги 167-169 (прохання Ані)."""
import os, sys, psycopg
from dotenv import load_dotenv
load_dotenv(".env")
DOCS, QUEUE = [248, 249, 250], [167, 168, 169]
with psycopg.connect(os.environ["DATABASE_URL"].replace("postgresql+psycopg://","postgresql://")) as c, c.cursor() as cur:
    # Запобіжник: видаляємо ЛИШЕ якщо фактів і джерел справді нуль.
    cur.execute("""SELECT count(*) FROM fact_sources WHERE document_id = ANY(%s)""", (DOCS,))
    src = cur.fetchone()[0]
    cur.execute("""SELECT count(*) FROM facts f JOIN fact_sources s ON s.fact_id=f.id
                    WHERE s.document_id = ANY(%s)""", (DOCS,))
    fx = cur.fetchone()[0]
    print(f"джерел на ці документи: {src}, фактів через них: {fx}")
    if src or fx:
        print("НЕ видаляю: є посилання, це вже не тестове сміття")
        sys.exit(1)
    cur.execute("SELECT count(*) FROM documents")
    before = cur.fetchone()[0]
    cur.execute("DELETE FROM review_queue WHERE id = ANY(%s)", (QUEUE,))
    q = cur.rowcount
    cur.execute("DELETE FROM documents WHERE id = ANY(%s)", (DOCS,))
    d = cur.rowcount
    cur.execute("SELECT count(*) FROM documents")
    after = cur.fetchone()[0]
    print(f"видалено: завдань {q}, документів {d}")
    print(f"документів було {before}, стало {after}")
    # Аня чекала 201. Різниця -- мої три внутрішні інструкції 251-253, які
    # вона сама просила не чіпати; її число порахували до їхньої заливки.
    if after != 204:
        print(f"УВАГА: очікувалось 204, вийшло {after} -- НЕ фіксую")
        sys.exit(1)
    c.commit()
    print("ЗАФІКСОВАНО")
