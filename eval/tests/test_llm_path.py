# -*- coding: utf-8 -*-
"""LLM-шлях під тестом — МАКЕТОМ моделі, не справжньою моделлю.

## Чому це існує

Усі регресійні заміри (`eval/check_all`) ідуть `--no-llm`, і це навмисно:
модель не відтворювана побайтово, тож інакше цифра змінювалась би сама від
себе й «не ламає» перестало б щось означати. Але наслідок був неприйнятний:
аудит абляцією 23.08 показав, що цілий клас поведінки не міряється **нічим** --
прибери його, і жодна цифра не поворухнеться. А це найдорожчий клас, який у
нас є: саме тут система може ВИГАДАТИ значення.

Не міряло себе троє:

1. **заземлення** (`ground_llm_value`) — перевірка, що відкидає значення,
   якого в документі немає. Заміряний випадок: модель віддала «днів = 17» для
   документа, де підрядка «17» немає взагалі;
2. **пропуск моделі на доведено-порожньому слоті** — сенс
   `confirmed_empty_slot` саме в тому, щоб МОДЕЛЬ НЕ ПИТАЛИ; без LLM у прогоні
   немає чого пропускати, тобто перевірити нічого;
3. **гілки збою моделі** (`llm_error:*`) — що станеться, коли виклик кине
   виняток.

## Чому макет, а не справжня модель

Макет дає рівно те, чого бракувало, і не приносить того, чого не треба:
поведінку шляху видно, а відтворюваність не страждає. Справжня модель
перевіряється окремо — одним прогоном на сервері, де міряються ПОЛЯ; тут
міряються ПРАВИЛА.

Макет — це просто функція з тим самим інтерфейсом, що й
`llama.extract_batch`: `(field_defs, context_text, json_schema) -> {ім'я: значення}`.

Запуск:
    python -m pytest eval/tests/test_llm_path.py -q
"""
import glob
import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from pipeline.config import load_config
from pipeline.extraction.extract import (CONFIRMED_EMPTY_SLOT_METHOD,
                                         extract_document, ground_llm_value)
from pipeline.ingestion.ingest import load_document_blocks
from pipeline.run import build_resources

_LEAVE = os.path.join(_PROJECT_ROOT, "data", "eval", "samples", "leave",
                      "synthetic-2026-05", "docx")


@pytest.fixture(scope="module")
def env():
    cfg = load_config("config.yaml", project_root=_PROJECT_ROOT)
    res = build_resources(cfg, force_no_llm=True)
    schema = next(s for s in res["schemas"] if s["template"] == "leave_ticket")
    return cfg, res, schema


def _doc(name="LEAVE-011"):
    """LEAVE-011 -- документ із навмисними прогалинами: саме на ньому й
    заміряна вигадка моделі, тому саме він тут за вхід."""
    paths = [p for p in sorted(glob.glob(os.path.join(_LEAVE, "*.docx")))
             if name in os.path.basename(p)]
    assert paths, f"немає зразка {name}"
    return load_document_blocks(paths[0])


class Stub:
    """Макет моделі: віддає задані значення й веде облік викликів.

    `asked` -- імена полів, про які модель ПИТАЛИ. Саме воно перевіряє
    пропуск: поле з доведеною порожнечею не має тут з'явитись.
    """

    def __init__(self, answers=None, raise_on=None):
        self.answers = answers or {}
        self.raise_on = raise_on or set()
        self.asked = []
        self.calls = 0

    def __call__(self, field_defs, context_text, json_schema):
        self.calls += 1
        names = [f["name"] for f in field_defs]
        self.asked.extend(names)
        if self.raise_on & set(names):
            raise RuntimeError("макет моделі: імітований збій")
        return {n: self.answers.get(n) for n in names}


# --- 1. ЗАЗЕМЛЕННЯ ----------------------------------------------------------

