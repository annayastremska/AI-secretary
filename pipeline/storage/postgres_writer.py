"""Запис витягнутого запису в PostgreSQL за схемою db/schema.sql.

НЕ ПЕРЕВІРЕНО на живій базі -- у середовищі розробки немає ні сервера, ні
пакета `psycopg`. Ставитись як до чернетки до першого реального прогону.

Свідомі рішення, що відповідають context/project-expectations.md розд. 4:
- один документ може дати КІЛЬКА фактів -> facts вставляються циклом;
- дедуплікація документа -- UNIQUE(file_hash) у БД, а не перевірка в коді:
  так гарантія тримається навіть якщо паралельно працюють два процеси;
- особа НЕ матчиться автоматично за ПІБ. Створюється чернетковий subject і
  прив'язується до фактів; звірка/об'єднання ідентичностей -- окремий крок
  з людиною в контурі (open-questions.md), і вгадувати тут "той самий
  Іваненко чи інший" було б рівно тим класом тихої помилки, від якого
  застерігає architecture-proposal.md розд. 7.
"""
import json


class PostgresWriter:
    def __init__(self, dsn: str):
        import psycopg  # лінивий імпорт: потрібен лише для цього бекенда

        self._psycopg = psycopg
        self.dsn = dsn

    def write(self, document_meta: dict, record: dict, storage_key: str) -> dict:
        """Повертає {"document_id":..., "inserted": bool}. inserted=False
        означає, що документ з таким file_hash уже був у базі."""
        with self._psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO documents (id, file_hash, domain, template, status,
                                           storage_key, uploaded_at, meta)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (file_hash) DO NOTHING
                    RETURNING id
                    """,
                    (document_meta["id"], document_meta["file_hash"], document_meta.get("domain"),
                     document_meta.get("template"), document_meta.get("status"), storage_key,
                     document_meta.get("uploaded_at"), json.dumps(document_meta, ensure_ascii=False)),
                )
                row = cur.fetchone()
                if row is None:
                    return {"document_id": None, "inserted": False}
                document_id = row[0]

                subject_id = None
                subject = record.get("subject") or {}
                if any(v is not None for v in subject.values()):
                    cur.execute(
                        """
                        INSERT INTO subjects (kind, attributes, source_document_id, confirmed)
                        VALUES (%s, %s, %s, %s) RETURNING id
                        """,
                        ("person", json.dumps(subject, ensure_ascii=False), document_id, False),
                    )
                    subject_id = cur.fetchone()[0]

                for fact in record.get("facts", []):
                    cur.execute(
                        """
                        INSERT INTO facts (subject_id, fact_type, value_code, date_start, date_end,
                                           confirmed, status, source_document_id, additional_info)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (subject_id, fact.get("fact_type"), fact.get("value_code"),
                         fact.get("date_start"), fact.get("date_end"), fact.get("confirmed"),
                         fact.get("status"), document_id,
                         json.dumps(fact.get("additional_info") or {}, ensure_ascii=False)),
                    )
            conn.commit()
        return {"document_id": document_id, "inserted": True}
