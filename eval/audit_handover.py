# -*- coding: utf-8 -*-
"""Прилад передачі: що втратиться, якщо видалити цей документ.

## Навіщо

Репозиторій готуємо до передачі іншим людям. Частина написаного їм не
потрібна: наше внутрішнє листування, координація, промпти дослідницьких
прогонів. Але правило Ані від 01.09: **нічого важливого чи потенційно
важливого не втрачається.** А «важливе» на око не визначається — сьогодні
листування, яке за назвою виглядало проханням, тримало єдиний запис
зміряного рішення.

Тому рішення про видалення ухвалюється не смаком, а трьома замірами:

  УНІКАЛЬНЕ   -- рядки-рішення й рядки-заміри, яких НЕМА в жодному іншому
                 файлі репозиторію. Саме вони зникнуть назавжди;
  ПОСИЛАННЯ   -- хто на цей файл посилається. Видалити файл, на який
                 посилаються, означає зробити чужому читачеві битий слід;
  ДУБЛЬ       -- частка рядків файла, які вже є деінде. 100% дубль видаляється
                 без переносу.

## Чого прилад НЕ робить

Не вирішує. Він розділяє «можна видаляти» і «спершу перенести», а перенос
робить людина: витягти зміст у покажчик рішень -- робота на читання, не на
регулярний вираз.

## Запуск

    python eval/audit_handover.py                 # усі кандидати зі списку
    python eval/audit_handover.py docs/шлях.md    # окремі файли
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Маркери РІШЕННЯ і ЗАМІРУ. Це те, що має цінність для чужої людини:
#: «як вирішили і чому» та «яким числом це підтверджено».
DECISION = re.compile(r"(рішення|вирішил|домовил|ЗАКРИТО|не робимо|беремо|"
                      r"обрал|відкинул|правило|критерій)", re.I)
MEASURE = re.compile(r"\d+([.,]\d+)?\s*(с\b|мс|%|разів|хв|ГБ|МБ|питан|з \d+)")

#: Рядок коротший за це не порівнюємо: «## Коротко» збігається у двадцяти
#: файлах і нічого не доводить.
MIN_LEN = 40

#: Кандидати на видалення -- за групами з docs/tasks/2026-09-01_handover-*.md.
GROUPS = {
    "листування": [
        "docs/contracts/2026-08-11_database-handoff.md",
        "docs/contracts/2026-08-23_instruction-for-andriy.md",
        "docs/contracts/2026-08-25_for-anton-vgpu-driver.md",
        "docs/contracts/2026-08-25_loader-no-person-for-andriy.md",
        "docs/contracts/2026-08-26_for-andriy-delete-test-documents.md",
        "docs/contracts/2026-08-26_for-andriy-roster-matching.md",
        "docs/contracts/2026-08-27_answer-reload-four-documents.md",
        "docs/contracts/2026-08-27_answer-to-anya-three-tasks.md",
        "docs/contracts/2026-08-27_for-andriy-grant-normative-schema.md",
        "docs/contracts/2026-08-27_for-andriy-reload-five-documents.md",
        "docs/contracts/2026-08-27_for-andriy-stale-queue-task.md",
        "docs/contracts/2026-08-27_for-anya-chat-followups.md",
        "docs/contracts/2026-08-27_stats-page-metrics-handover.md",
        "docs/contracts/2026-08-28_for-andriy-normative-corpus.md",
        "docs/contracts/2026-08-28_for-anya-metrics-and-routing.md",
    ],
    "питання до людини": [
        "docs/questions-for-anya.md",
        "docs/tasks/2026-08-27_questions-for-anya-harness.md",
    ],
    "прогрес і координація": [
        "docs/tasks/2026-08-24_execution-structure.md",
        "docs/tasks/2026-08-24_app-chat-worklist.md",
        "docs/tasks/2026-08-24_stage1-progress.md",
        "docs/tasks/2026-08-24_stage2-progress.md",
        "docs/tasks/2026-08-25_stage3-progress.md",
        "docs/tasks/2026-08-25_stage4-progress.md",
        "docs/tasks/2026-08-25_waveA-progress.md",
        "docs/tasks/2026-08-25_waveB-progress.md",
        "docs/tasks/2026-08-28_plan-vechir.md",
    ],
    "сире рев'ю й аудит": [
        "docs/review-2026-08-22/arch.md",
        "docs/review-2026-08-22/code.md",
        "docs/review-2026-08-22/verdicts.md",
        "docs/audit-2026-08-23/complexity.md",
        "docs/audit-2026-08-23/hardcoding.md",
    ],
    "демо-день": [
        "docs/demo/access.md",
        "docs/demo/domain-research.md",
        "docs/demo-day-questions.md",
        "docs/demo-day-runbook.md",
        "docs/presentation-plan.md",
        "docs/tasks/2026-08-28_demo-day-deck-blocks.md",
        "docs/tasks/2026-08-29_demo-dialogs.md",
        "docs/tasks/2026-08-29_pitch-research.md",
    ],
    "промпти дослідницьких прогонів": [
        "docs/research/2026-07-27_document-types-corpus/prompt-v1.md",
        "docs/research/2026-07-27_document-types-corpus/prompt-v2.md",
        "docs/research/2026-07-27_document-types-corpus/prompt-changelog.md",
        "docs/research/2026-07-28_ocr-handwriting-benchmark/prompt.md",
        "docs/research/2026-07-28_ocr-handwriting-benchmark/prompt-as-sent.md",
        "docs/research/2026-08-08_embedding-model-selection/prompt-round-1.md",
        "docs/research/2026-08-08_embedding-model-selection/prompt-round-2-blind.md",
        "docs/research/2026-08-11_blind-architecture-design/brief-blind.md",
        "docs/research/2026-08-27_prompt-for-harness-research.md",
        "docs/research/2026-08-27_context-for-harness-research.md",
    ],
}


def norm(line):
    """Порівнюємо зміст, а не оформлення: розмітка, пробіли й регістр геть."""
    s = re.sub(r"[*_`>|#\-–—]", " ", line)
    return re.sub(r"\s+", " ", s).strip().lower()


def read(path):
    return io.open(os.path.join(ROOT, path), encoding="utf-8").read()


def all_text_files():
    """Усі текстові файли репозиторію, окрім .git і кешів."""
    out = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs
                   if d not in (".git", "__pycache__", "node_modules",
                                "data-private", ".venv", "venv")]
        for f in files:
            if f.endswith((".md", ".py", ".yaml", ".yml", ".html", ".txt",
                           ".tsv", ".json")):
                out.append(os.path.relpath(os.path.join(base, f), ROOT)
                           .replace("\\", "/"))
    return out


def main(argv):
    targets = []
    if argv:
        targets = [(a.replace("\\", "/"), "вручну") for a in argv]
    else:
        for g, files in GROUPS.items():
            targets += [(f, g) for f in files]

    missing = [t for t, _ in targets
               if not os.path.exists(os.path.join(ROOT, t))]
    targets = [(t, g) for t, g in targets if t not in missing]

    # Вміст усіх ІНШИХ файлів -- одним словником рядок -> де зустрічається.
    universe = {}
    tset = {t for t, _ in targets}
    for path in all_text_files():
        if path in tset:
            continue
        try:
            txt = read(path)
        except (OSError, UnicodeDecodeError):
            continue
        for line in txt.replace("\r\n", "\n").split("\n"):
            n = norm(line)
            if len(n) >= MIN_LEN:
                universe.setdefault(n, path)

    # Хто на кого посилається.
    refs = {}
    for path in all_text_files():
        if path in tset:
            continue
        try:
            txt = read(path)
        except (OSError, UnicodeDecodeError):
            continue
        for t, _ in targets:
            if os.path.basename(t) in txt or t in txt:
                refs.setdefault(t, []).append(path)

    print("=" * 96)
    print("ПРИЛАД ПЕРЕДАЧІ: що втратиться при видаленні")
    print("=" * 96)
    print(f"{'файл':<62}{'унік.':>7}{'дубль':>7}{'посил.':>7}  вердикт")
    print("-" * 96)

    summary = {}
    details = []
    for t, group in targets:
        txt = read(t).replace("\r\n", "\n")
        lines = [l for l in txt.split("\n") if len(norm(l)) >= MIN_LEN]
        if not lines:
            continue
        dup = [l for l in lines if norm(l) in universe]
        valuable = [l for l in lines
                    if norm(l) not in universe
                    and (DECISION.search(l) or MEASURE.search(l))]
        # Себе й документи цієї ж чистки не рахуємо: перелік кандидатів
        # усередині приладу -- не посилання читача, а його вхідні дані.
        inbound = [r for r in refs.get(t, [])
                   if not r.startswith("docs/tasks/2026-09-01")
                   and r != "eval/audit_handover.py"]
        share = round(100 * len(dup) / len(lines))
        if valuable:
            verdict = "СПЕРШУ ПЕРЕНЕСТИ"
        elif inbound:
            verdict = "полагодити посилання"
        else:
            verdict = "можна видаляти"
        summary.setdefault(group, []).append((verdict, len(valuable)))
        print(f"{t[5:]:<62}{len(valuable):>7}{str(share)+'%':>7}"
              f"{len(inbound):>7}  {verdict}")
        if valuable:
            details.append((t, group, valuable, inbound))

    print("-" * 96)
    print("\nПО ГРУПАХ")
    for g, rows in summary.items():
        need = sum(1 for v, _ in rows if v == "СПЕРШУ ПЕРЕНЕСТИ")
        val = sum(n for _, n in rows)
        print(f"  {g:<34} файлів {len(rows):>2} · перенести {need:>2} · "
              f"унікальних рядків {val}")
    if missing:
        print("\nНЕ ЗНАЙДЕНО (перевір список):")
        for m in missing:
            print("  " + m)

    print("\n" + "=" * 96)
    print("ЩО САМЕ ПЕРЕНОСИТИ (унікальні рядки-рішення й рядки-заміри)")
    print("=" * 96)
    for t, group, valuable, inbound in details:
        print(f"\n-- {t}  [{group}]")
        if inbound:
            print(f"   посилаються: {', '.join(inbound[:4])}")
        for v in valuable[:12]:
            print("   • " + v.strip()[:140])
        if len(valuable) > 12:
            print(f"   … ще {len(valuable) - 12}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
