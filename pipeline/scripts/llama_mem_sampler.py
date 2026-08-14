#!/usr/bin/env python
"""Побічний семплер пам'яті `llama-server`: RSS І commit, раз на N секунд.

Навіщо окремо від `ocr_batch_diag.py`: той пише RSS сервера раз на документ, і
на CPU-збірці цього ВИЯВИЛОСЬ НЕ ДОСИТЬ. Windows під тиском пам'яті обрізає
робочий набір процесу (working set), тому RSS може стояти на місці або навіть
падати, поки процес далі тримає сторінки -- вони просто переїхали у файл
підкачки. У такому режимі RSS перестає бути мірою накопичення, а commit
(`pagefile` у psutil, тобто зарезервована пам'ять процесу) лишається.

Заміряно 14.08.2026 на прогоні `--n-gpu-layers 0`: RSS llama-server стрибав
1.15 -> 1.73 -> 1.15 ГБ при вільній RAM 0.15 ГБ і свопі 5-6 ГБ. Без commit
відповісти «тече чи ні» на цих цифрах неможливо.

Запуск (паралельно до прогону, коштує один запит psutil на семпл):

    python pipeline/scripts/llama_mem_sampler.py \
        --out data/eval/reports/w7-ngl0-mem-sampler.tsv --interval 30
"""
import argparse
import time
from pathlib import Path

import psutil

MB = 1024 ** 2


def _samples():
    for proc in psutil.process_iter(["pid", "name"]):
        name = (proc.info.get("name") or "").lower()
        if "llama-server" in name or "llama_server" in name:
            try:
                mi = proc.memory_info()
            except psutil.Error:
                continue
            yield proc.info["pid"], mi.rss, getattr(mi, "pagefile", 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--interval", type=float, default=30.0)
    # 0 = поки не зникне останній llama-server (тобто поки триває прогін).
    parser.add_argument("--max-minutes", type=float, default=0)
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    seen_server = False
    # Порожній семпл САМ ПО СОБІ не означає, що сервер зник: заміряно
    # 14.08.2026, що `process_iter` час від часу віддає порожньо на живому
    # сервері (pid у логу до і після той самий), і семплер обривався посеред
    # прогону. Плюс між `stop()` і `start()` самозцілення сервера законно немає.
    empty_in_row = 0
    EMPTY_LIMIT = 5
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("sec\tpid\trss_mb\tcommit_mb\tfree_mb\tswap_mb\n")
        while True:
            rows = list(_samples())
            vm = psutil.virtual_memory()
            sw = psutil.swap_memory()
            now = time.time() - started
            for pid, rss, commit in rows:
                seen_server = True
                fh.write(f"{now:.0f}\t{pid}\t{rss / MB:.0f}\t{commit / MB:.0f}"
                         f"\t{vm.available / MB:.0f}\t{sw.used / MB:.0f}\n")
            fh.flush()
            # Вихід за фактом зникнення сервера, а не за таймером: інакше
            # семплер або обривався б посеред прогону, або жив би після нього.
            empty_in_row = 0 if rows else empty_in_row + 1
            if seen_server and empty_in_row >= EMPTY_LIMIT:
                break
            if args.max_minutes and now > args.max_minutes * 60:
                break
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
