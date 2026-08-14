#!/usr/bin/env python
"""Приладовий прогін OCR по пакету зображень -- рядок цифр НА КОЖЕН документ.

Навіщо окремий скрипт, а не `run_pipeline.py`: пайплайн міряє ЯКІСТЬ полів, а
тут треба міряти РЕСУРС і поведінку сервера розпізнавання. Класифікація й
екстракція сюди не входять свідомо -- вони додали б власний час і власну
пам'ять у кожен рядок, і накопичувальний збій OCR перестав би бути видимим на
тлі шуму.

Що пишеться по кожному документу (розд. B1 плану, docs/known-weak-spots.md
2.18-2.19):
  * вільна RAM у системі до/після і використання файлу підкачки;
  * RSS клієнта (цей процес) і RSS УСІХ живих llama-server, а не лише того,
    про якого знає Surya -- саме осиротілий сервер після перезапуску був би
    невидимий для `manager`, а пам'ять їв би;
  * `/health` сервера (питаємо сам сервер, не «чи є процес з таким pid»);
  * кількість блоків і символів;
  * час на документ;
  * ДЕЛЬТА власного логу llama-server за цей документ -- скільки за нього
    з'явилось рядків `failed to parse grammar`, `truncated = 1`,
    `ErrorDeviceLost`, і остання рядок-помилка дослівно.

Останній пункт -- головний. Правило розслідування: не приписувати причину без
рядка з логу. Клієнтський бік бачить лише «0 блоків»; ЧОМУ їх нуль, написано
тільки в `~/.cache/datalab/surya/llamacpp_server.log`, і цей файл дописується
між прогонами, тому різницю треба різати по зміщенню.

Запуск із кореня репозиторію:

    python pipeline/scripts/ocr_batch_diag.py \
        --input data/eval/samples/leave/synthetic-2026-05/png \
        --out data/eval/reports/w6-diag-batch16.log
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.ocr.surya_reader import make_surya_reader  # noqa: E402

GB = 1024 ** 3

# Власний лог llama-server. Шлях зашитий у surya
# (surya/inference/backends/llamacpp.py: log_path), тому дублюємо його тут, а
# не імпортуємо: імпорт приватної деталі бекенда зламався б тихо при оновленні
# surya, а відсутній файл ми й так обробляємо як «немає логу».
SERVER_LOG = Path("~/.cache/datalab/surya/llamacpp_server.log").expanduser()

# Рядки, які взагалі мають значення для розслідування. Ключ -- як писати в
# звіт, значення -- підрядок у логу.
LOG_MARKERS = {
    "grammar": "failed to parse grammar",
    "truncated": "truncated = 1",
    "device_lost": "ErrorDeviceLost",
    "no_slot": "no slot available",
    "ctx_shift": "context shift",
    "img_fail": "failed to process image",
    # Сервер перевикористовує KV-кеш попереднього запиту, якщо промпти схожі.
    # Наші бланки майже однакові, тож це РЕАЛЬНИЙ перенос стану між
    # документами -- єдиний механізм у стеку, який взагалі може «накопичувати».
    "kv_reuse": "selected slot by LCP similarity",
    "kv_lru": "selected slot by LRU",
}

_ERROR_LINE_RE = re.compile(r"^\d[\d.]* E ")


def _llama_processes():
    """Усі живі llama-server, а не лише «наш».

    Саме тут ховається кандидат на накопичення: `LlamaCppBackend.stop()` у
    surya НЕ вбиває процес (прибирання висить на atexit того, хто спавнив), а
    на Windows у логах уже бачили `Failed to stop llamacpp: [WinError 5]`.
    Якщо після перезапуску старий сервер лишається жити, вільна RAM падає на
    ~2.5 ГБ за кожен такий залишок -- і це видно ЛИШЕ якщо рахувати всі.
    """
    out = []
    for proc in psutil.process_iter(["pid", "name"]):
        name = (proc.info.get("name") or "").lower()
        if "llama-server" in name or "llama_server" in name:
            try:
                out.append((proc.info["pid"], proc.memory_info().rss))
            except psutil.Error:
                pass
    return sorted(out)


def _mem_snapshot(proc):
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    try:
        mi = proc.memory_info()
        rss = mi.rss
        # Windows: pagefile == commit charge процесу; зростання при рівному RSS
        # означає, що сторінки поїхали у підкачку.
        commit = getattr(mi, "pagefile", 0)
        faults = getattr(mi, "num_page_faults", 0)
    except psutil.Error:
        rss = commit = faults = 0
    return {
        "free": vm.available,
        "used_pct": vm.percent,
        "swap_used": sw.used,
        "swap_pct": sw.percent,
        "rss": rss,
        "commit": commit,
        "faults": faults,
    }


def _log_size():
    try:
        return SERVER_LOG.stat().st_size
    except OSError:
        return 0


def _log_delta(offset):
    """Що додалось у лог сервера з позиції `offset`. Повертає (лічильники,
    остання рядок-помилка, новий offset)."""
    counts = {k: 0 for k in LOG_MARKERS}
    last_error = ""
    new_offset = _log_size()
    if new_offset <= offset:
        return counts, last_error, new_offset
    try:
        with open(SERVER_LOG, "rb") as fh:
            fh.seek(offset)
            chunk = fh.read(new_offset - offset).decode("utf-8", errors="replace")
    except OSError as exc:
        return counts, f"(лог недоступний: {exc})", new_offset
    for line in chunk.splitlines():
        for key, needle in LOG_MARKERS.items():
            if needle in line:
                counts[key] += 1
        if _ERROR_LINE_RE.match(line) or "error" in line.lower()[:80]:
            last_error = line.strip()[:160]
    return counts, last_error, new_offset


def _fmt_llama(procs):
    if not procs:
        return "llama=НЕМАЄ"
        # «немає процесу» -- це не те саме, що «сервер мертвий»: сервер міг ще
        # не стартувати. Розрізняє їх колонка health.
    body = " ".join(f"{pid}:{rss / GB:.2f}" for pid, rss in procs)
    total = sum(rss for _, rss in procs) / GB
    return f"llama[{len(procs)}]={body} сум={total:.2f}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="папка із зображеннями")
    parser.add_argument("--out", required=True, help="файл звіту")
    parser.add_argument("--pattern", default="*.png")
    parser.add_argument("--limit", type=int, default=0, help="0 = усі")
    parser.add_argument("--inference-parallel", default=None)
    parser.add_argument("--llama-server-path", default=None)
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    files = sorted(Path(args.input).glob(args.pattern))
    if args.limit:
        files = files[: args.limit]
    if not files:
        print(f"нічого не знайдено: {args.input}/{args.pattern}", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    me = psutil.Process(os.getpid())

    # Позицію в логу беремо ДО завантаження моделі, щоб рядки старту сервера
    # потрапили в дельту першого документа, а не загубились.
    offset = _log_size()

    with open(out_path, "w", encoding="utf-8") as log:
        def emit(text):
            print(text)
            log.write(text + "\n")
            log.flush()

        emit(f"# приладовий прогін OCR: {len(files)} документів, {args.input}")
        emit(f"# лог сервера: {SERVER_LOG} (стартове зміщення {offset})")
        emit(f"# машина: RAM {psutil.virtual_memory().total / GB:.2f} ГБ, "
             f"вільно на старті {psutil.virtual_memory().available / GB:.2f} ГБ, "
             f"llama на старті: {_fmt_llama(_llama_processes())}")

        t_load = time.time()
        reader = make_surya_reader(args.llama_server_path, args.inference_parallel)
        emit(f"# модель завантажена за {time.time() - t_load:.1f} с")

        for index, path in enumerate(files, 1):
            before = _mem_snapshot(me)
            t0 = time.time()
            error = ""
            try:
                blocks = reader(str(path))
            except Exception as exc:  # noqa: BLE001 -- прогін не має падати на одному документі
                blocks = []
                error = f"{type(exc).__name__}: {exc}"
            elapsed = time.time() - t0
            after = _mem_snapshot(me)
            procs = _llama_processes()
            try:
                health = reader.health()
            except Exception:  # noqa: BLE001
                health = None
            chars = sum(len(b.get("text") or "") for b in blocks)
            counts, last_error, offset = _log_delta(offset)

            emit(
                f"{index:3d} | {path.name:<22} | {elapsed:7.1f} с"
                f" | RAM до {before['free'] / GB:5.2f} / після {after['free'] / GB:5.2f} ГБ"
                f" | своп {after['swap_used'] / GB:5.2f} ГБ ({after['swap_pct']:.0f}%)"
                f" | клієнт rss {after['rss'] / GB:.2f} commit {after['commit'] / GB:.2f}"
                f" pf+{after['faults'] - before['faults']}"
                f" | {_fmt_llama(procs)}"
                f" | health={health}"
                f" | блоків={len(blocks)} символів={chars}"
                f" | лог: " + " ".join(f"{k}={v}" for k, v in counts.items() if v)
                + (f" | ПОМИЛКА КЛІЄНТА: {error}" if error else "")
            )
            if last_error:
                emit(f"      останній рядок-помилка сервера: {last_error}")

        emit("# кінець")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
