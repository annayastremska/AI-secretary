#!/usr/bin/env python
"""Завантаження ваг MamayLM (GGUF) у models/ -- те, чого pip зробити не може.

    python pipeline/scripts/download_model.py            # 12B, ~6.8 ГБ (за замовчуванням)
    python pipeline/scripts/download_model.py --size 4b  # 4B, ~2.5 ГБ (якщо машина не тягне 12B)
    python pipeline/scripts/download_model.py --size 27b # 27B, ~16.5 ГБ (лише GPU-сервер)

27B має сенс тільки там, де вся модель влазить у VRAM (H100 80 ГБ) -- на
ноутбуці вона не поміститься ні у пам'ять, ні в розумний час. На спільному
сервері ваги кладемо в спільну папку, а не собі:

    python pipeline/scripts/download_model.py --size 27b --models-dir ~/shared/models

Файл кладеться під ФІКСОВАНОЮ локальною назвою (models/mamaylm-<size>-q4_k_m.gguf),
на яку вже вказує config.example.yaml, тому після завантаження конфіг правити
не потрібно.

Назву файлу в репозиторії скрипт визначає сам, а не тримає зашитою: схеми
назв у різних релізах відрізняються (у 4B "...v1.0.Q4_K_M.gguf" з точкою, у
12B "...v2.0-Q4_K_M.gguf" з дефісом), і зашите значення ламалося б на
кожному новому релізі.
"""
import argparse
import os
import shutil
import sys

REPOS = {
    "4b": "INSAIT-Institute/MamayLM-Gemma-3-4B-IT-v1.0-GGUF",
    "12b": "INSAIT-Institute/MamayLM-Gemma-3-12B-IT-v2.0-GGUF",
    "27b": "INSAIT-Institute/MamayLM-Gemma-3-27B-IT-v2.0-GGUF",
}
# Приблизний розмір Q4_K_M -- лише для повідомлень і підказки в --help.
SIZES_GB = {"4b": 2.5, "12b": 6.8, "27b": 16.5}
QUANT = "Q4_K_M"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Завантажити ваги MamayLM у models/")
    parser.add_argument("--size", choices=sorted(REPOS), default="12b",
                        help="12b (~6.8 ГБ, основна), 4b (~2.5 ГБ, якщо машина не тягне 12B) "
                             "або 27b (~16.5 ГБ, лише для GPU-сервера)")
    parser.add_argument("--models-dir", default=os.path.join(PROJECT_ROOT, "models"))
    parser.add_argument("--filename", default=None,
                        help="конкретний .gguf у репозиторії, якщо їх кілька")
    args = parser.parse_args(argv)

    models_dir = os.path.expanduser(args.models_dir)
    target = os.path.join(models_dir, f"mamaylm-{args.size}-q4_k_m.gguf")
    if os.path.exists(target):
        print(f"Ваги вже на місці: {target}")
        print(f"({round(os.path.getsize(target) / 1024 ** 3, 2)} ГБ) -- нічого не завантажую.")
        return 0

    # Місце перевіряємо ДО завантаження: обірватись на 90% 16-гігабайтного
    # файлу через забитий диск -- це змарновані десятки хвилин, та ще й на
    # спільному сервері, де диск закінчується не лише для тебе.
    need_gb = SIZES_GB[args.size]
    probe = models_dir if os.path.isdir(models_dir) else os.path.dirname(os.path.abspath(models_dir)) or "."
    free_gb = shutil.disk_usage(probe).free / 1024 ** 3
    print(f"Вільно на диску: {free_gb:.1f} ГБ, потрібно ~{need_gb} ГБ")
    if free_gb < need_gb * 1.1:
        print(f"Замало місця для {args.size.upper()}: потрібно ~{need_gb} ГБ плюс запас, "
              f"вільно {free_gb:.1f} ГБ. Звільніть місце або візьміть менший розмір.",
              file=sys.stderr)
        return 1

    try:
        from huggingface_hub import hf_hub_download, list_repo_files
    except ImportError:
        print("Потрібен huggingface_hub: pip install -r requirements.txt", file=sys.stderr)
        return 2

    repo_id = REPOS[args.size]
    print(f"Репозиторій: {repo_id}")
    try:
        candidates = [f for f in list_repo_files(repo_id)
                      if f.endswith(".gguf") and QUANT in f]
    except Exception as exc:
        print(f"Не вдалося прочитати список файлів репозиторію: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    if not candidates:
        print(f"У {repo_id} не знайдено жодного .gguf з квантизацією {QUANT}.", file=sys.stderr)
        return 1
    if len(candidates) > 1:
        # Раніше просто брався перший за алфавітом -- якщо в репозиторії
        # з'явиться ще один артефакт із Q4_K_M у назві (старий реліз,
        # companion-файл), скрипт мовчки завантажив би не той. Краще спитати
        # людину, ніж вгадати.
        print(f"У {repo_id} кілька файлів із {QUANT}:", file=sys.stderr)
        for f in sorted(candidates):
            print(f"   {f}", file=sys.stderr)
        print("Уточніть потрібний через --filename.", file=sys.stderr)
        return 1

    filename = args.filename or candidates[0]
    print(f"Файл: {filename}\nЗавантаження (це кілька ГБ, буде довго)...")
    os.makedirs(models_dir, exist_ok=True)
    try:
        downloaded = hf_hub_download(repo_id=repo_id, filename=filename,
                                     local_dir=models_dir)
    except KeyboardInterrupt:
        print("\nПерервано користувачем -- частково завантажений файл лишився "
              "в кеші huggingface_hub, повторний запуск продовжить з того місця.",
              file=sys.stderr)
        return 130
    except Exception as exc:
        # Розрив мережі посередині кількагігабайтного завантаження -- очікувана
        # ситуація, а не причина показувати traceback.
        print(f"Завантаження не вдалося ({type(exc).__name__}: {exc}).\n"
              "huggingface_hub кешує вже отримані частини -- просто запустіть скрипт знову.",
              file=sys.stderr)
        return 1

    # Перейменовуємо у фіксовану назву, щоб config.example.yaml був
    # детермінованим і не залежав від схеми назв конкретного релізу.
    if os.path.abspath(downloaded) != os.path.abspath(target):
        shutil.move(downloaded, target)

    print(f"\nГотово: {target}")
    print(f"Розмір: {round(os.path.getsize(target) / 1024 ** 3, 2)} ГБ")
    rel = os.path.relpath(target, PROJECT_ROOT).replace(os.sep, "/")
    if rel.startswith(".."):
        # Ваги поза репозиторієм (напр. спільна папка на сервері) -- відносний
        # шлях виду ../../shared/... у конфізі нічого не пояснює й ламається
        # при переносі. Показуємо абсолютний.
        rel = os.path.abspath(target).replace(os.sep, "/")
    if args.size == "12b" and not rel.startswith("/"):
        print("\nconfig.example.yaml уже вказує на цей шлях -- правити нічого не потрібно.")
    else:
        print(f"\nУ config.yaml вкажіть:\n  llm:\n    model_path: {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
