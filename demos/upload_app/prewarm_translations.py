# -*- coding: utf-8 -*-
"""Наповнити кеш перекладів заздалегідь -- щоб на демо не чекати модель.

Кеш -- це продукт (див. `translate.py`), а цей скрипт -- його генератор.
Джерела рядків, у порядку важливості:

  1. **сторінки** (`static/*.html`, `static/*.js`) -- підписи, кнопки, стани.
     Беруться і рядкові літерали скриптів, і видимий текст розмітки: перша
     версія тесту повноти перевіряла лише літерали й була зелена, хоч `<h1>`
     і абзаци лишались українськими;
  2. **живі відповіді чата** -- якщо передати `--corpus <файл>` із текстом
     відповідей. Саме там лексика, якої в розмітці немає взагалі: «Доповідаю»,
     «Зріз», перелік осіб, блок «джерело», цитати з норм;
  3. **каталог шаблонів** -- назви й `answer_hint` (їх людина бачить у блоці
     «джерело»).

Запуск (на сервері, де є модель і карта):

    TRANSLATE_MODEL=1 .venv/bin/python demos/upload_app/prewarm_translations.py \
        --corpus /tmp/corpus.txt

Без `TRANSLATE_MODEL=1` скрипт лише ПОКАЖЕ, скільком рядків бракує, і нічого не
запише -- зручно, щоб побачити обсяг, не вантажачи модель.
"""
import argparse
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from demos.upload_app import translate as tr  # noqa: E402

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(APP_DIR, "static")


def _from_page(path):
    """Рядки сторінки: літерали скриптів + видимий текст розмітки."""
    s = io.open(path, encoding="utf-8").read()
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    out = set()
    # 1) рядкові літерали (без коментарів JS)
    code = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    for lit in re.findall(r'"([^"\n]*[А-ЯІЇЄҐа-яіїєґ][^"\n]*)"', code):
        t = lit.strip()
        if len(t) > 1 and "<" not in t:
            out.add(t)
    # 2) видимий текст розмітки
    if path.endswith(".html"):
        body = re.sub(r"<script.*?</script>", "", s, flags=re.S)
        body = re.sub(r"<style.*?</style>", "", body, flags=re.S)
        for chunk in re.split(r"<[^>]+>", body):
            t = " ".join(chunk.split())
            if len(t) > 1 and re.search(r"[А-ЯІЇЄҐа-яіїєґ]", t):
                out.add(t)
    return out


def _from_corpus(path):
    """Рядки з тексту живих відповідей чата (як їх побачить DOM)."""
    s = io.open(path, encoding="utf-8").read()
    s = s.replace("<br>", "\n")
    s = re.sub(r"<[^>]+>", "\n", s)
    out = set()
    for ln in s.split("\n"):
        t = " ".join(ln.split())
        # Службові маркери файла зняття («### питання», «###END») -- не текст
        # сторінки. Без цього рядка кеш поповнюється рядками, яких людина
        # ніколи не побачить: перший прогін приніс 25 таких.
        if t.startswith("###") or t.startswith("<<<"):
            continue
        if len(t) > 1 and re.search(r"[А-ЯІЇЄҐа-яіїєґ]", t):
            out.add(t)
    return out


def _from_chat_code():
    """Підписи сторінки чата -- вони в коді, не в розмітці.

    Чат малює Gradio, тому його шапка, кнопки й підказки живуть рядковими
    літералами в `chat_gradio/app.py` і `tiers.py`. Без цього джерела кеш
    покривав би дві звичайні сторінки й не покривав головний екран демо.
    """
    out = set()
    base = os.path.join(APP_DIR, "chat_gradio")
    for name in ("app.py", "tiers.py"):
        try:
            src = io.open(os.path.join(base, name), encoding="utf-8").read()
        except OSError:
            continue
        src = re.sub(r"(?m)^\s*#.*$", "", src)
        # Докстрінги -- це коментарі для нас, а не підписи: вирізаємо.
        src = re.sub(r'"""[\s\S]*?"""', "", src)
        for quote in ('"', "'"):
            pat = quote + r"([^" + quote + r"\n]*[А-ЯІЇЄҐа-яіїєґ][^" \
                  + quote + r"\n]*)" + quote
            for lit in re.findall(pat, src):
                t = " ".join(lit.split())
                if _looks_like_label(t):
                    out.add(t)
    return out


#: Символи, яких у ПІДПИСІ для людини не буває, а в регулярці чи в уламку
#: f-рядка -- буває. Без цього фільтра з коду чата виходило 608 «рядків», з
#: яких половина була регулярками («(?:у|в)\\s+(?:частин|штат)») і шматками
#: розмітки. Кеш заповнився б сміттям, а модель витратила б на нього час.
_NOT_LABEL = set("{}\\|[]<>%$^*+~`")


def _looks_like_label(t):
    if len(t) < 3 or len(t) > 400:
        return False
    if not re.search(r"[А-ЯІЇЄҐа-яіїєґ]", t):
        return False
    if any(ch in _NOT_LABEL for ch in t):
        return False
    if "(?" in t or t.startswith("_") or "  " in t:
        return False
    return True


def _from_catalog():
    """Назви шаблонів і підказки відповіді -- людина бачить їх у «джерелі»."""
    try:
        import yaml
        path = os.path.join(APP_DIR, "query_catalog.yaml")
        data = yaml.safe_load(io.open(path, encoding="utf-8"))
    except Exception:
        return set()
    out = set()
    for t in data.get("templates") or []:
        for key in ("title", "refusal"):
            v = (t.get(key) or "").strip()
            if v:
                out.add(" ".join(v.split()))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", default=None,
                    help="файл із текстом живих відповідей чата")
    ap.add_argument("--limit", type=int, default=0,
                    help="перекласти не більше N нових рядків (для проби)")
    args = ap.parse_args()

    texts = set()
    for name in sorted(os.listdir(STATIC)):
        if name.endswith((".html", ".js")) and name != "lang-toggle.js":
            texts |= _from_page(os.path.join(STATIC, name))
    texts |= _from_catalog()
    texts |= _from_chat_code()
    if args.corpus:
        texts |= _from_corpus(args.corpus)

    texts = sorted(t for t in texts if not tr._skip(t))
    known = tr.cache()
    missing = [t for t in texts if t not in known]

    print(f"рядків усього: {len(texts)}")
    print(f"уже в кеші:    {len(texts) - len(missing)}")
    print(f"бракує:        {len(missing)}")
    if not missing:
        return 0
    if not tr.MODEL_ENABLED:
        print("\nмодель вимкнена (TRANSLATE_MODEL=1, щоб перекласти). "
              "Перші десять, яких бракує:")
        for t in missing[:10]:
            print("   " + t[:100])
        return 0

    if args.limit:
        missing = missing[:args.limit]
    print(f"перекладаю {len(missing)}…")
    got = tr.translate(missing, allow_model=True)
    print(f"переклала: {len(got)} із {len(missing)}")
    tr.save_cache()
    print(f"кеш: {tr.CACHE_PATH} ({len(tr.cache())} рядків)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
