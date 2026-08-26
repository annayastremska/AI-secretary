# -*- coding: utf-8 -*-
"""Шаблон, який віддають ПРАВИЛА, мусить бути підключений у app._STATE_TEMPLATES.

Знайдено живим прогоном 26.08. Я додала шаблон `list_by_state_in_subdivision`,
правила почали його віддавати -- а в `_STATE_TEMPLATES` його не було. Наслідок
не «нічого не сталося», а гірший: питання ТИХО поїхало на стару дорогу, і чат
на «хто відсутній у 2 роті 28 серпня» попросив назвати дату. Тобто нова
можливість виглядала як поломка розуміння.

Тест перебирає питання роутерного тест-сету плюс явні приклади з каталогу:
кожен шаблон, який правила справді віддають, має бути або в перехваті старої
дороги, або свідомо винесений у ALLOWED_OUTSIDE нижче -- з причиною.

Запуск:
    python -m pytest demos/upload_app/tests/test_gate_templates_are_wired.py -q
"""
import io
import os

import yaml

from demos.upload_app.chat_gradio import app as chat_app

tiers = chat_app.tier_chat

#: Шаблони, які СВІДОМО не в перехваті: у них своя гілка у dispatch, або стара
#: дорога для них жива й не бреше.
ALLOWED_OUTSIDE = {
    "person_status", "doc_by_number", "review_queue_count", "unconfirmed_count",
    "documents_count", "count_by_doc_type", "count_by_reason", "drafts_list",
    "date_conflict_docs", "fact_provenance", "failed_docs_count",
    "with_co_travelers", "with_travel_document", "smalltalk",
    "absent_without_docs_impossible", "roster_total",
}


def _catalog_examples():
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "query_catalog.yaml")
    data = yaml.safe_load(io.open(path, encoding="utf-8"))
    out = []
    for t in data["templates"]:
        out += list(t.get("examples") or [])
    return out


ROSTER = ["1-ша механізована рота", "2-га механізована рота",
          "3-тя механізована рота", "Взвод забезпечення",
          "Управління батальйону"]


def test_every_routed_template_is_reachable(monkeypatch):
    # Перелік підрозділів -- підставний: тест про МАРШРУТИ, і бази тут немає.
    # (`subdivisions()` більше не глушить помилку бази -- інакше при впалій
    # базі чат казав би «такого підрозділу немає» замість «база недоступна».)
    monkeypatch.setattr(tiers, "subdivisions", lambda: ROSTER)
    missing = {}
    for question in _catalog_examples():
        route = tiers.rules_route(question)
        if not route:
            continue
        tid = route[0]
        if tid in chat_app._STATE_TEMPLATES or tid in ALLOWED_OUTSIDE:
            continue
        missing.setdefault(tid, question)
    assert not missing, (
        "правила віддають ці шаблони, а перехват старої дороги про них не "
        f"знає -- питання тихо поїде на стару дорогу: {missing}")
