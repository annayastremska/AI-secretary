# -*- coding: utf-8 -*-
"""Промпт виїхав із коду у файл — і від цього НЕ змінився ні на байт.

Задача 4.5 плану («own your prompts», 12-Factor). Ризик такого переносу
рівно один: непомітно переформулювати промпт разом із переїздом, а потім
шукати, чому модель відповідає інакше. Тому тут не «промпт виглядає схоже», а
ПОБАЙТОВЕ порівняння з тим, що будував старий код.

Літерал старої версії лежить нижче навмисно: це не дублювання, а якір. Коли
промпт колись справді знадобиться змінити, тест впаде і змусить змінити якір
СВІДОМО, одним рухом із правкою файла.

Запуск:
    python -m pytest demos/upload_app/tests/test_prompt_files.py -q
"""
import datetime

import pytest

from demos.upload_app.chat_gradio import app as chat_app

# Той самий модуль, що вживає апка (див. коментар у test_free_sql_gate.py):
# файл tiers.py живе в sys.modules двічі, і патчити треба робочу копію.
tiers = chat_app.tier_chat
prompts = tiers.prompts


def _route_system_before_the_move():
    """Незалежна копія `_route_system()` -- ЯКІР, а не дублювання.

    Оновлено 27.08 СВІДОМО, одним рухом із правкою промпта, як і вимагає
    докстрінг цього файла. Дві зміни, обидві зміряні (блок B харнесу):

      1. абзац про РОЗМОВУ. Без нього наступний хід («а хто?») не мав ні
         стану, ні дати: історія до маршрутизатора не доходила. Формулювання
         зміряне -- «ЗАПОВНИ його поле» проти «перелічи в carried_over»:
         друге модель робила дослівно, тобто клала значення в carried_over, а
         саме поле лишала порожнім;
      2. `route_hint` у рядку шаблона. Уточнення для МОДЕЛІ живе окремим
         полем каталогу, а не в `title`: заголовок людина бачить у блоці
         «джерело», і службовий текст там читався б як помилка.

    Копія лишається незалежною: якщо промпт зміниться випадково, тест упаде.
    """
    lines = ["Ти -- маршрутизатор питань до бази обліку документів "
             "військової частини. НЕ відповідай на питання. Обери один "
             "шаблон зі списку і витягни параметри.",
             "Тобі можуть показати РОЗМОВУ, а не одне питання. Наступний хід "
             "часто спирається на попередній: «а хто?» після «скільком у "
             "відпустці на дату» означає той самий стан і ту саму дату, "
             "змінюється лише форма відповіді. Якщо параметра немає в "
             "останньому ході, але він однозначно випливає з попередніх -- "
             "ЗАПОВНИ його поле значенням з попереднього ходу, а в "
             "carried_over додай лише НАЗВИ таких параметрів, без значень. "
             "Якщо попередній хід був про інше -- не переноси з нього "
             "нічого.",
             "Шаблони:"]
    for tid, t in tiers._CATALOG.items():
        ex = "; ".join(t.get("examples", [])[:2])
        head = t["title"]
        hint = (t.get("route_hint") or "").strip()
        if hint:
            head += f"; {hint}"
        lines.append(f"- {tid}: {head}. Приклади: {ex}")
    lines.append("- вільний_sql: питання про дані бази, яке не лягає на "
                 "жоден шаблон")
    lines.append("- відмова: питання не про дані бази (погода, поради, "
                 "прохання щось змінити чи видалити)")
    lines.append(
        "Параметри: state -- leave (відпустка) / deployment (відрядження) / "
        "absent (відсутні взагалі) / null. Дати -- YYYY-MM-DD або null; "
        "«зараз»/«сьогодні» -> сьогоднішня дата "
        f"({datetime.date.today().isoformat()}). name -- прізвище або ПІБ. "
        "doc_number -- номер документа без «№». Нічого не вигадуй: якщо "
        "параметра в питанні немає -- null.")
    return "\n".join(lines)


def test_prompt_from_file_is_byte_identical_to_the_old_code():
    """ГОЛОВНЕ твердження переносу."""
    assert tiers._route_system() == _route_system_before_the_move()


def test_template_list_comes_from_the_catalog_not_from_the_file():
    """Каталог -- єдине джерело шаблонів. У файлі промпта переліку немає, він
    підставляється; інакше новий шаблон правився б у двох місцях і копії
    розійшлись би мовчки (як `FACT_TYPE_VALIDITY` у лоадері БД)."""
    raw = prompts.load("route.md")
    for tid in tiers._CATALOG:
        assert tid not in raw, f"{tid} вписаний у файл промпта -- це друге джерело"
    rendered = tiers._route_system()
    for tid in tiers._CATALOG:
        assert tid in rendered, tid


def test_placeholders_of_file_and_code_match():
    """Файл і код не мають розійтись: у файлі з'явився новий плейсхолдер, а
    код його не підставляє -> у промпт поїде дослівне «{unit}»."""
    assert prompts.placeholders("route.md") == {"templates", "today"}
    assert prompts.placeholders("free_sql.md") == {"schema"}
    assert "{" not in tiers._route_system().replace("{", "", 0) or True
    # жодного нерозгорнутого плейсхолдера у готовому промпті
    import re
    assert not re.search(r"\{[a-z_]+\}", tiers._route_system())


def test_today_is_substituted_not_hardcoded():
    """Модель не має «знати» дату зі своїх ваг: вона приходить із коду."""
    assert datetime.date.today().isoformat() in tiers._route_system()


def test_comment_header_is_not_part_of_the_prompt():
    """Шапка файла -- пояснення для людини. Якщо вона поїде в промпт, модель
    отримає інструкції про наші внутрішні правила замість задачі."""
    text = prompts.load("route.md")
    assert "<!--" not in text and "12-Factor" not in text


def test_missing_prompt_file_degrades_instead_of_lying(monkeypatch):
    """Немає файла -> модельний ярус чесно віддає питання далі (None), а не
    підставляє вбудовану копію. Копія була б другим джерелом правди."""
    monkeypatch.setattr(prompts, "PROMPTS_DIR", "/nonexistent/prompts")
    monkeypatch.setattr(prompts, "_cache", {})
    assert tiers._route_system() is None
    assert tiers.model_route("Скільком зараз у відпустці?") is None


def test_free_sql_prompt_is_byte_identical_too():
    """Другий промпт -- той самий якір: ярус працює, і його поведінка мусить
    лишитись тією, що заміряна."""
    before = ("Ти складаєш ОДИН SQL SELECT до PostgreSQL за питанням "
              "користувача. Поверни JSON {\"sql\": \"...\"}. "
              "Нічого не пояснюй.\n" + tiers.DB_SCHEMA_HINT)
    after = prompts.render("free_sql.md", schema=tiers.DB_SCHEMA_HINT)
    assert after == before


def test_curly_braces_inside_a_prompt_survive():
    """У промпті вільного SQL є JSON-приклад `{"sql": "..."}`. Підстановка
    мусить бути буквальною: `str.format` на такому падає -- саме тому
    render() не використовує format."""
    out = prompts.render("free_sql.md", schema="СХЕМА-ТУТ")
    assert '{"sql": "..."}' in out
    assert "СХЕМА-ТУТ" in out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
