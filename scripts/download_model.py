#!/usr/bin/env python
"""Завантаження ваг MamayLM (GGUF) у models/ -- те, чого pip зробити не може.

    python scripts/download_model.py            # 4B, ~2.5 ГБ (за замовчуванням)
    python scripts/download_model.py --size 12b # 12B, ~6.8 ГБ (якісніше, повільніше)

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
}
QUANT = "Q4_K_M"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Завантажити ваги MamayLM у models/")
    parser.add_argument("--size", choices=sorted(REPOS), default="4b",
                        help="4b (~2.5 ГБ, для ноутбука) або 12b (~6.8 ГБ, якісніше)")
    parser.add_argument("--models-dir", default=os.path.join(PROJECT_ROOT, "models"))
    args = parser.parse_args(argv)

    target = os.path.join(args.models_dir, f"mamaylm-{args.size}-q4_k_m.gguf")
    if os.path.exists(target):
        print(f"Ваги вже на місці: {target}")
        print(f"({round(os.path.getsize(target) / 1024 ** 3, 2)} ГБ) -- нічого не завантажую.")
        return 0

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

    filename = sorted(candidates)[0]
    print(f"Файл: {filename}\nЗавантаження (це кілька ГБ, буде довго)...")
    os.makedirs(args.models_dir, exist_ok=True)
    downloaded = hf_hub_download(repo_id=repo_id, filename=filename, local_dir=args.models_dir)

    # Перейменовуємо у фіксовану назву, щоб config.example.yaml був
    # детермінованим і не залежав від схеми назв конкретного релізу.
    if os.path.abspath(downloaded) != os.path.abspath(target):
        shutil.move(downloaded, target)

    print(f"\nГотово: {target}")
    print(f"Розмір: {round(os.path.getsize(target) / 1024 ** 3, 2)} ГБ")
    rel = os.path.relpath(target, PROJECT_ROOT).replace(os.sep, "/")
    if args.size != "4b":
        print(f"\nУ config.yaml вкажіть:\n  llm:\n    model_path: {rel}")
    else:
        print("\nconfig.example.yaml уже вказує на цей шлях -- правити нічого не потрібно.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
