"""Одна форма дати на весь каталог -- чи стає краще, і чи щось ламається.

Запуск (модель піднята):
    git show origin/anya-pipeline:demos/upload_app/query_catalog.yaml > /tmp/qc.yaml
    python db/scripts/measure_date_naming.py --today 2026-08-27

## Гіпотеза, яку це перевіряє

Чотири провали, що лишились у найкращому варіанті, -- це одна й та сама
розмова: «Скільки людей у відпустці [на дату]?» -> «а хто?». У ній дата мусить
змінити ім'я: шаблон підрахунку зве її `on_date`, шаблон переліку -- `date_from`
і `date_to`. Заміряно, що модель несе `on_date` з попереднього ходу і далі:

* або лишає межі порожніми (дата є, поле не те);
* або обирає `list_by_state_in_subdivision` -- ЄДИНИЙ перелік, що приймає
  `on_date`, -- тобто підбирає шаблон під параметр, який у неї вже є, і лишає
  підрозділ порожнім.

Якщо причина справді в цьому, то одна форма дати на весь каталог знімає обидва
наслідки, і жодного правила в коді не потрібно.

## Чотири варіанти, і всі з виправленим промптом

Виправлення промпту («заповни поле, а в carried_over лише назви») зміряно
раніше як обов'язкове -- воно знімає хибне успадкування. Тому тут воно стоїть
у всіх варіантах, і порівнюється лише каталог.

    база    оригінал Ані
    v1      три заголовки (підказка про межі, правило про підрозділ, doc_by_number)
    v2      ОДНА форма дати: у 7 шаблонах on_date -> date_from + date_to
    v1+v2   і те, і те

## Чому в v2 інші траси -- і чому це не підтасовка

Очікування трас написані під старий контракт (`on_date=...`). Якщо каталог
перейшов на межі, а еталон ні, замір показав би провали через розбіжність із
еталоном. Тому для v2 узята КОПІЯ трас, перетворена правилом:

    on_date=V  ->  date_from=V;date_to=V
    !on_date   ->  !date_from;!date_to

Друге -- перевірки «не мусить успадкуватись» на змінах теми. Їх перенесено
теж; інакше нова форма перестала б перевірятись на хибне успадкування і
виглядала б безпечнішою, ніж є.

Порівняння лишається чесним у формулюванні «скільком ходам система дає
правильний шаблон і правильні параметри ЗА СВОЇМ ЖЕ контрактом».
"""
import argparse
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml  # noqa: E402

import measure_followup_route as M  # noqa: E402
from measure_catalog_variants import NEW, OLD  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load(path):
    with open(path, encoding="utf-8") as fh:
        return {t["id"]: t for t in yaml.safe_load(fh)["templates"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="/tmp/qc.yaml")
    ap.add_argument("--today", required=True)
    ap.add_argument("--mode", default="gold")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()

    if OLD not in M.SYSTEM:
        raise SystemExit("фрагмент SYSTEM не знайдено -- прилад змінився")
    system_fixed = M.SYSTEM.replace(OLD, NEW)

    e = os.path.join(ROOT, "eval", "chat")
    variants = [
        ("база", args.base, os.path.join(e, "followups.tsv")),
        ("v1 заголовки", os.path.join(e, "query_catalog_v1.yaml"),
         os.path.join(e, "followups.tsv")),
        ("v2 одна форма", os.path.join(e, "query_catalog_v2.yaml"),
         os.path.join(e, "followups_v2.tsv")),
        ("v1+v2", os.path.join(e, "query_catalog_v1v2.yaml"),
         os.path.join(e, "followups_v2.tsv")),
    ]

    print(f"режим {args.mode}, промпт виправлений у ВСІХ варіантах, "
          f"сьогодні {args.today}\n")
    original = M.SYSTEM
    rows = []
    try:
        M.SYSTEM = system_fixed
        for name, cat_path, tr_path in variants:
            catalog = load(cat_path)
            traces = M.load_traces(tr_path, args.today)
            schema = M.schema_for(catalog)
            ps, bads, details = [], [], ""
            for _rep in range(args.repeat):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    t_ok, p_ok, n, _d, _times, _c = M.run(
                        catalog, schema, traces, args.mode, args.today, False)
                out = buf.getvalue()
                ps.append(p_ok)
                bads.append(out.count("успадковано хибно"))
                details = out
            rows.append((name, t_ok, ps, n, sum(bads), details))
            print(f"  {name:<14} шаблон {t_ok}/{n}  "
                  f"+параметри {', '.join(str(x) for x in ps)}/{n}  "
                  f"хибних успадкувань {sum(bads)}")
    finally:
        M.SYSTEM = original

    print(f"\n{'=' * 70}\n{'варіант':<16}{'шаблон':>9}{'+параметри':>13}"
          f"{'хибних успадк.':>17}")
    for name, t_ok, ps, n, bad, _d in rows:
        med = sorted(ps)[len(ps) // 2]
        print(f"{name:<16}{f'{t_ok}/{n}':>9}{f'{med}/{n}':>13}{bad:>17}")

    print("\nщо саме лишилось провальним у кожному варіанті:")
    for name, _t, _p, _n, _bad, details in rows:
        turns = [seg.split()[0] for seg in details.split("✗ ")[1:]]
        print(f"  {name:<16} {', '.join(turns) or 'нічого'}")

    print("\nГіпотеза підтверджується, якщо v2 закриває саме ті ходи, що були\n"
          "«скільки -> а хто» (T01, T03, T06/3, T10/3), і не додає хибних\n"
          "успадкувань. Якщо v2 нічого не змінює -- причина була не в назвах.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
