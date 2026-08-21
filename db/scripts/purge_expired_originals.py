"""Видаляє оригінали документів, чий строк зберігання вийшов.

Замінює 30-денне ILM-правило MinIO, яке зникло разом із MinIO. Різниця
принципова: раніше файл видаляло сховище саме, тепер це робимо ми -- отже
скрипт МУСИТЬ запускатись за розкладом, інакше оригінали лежатимуть вічно,
а видалення прописане в ТЗ (project-expectations.md, "Зберігання оригіналів
документів").

Джерело істини -- documents.expires_at, який заповнює завантажувач. Тобто
політика тепер видима в базі й перевіряється запитом, а не схована в
конфігурації бакета.

Порядок дій навмисно такий: спершу видаляємо файл, і лише після успіху
занулюємо raw_uri. Якщо впасти між цими кроками, у базі лишиться посилання
на вже видалений файл -- це видно й виправно наступним прогоном. Зворотний
порядок дав би файл, на який ніщо не посилається: він не видалиться вже
ніколи, і знайти його можна буде тільки вручну.

Запуск:
    python db/scripts/purge_expired_originals.py            # видалити
    python db/scripts/purge_expired_originals.py --dry-run  # лише показати
"""
import argparse
import os
import sys
from urllib.parse import unquote, urlparse

import psycopg

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "airflow", "plugins"))
import ai_secretary_loader  # noqa: E402


def _local_path(raw_uri: str):
    """file:///... -> шлях у файловій системі. Інші схеми не наші: документ
    може посилатись на вихід пайплайна (ai-secretary-output:...), і його ми
    не чіпаємо."""
    if not raw_uri or not raw_uri.startswith("file:///"):
        return None
    return os.path.normpath(unquote(urlparse(raw_uri).path).lstrip("/"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="показати, але не видаляти")
    args = ap.parse_args(argv)

    originals = os.path.abspath(ai_secretary_loader.originals_dir())
    deleted = missing = skipped = 0

    with psycopg.connect(ai_secretary_loader.get_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, raw_uri, expires_at FROM documents
                WHERE expires_at IS NOT NULL AND expires_at <= now()
                  AND raw_uri IS NOT NULL
                ORDER BY expires_at
                """
            )
            rows = cur.fetchall()
            print(f"Прострочених документів з оригіналом: {len(rows)}")

            for doc_id, raw_uri, expires_at in rows:
                path = _local_path(raw_uri)
                if path is None:
                    skipped += 1
                    continue
                # Не виходимо за межі своєї теки: raw_uri приходить із бази,
                # і видаляти за ним будь-який шлях у ФС -- погана ідея.
                if os.path.commonpath([originals, os.path.abspath(path)]) != originals:
                    print(f"  [пропуск] documents.id={doc_id}: {path} поза {originals}")
                    skipped += 1
                    continue
                if args.dry_run:
                    print(f"  [dry-run] documents.id={doc_id} ({expires_at:%Y-%m-%d}): {path}")
                    continue
                try:
                    os.remove(path)
                    deleted += 1
                except FileNotFoundError:
                    # Уже немає -- нормально: могли видалити руками або
                    # попередній прогін упав саме тут. raw_uri все одно чистимо.
                    missing += 1
                except OSError as exc:
                    print(f"  [помилка] documents.id={doc_id}: {type(exc).__name__}: {exc}")
                    skipped += 1
                    continue
                cur.execute("UPDATE documents SET raw_uri = NULL WHERE id = %s", (doc_id,))
        if not args.dry_run:
            conn.commit()

    if args.dry_run:
        print("dry-run: нічого не змінено")
    else:
        print(f"Видалено: {deleted}, вже не було: {missing}, пропущено: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
