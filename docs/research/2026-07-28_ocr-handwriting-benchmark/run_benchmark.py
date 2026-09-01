#!/usr/bin/env python3
"""
Бенчмарк розпізнавання рукописних документів через OpenRouter.

Що робить:
  1. Бере PDF з documents/, рендерить кожну сторінку в PNG (pdftoppm, 200 dpi).
  2. Проганяє кожну сторінку через кожну модель зі models.txt.
  3. Просить JSON по полях зі fields.yaml — щоб ключі в усіх моделей були однакові.
  4. Складає звіт: таблиця «поле × модель» на кожну сторінку + порожня колонка
     «✍️ руками», куди ти вписуєш правильне значення під час ручного проходу.

Залежностей Python нема. Потрібен pdftoppm (є: /opt/homebrew/bin/pdftoppm).
Ключ: змінна OPENROUTER_API_KEY або файл .env поруч зі скриптом.
"""

import argparse
import base64
import concurrent.futures
import datetime
import json
import mimetypes
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(HERE, "documents")
RESULTS_DIR = os.path.join(HERE, "results")
MODELS_FILE = os.path.join(HERE, "models.txt")
PROMPT_FILE = os.path.join(HERE, "prompt.md")
FIELDS_FILE = os.path.join(HERE, "fields.yaml")
PAGES_DIR = os.path.join(HERE, ".pages-cache")
API_URL = "https://openrouter.ai/api/v1/chat/completions"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def build_ssl_context():
    """Python з python.org часто не має кореневих сертифікатів — беремо з certifi."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


SSL_CONTEXT = build_ssl_context()


# ─────────────────────────────── конфіги ───────────────────────────────

def load_api_key():
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    env_path = os.path.join(HERE, ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY"):
                    return line.split("=", 1)[1].strip().strip("\"'")
    sys.exit(
        "Немає ключа OPENROUTER_API_KEY.\n"
        "  openrouter.ai → Keys → Create key, потім:\n"
        f"  echo 'OPENROUTER_API_KEY=sk-or-...' > {os.path.join(HERE, '.env')}"
    )


def load_models():
    models = []
    with open(MODELS_FILE, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if line:
                models.append(line)
    if not models:
        sys.exit("У models.txt усі рядки закомментовані.")
    return models


def load_fields():
    """fields.yaml: рядки виду `- ключ: Опис`. Секції — `## Назва`."""
    if not os.path.exists(FIELDS_FILE):
        sys.exit(f"Нема {FIELDS_FILE} — спершу треба скласти схему полів шаблону.")
    fields, section = [], None
    with open(FIELDS_FILE, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip()
            if not line.strip() or line.lstrip().startswith("#") and not line.startswith("## "):
                continue
            if line.startswith("## "):
                section = line[3:].strip()
                continue
            stripped = line.strip()
            if stripped.startswith("- ") and ":" in stripped:
                key, desc = stripped[2:].split(":", 1)
                fields.append({"key": key.strip(), "desc": desc.strip(), "section": section})
    if not fields:
        sys.exit(f"У {FIELDS_FILE} не знайшов жодного поля (формат: `- ключ: Опис`).")
    return fields


def build_prompt(fields):
    template = open(PROMPT_FILE, encoding="utf-8").read()
    lines = []
    current = None
    for f in fields:
        if f["section"] and f["section"] != current:
            current = f["section"]
            lines.append(f"  // {current}")
        lines.append(f'  "{f["key"]}": "…",  // {f["desc"]}')
    schema = "{\n" + "\n".join(lines) + "\n}"
    if "{ПОЛЯ}" not in template:
        sys.exit("У prompt.md нема місця {ПОЛЯ} — нема куда вставити схему.")
    return template.replace("{ПОЛЯ}", schema)


# ─────────────────────────────── сторінки ───────────────────────────────

def render_pages(dpi, pages_limit):
    """PDF → PNG посторінково. Кеш у .pages-cache/. Повертає список сторінок."""
    if not os.path.isdir(DOCS_DIR):
        sys.exit(f"Нема папки {DOCS_DIR}")
    if not shutil.which("pdftoppm"):
        sys.exit("Нема pdftoppm. Встанови: brew install poppler")

    os.makedirs(PAGES_DIR, exist_ok=True)
    pages = []
    for name in sorted(os.listdir(DOCS_DIR)):
        src = os.path.join(DOCS_DIR, name)
        stem, ext = os.path.splitext(name)
        ext = ext.lower()

        if ext in IMAGE_EXT:
            pages.append({"doc": stem, "page": 1, "path": src, "id": stem})
            continue
        if ext != ".pdf":
            continue

        out_dir = os.path.join(PAGES_DIR, stem)
        stamp_file = os.path.join(out_dir, f".rendered-{dpi}dpi")
        if not (os.path.exists(stamp_file) and os.path.getmtime(stamp_file) >= os.path.getmtime(src)):
            shutil.rmtree(out_dir, ignore_errors=True)
            os.makedirs(out_dir, exist_ok=True)
            cmd = ["pdftoppm", "-png", "-r", str(dpi), src, os.path.join(out_dir, "стор")]
            try:
                subprocess.run(cmd, check=True, capture_output=True)
            except subprocess.CalledProcessError as exc:
                print(f"  ⚠️  {name}: pdftoppm упав — {exc.stderr.decode('utf-8', 'replace')[:200]}")
                continue
            open(stamp_file, "w").close()

        page_files = sorted(f for f in os.listdir(out_dir) if f.endswith(".png"))
        if pages_limit:
            page_files = page_files[:pages_limit]
        for path_name in page_files:
            num = int(re.search(r"(\d+)", path_name).group(1))
            pages.append({
                "doc": stem,
                "page": num,
                "path": os.path.join(out_dir, path_name),
                "id": f"{stem}__стор{num:02d}",
            })
    return pages


def as_data_url(path):
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as fh:
        return f"data:{mime};base64," + base64.b64encode(fh.read()).decode("ascii")


# ─────────────────────────────── запит ───────────────────────────────

def extract_json(text):
    """Дістає JSON з відповіді: ``` обгортки, преамбула, зайвий хвіст."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def call_model(api_key, model, prompt, data_url, timeout, retries):
    body = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
        "temperature": 0,
        "max_tokens": 4000,
        "usage": {"include": True},
    }
    data = json.dumps(body).encode("utf-8")
    last_error = None

    for attempt in range(retries + 1):
        req = urllib.request.Request(API_URL, data=data, headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/KSE-AI-Agentic-School/docflow-expertise",
            "X-Title": "docflow-expertise OCR handwriting benchmark",
        })
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as resp:
                parsed = json.loads(resp.read().decode("utf-8"))
            elapsed = round(time.time() - started, 1)
            choice = (parsed.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            if isinstance(text, list):
                text = "".join(part.get("text", "") for part in text)
            text = text.strip()
            usage = parsed.get("usage") or {}
            fields = extract_json(text)
            return {
                "ok": bool(text),
                "json_ok": fields is not None,
                "fields": fields or {},
                "text": text,
                "seconds": elapsed,
                "cost": usage.get("cost"),
                "finish_reason": choice.get("finish_reason"),
                "error": None if text else "порожня відповідь",
            }
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            last_error = f"HTTP {exc.code}: {detail}"
            if exc.code in (400, 401, 402, 403, 404):
                break  # модель не бачить картинок / нема ключа / нема грошей — ретрай не поможе
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            time.sleep(3 * (attempt + 1))

    return {"ok": False, "json_ok": False, "fields": {}, "text": "", "seconds": None,
            "cost": None, "finish_reason": None, "error": last_error}


# ─────────────────────────────── звіт ───────────────────────────────

def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def cell(value):
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    text = str(value).strip() or "—"
    return text.replace("|", "\\|").replace("\n", " ")


def write_report(run_dir, pages, models, fields, results):
    short = {m: m.split("/")[-1][:24] for m in models}
    out = [
        "# Порівняння моделей — розпізнавання рукописних документів",
        "",
        f"Запуск: {os.path.basename(run_dir)} · сторінок: {len(pages)} · моделей: {len(models)}",
        "",
        "## 1. Зведення по моделях",
        "",
        "| Модель | Відповіла | JSON валідний | Заповнено полів | Сер. час, с | $ |",
        "|---|---|---|---|---|---|",
    ]
    for m in models:
        rows = [results[(p["id"], m)] for p in pages]
        ok = [r for r in rows if r["ok"]]
        jsons = [r for r in rows if r["json_ok"]]
        filled = [
            sum(1 for f in fields if str(r["fields"].get(f["key"], "")).strip() not in ("", "—", "[?]", "None"))
            for r in jsons
        ]
        times = [r["seconds"] for r in ok if r["seconds"] is not None]
        costs = [r["cost"] for r in rows if isinstance(r["cost"], (int, float))]
        out.append(
            f"| `{m}` | {len(ok)}/{len(rows)} | {len(jsons)}/{len(rows)} "
            f"| {round(sum(filled) / len(filled), 1) if filled else '—'} з {len(fields)} "
            f"| {round(sum(times) / len(times), 1) if times else '—'} "
            f"| {round(sum(costs), 4) if costs else '—'} |"
        )

    errors = sorted({
        f"`{m}` → {results[(p['id'], m)]['error'][:160]}"
        for p in pages for m in models if results[(p["id"], m)]["error"]
    })
    if errors:
        out += ["", "**Помилки:**", ""] + [f"- {e}" for e in errors]

    out += [
        "",
        "## 2. Поле за полем",
        "",
        "Колонка **✍️ руками** — твоя. Вписуєш правильне значення з фото, ",
        "решта колонок стають перевіркою: збіглось / не збіглось.",
        "",
    ]
    for p in pages:
        out += [f"### {p['doc']} — сторінка {p['page']}", "", f"Картинка: `{os.path.relpath(p['path'], HERE)}`", ""]
        header = "| Поле | ✍️ руками | " + " | ".join(short[m] for m in models) + " |"
        out += [header, "|" + "---|" * (len(models) + 2)]
        section = None
        for f in fields:
            if f["section"] and f["section"] != section:
                section = f["section"]
                out.append(f"| **{section}** |" + " |" * (len(models) + 1))
            row = [f"`{f['key']}`", " "]
            for m in models:
                r = results[(p["id"], m)]
                row.append(cell(r["fields"].get(f["key"])) if r["json_ok"] else "❌")
            out.append("| " + " | ".join(row) + " |")
        out.append("")

    report = os.path.join(run_dir, "ПОРІВНЯННЯ.md")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    return report


# ─────────────────────────────── main ───────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Бенчмарк OCR рукописних документів через OpenRouter")
    ap.add_argument("--models", help="через кому — тільки ці моделі (замість models.txt)")
    ap.add_argument("--docs", type=int, default=0, help="узяти лише N перших документів")
    ap.add_argument("--pages", type=int, default=0, help="узяти лише N перших сторінок кожного документа")
    ap.add_argument("--dpi", type=int, default=200, help="роздільність рендеру PDF")
    ap.add_argument("--workers", type=int, default=3, help="запитів паралельно")
    ap.add_argument("--timeout", type=int, default=300, help="таймаут запиту, с")
    ap.add_argument("--retries", type=int, default=1, help="повторів після збою мережі")
    ap.add_argument("--dry-run", action="store_true", help="показати план і не витрачати гроші")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",")] if args.models else load_models()
    fields = load_fields()

    print("Рендерю сторінки…")
    pages = render_pages(args.dpi, args.pages)
    if args.docs:
        keep = sorted({p["doc"] for p in pages})[: args.docs]
        pages = [p for p in pages if p["doc"] in keep]
    if not pages:
        sys.exit(f"У documents/ нема PDF або картинок.\nПоклади файли сюди: {DOCS_DIR}")

    docs = sorted({p["doc"] for p in pages})
    print(f"\nДокументів: {len(docs)} · сторінок: {len(pages)} · моделей: {len(models)} "
          f"· полів у схемі: {len(fields)} · запитів: {len(pages) * len(models)}")
    for d in docs:
        print(f"  📄 {d} — {sum(1 for p in pages if p['doc'] == d)} стор.")
    for m in models:
        print(f"  🤖 {m}")
    if args.dry_run:
        print("\n--dry-run: нічого не відправляв.")
        return

    api_key = load_api_key()
    prompt = build_prompt(fields)

    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    run_dir = os.path.join(RESULTS_DIR, stamp)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "промпт-як-відправлено.md"), "w", encoding="utf-8") as fh:
        fh.write(prompt)

    data_urls = {p["id"]: as_data_url(p["path"]) for p in pages}
    jobs = [(p, m) for p in pages for m in models]
    results, done = {}, 0

    print(f"\nПоїхали ({args.workers} паралельно)…")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(call_model, api_key, m, prompt, data_urls[p["id"]], args.timeout, args.retries): (p, m)
            for p, m in jobs
        }
        for future in concurrent.futures.as_completed(futures):
            p, m = futures[future]
            r = future.result()
            results[(p["id"], m)] = r
            done += 1
            mark = "✅" if r["json_ok"] else ("⚠️" if r["ok"] else "❌")
            tail = f"{r['seconds']}s" if r["ok"] else (r["error"] or "")[:90]
            if r["ok"] and not r["json_ok"]:
                tail += " (відповіла, але не JSON)"
            print(f"  [{done}/{len(jobs)}] {mark} {p['id']} × {m} — {tail}")

            model_dir = os.path.join(run_dir, slug(m))
            os.makedirs(model_dir, exist_ok=True)
            with open(os.path.join(model_dir, p["id"] + ".json"), "w", encoding="utf-8") as fh:
                json.dump(r["fields"], fh, ensure_ascii=False, indent=2)
            if not r["json_ok"]:
                with open(os.path.join(model_dir, p["id"] + ".raw.txt"), "w", encoding="utf-8") as fh:
                    fh.write(r["text"] or f"ПОМИЛКА: {r['error']}")

    with open(os.path.join(run_dir, "raw.json"), "w", encoding="utf-8") as fh:
        json.dump([{"page": p["id"], "model": m, **results[(p["id"], m)]} for p, m in jobs],
                  fh, ensure_ascii=False, indent=2)

    report = write_report(run_dir, pages, models, fields, results)
    total = sum(r["cost"] for r in results.values() if isinstance(r["cost"], (int, float)))
    print(f"\nГотово. Витрачено ${round(total, 4)}")
    print(f"Звіт: {report}")


if __name__ == "__main__":
    main()