def test_invented_number_is_rejected(env):
    """Заміряний випадок: модель віддає «17», якого в документі немає."""
    _cfg, res, schema = env
    text, blocks = _doc()
    assert "17" not in text, "тест втратив сенс: у документі з'явилось 17"
    stub = Stub({"duration_days": 17})
    out = extract_document(schema, text, blocks, res["dictionaries"],
                           llm_extract_batch=stub, form_recognized=False)
    value, reason = out["duration_days"]
    assert value is None, (value, reason)
    assert reason != "llm", reason


def test_value_present_in_the_document_is_accepted(env):
    """Дзеркало: заземлення не сміє відкидати ПРАВИЛЬНЕ значення, інакше воно
    перетворюється на «модель не використовуємо»."""
    _cfg, res, schema = env
    text, blocks = _doc("LEAVE-001")
    stub = Stub({"destination_place": "м. Житомир"})
    out = extract_document(schema, text, blocks, res["dictionaries"],
                           llm_extract_batch=stub)
    # Поле детермінований шлях і так знаходить -- перевіряємо саму функцію
    # заземлення на тому ж тексті, щоб тест не залежав від порядку шляхів.
    grounded, reason = ground_llm_value({"name": "x", "type": "text"},
                                        "м. Житомир", text)
    assert grounded == "м. Житомир", (grounded, reason)


def test_invented_text_is_rejected_but_substring_is_not(env):
    """Межа перевірки: вигадка відкидається, а значення, яке дослівно є в
    тексті, -- ні. Без другої половини заземлення було б просто забороною."""
    _cfg, _res, _schema = env
    text, _blocks = _doc("LEAVE-001")
    invented, reason = ground_llm_value({"name": "x", "type": "text"},
                                        "м. Вигадкове", text)
    assert invented is None and reason, reason
    real, _ = ground_llm_value({"name": "x", "type": "text"},
                               "м. Житомир", text)
    assert real == "м. Житомир"


# --- 2. ПРОПУСК МОДЕЛІ НА ДОВЕДЕНО-ПОРОЖНЬОМУ СЛОТІ ------------------------

def test_proven_empty_slot_is_not_sent_to_the_model(env):
    """Сенс `confirmed_empty_slot` -- саме НЕ ПИТАТИ модель: вона бачить той
    самий текст, у якому значення немає, тому єдина законна відповідь -- null,
    а вигадка коштувала б підтвердженого факту. Без LLM у прогоні цього
    перевірити неможливо: пропускати нічого."""
    _cfg, res, schema = env
    text, blocks = _doc("LEAVE-003")
    baseline = extract_document(schema, text, blocks, res["dictionaries"])
    proven = [n for n, (_v, r) in baseline.items()
              if str(r).startswith(CONFIRMED_EMPTY_SLOT_METHOD)]
    assert proven, "на цьому документі мусить бути хоч один доведений слот"

    stub = Stub({n: "ВИГАДАНО" for n in proven})
    out = extract_document(schema, text, blocks, res["dictionaries"],
                           llm_extract_batch=stub)
    for name in proven:
        assert name not in stub.asked, (
            f"поле {name} доведено порожнє, але модель про нього питали")
        value, reason = out[name]
        assert value is None, (name, value, reason)
        assert str(reason).startswith(CONFIRMED_EMPTY_SLOT_METHOD), (name, reason)


# --- 3. ЗБІЙ МОДЕЛІ ---------------------------------------------------------

def _foreign_edition():
    """Документ, на якому модель СПРАВДІ питають. Заміряно 24.08: на всьому
    синтетичному docx-корпусі модель не викликається жодного разу -- бланк
    впізнаний, детермінований шлях покриває все, а порожні слоти доведені,
    тобто фолбек не потрібен. Модель починає працювати рівно там, де форма НЕ
    впізнана: інша редакція бланка дає 15 полів на запит.

    Це і є причина, чому «прогін із моделлю» на наших корпусах нічого про
    модель не доводив би."""
    path = os.path.join(_PROJECT_ROOT, "data", "eval", "samples", "leave",
                        "відпускний_квиток_інша_редакція.docx")
    assert os.path.exists(path), path
    return load_document_blocks(path)


