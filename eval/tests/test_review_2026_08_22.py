# Тести виправлень за рев'ю 22.08.2026 (docs/review-2026-08-22/verdicts.md).
#
# Як і в test_review_fixes.py, кожен тест ловить САМЕ ТИШУ зі знахідки:
# не «правильний результат є», а «зіпсований стан більше не дає 100%».
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from eval.evaluate import evaluate_record, main

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LEAVE_DOCX = os.path.join(_PROJECT_ROOT, "data", "eval", "samples", "leave",
                           "synthetic-2026-05", "docx")


def _meta(status="confirmed", confirmed=(True,), **extra):
    """Запис, у якому НЕМА жодної підстави для сумніву: шаблон визначено,
    факти є, прогалин немає, попереджень немає."""
    meta = {
        "status": status,
        "template": "leave_ticket",
        "facts": [{"confirmed": c, "additional_info": {}} for c in confirmed],
        "subject": {},
        "unknown_critical_fields": [],
        "warnings": [],
    }
    meta.update(extra)
    return meta


def _check(meta, key):
    row = evaluate_record(meta, {"id": "X-001"}, {}, None)
    hits = [c for c in row["checks"] if c["key"] == key]
    assert len(hits) == 1, f"перевірка '{key}' мусить бути в КОЖНОМУ записі"
    return hits[0]


# --- A-01: прилад мусить карати НЕОБҐРУНТОВАНУ ВІДМОВУ ---------------------
#
# Репро знахідки: пайплайн, у якого КОЖЕН запис -- needs_review з
# facts[*].confirmed=False, отримував ті самі 224/224 = 100.0%. Тобто цифра,
# якою міряють якість, не падала від того, що система перестала підтверджувати
# будь-що.

def test_mass_refusal_without_grounds_is_a_failure():
    check = _check(_meta("needs_review", (False,)), "відмова_обґрунтована")
    assert check["ok"] is False
    assert check["refusal_grounds"] == []


def test_refusal_with_a_critical_gap_is_fine():
    meta = _meta("needs_review", (False,), unknown_critical_fields=["surname"])
    assert _check(meta, "відмова_обґрунтована")["ok"] is True


@pytest.mark.parametrize("extra", [
    {"unresolved_values": {"days": "13"}},
    {"consistency_problems": ["кінець раніше початку"]},
    {"date_range_error": True},
    {"warnings": ["OCR: сторінка 2 порожня"]},
    {"template": None},
    {"facts": []},
])
def test_every_objective_ground_justifies_refusal(extra):
    """Підстави читаються з ВИХОДУ пайплайна, не зі сценарію еталона."""
    assert _check(_meta("needs_review", (False,), **extra),
                  "відмова_обґрунтована")["ok"] is True


def test_confirmed_document_does_not_get_punished_by_the_new_check():
    """Інваріант карає лише відмову. Брехню в бік довіри карає
    `чернетка_не_факт` -- перевірки не мусять дублювати одна одну."""
    check = _check(_meta("confirmed", (True,)), "відмова_обґрунтована")
    assert check["ok"] is True and check["trivial"] is True


def test_both_directions_are_now_measured():
    """Симетрія: обидва напрямки брехні мусять давати мінус у знаменнику."""
    lying_up = evaluate_record(_meta("needs_review", (True,)), {"id": "X"}, {}, None)
    refusing = evaluate_record(_meta("needs_review", (False,)), {"id": "X"}, {}, None)
    healthy = evaluate_record(_meta("confirmed", (True,)), {"id": "X"}, {}, None)
    assert lying_up["fields_ok"] < lying_up["fields_total"]
    assert refusing["fields_ok"] < refusing["fields_total"]
    assert healthy["fields_ok"] == healthy["fields_total"]


def test_refusal_check_enters_the_denominator():
    row = evaluate_record(_meta("needs_review", (False,)), {"id": "X-001"}, {}, None)
    assert any(c["key"] == "відмова_обґрунтована" for c in row["checks"])
    assert row["fields_total"] == len(row["checks"])


# --- A-09: три цифри окремо, тривіальні перевірки видно --------------------

