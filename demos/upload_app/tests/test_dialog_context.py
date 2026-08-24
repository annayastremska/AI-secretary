"""Контекст діалогу Андрія в об'єднаному чаті (задача 1.2).

Критерій плану: «а 22?», відносні дати, одруківки місяців працюють.
Тести не потребують ні бази, ні моделі: функції контексту детерміновані,
а наскрізний прогін «а 22?» іде без моделі (CHAT_MODEL_PATH неіснуючий,
див. conftest) з підміненими функціями стику db.
"""
import datetime

import demos.upload_app.chat_gradio.app as chat_app


# ── extract_date: усі форми, які розуміє Андріїва версія ─────────────────────

def test_extract_date_iso():
    assert chat_app.extract_date("Хто відсутній 2026-05-15?") == "2026-05-15"


def test_extract_date_dotted():
    assert chat_app.extract_date("а 15.05.2026?") == "2026-05-15"


def test_extract_date_words():
    assert chat_app.extract_date("а 23 травня?") == "2026-05-23"


def test_extract_date_month_typo():
    # одруківка в КОРЕНІ слова -- стем не ловить, ловить difflib по повних
    # формах (раніше це виправляла модель, але дату в неї забрали)
    assert chat_app.extract_date("а 23 тралня?") == "2026-05-23"
    assert chat_app.extract_date("хто у відпустці 6 трвня?") == "2026-05-06"


def test_extract_date_relative():
    today = datetime.date.today()
    assert chat_app.extract_date("хто повертається завтра?") == str(
        today + datetime.timedelta(days=1))
    assert chat_app.extract_date("скільки відсутніх сьогодні?") == str(today)
    assert chat_app.extract_date("хто поїхав вчора?") == str(
        today - datetime.timedelta(days=1))


def test_extract_date_not_subdivision():
    # «2-га механізована рота» не мусить ставати датою
    assert chat_app.extract_date("а по 2-й механізованій роті?") is None


# ── _refine_day: «а 22?» ─────────────────────────────────────────────────────

def test_refine_day_basic():
    assert chat_app._refine_day("а 22?", "2026-05-23") == "2026-05-22"
    assert chat_app._refine_day("22", "2026-05-23") == "2026-05-22"
    assert chat_app._refine_day("а 15-го?", "2026-05-23") == "2026-05-15"


def test_refine_day_invalid_or_foreign():
    # 31 квітня не існує -- не вигадуємо
    assert chat_app._refine_day("а 31?", "2026-04-10") is None
    # після числа іменник -- це не «число місяця»
    assert chat_app._refine_day("а по 2 роті?", "2026-05-23") is None
    # без попередньої дати добирати нема звідки
    assert chat_app._refine_day("а 22?", None) is None


# ── _answers_clarification: гейт склейки уточнень ────────────────────────────

def test_answers_clarification():
    assert chat_app._answers_clarification("2026-05-05") is True
    assert chat_app._answers_clarification("покажи №301") is True
    # нове питання без сутності -- НЕ відповідь на уточнення: людина після
    # «за яку дату?» спитала інше і не мусить впертись у жорстку відмову
    assert chat_app._answers_clarification("а скільки у відпустці?") is False


# ── _last_user_question: останнє питання З наміром ───────────────────────────

def _hist(*pairs):
    out = []
    for role, text in pairs:
        out.append({"role": role, "content": text})
    return out


def test_last_user_question_prefers_intent():
    h = _hist(("user", "Хто повертається 23 травня?"),
              ("assistant", "відповідь"),
              ("user", "а 22?"),
              ("assistant", "відповідь"))
    # ланцюжок уточнень: контекстом іде питання з наміром, а не «а 22?»
    assert chat_app._last_user_question(h) == "Хто повертається 23 травня?"


def test_last_user_question_fallback_to_last():
    h = _hist(("user", "просто репліка без наміру"), ("assistant", "х"))
    assert chat_app._last_user_question(h) == "просто репліка без наміру"


# ── _carry_over: слоти лише доречні наміру ───────────────────────────────────

def test_carry_over_respects_intent_slots():
    prev = {"intent": "хто_відсутній", "date": "2026-05-23",
            "subdivision": None, "name": None, "doc_number": None}
    got = chat_app._carry_over(
        {"intent": "документ_за_номером", "date": None, "subdivision": None,
         "name": None, "doc_number": "№301"}, prev)
    # документ_за_номером не тягне дату: «Хто у відпустці за квитком №301?»
    # після питання з датою не мусить успадкувати ту дату
    assert got["date"] is None
    got2 = chat_app._carry_over(
        {"intent": None, "date": None, "subdivision": None,
         "name": None, "doc_number": None}, prev)
    assert got2["intent"] == "хто_відсутній" and got2["date"] == "2026-05-23"


# ── наскрізний прогін «а 22?» без моделі ─────────────────────────────────────

def test_bare_day_followup_end_to_end(monkeypatch):
    """«Хто повертається 23 травня?» -> «а 22?» відповідає за 2026-05-22.

    Модель недоступна (rules-only), база підмінена: перевіряється сам
    механізм — стан у HTML-коментарі, _refine_day, успадкування наміру."""
    monkeypatch.setattr(chat_app.db, "returning_on_date",
                        lambda date, subdivision=None: [])
    monkeypatch.setattr(chat_app.db, "unconfirmed_absences_on_date",
                        lambda date: 0)
    monkeypatch.setattr(chat_app.db, "people_total", lambda: 5)

    q1 = "Хто повертається 23 травня?"
    r1 = chat_app.answer(q1, [])
    assert "2026-05-23" in r1
    assert chat_app.STATE_RE.search(r1), "слоти мусять зберегтись у маркері"

    hist = [{"role": "user", "content": q1},
            {"role": "assistant", "content": r1}]
    r2 = chat_app.answer("а 22?", hist)
    assert "2026-05-22" in r2, r2
    assert "2026-05-23" not in r2.split("<!--")[0], \
        "відповідь не мусить лишитись за старою датою"
