# -*- coding: utf-8 -*-
"""Дві копії одного реєстру не мусять розходитись МОВЧКИ (A-07).

`validity_model` кожного типу факту оголошений у нашому
`pipeline/dictionaries/fact_type_registry.yaml`. Завантажувач БД тримає ДРУГУ
копію -- літерал `FACT_TYPE_VALIDITY` у `airflow/plugins/ai_secretary_loader.py`
-- і на 22.08.2026 копії вже розійшлися: у ній немає чотирьох кодів, а
`get_or_create_dimension` на невідомий код підставляє `"ranged"`.

Чому тест тут, а не правка там. `airflow/plugins/` -- зона власника `db/`;
ми копію прибрати не можемо. Але можемо зробити так, щоб НОВА розбіжність
падала одразу, а не через місяць у базі. Тобто це не дублювання перевірки, а
детектор дрейфу.

Наш бік свою половину вже зробив: `validity_model` їде в КОЖНОМУ факті
(`facts[*].validity_model`), тож копію можна замінити читанням поля.

Запуск:
    python -m pytest eval/tests/test_registry_divergence.py -q
"""
import io
import os
import re
import sys

import pytest
import yaml

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

_REGISTRY = os.path.join(_PROJECT_ROOT, "pipeline", "dictionaries",
                         "fact_type_registry.yaml")
_LOADER = os.path.join(_PROJECT_ROOT, "airflow", "plugins",
                       "ai_secretary_loader.py")

#: Розбіжність, ЗАМІРЯНА 23.08.2026 і залишена свідомо: прибрати її може лише
#: власник `db/`. Ключ тесту -- щоб перелік не РІС непоміченим.
#:
#: `rank` і `position` у цьому переліку не страшні: обидва виміри створює
#: міграція `1283dc745daa` (rank/position) і виставляє `current_state`
#: міграція `349d428a0094`, тому дефолт `"ranged"` для них ніколи не
#: спрацьовує -- `get_or_create_dimension` знаходить готовий рядок.
#: Реально відкриті -- `travel_document` і `unrecognized`: їх не створює жодна
#: міграція, тому перший факт заводить вимір із `ranged` замість
#: `permanent_event`.
KNOWN_MISSING_IN_LOADER = {
    "position": "current_state",
    "rank": "current_state",
    "travel_document": "permanent_event",
    "unrecognized": "permanent_event",
    # ДОДАНО НАМИ 23.08.2026 разом із полем `actual_return_date` посвідчення
    # (weak-spots 10.2). Тобто цей рядок -- не дрейф чужої копії, а НАШ новий
    # тип факту, якого в ній ще не може бути. Саме тому він у переліку з
    # причиною, а не «оновили тест, щоб зелений».
    "deployment_actual_return": "permanent_event",
}

#: Виміри, які створює міграція БД -- на них дефолт лоадера не діє.
PRESEEDED_BY_MIGRATION = {"rank", "position"}

LOADER_DEFAULT = "ranged"


def _ours():
    reg = yaml.safe_load(io.open(_REGISTRY, encoding="utf-8"))
    return {ft["code"]: ft.get("validity_model") for ft in reg["fact_types"]}


def _theirs():
    src = io.open(_LOADER, encoding="utf-8").read()
    block = re.search(r"FACT_TYPE_VALIDITY\s*=\s*\{(.*?)\}", src, re.S)
    assert block, "у лоадері більше немає FACT_TYPE_VALIDITY -- перевірте, чи він тепер читає facts[*].validity_model"
    return dict(re.findall(r'"([^"]+)":\s*"([^"]+)"', block.group(1)))


def test_no_new_divergence_between_the_two_copies():
    """Нова розбіжність = помилка. Стара -- у переліку з причиною."""
    ours, theirs = _ours(), _theirs()
    missing = {c: m for c, m in ours.items() if c not in theirs}
    assert missing == KNOWN_MISSING_IN_LOADER, (
        "перелік відсутніх у копії лоадера змінився; якщо це навмисно -- "
        f"оновіть KNOWN_MISSING_IN_LOADER разом із причиною. Зараз: {missing}")
    extra = sorted(set(theirs) - set(ours))
    assert not extra, f"у лоадері є код, якого немає в НАШОМУ реєстрі: {extra}"
    conflicts = {c: (ours[c], theirs[c]) for c in set(ours) & set(theirs)
                 if ours[c] != theirs[c]}
    assert not conflicts, f"те саме fact_type із РІЗНИМИ моделями: {conflicts}"


def test_the_divergence_that_actually_reaches_the_database():
    """Не «є розбіжність», а ЩО САМЕ поїде неправильно: код, якого немає ні в
    копії, ні в міграціях, заводить вимір із дефолтом `ranged`."""
    ours, theirs = _ours(), _theirs()
    wrong = {c: (ours[c], LOADER_DEFAULT)
             for c in ours
             if c not in theirs and c not in PRESEEDED_BY_MIGRATION
             and ours[c] != LOADER_DEFAULT}
    assert wrong == {"travel_document": ("permanent_event", "ranged"),
                     "unrecognized": ("permanent_event", "ranged"),
                     # третій із 23.08.2026 -- наше нове поле
                     "deployment_actual_return": ("permanent_event", "ranged")}, wrong


def test_our_side_ships_the_model_with_every_fact():
    """Половина, яку робимо МИ: копія стає непотрібною лише тоді, коли
    значення їде разом із фактом."""
    from pipeline.config import load_config
    from pipeline.run import build_resources, process_file

    cfg = load_config("config.yaml", project_root=_PROJECT_ROOT)
    cfg["llm"]["enabled"] = False
    cfg["intake"]["archive"] = False
    out = os.path.join(_PROJECT_ROOT, "data", "output", "test-validity")
    cfg["paths"]["output_dir"] = out
    cfg["storage"]["local_root"] = out
    res = build_resources(cfg, force_no_llm=True)
    res["store"] = None
    sample = os.path.join(_PROJECT_ROOT, "data", "eval", "samples", "leave",
                          "synthetic-2026-05", "docx")
    files = sorted(f for f in os.listdir(sample) if f.endswith(".docx"))
    if not files:
        pytest.skip("немає зразків leave/docx")
    meta = process_file(os.path.join(sample, files[0]), res, cfg,
                        reprocess=True) or {}
    # Ключ у записі -- `fact_type` (він же `dimensions.code` на боці БД).
    facts = [f for f in (meta.get("facts") or []) if f.get("fact_type")]
    assert facts, "документ мусить давати факти з типом"
    ours = _ours()
    for fact in facts:
        assert fact.get("validity_model") == ours.get(fact["fact_type"]), fact


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