def test_checks_are_split_into_three_groups():
    row = evaluate_record(_meta(), {"id": "X-001"}, {}, None)
    groups = row["by_group"]
    assert set(groups) == {"field", "invariant", "blank"}
    assert sum(g["total"] for g in groups.values()) == row["fields_total"]
    assert sum(g["ok"] for g in groups.values()) == row["fields_ok"]
    # Статусні перевірки -- інваріанти, а не «поля»: саме через цю склейку
    # «224/224» читалось як «224 витягнутих поля».
    assert groups["invariant"]["total"] >= 3


def test_link_check_on_a_document_without_a_pair_is_marked_trivial():
    """Документ без пари: правильна відповідь -- «зв'язків немає», і її
    отримує задарма пайплайн, який зв'язків не витягує взагалі. Заміряно:
    14 із 16 у корпусі leave."""
    check = _check(_meta(), "зв'язок_скасування")
    assert check["ok"] is True and check["trivial"] is True


def test_link_check_on_a_real_pair_is_not_trivial():
    meta = _meta(document_links=[{"link_type": "supersedes",
                                  "target_document_number": "157"}])
    check = evaluate_record(meta, {"id": "X-001", "пара": {"replaces": "157"}},
                            {}, None)
    hit = next(c for c in check["checks"] if c["key"] == "зв'язок_скасування")
    assert hit["ok"] is True and hit["trivial"] is False


# --- A-15 + C-11: провал вимірювання мусить давати ненульовий код ----------

def test_exit_code_is_nonzero_below_the_fail_under_threshold():
    """Прилад без цього не можна поставити в CI: до 22.08.2026 `main`
    беззастережно повертав 0, тобто провал вимірювання не відрізнявся від
    успіху."""
    code = main(["--no-llm", "--input", _LEAVE_DOCX, "--fail-under", "100.5"])
    assert code == 1


def test_exit_code_is_zero_on_a_clean_run():
    code = main(["--no-llm", "--input", _LEAVE_DOCX, "--fail-under", "99.0"])
    assert code == 0


# --- A-06: holdout-еталон і гілка freeform -------------------------------
#
# `load_ground_truth` читав лише `per-document/*.json` і ключував за
# `data["id"]`; holdout-еталони цього ключа не мають, тому не мірялись НІЧИМ,
# а `grep -rin freeform eval/tests` давав 0 рядків.

_HOLDOUT = os.path.join(_PROJECT_ROOT, "data", "eval", "samples", "holdout")


def test_holdout_ground_truth_is_loaded_without_an_id_key():
    from eval.evaluate import load_ground_truth

    truth = load_ground_truth(_HOLDOUT)
    assert truth, "holdout-еталони мусять читатись, а не тихо не знаходитись"
    key = "ДОВІДКА_ЛІКУВАННЯ_01"
    assert key in truth, sorted(truth)
    # id виведено з назви файлу; сам еталон не редагувався.
    assert truth[key]["id"] == "довідка_лікування_01"
    assert "правильні_відповіді" in truth[key]


def test_holdout_document_is_matched_to_its_ground_truth():
    from eval.evaluate import doc_id_from_filename, load_ground_truth

    truth = load_ground_truth(_HOLDOUT)
    got = doc_id_from_filename("довідка_лікування_02.docx", tuple(truth))
    assert got == "ДОВІДКА_ЛІКУВАННЯ_02"


def test_ground_truth_answers_nobody_compares_are_reported():
    """Дзеркало `unmeasured:`: відповідь еталона, якої прилад не питає, більше
    не зникає в тиші. На holdout таких 11 з 11."""
    from eval.evaluate import load_ground_truth

    truth = load_ground_truth(_HOLDOUT)["ДОВІДКА_ЛІКУВАННЯ_01"]
    row = evaluate_record(_meta("unresolved", (), template=None, facts=[]),
                          truth, {}, None)
    assert set(row["unmeasured_expected"]) == set(truth["правильні_відповіді"])


