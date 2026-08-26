"""Демо-апка «завантажив фото → воно в базі» (запит команди БД,
docs/tasks/2026-08-22_plan-to-demo.md).

Головне правило: апка НЕ дублює мапінг. Два виклики:
  1) пайплайн -- subprocess `python run_pipeline.py --config ... --input <файл>`;
  2) запис у БД -- імпортом `ai_secretary_loader.load(md_path, original_path)`
     (той самий модуль, що в CLI-обгортки db/scripts/load_ai_secretary_output.py).

YAML-шапку вихідного .md апка читає РІВНО ЯК ПРИЛАД -- показати людині, що
витягнулось, ДО запису в базу (правило продукту: чернетка ≠ факт, кнопка
запису окрема від завантаження). Жодного власного SQL і жодного «яке поле в
яку таблицю» тут немає.

Запуск (з кореня репозиторію):
    python -m uvicorn demos.upload_app.app:app --host 127.0.0.1 --port 8000
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid

import yaml
from fastapi import FastAPI, UploadFile
from fastapi.responses import FileResponse, JSONResponse

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(APP_DIR))
# Профіль пайплайна: локальний CPU-дефолт, на GPU-сервері перевизначається
# змінною оточення (docs/deploy-gpu-server.md). Env, а не прапорець CLI:
# апку запускає uvicorn, і власних аргументів у неї немає.
CONFIG_PATH = os.environ.get(
    "APP_PIPELINE_CONFIG",
    os.path.join(APP_DIR, "config-app.yaml"))
if not os.path.isabs(CONFIG_PATH):
    CONFIG_PATH = os.path.join(PROJECT_ROOT, CONFIG_PATH)

# Тимчасові завантаження -- у гітігнорений шлях (data/inbox/* у .gitignore
# цілком). Підпапка, а не сам inbox: пакетний прогін `python run_pipeline.py`
# сканує inbox не рекурсивно, тож наші копії йому не заважають.
UPLOADS_DIR = os.path.join(PROJECT_ROOT, "data", "inbox", "upload_app")

# Вихід пайплайна читаємо З ТОГО САМОГО профілю, який пайплайну й передаємо.
#
# Раніше тут був літерал `data/output`, і це ламало головний сценарій демо.
# Заміряно 25.08.2026 на сервері: профіль `config-gpu.yaml` пише в
# `data/output-demo` (окремий вихід під демо-набір -- навмисно, щоб у базу
# їхав РІВНО він), апка ж шукала запис у `data/output/index/processed.jsonl`,
# не знаходила і віддавала «пайплайн завершився, але запису з таким хешем
# немає». Тобто пайплайн відпрацював правильно, а апка казала «не вдалося»:
# найгірший вид поломки -- та, що бреше про успішну роботу.
#
# Локально нічого не змінюється: у `config-app.yaml` вихід і є `data/output`.
def _output_root():
    """Корінь виходу пайплайна за активним профілем; `data/output` --
    останній рубіж, якщо профіль не читається (тоді апка все одно скаже
    про це на першому ж завантаженні, а не впаде на імпорті)."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        out = ((cfg.get("paths") or {}).get("output_dir")
               or (cfg.get("storage") or {}).get("local_root"))
        if out:
            return out if os.path.isabs(out) else os.path.join(PROJECT_ROOT, out)
    except (OSError, ValueError):
        pass
    return os.path.join(PROJECT_ROOT, "data", "output")


OUTPUT_ROOT = _output_root()
INDEX_PATH = os.path.join(OUTPUT_ROOT, "index", "processed.jsonl")

# Лоадер команди БД -- імпортом, як його ж CLI-обгортка. Шлях, не копія коду:
# мапінг «поле -> таблиця» лишається в одному місці (їхня зона).
sys.path.insert(0, os.path.join(PROJECT_ROOT, "airflow", "plugins"))