def test_the_corpus_never_calls_the_model_and_that_is_measured(env):
    """ЗАМІР, який об'єднує все нижче: на впізнаному бланку модель не питають
    ЗОВСІМ. Тому LLM-шлях і був невидимий -- не через прапорець `--no-llm`, а
    тому що на цих документах його немає за побудовою."""
    _cfg, res, schema = env
    asked = []

    def stub(field_defs, _ctx, _js):
        asked.extend(f["name"] for f in field_defs)
        return {f["name"]: None for f in field_defs}

    for name in ("LEAVE-001", "LEAVE-011"):
        text, blocks = _doc(name)
        extract_document(schema, text, blocks, res["dictionaries"],
                         llm_extract_batch=stub)
    assert asked == [], f"модель питали про {asked}"


def test_model_failure_does_not_erase_the_deterministic_result(env):
    """Виняток у виклику моделі не має ЗАБИРАТИ те, що детермінований шлях уже
    знайшов, і мусить бути видимим у провенансі як `llm_error`."""
    _cfg, res, schema = env
    text, blocks = _foreign_edition()
    baseline = extract_document(schema, text, blocks, res["dictionaries"],
                                form_recognized=False)
    matched = {n: v for n, (v, r) in baseline.items() if r == "matched"}

    stub = Stub(raise_on={"duration_days", "destination_place", "rank",
                          "surname", "given_name", "patronymic",
                          "leave_start_date", "leave_end_date_planned",
                          "actual_return_date", "document_number",
                          "document_date", "leave_type_and_destination",
                          "unit_to_report", "travel_document_number",
                          "co_travelers"})
    out = extract_document(schema, text, blocks, res["dictionaries"],
                           llm_extract_batch=stub, form_recognized=False)
    errors = [r for _v, r in out.values() if str(r).startswith("llm_error")]
    assert errors, "збій моделі мусить бути видимим у провенансі"
    for name, value in matched.items():
        assert out[name][0] == value, (name, out[name])


def test_model_returning_nothing_leaves_an_honest_gap(env):
    """Модель чесно віддала null -> поле лишається прогалиною, а не отримує
    значення «з нізвідки»."""
    _cfg, res, schema = env
    text, blocks = _foreign_edition()
    stub = Stub({})
    out = extract_document(schema, text, blocks, res["dictionaries"],
                           llm_extract_batch=stub)
    invented = [(n, v, r) for n, (v, r) in out.items()
                if v is not None and str(r).startswith("llm")]
    assert not invented, invented


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))


# --- 4. ЗАПИС ПРО ПОМИЛКУ МУСИТЬ ЛИШАТИСЬ ВАЛІДНИМ YAML ---------------------
#
# Заміряно 24.08.2026: OCR упав із багаторядковим повідомленням (SpawnError
# тягнув за собою лог чужого процесу), і воно поїхало в `reason` як є.
# Frontmatter після цього -- невалідний YAML, а завантажувач бази читає саме
# frontmatter. Тобто один зіпсований запис валив би завантаження пачки -- і
# рівно тоді, коли щось уже пішло не так.

def test_multiline_failure_reason_stays_one_line():
    from pipeline.run import MAX_REASON_CHARS, one_line_reason
    raw = ("не вдалося прочитати документ: SpawnError: vllm server failed\n"
           "--- last vllm server logs ---\n"
           "ERROR 08-24 07:00:00 [core.py:123] traceback...\n" + "x" * 500)
    out = one_line_reason(raw)
    assert "\n" not in out and "\r" not in out, out
    assert len(out) <= MAX_REASON_CHARS, len(out)
    assert out.startswith("не вдалося прочитати документ:"), out


def test_reason_survives_yaml_roundtrip():
    """Головна перевірка -- не «немає переводів рядка», а «YAML читається»."""
    import yaml
    from pipeline.run import one_line_reason
    reason = one_line_reason("помилка: рядок\nдругий рядок 'з лапкою' та \"ще\"")
    doc = yaml.safe_dump({"reason": reason}, allow_unicode=True)
    assert yaml.safe_load(doc)["reason"] == reason


def test_short_reason_is_untouched():
    from pipeline.run import one_line_reason
    assert one_line_reason("немає схеми") == "немає схеми"
