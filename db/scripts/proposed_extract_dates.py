"""Пропозиція до `tiers.extract_dates` -- чотири випадки зі звіту перед демо.

Запуск (нічого не змінює, лише доводить логіку):
    python db/scripts/test_extract_dates.py \\
        --tiers ~/anya/ai-secretary/demos/upload_app/chat_gradio/tiers.py \\
        --proposed

## Чому обгортка, а не правка у файлі Ані

Обгортка обробляє РІВНО чотири випадки, які зараз неправильні, а все інше
делегує наявній функції. Отже вона не може зламати те, що вже працює
(одна ISO-дата, `10.10.2026`, «28 серпня 2026», «протягом серпня 2026» --
перевірено тим самим тестом). Де цій логіці жити -- у самій `extract_dates`
чи окремим шаром -- вирішує Аня; моя частина тут довести, що логіка правильна.

## Чотири випадки й правило для кожного

1. **Дві явні дати -> діапазон.** Зараз `re.search` бере ПЕРШУ ISO-дату й
   віддає її як зріз, тому «з 2026-05-10 по 2026-10-10» стає одним днем.
   Сім разів із семи у звіті.

2. **Перевернутий період НЕ виправляється мовчки.** Повертається як є
   (from > to), щоб той, хто рендерить відповідь, сказав людині «кінець
   раніше за початок». Тихо поміняти межі місцями -- це відповісти не на те
   питання, яке поставили, і не сказати про це.

3. **«наступного дня після X» -> X+1.** Зараз віддається сам X. Тут же
   «наступного дня» БЕЗ дати навмисно НЕ обробляється: без попереднього
   ходу її нема звідки взяти, і вгадувати «сьогодні+1» гірше за уточнення.

4. **«не пізніше X» -> верхня межа**, «не раніше X» -> нижня. Зараз обидва
   стають точкою, тому «чия відпустка закінчується не пізніше 20-го» дає 0
   замість 1.

## Чого ця обгортка НЕ лікує

Друга половина механізму -- у склейці моделі й правил:

    params["date_from"] = _date("date_from") or _date("on_date") or r_from ...
    params["date_to"]   = _date("date_to")   or _date("on_date") or r_to   ...

Якщо модель віддала одну дату в `on_date`, вона підставляється в ОБИДВІ межі,
і період згортається в один день -- навіть коли правила знайшли діапазон, бо
`_date("on_date")` стоїть у ланцюжку РАНІШЕ за `r_from`/`r_to`. Тому разом із
цією обгорткою потрібна ще одна зміна: для діапазонних шаблонів явно знайдений
у тексті діапазон має бити одиничну дату моделі. Дві дати в питанні -- це
задеклароване, а одиничне поле моделі -- втратна згортка того самого.
"""
import datetime
import re

ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
DOTTED = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
NEXT_DAY_AFTER = re.compile(r"наступн\w*\s+дн\w*\s+(?:після|за)\s*", re.I)
NOT_LATER = re.compile(r"не\s+пізніше", re.I)
NOT_EARLIER = re.compile(r"не\s+раніше", re.I)


def _all_dates(question):
    """Усі дати в питанні, у порядку появи, обидва формати."""
    found = []
    for m in ISO.finditer(question):
        try:
            found.append((m.start(), datetime.date.fromisoformat(m.group(0))))
        except ValueError:
            pass
    for m in DOTTED.finditer(question):
        try:
            found.append((m.start(), datetime.date(
                int(m.group(3)), int(m.group(2)), int(m.group(1)))))
        except ValueError:
            pass
    found.sort()
    return [d for _pos, d in found]


def extract_dates(question, base):
    """-> (on_date, date_from, date_to). `base` -- наявна функція чата."""
    dates = _all_dates(question)

    # 3. «наступного дня після X» -- перевіряється ПЕРЕД рештою, бо в питанні
    # є дата, і будь-яка інша гілка віддала б саму X.
    if dates and NEXT_DAY_AFTER.search(question):
        return dates[0] + datetime.timedelta(days=1), None, None

    # 4. односторонні межі
    if len(dates) == 1 and NOT_LATER.search(question):
        return None, None, dates[0]
    if len(dates) == 1 and NOT_EARLIER.search(question):
        return None, dates[0], None

    # 1 і 2. дві дати -- це діапазон, і саме в тому порядку, у якому названі
    if len(dates) >= 2:
        return None, dates[0], dates[1]

    return base(question)


def inverted(date_from, date_to):
    """Чи період перевернутий. Окремою функцією, бо про це треба СКАЗАТИ, а не
    виправити: людина спитала одне, відповідь була б про інше."""
    return bool(date_from and date_to and date_from > date_to)
