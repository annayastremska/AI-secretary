#!/usr/bin/env python
"""CLI локального прогону пайплайна.

    python run_pipeline.py                             # уся папка з config.paths.input_dir
    python run_pipeline.py --input data/samples/deployment/посвідчення.docx
    python run_pipeline.py --no-llm                    # лише детермінований прохід
    python run_pipeline.py --template leave_ticket     # примусова схема (для тестів)
    python run_pipeline.py --dry-run                   # нічого не зберігати

Вихід -- коротка таблиця по документах; повний запис лягає у сховище
(data/output/documents/<domain>/<id>.md) з YAML-шапкою, що містить subject,
facts і провенанс кожного поля.
"""
import argparse
import os
import sys

from pipeline.config import load_config
from pipeline.run import build_resources, process_target

STATUS_MARK = {
    "confirmed": "OK  ",
    "needs_review": "REV ",
    "unresolved": "UNRS",
    "duplicate": "DUP ",
}


def _force_utf8_output():
    """Кирилиця в stdout/stderr незалежно від кодової сторінки консолі й
    редіректу у файл. Без цього `python run_pipeline.py > log.txt` на Windows
    міг кинути UnicodeEncodeError уже ПІСЛЯ того, як усі документи успішно
    оброблені й збережені -- втрачався лише звіт, але виглядало як падіння."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv=None):
    _force_utf8_output()
    parser = argparse.ArgumentParser(description="Обробка документів у структуровані записи")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--input", default=None, help="файл або папка; за замовчуванням paths.input_dir")
    parser.add_argument("--template", default=None, help="примусово використати цей шаблон схеми")
    parser.add_argument("--no-llm", action="store_true", help="лише детермінований прохід")
    parser.add_argument("--dry-run", action="store_true", help="не зберігати нічого")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if not cfg.get("config_found"):
        # Явно, а не мовчки: без цього запуск не з кореня репо тихо працював
        # у дефолтному режимі (без LLM/OCR), і причина була невидима.
        print(f"[увага] конфіг не знайдено ({args.config}) -- працюємо на значеннях "
              f"за замовчуванням з pipeline/config.py (LLM і OCR вимкнені)", file=sys.stderr)
    target = args.input or cfg["paths"]["input_dir"]
    if not os.path.exists(target):
        print(f"Немає такого шляху: {target}", file=sys.stderr)
        print(f"Покладіть файли в {cfg['paths']['input_dir']} або вкажіть --input", file=sys.stderr)
        return 2

    res = build_resources(cfg, force_no_llm=args.no_llm)
    if args.dry_run:
        res["store"] = None
    for warning in res["warnings"]:
        print(f"[увага] {warning}", file=sys.stderr)

    print(f"Схеми: {[s['template'] for s in res['schemas']]}")
    print(f"Довідники: {sorted(res['dictionaries'])}")
    print(f"LLM: {'увімкнено' if res['llm'] else 'вимкнено'} | "
          f"OCR: {cfg['ocr']['engine'] if res['ocr'] else 'немає'}\n")

    results, skipped = process_target(target, res, cfg, force_template=args.template)

    # Про пропущене кажемо вголос: інакше людина, що поклала в папку архів або
    # файл невідомого типу, бачить лише "не знайдено файлів" без пояснення.
    if skipped["unsupported"]:
        print(f"[пропущено] непідтримуваний тип ({len(skipped['unsupported'])}): "
              f"{', '.join(skipped['unsupported'][:5])}"
              f"{' ...' if len(skipped['unsupported']) > 5 else ''}")
    if skipped["subdirs"]:
        print(f"[пропущено] підпапки ({len(skipped['subdirs'])}): "
              f"{', '.join(skipped['subdirs'][:5])} -- сканування не рекурсивне, "
              "покладіть файли безпосередньо в папку-приймач")

    if not results:
        # Порожня папка-приймач -- нормальний стан для планового запуску (нічого
        # не надійшло), тому код виходу 0. Помилка (2) -- лише коли вказаного
        # шляху взагалі немає, це перевіряється вище.
        if os.path.isdir(target):
            print(f"У папці-приймачі немає файлів для обробки: {target}")
            return 0
        print("Не знайдено файлів для обробки (.docx/.pdf/.jpg/.png/...)")
        return 1

    for meta in results:
        mark = STATUS_MARK.get(meta["status"], meta["status"])
        line = f"{mark} {meta.get('source_file')}"
        if meta.get("template"):
            line += f" | {meta['template']}"
        if meta.get("unknown_critical_fields"):
            line += f" | критичні прогалини: {meta['unknown_critical_fields']}"
        elif meta.get("unknown_fields"):
            line += f" | некритичні прогалини: {meta['unknown_fields']}"
        if meta.get("review_reason") == "random_audit":
            line += " | у вибірці аудиту"
        if meta.get("reason"):
            line += f" | {meta['reason']}"
        if meta.get("archived_to"):
            line += f"\n     -> перенесено в {meta['archived_to']}"
        print(line)
        for warning in meta.get("warnings", []):
            print(f"     [увага] {warning}")

    counts = {}
    for meta in results:
        counts[meta["status"]] = counts.get(meta["status"], 0) + 1
    print("\nПідсумок:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
