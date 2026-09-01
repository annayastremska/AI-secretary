"""Чи витягує чат дати правильно -- на справжніх питаннях зі звіту перед демо.

Запуск (потрібен лише файл чата, ні бази, ні моделі):
    git show origin/anya-pipeline:demos/upload_app/chat_gradio/tiers.py > /tmp/tiers.py
    python db/scripts/test_extract_dates.py --tiers /tmp/tiers.py

## Навіщо

У звіті «Що показав чат» розділ «Дата» -- найбільший, і з п'яти пунктів лише
ОДИН про пам'ять («А у відрядженні?» після питання з датою). Решта чотири --
односхідні питання, де пам'ятати нічого не треба:

* «наступного дня після 2026-10-10» -> порахував на 10-те;
* «не пізніше 2026-10-20» -> прочитав як «саме 20-го»;
* «з 2026-05-10 по 2026-10-10» -> «зріз: 2026-05-10 -- 2026-05-10», сім разів
  із семи;
* перевернутий період не помічений.

`extract_dates(question)` -- чиста функція: рядок на вході, трійка
(on_date, date_from, date_to) на виході. Отже це перевіряється без бази,
без моделі й без сервера, за секунду. Саме тому тест і написаний: якщо
причина тут, то не треба ні пам'яті, ні переписування реплік.

## Чому цього НЕ досить, щоб закрити пункти звіту

Функція -- лише половина механізму. Друга половина в склейці моделі й правил:

    params["date_from"] = _date("date_from") or _date("on_date") or r_from or ...
    params["date_to"]   = _date("date_to")   or _date("on_date") or r_to   or ...

Якщо модель віддала ОДНУ дату в `on_date` (а вона це робить охоче -- заміряно
окремо), ця одна дата підставляється в ОБИДВІ межі, і період згортається в
один день -- навіть коли в питанні дві дати. Тобто причин дві, складених одна
на одну, і тест показує лише першу.
"""
import argparse
import datetime
import importlib.util
import sys

D = datetime.date

# (питання, очікувано on_date, date_from, date_to, звідки взялось)
CASES = [
    ("Скільки осіб у відпустці 2026-10-10?", D(2026, 10, 10), None, None,
     "працює -- контроль, щоб фікс це не зламав"),
    ("Скільки осіб у відпустці 10.10.2026?", D(2026, 10, 10), None, None,
     "звіт §23, працює"),
    ("Хто відсутній у 2 роті 28 серпня 2026?", D(2026, 8, 28), None, None,
     "виправлено раніше Анею, контроль проти регресу"),
    ("Хто був у відрядженні з 2026-05-10 по 2026-10-10?", None,
     D(2026, 5, 10), D(2026, 10, 10), "звіт §4 -- друга дата зникає"),
    ("Скільки осіб у відпустці з 2026-10-01 по 2026-09-01?", None,
     D(2026, 10, 1), D(2026, 9, 1), "звіт §5 -- перевернутий період"),
    ("Хто з 3-ї механізованої роти був у відпустці протягом серпня 2026?",
     None, D(2026, 8, 1), D(2026, 8, 31), "звіт §4, друге питання"),
    ("Скільки осіб у відпустці наступного дня після 2026-10-10?",
     D(2026, 10, 11), None, None, "звіт §2 -- рахує той самий день"),
    ("А чия відпустка закінчується не пізніше 2026-10-20?", None, None,
     D(2026, 10, 20), "звіт §3 -- «не пізніше» як точка"),
]


def load(path):
    """Імпортує tiers.py ЗА МІСЦЕМ, з його ж теки в sys.path.

    Модулі чата імпортують один одного плоско (`import prompts`), тому копія
    файла окремо не піднімається. Імпорт -- лише читання: файли Ані не
    змінюються, у базу нічого не йде.
    """
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(path)))
    spec = importlib.util.spec_from_file_location("tiers_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tiers_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tiers", default="/tmp/tiers.py")
    ap.add_argument("--proposed", action="store_true",
                    help="перевіряти пропозицію (обгортку) замість наявної функції")
    args = ap.parse_args()

    mod = load(args.tiers)
    fn = mod.extract_dates
    if args.proposed:
        import proposed_extract_dates as P
        base = mod.extract_dates
        fn = lambda q: P.extract_dates(q, base)  # noqa: E731
        print("перевіряється ПРОПОЗИЦІЯ (обгортка над наявною функцією)")
    else:
        print("перевіряється НАЯВНА функція чата")
    print()
    ok = bad = 0
    print(f"{'питання':<62}{'очікувано':<34}{'вийшло'}")
    for q, e_on, e_from, e_to, note in CASES:
        got = fn(q)
        exp = (e_on, e_from, e_to)
        good = got == exp
        ok, bad = (ok + 1, bad) if good else (ok, bad + 1)
        mark = "OK  " if good else "ПРОВАЛ"
        print(f"\n{mark} {q[:70]}")
        print(f"      очікувано: on={e_on} from={e_from} to={e_to}")
        print(f"      вийшло:    on={got[0]} from={got[1]} to={got[2]}")
        print(f"      {note}")
    print(f"\nразом: OK {ok}, провалів {bad} із {len(CASES)}")
    print("\nЦе половина механізму. Друга -- склейка, де ОДНА дата моделі\n"
          "підставляється в обидві межі; її цей тест не бачить.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
