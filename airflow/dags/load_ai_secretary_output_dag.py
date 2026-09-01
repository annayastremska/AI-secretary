"""Забирає готові результати пайплайна AI-secretary (repo ds-cprgb/AI-secretary,
branch anya) з примонтованої тек data/output/documents/ і вантажить у нашу
Postgres через airflow/plugins/ai_secretary_loader.py.

Тимчасове рішення на етапі демо: AI-secretary -- окремий репозиторій Ані, і
для DAG-а він примонтований як read-only volume прямо з її локального клону
(див. docker-compose.yml, сервіс airflow-*, volume ai_secretary_output).
У реальному розгортанні джерелом стане спільний inbox, не абсолютний шлях
на конкретній машині.

Дедуплікація -- на рівні БД (documents.checksum), тож повторний скан УСІХ
.md-файлів на кожному прогоні безпечний (get_or_create_document просто
пропускає вже завантажені). При зростанні обсягу це варто замінити на
трекання "останній обраний файл", але для демо-масштабу зайве.

Один зламаний .md-файл НЕ валить увесь прогін -- ізоляція збоїв per-file,
на відміну від run.py процесу AI-secretary самого (див. знахідку №1-4 у
ai-secretary-pipeline-review.md).
"""
import glob
import os
import sys
from datetime import timedelta

import pendulum
from airflow.decorators import dag, task

sys.path.insert(0, "/opt/airflow/plugins")

AI_SECRETARY_OUTPUT_DIR = "/opt/airflow/ai_secretary_output"


@dag(
    dag_id="load_ai_secretary_output",
    schedule="*/10 * * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz="Europe/Kyiv"),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    tags=["milidoc", "ai-secretary", "ingestion"],
)
def load_ai_secretary_output():

    @task
    def scan_and_load():
        import ai_secretary_loader

        if not os.path.isdir(AI_SECRETARY_OUTPUT_DIR):
            print(f"Тека {AI_SECRETARY_OUTPUT_DIR} не змонтована -- нічого робити.")
            return {"loaded": 0, "skipped": 0, "failed": 0}

        md_files = sorted(glob.glob(os.path.join(AI_SECRETARY_OUTPUT_DIR, "**", "*.md"), recursive=True))
        loaded, skipped, failed = 0, 0, []

        for path in md_files:
            try:
                result = ai_secretary_loader.load(path)
            except Exception as exc:
                print(f"ПОМИЛКА при завантаженні {path}: {type(exc).__name__}: {exc}")
                failed.append(path)
                continue

            if result["already_existed"]:
                skipped += 1
            else:
                loaded += 1
                print(f"Завантажено {path} -> documents.id={result['document_id']}, "
                      f"фактів: {len(result['facts_inserted'])}")

        print(f"Підсумок: нових={loaded}, уже в базі={skipped}, з помилкою={len(failed)}")
        if failed:
            print(f"Файли з помилкою (перевірити руками): {failed}")
        return {"loaded": loaded, "skipped": skipped, "failed": len(failed)}

    scan_and_load()


load_ai_secretary_output()
