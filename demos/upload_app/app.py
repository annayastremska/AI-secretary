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
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import (FileResponse, JSONResponse,
                               RedirectResponse, Response)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
# Кеш Gradio -- у НАШУ теку, а не в /tmp.
#
# Знайдено 27.08 живим падінням: `/tmp/gradio` належить root (створений, коли
# апка ще запускалась від root), а служба тепер працює від ubuntu. Через це
# Gradio міг лише ПЕРЕВИКОРИСТОВУВАТИ вже наявний кеш іконки -- і в ту мить,
# коли я змінила mark.svg, хеш файла став іншим, mkdir у чужій теці впав із
# PermissionError, і апка не піднялась узагалі. Тобто зміна картинки валила
# сервіс, і залежало це від сміття в /tmp.
#
# Чужу теку не чіпаємо (вона root-ова й не наша) -- ставимо свій шлях.
os.environ.setdefault(
    "GRADIO_TEMP_DIR",
    os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "data", ".gradio-cache"))
os.makedirs(os.environ["GRADIO_TEMP_DIR"], exist_ok=True)

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

# ── Гостьовий вхід за посиланням із QR-коду ─────────────────────────────────
#
# Запит Ані 27.08: QR, який пропускає без набирання пароля -- на демо люди
# сканують з екрана в залі, і диктувати їм пароль означає втратити половину.
#
# Ключ у посиланні (`?k=...`) -- це ТОЙ САМИЙ пароль, просто вписаний в
# адресу. Так це й треба називати вголос: не «вхід без пароля», а «пароль у
# посиланні». Для демо на синтетичних даних компроміс правильний; для реальних
# документів -- ні, і в ТЗ це записано («простий логін, глибока кібербезпека
# поза скоупом»).
#
# Рівень доступу визначається СПОСОБОМ ВХОДУ, а не роллю: ролей у демо немає,
# і вдавати їх ми не будемо. Тому `access_level`, а не `role`.
GUEST_TOKEN = os.environ.get("APP_GUEST_TOKEN") or ""
#: Ім'я cookie й назви рівнів -- в одному місці, щоб не розійшлися між
#: гейтом, сторінками й чатом.
ACCESS_COOKIE = "ai_secretary_access"
LEVEL_OPERATOR = "operator"
LEVEL_GUEST = "guest"


def _same(given, expected):
    """Порівняння секретів за постійний час, БЕЗ падіння на кирилиці.

    Знайдено власним тестом: `hmac.compare_digest` на рядках із неASCII
    кидає TypeError -- тобто ключ із кирилицею в адресі давав би 500 замість
    чесного 401. Порівнюємо БАЙТИ: тоді будь-який вхід -- це просто інші
    байти, а не виняток. Постійний час лишається: саме він тут і потрібен,
    бо звичайне `==` видає довжину спільного префікса через час відповіді.
    """
    import hmac
    try:
        return hmac.compare_digest(str(given).encode("utf-8"),
                                   str(expected).encode("utf-8"))
    except Exception:
        return False


def access_level(request):
    """Рівень доступу цього запиту: оператор, гість або None (не пускати).

    Порядок навмисний: пароль сильніший за посилання. Людина, яка ввела
    пароль, лишається оператором навіть якщо в неї в браузері є гостьова
    cookie від попереднього заходу за QR.
    """
    import base64
    import hmac
    if BASIC_USER and BASIC_PASS:
        header = request.headers.get("authorization") or ""
        if header.lower().startswith("basic "):
            try:
                raw = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
                user, _, password = raw.partition(":")
                # compare_digest на обох полях: порівняння рядків == видає
                # довжину спільного префікса через час відповіді.
                if (_same(user, BASIC_USER) and _same(password, BASIC_PASS)):
                    return LEVEL_OPERATOR
            except Exception:
                pass
    if GUEST_TOKEN:
        given = (request.query_params.get("k")
                 or request.cookies.get(ACCESS_COOKIE) or "")
        if given and _same(given, GUEST_TOKEN):
            return LEVEL_GUEST
    if not (BASIC_USER and BASIC_PASS):
        # Локально гейта немає зовсім -- і тоді все дозволено, як було.
        return LEVEL_OPERATOR
    return None