def test_run_without_a_single_field_check_is_not_a_success():
    """Прогін на holdout без моделі дає 100% з інваріантів при нулі
    порівняних значень. Код виходу мусить це називати провалом."""
    code = main(["--no-llm", "--eval-dir", _HOLDOUT, "--input", _HOLDOUT])
    assert code == 1


def test_the_expected_json_is_not_processed_as_a_document():
    """`*.expected.json` містить ID у назві, тому доти прилад міряв кожен
    holdout-документ двічі -- другий раз на його ж еталоні."""
    import glob as _glob

    from eval.evaluate import doc_id_from_filename, load_ground_truth

    truth = load_ground_truth(_HOLDOUT)
    names = [os.path.basename(p) for p in _glob.glob(os.path.join(_HOLDOUT, "*"))]
    docs = [n for n in names
            if not n.endswith(".expected.json")
            and doc_id_from_filename(n, tuple(truth))]
    assert len(docs) == 3, docs


def test_freeform_record_is_always_a_draft():
    """Гілка `_build_freeform_record` (форма не впізнана жодною схемою) не
    покривалась ЖОДНИМ тестом, а вона -- єдина, якою поїдуть holdout-документи
    на бойовому прогоні з моделлю. Правило продукту: форма невідома ->
    завжди чернетка, скільком би полям LLM не «вгадала» значення."""
    from pipeline.config import load_config
    from pipeline.run import _build_freeform_record, build_resources

    text = ("Довідка № 214/мед. Гарнізонний військовий госпіталь. "
            "молодший сержант ГАЙДУЧЕНКО Остап Миронович перебував на "
            "лікуванні 18 діб.")
    cfg = load_config(os.path.join(_PROJECT_ROOT, "config.yaml"))
    res = build_resources(cfg, force_no_llm=True)
    res["store"] = None

    class _Llm:
        """Модель, яка «вгадала» ВСЕ: жодне поле не порожнє."""
        def extract_batch(self, field_defs, context_text, json_schema):
            out = {}
            for f in field_defs:
                name = f["name"]
                if name == "person_rank":
                    out[name] = "молодший сержант"
                elif name == "person_surname":
                    out[name] = "ГАЙДУЧЕНКО"
                elif name == "person_given_name":
                    out[name] = "Остап"
                elif name == "person_patronymic":
                    out[name] = "Миронович"
                elif name == "document_title":
                    out[name] = "Довідка"
                elif name == "key_number":
                    out[name] = 18
                elif name == "document_number":
                    out[name] = "214/мед"
            return out

    res = dict(res, llm=_Llm())
    base_meta = {"id": "H-001", "source_file": "довідка.docx"}
    meta = _build_freeform_record(text, [], {"domain": "medical", "score": 0.1},
                                  base_meta, "electronic", [], res, cfg)

    assert meta["status"] == "needs_review"
    assert meta["template"] == "unrecognized"
    assert meta["review_queue"] == "unknown_type"
    assert meta["create_subject_object"] is False
    assert meta["facts"], "витяг мусив дати хоч один факт"
    assert all(f["confirmed"] is False for f in meta["facts"]), \
        "форма не впізнана -> факт не може бути підтвердженим"


def test_freeform_record_passes_the_instrument_invariants():
    """І той самий запис мусить проходити інваріанти приладу: відмова тут
    ОБҐРУНТОВАНА (форма не впізнана), а не необґрунтована."""
    from eval.evaluate import evaluate_record as ev

    meta = _meta("needs_review", (False,), template="unrecognized",
                 review_queue="unknown_type",
                 warnings=["форма не впізнана жодною схемою"])
    row = ev(meta, {"id": "H-001"}, {}, None)
    assert row["fields_ok"] == row["fields_total"]


