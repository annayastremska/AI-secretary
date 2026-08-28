# -*- coding: utf-8 -*-
"""Один додатковий документ для ЖИВОГО показу — і перевірка, що він не конфліктує.

## Навіщо ще один, якщо резерв уже є

У наборі є `live/` — три документи, які в базу НЕ заливаються, саме щоб на демо
провести один живцем. Перевірено 28.08:

  * **LIVE-01** (№151, Тимченко Демид Гордійович, UNIT-0012) — придатний:
    номер вільний, особа у штатці, документів про відсутність у неї нуль,
    дати 28.08–03.09 (тобто саме «зараз»);
  * **LIVE-02** (№241) — **уже з'їдений**: цей номер у базі, під Єресько
    Розалією Орестівною. Тобто резерв частково використали;
  * **LIVE-03** — фото, тобто OCR на кілька хвилин. Для живого показу погано.

Придатний лишився ОДИН. А потрібно щонайменше два: перший з'їсть репетиція
(дедуплікація за хешем не дасть залити той самий файл двічі — на демо це
виглядало б як «нічого не сталося»), другий піде на сам показ.

## Що перевірено перед вибором

| що | як перевірено |
|---|---|
| номер документа вільний | усі 146 номерів `document_number` із живої бази; 1130+ не зайняті жодним |
| особа Є у штатці | `people.service_id IS NOT NULL` — щоб показати зіставлення з реєстром |
| дати НЕ ПЕРЕТИНАЮТЬСЯ з наявними | правка Ані: особа може вже мати документ, головне щоб періоди не накладались. Тому обрані люди з відпусткою в ЧЕРВНІ, а нове відрядження -- у кінці серпня |
| прізвище УНІКАЛЬНЕ в реєстрі | тією самою регуляркою, якою шукає чат (`db.name_word_regex`). Інакше картка особи впала б у дефект блоку C, і показ ловив би мій же баг |
| дати всередині покриття бази | покриття 2026-05-10 — 2026-10-10 |

Обрано двоє, обидва з наявною відпусткою в червні:

  * **LIVE-04**, `.docx` -- UNIT-0022 Гриценко Олесь Стефанович, відрядження
    №1130, 28.08-02.09 (накриває сьогодні, тому цифра «зараз у відрядженні»
    після завантаження поїде на очах);
  * **LIVE-05**, фото -- UNIT-0166 Павленко Святослав Ааронович, відрядження
    №1131, 29.08-03.09.

## Чому окремий скрипт, а не правка `generate_demo_story.py`

Той генератор перебудовує весь набір (story, bulk, pdf, фото) і переписує
еталони. Дати в ньому відносні, тому перегенерація іншого дня зсунула б УСІ
документи — а вони вже залиті в базу й звірені `verify_catalog` (35/35). Тут
переиспользуються його функції, але пишеться рівно один файл.

Запуск:
    python data/eval/samples/demo-story/generate_one_live.py
    python data/eval/samples/demo-story/generate_one_live.py --number 1131 \
        --who UNIT-0007 --id LIVE-05
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))

#: Функції беремо з основного генератора, а не копіюємо: бланк, розкладка
#: комірок і словесна форма числа днів мусять бути ТИМИ САМИМИ, інакше
#: пайплайн упізнає документ іншою схемою й демо покаже не той шлях.
_spec = importlib.util.spec_from_file_location(
    "gen_demo_story", os.path.join(HERE, "generate_demo_story.py"))
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--id", default="LIVE-04")
    ap.add_argument("--who", default="UNIT-0022",
                    help="service_id особи зі штатки")
    ap.add_argument("--number", default="1130", help="номер документа")
    ap.add_argument("--kind", default="deployment",
                    choices=("leave", "deployment"))
    ap.add_argument("--fmt", default="docx", choices=("docx", "photo", "pdf"))
    ap.add_argument("--start", type=int, default=0,
                    help="початок періоду, днів від «сьогодні»")
    ap.add_argument("--end", type=int, default=5,
                    help="кінець періоду, днів від «сьогодні»")
    ap.add_argument("--today", default=None,
                    help="дата відліку YYYY-MM-DD (дефолт -- системна)")
    ap.add_argument("--out", default=os.path.join(HERE, "live"))
    args = ap.parse_args(argv)

    today = (dt.date.fromisoformat(args.today) if args.today
             else dt.date.today())
    roster = gen.load_roster()
    if args.who not in roster:
        print(f"у штатці немає {args.who}")
        return 1
    who = gen.person_slots(roster[args.who], in_roster=True)

    # Період за замовчуванням накриває СЬОГОДНІ: тоді «скільком зараз у
    # відрядженні» одразу показує зміну після завантаження, і на демо видно
    # не «документ прийнято», а що цифра поїхала.
    #
    # Дати перевіряються окремо (розділ «що перевірено» у шапці): вони не
    # мусять перетинатися з тим, що в особи вже є. Саме тому за замовчуванням
    # обрана людина з відпусткою в ЧЕРВНІ -- накладення неможливе.
    if args.kind == "leave":
        spec = dict(id=args.id, kind="leave", who=args.who,
                    number=args.number, issue=-2,
                    start=args.start, end=args.end,
                    leave_type="щорічна основна відпустка за 2026 рік",
                    place="м. Заріччя", vpd="4480/26", fmt=args.fmt)
    else:
        spec = dict(id=args.id, kind="deployment", who=args.who,
                    number=args.number, issue=-2, order=-3,
                    order_number="447", start=args.start, end=args.end,
                    dest="м. Заріччя", dest_org="військова частина К2317",
                    purpose="приймання матеріально-технічних засобів",
                    fmt=args.fmt)

    values, truth = gen._build_one(spec, who, today)
    os.makedirs(args.out, exist_ok=True)
    if args.fmt == "docx":
        path = os.path.join(args.out, f"{args.id}.docx")
        (gen.fill_leave_docx if args.kind == "leave"
         else gen.fill_deployment_docx)(path, values)
    else:
        # Фото складається з ТОГО САМОГО pdf, що й решта набору: спершу
        # текстовий шар, потім «знімок телефоном» із поворотом, тінню, шумом
        # і артефактами JPEG. Псується навмисно -- інакше це не фото, а скан.
        import random
        pages = (gen.leave_pdf_pages(values) if args.kind == "leave"
                 else gen.deployment_pdf_pages(values))
        pdf_path = os.path.join(args.out, f"{args.id}.pdf")
        gen.render_pdf(pdf_path, pages)
        if args.fmt == "pdf":
            path = pdf_path
        else:
            path = gen.make_photo(pdf_path, args.out, args.id, "normal",
                                  random.Random(20260828))
            os.remove(pdf_path)

    # Еталон -- у тому самому форматі, що решта набору: без нього документ
    # неможливо перевірити приладом, лишається тільки «на око».
    gen.write_expected(args.id, spec, who, values, truth)

    print(f"створено: {path}")
    print(f"  особа:  {who['full_name']} ({args.who}, {who['rank']}, "
          f"{who['subdivision']})")
    print(f"  номер:  №{args.number}, виданий {values['ISSUE_DATE']}")
    print(f"  період: {values['START_D']} {values['START_M']} — "
          f"{values['END_D']} {values['END_M']} 20{values['END_Y']}")
    print(f"  тип:    {'відпустка' if args.kind == 'leave' else 'відрядження'}"
          f", формат {args.fmt}")
    print(f"  розмір: {os.path.getsize(path)} байт")
    exp = os.path.join(ROOT, "data", "eval", "demo-story", "per-document",
                       f"{args.id}.json")
    if os.path.exists(exp):
        print(f"  еталон: {exp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