@app.middleware("http")
async def _basic_auth(request, call_next):
    level = access_level(request)
    if level is not None:
        # Рівень їде далі у стані запиту: маршрут запису читає його, а не
        # вгадує вдруге. Одне місце, де рівень визначається.
        request.state.access_level = level
        response = await call_next(request)
        # Ключ із адреси -> у cookie, щоб він не світився в рядку браузера й
        # не поїхав у скопійованому посиланні. Дальші сторінки (і XHR чата)
        # ідуть уже з cookie.
        if (level == LEVEL_GUEST and request.query_params.get("k")
                and not request.cookies.get(ACCESS_COOKIE)):
            response.set_cookie(ACCESS_COOKIE, GUEST_TOKEN, httponly=True,
                                samesite="lax", max_age=12 * 3600)
        return response
    # Не пустили: 401 і запит пароля. Гостьовий ключ тут не згадуємо
    # навмисно -- підказувати спосіб входу тому, хто його не має, не треба.
        # realm -- ЛАТИНКОЮ: HTTP-заголовки кодуються latin-1, і кириличний
        # realm валив увесь гейт у 500 замість 401 (перевірено curl-ом:
        # UnicodeEncodeError у starlette при формуванні raw_headers). Тіло
        # відповіді українською -- воно в UTF-8 і це дозволено.
    return JSONResponse({"error": "потрібна авторизація"}, status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="AI-sekretar"'})


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
    # no-store -- та сама причина, що в маршруті статики: у демо правка
    # тексту мусить доїжджати до людини без очищення кеша браузера.
    return FileResponse(os.path.join(APP_DIR, "static", "index.html"),
                        headers={"Cache-Control": "no-store"})


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
    # Той самий шолом, але з ЯВНИМ кольором: <img> -- окремий документ, у
    # якому currentColor нічого не наслідує. Ним живуть аватар у стрічці
    # чата й іконка сторінки (favicon).
    "mark-avatar.svg": (os.path.join(APP_DIR, "chat_gradio", "assets",
                                     "mark-avatar.svg"), "image/svg+xml"),
    # Перемикач світлої/темної теми. Один файл на всі три екрани; лежить
    # локально, як і все інше -- зі сторінок назовні не йде жодного запиту.
    "theme-toggle.js": (os.path.join(APP_DIR, "static", "theme-toggle.js"),
                        "application/javascript"),
    # Показ рівня доступу. Сторінки статичні, а чат Gradio будує один раз при
    # запуску -- тобто рівень КОНКРЕТНОЇ людини вставити туди неможливо за
    # побудовою. Один запит /api/whoami з браузера вирішує це однаково на
    # всіх трьох екранах.
    "access.js": (os.path.join(APP_DIR, "static", "access.js"),
                  "application/javascript"),
    # Телефонна розкладка Й вимірювання висот, на яких тримається розкладка
    # чата (правило 18 у темі). Потрібен на будь-якому екрані, не лише на
    # телефоні -- тому підключається всюди.
    "mobile.js": (os.path.join(APP_DIR, "static", "mobile.js"),
                  "application/javascript"),
}

# Шрифти обличчя v2 -- у підпапці, і маршрут статики їх спершу НЕ віддавав
# (перелік плоский, підпапки в ньому немає -> 404, а сторінка тихо падала на
# системний шрифт). Додаємо їх у той самий білий перелік, а не відкриваємо
# видачу файлів за шляхом: перелік -- це і є захист від «/static/../.env».
for _f in sorted(os.listdir(os.path.join(APP_DIR, "static", "fonts"))
                 if os.path.isdir(os.path.join(APP_DIR, "static", "fonts"))
                 else []):
    if _f.endswith(".woff2"):
        STATIC_FILES["fonts/" + _f] = (
            os.path.join(APP_DIR, "static", "fonts", _f), "font/woff2")


#: Яку версію обличчя віддавати: v1 (як було) або v2 («строга» база, олива,
#: IBM Plex, дві теми). Рішення Ані 27.08: стару НЕ видаляти, дати порівняти.
#: Тому перемикання -- одна змінна, а не правка файлів.
APP_THEME = os.environ.get("APP_THEME", "v1").strip().lower()

#: Пари файлів на кожну версію: токени + шар звичайних сторінок.
_SKINS = {
    "v1": ("theme-tokens.css", "pages.css"),
    "v2": ("theme-tokens-v2.css", "pages-v2.css"),
    # v3 читає ДВА файли токенів: у v2 лежать @font-face (файли шрифтів ті
    # самі), у v3 -- палітра, ритм і брендовий блок. Дублювати @font-face у
    # двох файлах означало б два джерела правди.
    "v3": ("theme-tokens-v2.css", "theme-tokens-v3.css", "pages-v3.css"),
}


@app.get("/static/skin.css")
def skin_css():
    """Одне посилання зі сторінок замість двох файлів.

    Нащо маршрут, а не два <link>: інакше перемикання версії означало б
    правку розмітки обох сторінок, тобто ще одне місце, де версії можуть
    розійтись. Тут вибір робиться в одному рядку."""
    names = _SKINS.get(APP_THEME, _SKINS["v1"])
    parts = []
    for name in names:
        with open(os.path.join(APP_DIR, "static", name), encoding="utf-8") as fh:
            parts.append("/* " + name + " */\n" + fh.read())
    return Response("\n".join(parts), media_type="text/css",
                    headers={"Cache-Control": "no-store"})


#: QR гостьового входу. Картинка ГОТОВА -- її складають локально при
#: розгортанні й копіюють на сервер. Чому не генеруємо тут: на сервері немає
#: `qrcode`, а ставити пакет у спільний venv -- рівно та дія, якою 25.08
#: llama-cpp тихо стала процесорною. Файл гітігнорений: у ньому ключ.
QR_PATH = os.path.join(PROJECT_ROOT, "data", "qr-guest.png")


