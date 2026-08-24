"""Звіт прогону: метрики якості, які лишаються ПІСЛЯ прогону.

Причина існування (відгуки, 24.08.2026): «все наче працює, але не
вимірюється». Вимірювання в проєкті є (eval/), але воно проти еталона й
живе в терміналі розробника — прогін на реальних документах не лишав по
собі жодного артефакту з цифрами. Цей модуль закриває рівно цю дірку:
кожен прогін пише `run-report.json` поруч із результатами й друкує людське
зведення.

МЕЖА ЧЕСНОСТІ, названа прямо: без еталона неможливо сказати «оброблено
ПРАВИЛЬНО» — правильність міряє eval/ на корпусах з еталоном. Тут міряється
те, що можна виміряти чесно на будь-якому вході:
  * скільки документів і чим закінчились (confirmed / needs_review / unresolved);
  * скільки полів вирішено, скільки доведено порожні, скільки прогалин;
  * звідки взялись значення (верстка бланка / модель / похідні) — бо
    «matched з бланка» і «здогадка моделі» мають різну вагу;
  * які поля западають найчастіше — мапа, куди дивитись наступним.

Звіт НЕ підміняє eval/: якщо для корпусу є еталон, правильна цифра — з
eval/evaluate. У звіті це сказано в самому JSON (`_що_це_міряє`).
"""
import io
import json
import os
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone

#: Класи провенансу поля. Порядок = порядок у зведенні.
#: «вирішено детерміновано» і «вирішено моделлю» розділені навмисно:
#: перше — прочитано з верстки бланка (довіра 0.9), друге — здогадка моделі,
#: заземлена в текст (0.6). Одна спільна цифра «заповнено» ховала б різницю,
#: заради якої весь провенанс і існує.
_METHOD_CLASSES = (
    ("вирішено_детерміновано", ("matched", "derived")),
    ("вирішено_моделлю", ("llm", "llm_split_vote")),
    ("доведено_порожні", ("confirmed_empty_slot",)),
    ("відкладені", ("deferred",)),
)


def _classify_method(method) -> str:
    m = str(method or "")
    for label, prefixes in _METHOD_CLASSES:
        if any(m == p or m.startswith(p + ":") for p in prefixes):
            return label
    # Усе інше -- поле не вирішене: no_value / no_label / ambiguous_* /
    # oversized_* / llm_error:* / unverified_foreign_edition / name_tail_* ...
    # Це чесна ПРОГАЛИНА, а не помилка класифікації.
    return "прогалини"


def _git_commit() -> str:
    """Якою версією коду зроблений прогін. Без цього два звіти неможливо
    порівнювати: «цифра змінилась» може означати і інші документи, і інший
    код."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10,
                             cwd=os.path.dirname(os.path.dirname(
                                 os.path.abspath(__file__))))
        return out.stdout.strip() or "невідомо"
    except Exception:
        return "невідомо"


def build_report(results: list) -> dict:
    """Агрегує метадані оброблених документів у звіт. Чиста функція:
    жодного вводу-виводу, щоб її можна було тестувати на копійчаних метах."""
    statuses = Counter(m.get("status") for m in results)
    templates = Counter(m.get("template") or "(без шаблону)" for m in results)
    domains = Counter(m.get("domain") or "(без домену)" for m in results)
    queues = Counter(m.get("review_queue") for m in results
                     if m.get("review_queue"))

    fields = Counter()
    gap_by_field = Counter()
    per_class_by_field = defaultdict(Counter)
    for m in results:
        for name, prov in (m.get("field_provenance") or {}).items():
            cls = _classify_method((prov or {}).get("method"))
            fields[cls] += 1
            per_class_by_field[cls][name] += 1
            if cls == "прогалини":
                gap_by_field[name] += 1

    facts_total = sum(len(m.get("facts") or []) for m in results)
    facts_confirmed = sum(1 for m in results for f in (m.get("facts") or [])
                          if f.get("confirmed"))
    critical_gap_docs = [m.get("source_file") for m in results
                         if m.get("unknown_critical_fields")]

    # Знаменник полів БЕЗ відкладених: відкладене поле -- не обіцянка,
    # і рахувати його прогалиною означало б занижувати цифру штучно.
    promised = sum(v for k, v in fields.items() if k != "відкладені")

    return {
        "_що_це_міряє": (
            "Процесні метрики прогону БЕЗ еталона: статуси, походження "
            "значень, прогалини. «Оброблено правильно» міряється окремо, "
            "приладом eval/evaluate на корпусах з еталоном -- цей звіт "
            "правильності НЕ вимірює і не стверджує."),
        "коли": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "версія_коду": _git_commit(),
        "документів": {
            "усього": len(results),
            "за_статусом": dict(statuses),
            "за_шаблоном": dict(templates),
            "за_доменом": dict(domains),
            "у_чергах_рев'ю": dict(queues),
            "з_критичними_прогалинами": len(critical_gap_docs),
        },
        "факти": {
            "усього": facts_total,
            "підтверджені": facts_confirmed,
            "чернетки": facts_total - facts_confirmed,
        },
        "поля": {
            "обіцяних_схемами": promised,
            **{k: fields.get(k, 0) for k, _ in _METHOD_CLASSES},
            "прогалини": fields.get("прогалини", 0),
        },
        "прогалини_за_полем": dict(gap_by_field.most_common()),
        "вирішено_моделлю_за_полем": dict(
            per_class_by_field["вирішено_моделлю"].most_common()),
        "документи_з_критичними_прогалинами": critical_gap_docs,
    }


def write_report(report: dict, output_dir: str) -> str:
    path = os.path.join(output_dir, "run-report.json")
    os.makedirs(output_dir, exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return path


def print_report(report: dict) -> None:
    d, f, p = report["документів"], report["факти"], report["поля"]
    print("\n=== звіт прогону ===")
    print(f"документів: {d['усього']} | "
          + ", ".join(f"{k}={v}" for k, v in sorted(d["за_статусом"].items())))
    if d["з_критичними_прогалинами"]:
        print(f"  з критичними прогалинами: {d['з_критичними_прогалинами']} "
              f"(підуть людині, у підрахунки не входять)")
    print(f"фактів: {f['усього']} (підтверджених {f['підтверджені']}, "
          f"чернеток {f['чернетки']})")
    total = max(1, p["обіцяних_схемами"])
    print(f"полів обіцяно схемами: {p['обіцяних_схемами']}")
    for label, _ in _METHOD_CLASSES:
        if label == "відкладені":
            continue
        n = p.get(label, 0)
        print(f"  {label:24} {n:>4}  ({100 * n / total:.1f}%)")
    n = p.get("прогалини", 0)
    print(f"  {'прогалини':24} {n:>4}  ({100 * n / total:.1f}%)")
    worst = list(report["прогалини_за_полем"].items())[:5]
    if worst:
        print("  найчастіші прогалини:",
              ", ".join(f"{k}×{v}" for k, v in worst))
    print("  ^ правильність проти еталона цей звіт НЕ міряє -- її дає "
          "eval/evaluate\n    на корпусах з еталоном (див. _що_це_міряє у "
          "run-report.json).")