# --- A-12: чутливість мірки «інша редакція» мусить бути ЗАМІРЯНА -----------
#
# Сама мірка (`blank_edition_verdict`) живе в пайплайні; тут -- прилад, який
# міряє її чутливість. Доти чутливість не міряв ніхто: test_foreign_edition.py
# перевіряє лише ДВІ точки (наш бланк вище порога, штучна чужа редакція --
# нижче), а що між ними -- невідомо.
#
# Драбина нижче -- перефразування N найдовших друкованих рядків бланка
# (вставка одного слова в середину рядка: саме та зміна, якою відрізняються
# редакції). Заміряно 22.08.2026 на LEAVE-001 (27 різаків, поріг 0.5):
#     базове              0.926  recognized=True
#     6 рядків (3 влучні)  0.815  recognized=True
#     10 (7 влучних)       0.667  recognized=True
#     13 (9 влучних)       0.593  recognized=True   <-- половина бланка інша
#     20 (13 влучних)      0.444  recognized=False
#     29 (21 влучний)      0.148  recognized=False
# Тобто документ, у якого КОЖЕН ДРУГИЙ друкований рядок інший, вердикт усе ще
# називає «наша форма» -- і жодного проміжного стану («схоже, але не воно»)
# мірка не має за побудовою.

_SCHEMAS_DIR = os.path.join(_PROJECT_ROOT, "pipeline", "schemas")
_LEAVE_001 = os.path.join(_LEAVE_DOCX, "LEAVE-001.docx")


def _edition_ladder():
    """[(скільки рядків перефразовано, покриття, recognized)] -- замір, а не
    твердження. Повертає драбину, щоб і тест, і людина бачили ту саму цифру."""
    from pipeline.extraction.blank_form import (MIN_CUTTER_CHARS, _read_lines,
                                                blank_template_path,
                                                printed_cutters)
    from pipeline.identification import blank_edition_verdict, load_schemas
    from pipeline.ingestion.ingest import extract_docx_blocks

    schemas = load_schemas(_SCHEMAS_DIR)
    leave = next(s for s in schemas if s["template"] == "leave_ticket")
    text, _blocks = extract_docx_blocks(_LEAVE_001)
    lines = sorted([l.strip() for l in _read_lines(blank_template_path(leave))
                    if len(l.strip()) >= MIN_CUTTER_CHARS],
                   key=len, reverse=True)
    total_cutters = len(printed_cutters(leave))

    def paraphrase(line):
        words = line.split()
        mid = len(words) // 2
        return " ".join(words[:mid] + ["відповідно"] + words[mid:])

    ladder = []
    for n in (0, 6, 10, 13, 20, 29):
        mutated = text
        for line in lines[:n]:
            if line in mutated:
                mutated = mutated.replace(line, paraphrase(line))
        verdict = blank_edition_verdict(mutated, leave)
        ladder.append((n, verdict["coverage"], verdict["recognized"]))
    return total_cutters, ladder


def test_edition_metric_degrades_monotonically():
    """Мінімум, який прилад мусить гарантувати: мірка взагалі РЕАГУЄ на
    перефразування, і реагує в один бік."""
    total_cutters, ladder = _edition_ladder()
    assert total_cutters > 20, total_cutters
    coverages = [c for _n, c, _r in ladder]
    assert coverages == sorted(coverages, reverse=True), ladder
    assert coverages[0] > 0.9 and coverages[-1] < 0.2, ladder


def test_the_band_between_native_and_foreign_is_documented():
    """МЕЖА МІРКИ, зафіксована як замір: половина перефразованих рядків дає
    покриття ~0.6, і це ще «наша форма». Тест не схвалює цю поведінку -- він
    не дає їй змінитись непоміченою."""
    _total, ladder = _edition_ladder()
    half = next(c for n, c, _r in ladder if n == 13)
    assert 0.5 < half < 0.78, half


@pytest.mark.xfail(strict=False, reason="A-12: вердикт бінарний, проміжного "
                                        "стану «схоже, але не воно» немає")
def test_half_paraphrased_blank_should_not_pass_as_our_form():
    """ЧОГО МИ ХОЧЕМО (падає сьогодні): документ, у якого кожен другий
    друкований рядок інший, не має проходити як впізнана форма зі статусом
    confirmed і без жодного попередження. Коли мірку виправлять (не бінарний
    вердикт: recognized | partial | foreign), цей тест почне проходити."""
    _total, ladder = _edition_ladder()
    recognized_at_half = next(r for n, _c, r in ladder if n == 13)
    assert recognized_at_half is False
