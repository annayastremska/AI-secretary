"""Що саме лікує сім провалів: каталог, формулювання промпту, чи обидва.

Запуск (модель піднята):
    git show origin/anya-pipeline:demos/upload_app/query_catalog.yaml > /tmp/qc.yaml
    python db/scripts/measure_catalog_variants.py \\
        --base /tmp/qc.yaml --v1 eval/chat/query_catalog_v1.yaml --today 2026-08-27

## Навіщо окремий прилад

`measure_followup_route` показав 14/21 у режимі `gold` і сім провалів. Сирі поля
моделі кажуть, що посилання розв'язане в УСІХ семи, а ламається двоє інших
речей -- і вони з різних боків:

* **каталог**: `list_by_state` просить `date_from`/`date_to`, але заголовок каже
  «на дату або за період», обидва приклади в промпті -- про одну дату, і ніде не
  сказано, що одна дата -- це дві однакові межі. Модель віддає `on_date`;
* **промпт**: системний текст каже «візьми його звідти і ПЕРЕЛІЧИ такі параметри
  в carried_over». Модель у частині випадків саме це й робить -- кладе значення
  в `carried_over`, а поле лишає порожнім.

Якби я правив обидве разом і побачив приріст, то не знав би, що подіяло. Тому
чотири комбінації, і кожна відрізняється від базової рівно однією річчю.

## Що НЕ змінюється в жодному варіанті

Траси й очікування (`eval/chat/followups.tsv`) -- недоторкані. Це головна
засторога: якщо підправити еталон під власну зміну, приріст буде намальований.
Параметри й приклади шаблонів у копії каталогу теж незмінні -- відрізняються
рівно три заголовки (перевірено порівнянням).
"""
import argparse
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml  # noqa: E402

import measure_followup_route as M  # noqa: E402

# Оригінальне формулювання, яке треба замінити (фрагмент SYSTEM).
OLD = ("візьми його звідти і перелічи такі параметри в carried_over")
NEW = ("ЗАПОВНИ його поле значенням з попереднього ходу, а в carried_over "
       "додай лише НАЗВИ таких параметрів, без значень")


def load(path):
    with open(path, encoding="utf-8") as fh:
        return {t["id"]: t for t in yaml.safe_load(fh)["templates"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="/tmp/qc.yaml")
    ap.add_argument("--v1", default="eval/chat/query_catalog_v1.yaml")
    ap.add_argument("--traces", default="eval/chat/followups.tsv")
    ap.add_argument("--today", required=True)
    ap.add_argument("--mode", default="gold")
    ap.add_argument("--repeat", type=int, default=3,
                    help="прогонів на варіант: модель не детермінована")
    args = ap.parse_args()

    if OLD not in M.SYSTEM:
        raise SystemExit("фрагмент SYSTEM не знайдено -- прилад змінився, "
                         "перевір формулювання")
    system_fixed = M.SYSTEM.replace(OLD, NEW)
    base, v1 = load(args.base), load(args.v1)
    traces = M.load_traces(args.traces, args.today)

    variants = [
        ("базовий", base, M.SYSTEM),
        ("каталог", v1, M.SYSTEM),
        ("промпт", base, system_fixed),
        ("каталог+промпт", v1, system_fixed),
    ]
    print(f"траси: {len(traces)}   режим: {args.mode}   сьогодні: {args.today}")
    print(f"шаблонів: базовий {len(base)}, копія {len(v1)}\n")

    # Повтори -- не надмірність. Перший прогін дав базовому 13/21, а
    # попередній замір тим самим приладом -- 14/21: модель не детермінована,
    # тому один хід різниці -- це шум. Приріст +2 без оцінки розкиду означав би
    # рівно нічого.
    stats = {name: {"p": [], "t": [], "bad_inherit": [], "med": []}
             for name, _c, _s in variants}
    original_system = M.SYSTEM
    n_turns = 0
    try:
        for rep in range(args.repeat):
            for name, catalog, system in variants:
                M.SYSTEM = system
                schema = M.schema_for(catalog)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    t_ok, p_ok, n, _disp, times, _compl = M.run(
                        catalog, schema, traces, args.mode, args.today, False)
                n_turns = n
                out = buf.getvalue()
                # Хибне успадкування -- окрема, найважливіша ознака: це шкода
                # від пам'яті, а не її брак. Витягуємо з тексту претензій,
                # бо run() віддає лише клас траси.
                stats[name]["p"].append(p_ok)
                stats[name]["t"].append(t_ok)
                stats[name]["med"].append(sorted(times)[len(times) // 2]
                                          if times else 0)
                stats[name]["bad_inherit"].append(
                    out.count("успадковано хибно"))
                print(f"  прогін {rep + 1}  {name:<16} шаблон {t_ok}/{n}  "
                      f"+параметри {p_ok}/{n}  "
                      f"хибних успадкувань {out.count('успадковано хибно')}")
    finally:
        M.SYSTEM = original_system

    print(f"\n{'=' * 74}")
    print(f"{'варіант':<18}{'+параметри':>22}{'мед.':>7}{'хибних успадк.':>17}")
    for name in stats:
        p = stats[name]["p"]
        med_p = sorted(p)[len(p) // 2]
        print(f"{name:<18}{', '.join(str(x) for x in p):>22}"
              f"{f'{med_p}/{n_turns}':>7}"
              f"{sum(stats[name]['bad_inherit']):>17}")

    base = stats[variants[0][0]]["p"]
    base_med = sorted(base)[len(base) // 2]
    spread = max(base) - min(base)
    # УВАГА на те, чого цей розкид НЕ міряє, -- я на цьому вже спіткнувся.
    # Повтори біжать в одному процесі проти того самого інстансу llama-server,
    # тому головного джерела нестабільності вони не бачать. Заміряно окремо:
    # при temperature=0 три РІЗНІ процеси на одному інстансі дали 12, 12, 12,
    # а після ПЕРЕЗАПУСКУ сервера той самий базовий варіант дав 13 (і 14 на ще
    # іншому інстансі). Причина відома: при близьких логітах порядок редукції
    # в llama.cpp залежить від стану кешу й батчингу, тому «нуль температури»
    # не дає відтворюваності МІЖ інстансами.
    #
    # Для порівняння варіантів це не вада, а навпаки: усі чотири біжать в
    # одному інстансі, тобто в однакових умовах. Невідтворюваність ±2
    # стосується АБСОЛЮТНОГО числа, а не приросту.
    print(f"\nрозкид у межах одного інстансу: {spread} ходів "
          f"({min(base)}..{max(base)})")
    print("  Це НЕ повна оцінка шуму: між перезапусками сервера той самий")
    print("  базовий варіант давав 12..14 при temperature=0. Порівняння")
    print("  варіантів від цього не страждає -- вони в одному інстансі.")
    print("приріст медіани проти базового (у межах інстансу):")
    for name, _c, _s in variants[1:]:
        p = stats[name]["p"]
        d = sorted(p)[len(p) // 2] - base_med
        print(f"  {name:<16} {d:+d}")
    print("\nХибне успадкування -- стовпець, який важить найбільше: це шкода\n"
          "від пам'яті, а не її брак. Варіант із приростом і з хибними\n"
          "успадкуваннями гірший за варіант без приросту й без них.")
    print("Траси не змінювались ні в одному варіанті.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
