# -*- coding: utf-8 -*-
"""Машинний слід одного ходу чата: по номеру звернення видно, ЯК склалась відповідь.

Запит Дениса 27.08: «щоб на скріні був цей код і можна було автоматично
прогнати аналіз, як система там давала інфо». Тобто номер на скріншоті — це
ключ, а по ключу мусить діставатись не рядок тексту, а СТРУКТУРА: якою дорогою
пішло питання, який шаблон обрано, який SQL виконано, скільком рядків він дав,
скільком це тривало.

## Чим це відрізняється від журналу, який уже є

Журнал (`logs/app.log`) — це рядок на хід для ЛЮДИНИ: коли, скільком, що
спитали, якою дорогою. Його читають очима, і цього досить, щоб знайти хід.

Цей слід — для МАШИНИ: один рядок JSON на хід, який можна прогнати скриптом і
порахувати, наприклад, «на скількох ходах модель складала SQL сама» або «які
шаблони жодного разу не спрацювали». Читати очима його ніхто не мусить.

## Що тут НЕ зберігається, і це головне

Правило проєкту: у git і в логи не їде жодна персональна чи бойова
інформація. Тому в сліді:

  * **немає тексту відповіді** — лише її довжина. Саме у відповіді стоять ПІБ,
    підрозділи, номери наказів, тобто все, чого тут бути не може;
  * **немає значень із бази** — лише СКІЛЬКОМ рядків повернув запит;
  * **є SQL ШАБЛОНУ, а не виконаний запит зі значеннями** — шаблон написали ми
    самі, він у git і персональних даних не містить за побудовою;
  * **є питання людини** — його вона написала сама, і воно вже є у звичайному
    журналі. Обрізається до 200 символів;
  * **є параметри** — дата, стан, підрозділ, шаблон імені. Ім'я в параметрі
    з'являється лише тому, що людина сама його назвала в питанні.

Якщо колись знадобиться зберігати відповіді — це окреме рішення, і зона
відповідальності інша. Тут його немає й не мусить бути.

## Як цим користуватись

    python demos/upload_app/trace_lookup.py cd3433        # один хід
    python demos/upload_app/trace_lookup.py --stats        # зведення по всіх
    curl -u operator:... http://.../api/trace/cd3433       # те саме маршрутом

Слід збирається ЗА ХІД: `begin(cid, question)` на початку, `step(...)` із
ярусів, `finish(...)` у кінці. Між ходами стан не тече — він живе в
contextvar, тобто окремо для кожного запиту навіть при паралельних питаннях.
"""
import contextvars
import datetime
import io
import json
import os
import threading

#: Куди пишемо. Поряд зі звичайним журналом, але окремим файлом: у того
#: читач — людина, у цього — скрипт, і змішувати їх означало б зробити обидва
#: незручними.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
TRACE_PATH = os.environ.get(
    "CHAT_TRACE_PATH", os.path.join(ROOT, "logs", "chat-trace.jsonl"))

#: Скільком символів питання зберігаємо. 200 -- досить, щоб хід було видно, і
#: не досить, щоб файл ріс від довгих вставок.
QUESTION_CHARS = 200

#: Слід ПОТОЧНОГО ходу. contextvar, а не глобальна змінна: два питання можуть
#: обробляти паралельно, і глобальна змінна змішала б їх сліди.
_current = contextvars.ContextVar("chat_trace", default=None)

_write_lock = threading.Lock()

#: Вимикається явно, якщо слід десь не потрібен.
ENABLED = os.environ.get("CHAT_TRACE", "1").strip().lower() not in (
    "0", "false", "no", "off")


def begin(cid, question):
    """Почати слід ходу. -> токен для reset(), або None, якщо вимкнено."""
    if not ENABLED:
        return None
    return _current.set({
        "id": cid,
        "at": datetime.datetime.now().replace(microsecond=0).isoformat(),
        "question": (question or "")[:QUESTION_CHARS],
        "steps": [],
    })


def reset(token):
    if token is not None:
        _current.reset(token)


def step(kind, **fields):
    """Записати крок ходу: вибір ярусу, шаблон, SQL, кількість рядків.

    Викликається з ярусів. Якщо сліду немає (вимкнено або поза ходом) --
    нічого не робить: ярус не мусить знати, чи ведеться слід.
    """
    cur = _current.get()
    if cur is None:
        return
    cur["steps"].append(dict(kind=kind, **fields))


def finish(road=None, seconds=None, answer_chars=None, refusal=None,
           has_source=None, error=None):
    """Дописати підсумок і зберегти рядок. Винятків не кидає НІКОЛИ.

    Слід -- допоміжне знання. Якщо його не вдалось записати (немає теки, диск
    повний, права), людина однаково мусить отримати відповідь на своє питання.
    """
    cur = _current.get()
    if cur is None:
        return
    cur.update({
        "road": road,
        "seconds": None if seconds is None else round(float(seconds), 2),
        "answer_chars": answer_chars,
        "refusal": refusal,
        "has_source": has_source,
        "error": error,
    })
    try:
        os.makedirs(os.path.dirname(TRACE_PATH), exist_ok=True)
        line = json.dumps(cur, ensure_ascii=False)
        with _write_lock:
            with io.open(TRACE_PATH, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")
    except Exception:
        pass


def read_all(path=None):
    """Усі сліди як список словників. Битий рядок пропускається (файл
    дописується на льоту, і останній рядок може бути обрізаний)."""
    out = []
    try:
        with io.open(path or TRACE_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return out


def find(cid, path=None):
    """Слід за номером звернення. -> словник або None.

    Останній збіг, а не перший: номер шестизначний, і на довгому файлі
    повторення теоретично можливе; при розборі скарги цікавить свіжий хід.
    """
    hit = None
    for row in read_all(path):
        if row.get("id") == cid:
            hit = row
    return hit


def summary(path=None):
    """Зведення по всіх ходах -- те, для чого слід і потрібен машині."""
    rows = read_all(path)
    if not rows:
        return {"turns": 0}
    roads, templates = {}, {}
    refusals = errors = no_source = 0
    times = []
    for r in rows:
        roads[r.get("road") or "невідомо"] = \
            roads.get(r.get("road") or "невідомо", 0) + 1
        for st in r.get("steps") or []:
            tid = st.get("template")
            if tid:
                templates[tid] = templates.get(tid, 0) + 1
        if r.get("refusal"):
            refusals += 1
        if r.get("error"):
            errors += 1
        if r.get("has_source") is False:
            no_source += 1
        if isinstance(r.get("seconds"), (int, float)):
            times.append(r["seconds"])
    times.sort()
    return {
        "turns": len(rows),
        "roads": roads,
        "templates": templates,
        "refusals": refusals,
        "errors": errors,
        # Найважливіше число зведення: відповідь без джерела -- зламане
        # правило продукту, а не просто погана цифра.
        "answers_without_source": no_source,
        "median_seconds": times[len(times) // 2] if times else None,
        "slowest_seconds": times[-1] if times else None,
        "first_at": rows[0].get("at"),
        "last_at": rows[-1].get("at"),
    }