ALLOWED_EXTS = {".docx", ".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}

# OCR-прогони по одному (вимога перевірки ТЗ; два паралельні llama-server
# на CPU і не помістяться в пам'ять). Один лок на всі прогони пайплайна.
PIPELINE_LOCK = threading.Lock()

# Пайплайн на фото: старт Surya ~1-2 хв + OCR 1.5-3 хв/фото + LLM-виклики
# десятки секунд кожен. 30 хв -- стеля, після якої це вже збій.
PIPELINE_TIMEOUT_S = 30 * 60

JOBS = {}
JOBS_LOCK = threading.Lock()

app = FastAPI(title="AI-секретар")

# ── Гейт доступу ────────────────────────────────────────────────────────────
#
# Локально апка слухає 127.0.0.1 і гейт не потрібен. На спільному сервері
# порт прокинутий НАЗОВНІ (HTTP 7302), а в апці немає жодного поняття
# користувача: будь-хто, хто знає адресу, може завантажити документ і
# ставити питання до бази. Тому: якщо виставлено APP_BASIC_USER/PASS --
# вимагаємо HTTP Basic на ВСІХ маршрутах (включно з /chat, який Gradio
# обслуговує своїми XHR -- браузер підставляє Basic автоматично, тому
# схема працює і для нього, на відміну від токена в заголовку).
#
# Чесна межа: Basic поверх plain HTTP передає пароль у відкритому вигляді.
# Це загорожа від випадкового сканера, а НЕ захист даних. Саме тому окреме
# правило розгортання: на сервер їдуть лише синтетичні документи
# (docs/deploy-gpu-server.md, розд. «Що НЕ їде на сервер»).
BASIC_USER = os.environ.get("APP_BASIC_USER") or ""
BASIC_PASS = os.environ.get("APP_BASIC_PASS") or ""


@app.middleware("http")
async def _basic_auth(request, call_next):
    if not (BASIC_USER and BASIC_PASS):
        return await call_next(request)
    import base64
    import hmac
    header = request.headers.get("authorization") or ""
    ok = False
    if header.lower().startswith("basic "):
        try:
            raw = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
            user, _, password = raw.partition(":")
            # compare_digest на обох полях: порівняння рядків == видає
            # довжину спільного префікса через час відповіді.
            ok = (hmac.compare_digest(user, BASIC_USER)
                  and hmac.compare_digest(password, BASIC_PASS))
        except Exception:
            ok = False
    if not ok:
        # realm -- ЛАТИНКОЮ: HTTP-заголовки кодуються latin-1, і кириличний
        # realm валив увесь гейт у 500 замість 401 (перевірено curl-ом:
        # UnicodeEncodeError у starlette при формуванні raw_headers). Тіло
        # відповіді українською -- воно в UTF-8 і це дозволено.
        return JSONResponse({"error": "потрібна авторизація"}, status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="AI-sekretar"'})
    return await call_next(request)


def _now():
    return time.perf_counter()


def _file_sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_index():
    """{file_hash: storage_key} з індексу пайплайна. Читаємо як дані (це
    його власний публічний слід дедуплікації), нічого не пишемо."""
    index = {}
    if not os.path.exists(INDEX_PATH):
        return index
    with open(INDEX_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("file_hash"):
                index[row["file_hash"]] = row.get("key")
    return index


def _read_frontmatter(md_path):
    """YAML-шапка вихідного .md -- ЛИШЕ для показу людині (не мапінг)."""
    with open(md_path, encoding="utf-8") as f:
        content = f.read()
    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"у {md_path} немає YAML-шапки")
    return yaml.safe_load(parts[1])


def _fact_labels():
    """Людські назви типів фактів -- з довідника КОМАНДИ БД (той самий
    FACT_TYPE_LABELS, що керує dimensions.name), а не власна копія: підпис у
    UI і назва виміру в базі мають збігатися. Лише для показу, не мапінг."""
    try:
        import ai_secretary_loader
        labels = dict(ai_secretary_loader.FACT_TYPE_LABELS)
    except Exception:
        labels = {}
    labels.setdefault("rank", "Звання")
    labels.setdefault("position", "Посада")
    return labels


def _preview_from_meta(meta):
    """Рівно те, що треба показати ДО запису в базу. Прогалини -- видимі
    прогалини, не порожні клітинки."""
    provenance = meta.get("field_provenance") or {}
    return {
        "fact_labels": _fact_labels(),
        "status": meta.get("status"),
        "template": meta.get("template"),
        "domain": meta.get("domain"),
        "source_kind": meta.get("source_kind"),
        "reason": meta.get("reason"),
        "review_reason": meta.get("review_reason"),
        "review_queue": meta.get("review_queue"),
        "subject": meta.get("subject") or {},
        "facts": meta.get("facts") or [],
        "field_provenance": provenance,
        "unknown_fields": meta.get("unknown_fields") or [],
        "unknown_critical_fields": meta.get("unknown_critical_fields") or [],
        "confirmed_empty_fields": meta.get("confirmed_empty_fields") or [],
        "warnings": meta.get("warnings") or [],
        "ocr_blocks": meta.get("ocr_blocks"),
        "ocr_chars": meta.get("ocr_chars"),
        "date_range_error": meta.get("date_range_error"),
        "consistency_problems": meta.get("consistency_problems") or {},
    }


def _set_step(job, name, state, seconds=None, detail=None):
    # started_at -- щоб секундомір у UI рахувався від старту кроку НА СЕРВЕРІ:
    # сторінку можна перезавантажити посеред довгого OCR, і таймер не має
    # починатися з нуля.
    for step in job["steps"]:
        if step["name"] == name:
            step["state"] = state
            if state == "running" and not step.get("started_at"):
                step["started_at"] = time.time()
            if seconds is not None:
                step["seconds"] = round(seconds, 1)
            if detail is not None:
                step["detail"] = detail
            return
    job["steps"].append({"name": name, "state": state,
                         "started_at": time.time() if state == "running" else None,
                         "seconds": round(seconds, 1) if seconds is not None else None,
                         "detail": detail})


def _run_pipeline_job(job_id):
    with JOBS_LOCK:
        job = JOBS[job_id]
    original_path = job["original_path"]

    file_hash = _file_sha256(original_path)
    job["file_hash"] = file_hash
    was_known = file_hash in _read_index()

    # Короткі підказки про тривалість показує фронтенд за job["is_image"] --
    # бекенд не диктує тексти UI.
    _set_step(job, "pipeline", "running")
    job["state"] = "pipeline_running"
    started = _now()

    with PIPELINE_LOCK:  # OCR-прогони строго по одному
        try:
            proc = subprocess.run(
                [sys.executable, "run_pipeline.py",
                 "--config", CONFIG_PATH, "--input", original_path],
                cwd=PROJECT_ROOT, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=PIPELINE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            _set_step(job, "pipeline", "failed", _now() - started,
                      f"пайплайн не завершився за {PIPELINE_TIMEOUT_S // 60} хв -- перервано")
            job["state"] = "pipeline_failed"
            job["error"] = "таймаут пайплайна"
            return
        except OSError as exc:
            _set_step(job, "pipeline", "failed", _now() - started, str(exc))
            job["state"] = "pipeline_failed"
            job["error"] = f"не вдалося запустити пайплайн: {exc}"
            return

    elapsed = _now() - started
    job["pipeline_stdout"] = proc.stdout[-4000:]
    job["pipeline_stderr"] = proc.stderr[-4000:]

    if proc.returncode != 0:
        _set_step(job, "pipeline", "failed", elapsed,
                  f"код виходу {proc.returncode}")
        job["state"] = "pipeline_failed"
        job["error"] = (proc.stderr or proc.stdout or "").strip()[-1500:] \
            or f"пайплайн упав з кодом {proc.returncode}"
        return

    # Результат шукаємо за хешем в індексі пайплайна -- так само його шукає
    # і сам пайплайн (дедуплікація). Для дубліката індекс уже містив хеш,
    # і ключ вказує на ІСНУЮЧИЙ запис -- саме його й показуємо.
    key = _read_index().get(file_hash)
    if not key:
        _set_step(job, "pipeline", "failed", elapsed, "вихідний .md не знайдено в індексі")
        job["state"] = "pipeline_failed"
        # Шлях у тексті -- справжній, а не літерал: саме розбіжність між
        # «де апка шукала» і «куди профіль писав» і давала цю помилку
        # 25.08, і повідомлення з вигаданим шляхом тоді збивало зі сліду.
        job["error"] = (f"пайплайн завершився, але запису з таким хешем немає в "
                        f"{INDEX_PATH} (профіль: {CONFIG_PATH}) -- див. лог "
                        f"пайплайна нижче")
        return

    md_path = os.path.join(OUTPUT_ROOT, key.replace("/", os.sep))
    try:
        meta = _read_frontmatter(md_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        _set_step(job, "pipeline", "failed", elapsed, str(exc))
        job["state"] = "pipeline_failed"
        job["error"] = f"не вдалося прочитати вихідний .md: {exc}"
        return

    _set_step(job, "pipeline", "done", elapsed)
    job["md_path"] = md_path
    job["storage_key"] = key
    job["duplicate_upload"] = was_known
    job["preview"] = _preview_from_meta(meta)
    job["state"] = "ready_for_review"


def _commit_job(job_id):
    with JOBS_LOCK:
        job = JOBS[job_id]
    _set_step(job, "db", "running")
    job["state"] = "committing"
    started = _now()
    try:
        import ai_secretary_loader
        result = ai_secretary_loader.load(job["md_path"], job["original_path"])
    except KeyError as exc:
        _set_step(job, "db", "failed", _now() - started)
        job["state"] = "commit_failed"
        job["error"] = (f"БД недоступна: немає змінної середовища {exc} -- "
                        "перевірте .env у корені репозиторію (потрібен рядок "
                        "DATABASE_URL=..., див. README апки)")
        return
    except Exception as exc:
        _set_step(job, "db", "failed", _now() - started)
        job["state"] = "commit_failed"
        # НЕ називаємо кожен збій «БД недоступна» (знайдено блоком 1 перевірки
        # 26.08). Два коміти провалились, і повідомлення сказало «БД
        # недоступна» -- при живій базі, у яку сусідній документ записався
        # секундою раніше. Причина збою може бути будь-яка (сам документ,
        # правило лоадера, конфлікт), і людина за неправильною причиною піде
        # лагодити не те.
        import psycopg as _pg
        if isinstance(exc, _pg.OperationalError):
            job["error"] = (f"БД недоступна: {type(exc).__name__}: {exc}. "
                            "Спробуйте ще раз, коли база підніметься — "
                            "документ уже оброблений, повторна обробка не "
                            "потрібна.")
        else:
            job["error"] = (f"Запис не вдався (база доступна): "
                            f"{type(exc).__name__}: {exc}")
        return
    _set_step(job, "db", "done", _now() - started)
    job["db_result"] = {
        "document_id": result.get("document_id"),
        "doc_state": result.get("doc_state"),
        "facts_inserted": [list(pair) for pair in result.get("facts_inserted") or []],
    }
    job["state"] = "committed"


@app.get("/")
def index():
    return FileResponse(os.path.join(APP_DIR, "static", "index.html"))


# ── Статичні файли обличчя ──────────────────────────────────────────────────
#
# Явний перелік, а не StaticFiles на теку: назви файлів тут відомі наперед, а
# перелік не дає ні обходу шляху (`/static/../.env`), ні випадкової віддачі
# файлу, який хтось поклав у теку «на хвилинку». Знак системи один на всі
# сторінки -- він живе в chat_gradio/assets і не дублюється.
STATIC_FILES = {
    "theme-tokens.css": (os.path.join(APP_DIR, "static", "theme-tokens.css"),
                         "text/css"),
    "pages.css": (os.path.join(APP_DIR, "static", "pages.css"), "text/css"),
    "mark.svg": (os.path.join(APP_DIR, "chat_gradio", "assets", "mark.svg"),
                 "image/svg+xml"),
}


@app.get("/static/{name}")
def static_file(name: str):
    entry = STATIC_FILES.get(name)
    if entry is None:
        return JSONResponse(status_code=404, content={"error": "немає такого файлу"})
    path, media = entry
    return FileResponse(path, media_type=media)


# ── Сторінка «Статистика» (задача B2) ───────────────────────────────────────
#
# П'ята річ, яку мусить уміти демо: показати цифри якості. Досі відповідь на
# «де цифри» була «у консолі»: run_pipeline.py пише run-report.json, база
# знає свої лічильники, а в інтерфейсі не було ні того, ні того.
#
# Числа збирає demos/upload_app/stats.py -- тим самим read-only способом, що
# чат (chat_gradio/db.py::_query). Ключове правило продукту, яке ця сторінка
# мусить ПОКАЗУВАТИ, а не переказувати: підтверджені факти й чернетки --
# окремі числа, які ніде не складаються в одне.
from demos.upload_app import stats as stats_mod  # noqa: E402


@app.get("/stats")
def stats_page():
    return FileResponse(os.path.join(APP_DIR, "static", "stats.html"))


@app.get("/api/stats")
def stats_api():
    # collect() винятків не кидає: недоступна база -- це поле db_available у
    # відповіді, і сторінка каже «база недоступна» замість 500.
    # Звіт прогону шукається у теці виходу ТОГО профілю, який читає апка.
    return stats_mod.collect(
        report_path=stats_mod.default_report_path(CONFIG_PATH))


# ── Чат (друга сторінка тієї самої апки, /chat) ──────────────────────────────
# Вікно -- Gradio-чат команди (demos/upload_app/chat_gradio/, джерело:
# answer/chat@andriy-followup-context, адаптація під Postgres), змонтований у
# цю саму FastAPI: один процес, один порт, і той самий Basic-auth гейт
# (_basic_auth вище -- HTTP-middleware обгортає ВЕСЬ ASGI-стек, тож і
# змонтований Gradio під /chat; перевіряється тестом
# tests/test_app_gate.py). Стара саморобна сторінка static/chat.html і
# /api/chat прибрані; колишній chat.py перенесено в chat_gradio/tiers.py --
# звідти чат бере каталог шаблонів, ярус 2 і резидентну модель.

from demos.upload_app.chat_gradio import app as chat_app  # noqa: E402

# Прогрів моделі у фоні при старті: перший користувач не платить ~30 с
# за завантаження ваг (модель резидентна, вантажиться один раз на процес).
chat_app.warm_up_async()


@app.post("/api/upload")
async def upload(file: UploadFile):
    name = os.path.basename(file.filename or "")
    ext = os.path.splitext(name)[1].lower()
    if not name or ext not in ALLOWED_EXTS:
        return JSONResponse(status_code=400, content={
            "error": f"непідтримуваний тип файлу '{ext or name}'. "
                     f"Приймаються: {', '.join(sorted(ALLOWED_EXTS))}"})

    job_id = str(uuid.uuid4())
    job_dir = os.path.join(UPLOADS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    dest = os.path.join(job_dir, name)

    started = _now()
    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)
    upload_seconds = _now() - started

    job = {
        "id": job_id,
        "filename": name,
        "original_path": dest,
        "is_image": ext not in (".docx", ".pdf"),
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "state": "queued",
        "steps": [{"name": "upload", "state": "done",
                   "seconds": round(upload_seconds, 1), "detail": None}],
        "error": None,
        "preview": None,
        "db_result": None,
    }
    with JOBS_LOCK:
        JOBS[job_id] = job

    threading.Thread(target=_run_pipeline_job, args=(job_id,), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "немає такої задачі"})
    payload = {k: v for k, v in job.items() if k not in ("original_path", "md_path")}
    # Для кроку, що триває, віддаємо поточний лічильник із сервера --
    # секундомір у UI переживає перезавантаження сторінки.
    payload["steps"] = [
        dict(step, seconds=(round(time.time() - step["started_at"], 1)
                            if step["state"] == "running" and step.get("started_at")
                            else step["seconds"]))
        for step in payload["steps"]
    ]
    return payload


#: Публічний режим: сторінки й чат працюють, ЗАПИС у базу заблокований.
#:
#: Нащо. Для демо сайт відкривають за посиланням і QR -- отже кнопку
#: «підтвердити» бачить будь-хто в залі. Один натиск -- і в базі зʼявляється
#: документ, а цифри на екрані розходяться з тими, які ми щойно назвали. Саме
#: це я зробила собі сама 26.08, перевіряючи крайні випадки: три сміттєвих
#: документи в живій базі й розбіжність у приладі звірки.
#:
#: Обробку не глушимо: людина кладе файл, бачить кроки й витягнуті поля -- це і
#: є показ. Не відбувається лише останній крок, і про це сказано прямо.
PUBLIC_MODE = os.environ.get("APP_PUBLIC_MODE", "0").strip().lower() in (
    "1", "true", "yes", "on")


@app.post("/api/jobs/{job_id}/commit")
def commit(job_id: str):
    if PUBLIC_MODE:
        return JSONResponse(status_code=403, content={
            "error": "Демонстраційний доступ: обробку показуємо, а запис у "
                     "базу заблокований. Поля вище витягнуті з вашого файла "
                     "по-справжньому — просто вони не стануть фактами в "
                     "спільній базі."})
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "немає такої задачі"})
    if job["state"] not in ("ready_for_review", "commit_failed"):
        return JSONResponse(status_code=409, content={
            "error": f"запис у базу можливий лише після перегляду (стан: {job['state']})"})
    threading.Thread(target=_commit_job, args=(job_id,), daemon=True).start()
    return {"ok": True}


# Монтування Gradio-чату останнім: він додає власні маршрути під /chat,
# API-маршрути апки вище лишаються як були.
import gradio as gr  # noqa: E402

# theme/head у Gradio 6 передаються сюди, не в Blocks (у Blocks вони мовчки
# губляться в kwargs -- перевірено: сторінка приїжджала без theme.css)
app = gr.mount_gradio_app(app, chat_app.build_blocks(), path="/chat",
                          theme=chat_app.make_theme(),
                          head=chat_app.make_head_css())


# ── Запуск ──────────────────────────────────────────────────────────────────
#
# Локально: python -m demos.upload_app.app  ->  127.0.0.1:8000
# На GPU-сервері: APP_HOST=0.0.0.0 APP_PORT=80 (ззовні доступно як :7302).
#
# Дефолт саме 127.0.0.1, а не 0.0.0.0: апка без гейта не має слухати
# зовнішній інтерфейс навіть випадково. Нижче -- явна відмова стартувати на
# 0.0.0.0 без пароля, щоб «забув виставити APP_BASIC_*» не перетворювалось
# на публічний доступ до бази.
if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "8000"))
    # Гейт -- ОПЦІЯ, не вимога: локально (127.0.0.1) він не потрібен, і за
    # замовчуванням жодного пароля в проєкті немає. Але «слухати зовнішній
    # інтерфейс без будь-якої авторизації» -- рішення, яке має бути свідомим,
    # а не наслідком забутої змінної: у апки немає поняття користувача, тож
    # відкритий порт означає доступ будь-кого до завантаження документів і до
    # бази. Тому не відмова, а явне підтвердження APP_ALLOW_PUBLIC=1.
    public = host not in ("127.0.0.1", "localhost")
    if public and not (BASIC_USER and BASIC_PASS):
        if os.environ.get("APP_ALLOW_PUBLIC") != "1":
            raise SystemExit(
                f"host={host} слухає зовнішній інтерфейс без авторизації. "
                "Обери одне: APP_BASIC_USER + APP_BASIC_PASS -- закрити "
                "гейтом, або APP_ALLOW_PUBLIC=1 -- свідомо відкрито. "
                "Деталі -- docs/deploy-gpu-server.md, розд. 2.")
        print(f"[УВАГА] {host} -- відкрито БЕЗ авторизації "
              "(APP_ALLOW_PUBLIC=1). Реальних документів тут бути не має.")
    print(f"AI-секретар: http://{host}:{port}  (чат -- /chat, "
          f"конфіг пайплайна -- {os.path.relpath(CONFIG_PATH, PROJECT_ROOT)})")
    uvicorn.run(app, host=host, port=port)