@app.get("/api/whoami")
def whoami(request: Request):
    """Яким рівнем зайшла ця людина. Потрібно СТОРІНКАМ: вони статичні й самі
    про це не знають, а дізнатися про свій рівень із відмови 403 після
    натискання кнопки -- найгірший спосіб."""
    level = getattr(request.state, "access_level", LEVEL_OPERATOR)
    return {"level": level,
            "can_write": level == LEVEL_OPERATOR and not PUBLIC_MODE,
            "guest_entry": bool(GUEST_TOKEN),
            # Чому не можна писати -- окремо від того, чи можна: причини дві
            # різні («ти гість» і «запис вимкнено на весь показ»), і плутати
            # їх не можна.
            "reason": ("запис вимкнено на весь показ" if PUBLIC_MODE
                       else None if level == LEVEL_OPERATOR
                       else "гостьовий вхід за посиланням")}


@app.get("/operator")
def become_operator(request: Request):
    """Стати оператором: віддаємо 401, і БРАУЗЕР сам показує вікно пароля.

    Нащо окремий маршрут. Людина, яка зайшла за QR, уже «всередині» -- вікна
    пароля вона не побачить ніколи, бо гейт її пускає. Щоб перейти на вищий
    рівень, потрібен запит, який СВІДОМО відмовляє: тоді браузер питає пароль,
    і далі заголовок Basic їде з кожним запитом сам.

    Гостьову cookie при цьому знімаємо: інакше вона й далі вигравала б у
    людини, яка щойно ввела пароль... точніше не вигравала б (пароль
    сильніший), але лишалась би сміттям, яке заплутує при відлагодженні.
    """
    if getattr(request.state, "access_level", None) == LEVEL_OPERATOR:
        resp = RedirectResponse("/", status_code=302)
        resp.delete_cookie(ACCESS_COOKIE)
        return resp
    return JSONResponse(
        {"error": "введіть пароль оператора"}, status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="AI-sekretar operator"'})


@app.get("/static/qr-guest.png")
def qr_guest():
    if not (GUEST_TOKEN and os.path.exists(QR_PATH)):
        return JSONResponse(status_code=404,
                            content={"error": "гостьовий вхід не налаштований"})
    return FileResponse(QR_PATH, media_type="image/png",
                        headers={"Cache-Control": "no-store"})


@app.get("/static/{name:path}")
def static_file(name: str):
    entry = STATIC_FILES.get(name)
    if entry is None:
        return JSONResponse(status_code=404, content={"error": "немає такого файлу"})
    path, media = entry
    # no-store: демо живе на одному сервері, файли крихітні, а ціна кеша
    # висока. 27.08 Аня двічі дивилась на ВЖЕ скорочені тексти в старій
    # редакції -- сторінка лежала в кеші браузера, і жодна правка до неї
    # не доїжджала. Для демо передбачуваність важливіша за економію байтів.
    return FileResponse(path, media_type=media,
                        headers={"Cache-Control": "no-store"})


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
    return FileResponse(os.path.join(APP_DIR, "static", "stats.html"),
                        headers={"Cache-Control": "no-store"})


@app.get("/api/stats")
def stats_api():
    # collect() винятків не кидає: недоступна база -- це поле db_available у
    # відповіді, і сторінка каже «база недоступна» замість 500.
    # Звіт прогону шукається у теці виходу ТОГО профілю, який читає апка.
    return stats_mod.collect(
        report_path=stats_mod.default_report_path(CONFIG_PATH))


@app.get("/api/chat-live")
def chat_live_api():
    """Тільки живі лічильники часу відповіді -- без жодного запиту в базу.

    Нащо окремий маршрут, якщо ці числа є і в /api/stats. Сторінка мусить
    оновлювати їх часто (інакше вони не «живі»), а /api/stats на кожен виклик
    робить сім запитів у Postgres. Постійно смикати чужу базу заради секундоміра
    -- це рівно те, чого домовлено не робити (база -- зона Андрія, і навмисно
    вантажити її ми не будемо). Тут читання з пам'яті процесу, ціна нульова.
    """
    return stats_mod.livemetrics.snapshot()


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
def commit(job_id: str, request: Request):
    # Рівень читаємо зі СТАНУ ЗАПИТУ, який поставив гейт, а не визначаємо
    # вдруге: два місця, де вирішується «можна писати», розійшлися б.
    #
    # PUBLIC_MODE лишається -- він вимикає запис для ВСІХ, і це інша річ:
    # «показ без запису взагалі» проти «запис лише для оператора».
    if getattr(request.state, "access_level", LEVEL_OPERATOR) != LEVEL_OPERATOR:
        return JSONResponse(status_code=403, content={
            "error": "Гостьовий доступ: обробку показуємо, а запис у базу "
                     "робить оператор. Поля вище витягнуті з вашого файла "
                     "по-справжньому — просто вони не стануть фактами в "
                     "спільній базі. Так і задумано: підтвердити факт може "
                     "не кожен, хто відкрив посилання."})
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
