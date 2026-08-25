# -*- coding: utf-8 -*-
"""Кожен параметр, який просить шаблон, мусить доїжджати до SQL.

Дефект, заміряний на сервері 25.08. Я додала шаблони підрозділів із
параметром `subdivision`, але забула дописати його у білий перелік
`_SQL_PARAM_NAMES`. Наслідок:

    psycopg.ProgrammingError: query parameter missing: subdivision

Далі виняток глушився вище (`_catalog_tier` ловить усе), питання тихо їхало на
стару дорогу і та просила назвати дату. Тобто пропущений рядок у переліку
виглядав як **«чат не розуміє питання»** — і я двічі шукала помилку в
маршрутизації, доки не подивилась справжній стек на сервері.

Цей тест робить такий пропуск неможливим: він читає, які параметри згадані в
SQL КОЖНОГО шаблона, і перевіряє, що всі вони проходять фільтр.

Запуск:
    python -m pytest demos/upload_app/tests/test_sql_params_complete.py -q
"""
import re

import pytest

from demos.upload_app.chat_gradio import app as chat_app

tiers = chat_app.tier_chat

#: `%(ім'я)s` -- іменовані параметри psycopg у тексті запиту.
_PLACEHOLDER = re.compile(r"%\((\w+)\)s")


def _sql_placeholders(template):
    names = set()
    for key in ("sql", "sql_unconfirmed"):
        names |= set(_PLACEHOLDER.findall(template.get(key) or ""))
    return names


@pytest.mark.parametrize("template_id", sorted(tiers._CATALOG))
def test_every_placeholder_passes_the_filter(template_id):
    """ГОЛОВНЕ: параметр, який згадує SQL, не має відсіюватись у _sql_params."""
    needed = _sql_placeholders(tiers._CATALOG[template_id])
    missing = needed - set(tiers._SQL_PARAM_NAMES)
    assert not missing, (
        f"{template_id}: SQL просить {sorted(missing)}, а _SQL_PARAM_NAMES їх "
        f"не пропускає -- шаблон упаде з «query parameter missing»")


@pytest.mark.parametrize("template_id", sorted(tiers._CATALOG))
def test_declared_params_cover_the_sql(template_id):
    """Друга половина: те, що SQL просить, мусить бути оголошене в `params:`
    каталогу -- інакше params_for_template не збере значення, і шаблон
    отримає порожньо."""
    t = tiers._CATALOG[template_id]
    needed = _sql_placeholders(t)
    declared = set(t.get("params") or [])
    # `dims` подекуди підставляється кодом разом зі `state`; решта мусить бути
    # оголошена явно
    missing = needed - declared - {"dims"}
    assert not missing, (
        f"{template_id}: SQL просить {sorted(missing)}, а в params: каталогу "
        f"їх немає")


def test_filter_drops_service_values():
    """Запобіжник у інший бік: службові значення (`state`) у SQL не їдуть --
    psycopg на зайвий іменований параметр не скаржиться, але передавати в базу
    те, чого запит не просить, ми не хочемо."""
    out, _ = tiers._sql_params("count_by_state_on_date",
                               {"dims": ["leave"], "on_date": "2026-08-28",
                                "state": "leave", "щось": 1})
    assert set(out) == {"dims", "on_date"}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
