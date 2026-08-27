# -*- coding: utf-8 -*-
"""Подання відповіді: жирні підписи роблять ПОДАННЯ, а не текст відповіді.

Чому це окремий тест. Аня попросила виділяти ключові слова («Доповідаю:»,
«Зріз:», «Чернетки»). Спокуса -- дописати зірочки в answer(). Так робити
не можна: той самий текст читають прилади (measure_chat, verify_catalog) і
журнал звернень, і markdown-зірочки в них -- сміття, яке ще й ламає звірку
рядків. Тому жирність живе в render_reply(), і тест стежить саме за цим
розділенням: текст відповіді чистий, жирне з'являється лише на екрані.
"""
from demos.upload_app.chat_gradio import app as chat_app


def test_labels_become_bold():
    out = chat_app.render_reply(
        "Доповідаю: 1 особа у відпустці (усього в реєстрі 303 особи).\n"
        "Зріз: на 2026-10-10 (за підтвердженими фактами).\n"
        "Чернетки (не в підрахунку): 0.")
    assert "**Доповідаю:**" in out
    assert "**Зріз:**" in out
    assert "**Чернетки (не в підрахунку):**" in out


def test_list_and_code_lines_untouched():
    """Рядки переліку й блоки коду не чіпаємо: двокрапка там означає інше
    («- Іванов І.І.: відпустка» -- це значення, а не підпис розділу), а в SQL
    зірочки просто зламали б підсвітку."""
    out = chat_app.render_reply(
        "- Іванов І.І., 2 рота: відпустка\n"
        "```sql\n"
        "SELECT: 1\n"
        "```")
    assert "**" not in out


def test_answer_text_itself_has_no_markup():
    """Головне: сам текст відповіді лишається без зірочок."""
    import demos.upload_app.chat_gradio.tiers as tiers
    src = open(tiers.__file__, encoding="utf-8").read()
    assert "**Доповідаю" not in src


# ── Номер звернення: РІВНО ОДИН раз, і саме дрібним рядком знизу ────────────
#
# Аня 27.08: «код звернення дублюється, нехай він буде лише окремим шрифтом
# меншим знизу». До цього номер стояв і в блоці «джерело», і окремим рядком --
# два однакові шестизначні коди в одній відповіді читаються як два різні
# номери.

SRC_WITH_ID = (
    "Доповідаю: 32 записи.\n\n"
    "<details class=\"src\"><summary>джерело</summary>"
    "джерело: шаблон<br>дорога: каталог<br>"
    "зріз бази: 2026-08-27<br>звернення: 69b6ea</details>")


def test_request_id_is_shown_exactly_once():
    out = chat_app.render_reply(SRC_WITH_ID)
    assert out.count("69b6ea") == 1, out
    assert '<div class="req-id">звернення 69b6ea</div>' in out
    assert "звернення: 69b6ea" not in out, "номер лишився в блоці «джерело»"


def test_removing_the_id_leaves_no_dangling_separator():
    """Вирізаємо разом із розділювачем: інакше в «джерелі» лишається
    висячий `<br>` перед закриттям, тобто порожній рядок у розгортці."""
    out = chat_app.render_reply(SRC_WITH_ID)
    assert "<br></details>" not in out
    # І сам блок «джерело» цілий -- вирізали рядок, а не розтрощили розмітку
    assert "<details class=\"src\">" in out and "</details>" in out


def test_short_refusal_without_details_also_gets_one_id():
    """Короткі відмови приходять без `<details>`: там номер дописано порожнім
    рядком, і його теж треба перенести в дрібний рядок, а не подвоїти."""
    out = chat_app.render_reply("Відхилено.\n\nзвернення: abc123")
    assert out.count("abc123") == 1
    assert '<div class="req-id">звернення abc123</div>' in out


def test_raw_answer_still_carries_the_number():
    """ПЕРЕВІРКА НА ПОБІЧНУ ШКОДУ. Прибрано лише з ВІДОБРАЖЕННЯ. У сирому
    тексті номер мусить лишитись: на ньому тримається `_with_request_id`
    (готові константи-відмови, чий footer зібрався ще при імпорті) і заборона
    дописувати щось після службового маркера стану діалогу."""
    filled = chat_app._with_request_id("Відхилено.", "abc123")
    assert "звернення: abc123" in filled
    # і повторний виклик не подвоює
    assert chat_app._with_request_id(filled, "abc123").count("abc123") == 1
