"""Ріже документ на структурні одиниці й одразу перевіряє сам себе.

Запуск:
    python db/scripts/segment_documents.py --check              # база, усі 41
    python db/scripts/segment_documents.py --check --files a.md b.md
    python db/scripts/segment_documents.py --doc 235 --show 12

## Навіщо одиниці, а не 1200-символьні вікна

Вікно не має імені. Його не процитувати як «пункт 3.2», його не розмітити для
тесту, і воно ріже посеред статті. Одиниця має адресу, тому цитата має
джерело, а тест -- очікувану відповідь.

## Найважливіше обмеження: зсуви

Різання йде по СИРОМУ тексту. Жодного `strip_braces`, `unspace_letters` чи
склеювання пробілів: усе це змінює довжину, після чого `char_start` вказує не
туди, куди думає. Ціна -- у межі одиниці іноді потрапляє службова примітка;
це чесніше за зсуви, які тихо брешуть.

## Межі -- ОБ'ЄДНАННЯ маркерів, а не одна переможна родина

Перша версія обирала одну родину на документ («переможець забирає все») і на
цьому ламалась. Справжні документи змішують глибини: у документі 207 розділи
нумеровані `1.`, `2.`, а пункти -- `6.1.1`. Вибір `N.N.N` викинув усі межі в
перших 54 тисячах символів, і вони стали ОДНОЮ одиницею на 53901 символ.

Тому межа -- будь-який рядок, що починається маркером.

## Контейнери дають префікс мітки

`1.` всередині статті 12 і `1.` всередині статті 40 -- різні пункти з
однаковою міткою. Без префікса таких дублікатів було 1770 із 6975 одиниць, а
мітка, яка не адресує однозначно, робить неможливим і цитування, і розмітку
тесту. Тому Стаття / Розділ / Додаток / markdown-заголовок працюють як
контейнери: вони і межа, і префікс для вкладених пунктів.

Додаток тут не для краси: у ньому нумерація починається з нуля, тобто без
префікса він неминуче стикається з основним текстом.

## Жанр внутрішньої інструкції

markdown-заголовки (`## 1. Назва мережі та зона покриття`) -- це жанр
інструкції підрозділу (як називати вайфай, техпаспорт ДГУ), а не
законодавства. У корпусі zakon.rada його немає взагалі, тому перевіряти
різання лише на законах означає не перевірити випадок, якого в реальній
частині буде більшість.

## Зміст -- не структура

Рядок `1. Загальні положення ......... 5` виглядає як початок пункту, але це
рядок змісту. Без відкидання таких рядків документ ріжеться двічі: один раз по
змісту, другий -- по справжніх пунктах. Ознака -- пробіг точок або самотнє
число сторінки в кінці рядка.
"""
import argparse
import glob
import os
import re

# Межа не з голови, а з вікна моделі ембеддингу: e5 обрізає вхід на 512
# токенях, а українською виміряно 4.12 символи на токен -> 512 токенів це
# ~2100 символів. При MAX_UNIT=4000 (як було спершу) 8% одиниць пробивали цю
# межу, і хвіст такої одиниці ставав невидимим для семантичної гілки -- тихо,
# без жодної помилки. Лексична гілка хвіст усе одно бачить (tsvector будується
# з повного тексту), тому це була деградація, а не втрата, але непотрібна.
MAX_UNIT = 2000
MIN_UNIT = 80          # коротше -- як правило, залишок службового рядка

# Перекриття між частинами однієї розрізаної одиниці. Було у вікнах (200), я
# його при переході на одиниці прибрав -- це була регресія. Потрібне не для
# вигляду, а для ЗНАХОДЖЕННЯ: якщо відповідь лежить рівно на стику, без
# перекриття жодна частина не містить її цілком, і векторний збіг просідає
# в обох одразу.
OVERLAP = 200

# Межі, за якими можна різати, від найкращої до найгіршої. Раніше ланцюжок
# закінчувався на переносі рядка -- а в цьому корпусі переносів у потрібному
# діапазоні часто немає, і тоді розріз падав рівно на MAX_UNIT, тобто посеред
# речення. Кінець речення додано саме тому.
SENT_END = re.compile(r"[.;:!?]\s")
# Ознака того, що одиницю породило ОБРІЗАННЯ ЗА ДОВЖИНОЮ, а не структура.
# Потрібна однозначна: складена мітка «Додаток 1 / 2.» уже містить слеш.
SPLIT_MARK = "~частина "

# КОНТЕЙНЕРИ дають і межу, і префікс мітки. Нумеровані пункти -- лише межу.
CONTAINERS = {"стаття", "розділ", "додаток", "markdown"}
MARKERS = [
    ("стаття", re.compile(r"^[ \t]*(Стаття\s+\d+(?:[-–]\d+)?)\s*\.?", re.M)),
    ("розділ", re.compile(r"^[ \t]*((?:РОЗДІЛ|Розділ|ГЛАВА|Глава)\s+[IVXLC\d]+)", re.M)),
    # Решту рядка беремо разом із «Додаток»: сам «Додаток» без номера дає
    # однаковий префікс усім додаткам, тобто не розрізняє нічого -- а саме в
    # додатках нумерація починається з нуля й стикається з основним текстом.
    ("додаток", re.compile(r"^[ \t]*(Додаток[ \t]*\d*[^\n]{0,60})", re.M)),
    ("markdown", re.compile(r"^[ \t]*#{1,6}\s+(.{1,120})$", re.M)),
    ("пункт", re.compile(r"^[ \t]*(\d{1,2}\.\d{1,2}\.\d{1,2})\.?\s+(?=\S)", re.M)),
    ("пункт", re.compile(r"^[ \t]*(\d{1,2}\.\d{1,2})\.?\s+(?=\S)", re.M)),
    ("пункт", re.compile(r"^[ \t]*(\d{1,3})\.\s+(?=\S)", re.M)),
]

# Рядок змісту: пробіг точок, або назва + самотнє число сторінки в кінці.
TOC_LINE = re.compile(r"(\.\s*){4,}\s*\d+\s*$|\s{3,}\d{1,3}\s*$")
# Порожній рядок безпосередньо перед маркером -- ознака початку абзацу.
BLANK_LINE = re.compile(r"\n[ \t]*\n[ \t]*$")


def _is_toc(text, pos):
    nl = text.find("\n", pos)
    end = len(text) if nl < 0 else nl
    return bool(TOC_LINE.search(text[pos:end]))


def _blank_before(text, pos):
    """Чи маркер стоїть на початку абзацу (перед ним порожній рядок)."""
    before = text[max(0, pos - 400):pos]
    return pos == 0 or bool(BLANK_LINE.search(before))


def _depth(label):
    return label.count(".") + 1 if label[0].isdigit() else 0


def _nest(ordered):
    """Не ВІДКИДАТИ межу, що не продовжує послідовність, а ПЕРЕ-ПІДПОРЯДКУВАТИ.

    `seq` (відкидання) майже не допоміг: дублікатів 2210 -> 2016. Причина в
    тому, що відкидання губить справжню інформацію -- «1.» після «5.» це не
    сміття, це початок вкладеного переліку. Тому замість викидання будуємо
    шлях: така межа стає дитиною попередньої, і мітка виходить `5./1.`.

    Так зникає причина дублікатів (однакова мітка на різних глибинах) без
    втрати жодної межі.
    """
    out, stack = [], []      # stack: [(число, мітка_шляху)]
    for pos, kind, label in ordered:
        if kind in CONTAINERS:
            stack = []
            out.append((pos, kind, label))
            continue
        try:
            last = int(label.rstrip(".").split(".")[-1])
        except ValueError:
            out.append((pos, kind, label))
            continue
        # Розкручуємо стек, доки верхівка не стане тим, що ця межа продовжує.
        while stack and last != stack[-1][0] + 1:
            if last == 1:
                break                      # починається новий вкладений рівень
            stack.pop()
        if stack and last == stack[-1][0] + 1:
            parent = stack[-1][1].rsplit("/", 1)[0] if "/" in stack[-1][1] else ""
            stack[-1] = (last, f"{parent}/{label}" if parent else label)
        else:
            prefix = stack[-1][1] if stack else ""
            stack.append((last, f"{prefix}/{label}" if prefix else label))
        out.append((pos, kind, stack[-1][1]))
    return out


def boundaries(text, rule="union"):
    """-> [(позиція, вид, мітка)]. `rule` -- як приймаємо нумеровану межу.

    Проблема, яку це міряє: `^\d+\.` -- це і структурний пункт, і елемент
    переліку, і регулярка їх не відрізняє. Наслідок видно в числах: при
    прийманні всіх межей виходить 1836 одиниць коротших за 80 символів і 2268
    дублікатів міток на 12238 одиниць.

    Варіанти правила й що дав кожен на 41 документі:

      правило     одиниць  медіана  дрібних  дублі  цитовних
      union         12238      443     1836   2210      8443
      blank          3450     3565      129     45      3097
      seq           11985      465     1804   2016      8285
      blank+seq      3444     3565      128     45      3091
      nest          12238      443     1836    637      8443

    Читати це так:

    * `blank` (вимагати порожній рядок перед маркером) виглядає найчистішим за
      дублікатами, але це ілюзія: 56% його одиниць породжені обрізанням за
      довжиною, тобто це вікна з гарними мітками, а не структура. У цьому
      корпусі порожніх рядків між пунктами просто немає.
    * `seq` (відкидати межу, що не продовжує послідовність) -- моя спроба
      замінити мовне судження арифметикою. Провалилась: 2210 -> 2016.
      Відкидання губить справжню інформацію.
    * `nest` -- те саме спостереження, але межа не відкидається, а стає
      дитиною попередньої. Дублікатів 2210 -> 637 без втрати жодної одиниці.

    ЧОГО `nest` НЕ РОБИТЬ. Він дає унікальність мітки, але не правильність
    шляху. У документі 235 пункти доданого СТАТУТУ отримали мітки `2/1`,
    `2/2` -- нібито підпункти пункту 2 закону («Цей Закон набирає чинності»),
    хоча насправді це пункти окремого доданого документа. Причина: рядок
    «СТАТУТ ВНУТРІШНЬОЇ СЛУЖБИ ЗБРОЙНИХ СИЛ УКРАЇНИ» не розпізнається як
    контейнер. Мітка виглядає авторитетно, а є артефактом -- і жодна кількість
    регулярок цього класу не закриває, бо це вже питання «що тут документ».
    Найближчий механічний крок -- вважати контейнером рядок у ВЕРХНЬОМУ
    РЕГІСТРІ; це не перевірено.
    """
    found = {}
    for kind, pat in MARKERS:
        for m in pat.finditer(text):
            if _is_toc(text, m.start()):
                continue
            label = " ".join(m.group(1).split())
            prev = found.get(m.start())
            # На одній позиції лишаємо конкретніший (довший) маркер:
            # `6.1.1` важливіший за `6.` на тому самому місці.
            if prev is None or len(label) > len(prev[1]):
                found[m.start()] = (kind, label)

    ordered = sorted((pos, k, lbl) for pos, (k, lbl) in found.items())
    if rule == "union":
        return ordered

    if rule == "nest":
        return _nest(ordered)

    out, expected = [], {}
    for pos, kind, label in ordered:
        if kind in CONTAINERS:
            expected = {}                  # контейнер обнуляє нумерацію
            out.append((pos, kind, label))
            continue
        if "blank" in rule and not _blank_before(text, pos):
            continue
        if "seq" in rule:
            d = _depth(label)
            try:
                last = int(label.split(".")[-1] or label.split(".")[-2])
            except (ValueError, IndexError):
                continue
            want = expected.get(d)
            if want is not None and last != want:
                # Не продовжує послідовність. Якщо це «1», трактуємо як
                # початок вкладеного переліку і НЕ робимо межею.
                if last == 1:
                    continue
                # Стрибок уперед буває через пропущені пункти в тексті --
                # приймаємо, але переставляємо очікування.
            expected[d] = last + 1
            for deeper in [k for k in expected if k > d]:
                del expected[deeper]
        out.append((pos, kind, label))
    return out


def _cut_point(body, lo, hi):
    """Найкраща межа розрізу в [lo, hi): абзац > рядок > кінець речення > hi."""
    for sep in ("\n\n", "\n"):
        p = body.rfind(sep, lo, hi)
        if p > lo:
            return p + len(sep)
    best = -1
    for m in SENT_END.finditer(body, lo, hi):
        best = m.end()
    return best if best > lo else hi


def split_long(text, start, end, label):
    """Задовгу одиницю ріжемо на частини з перекриттям, по межах речень."""
    body = text[start:end]
    if len(body) <= MAX_UNIT:
        return [(start, end, label)]
    out, pos, part = [], 0, 1
    while pos < len(body):
        cut = min(pos + MAX_UNIT, len(body))
        if cut < len(body):
            cut = _cut_point(body, pos + MAX_UNIT // 2, cut)
        # Суфікс саме такий, а не `label/N`: складена мітка вже містить « / »
        # (контейнер + пункт), і я на цьому сам себе обманув -- шукав `/` як
        # ознаку обрізання за довжиною й порахував префікси контейнерів.
        out.append((start + pos, start + cut, f"{label} {SPLIT_MARK}{part}"))
        if cut >= len(body):
            break
        # Наступна частина починається РАНІШЕ за кінець попередньої, щоб стик
        # був покритий двічі. Але початок мусить бути СПРАВЖНЬОЮ межею
        # речення, інакше перекриття саме породжує обрізок із середини --
        # перша версія цього коду брала просто `cut - OVERLAP` і залишила ті
        # самі 31% частин, що починаються з малої літери.
        # УВАГА на систему координат: pos/cut/nxt -- ВІДНОСНІ зсуви в body, а
        # out[-1][0] -- АБСОЛЮТНИЙ. Перша версія порівнювала абсолютну позицію
        # з відносною, умова ніколи не виконувалась, і перекриття тихо не
        # застосовувалось узагалі -- частини стикувались упритул, як і до
        # «виправлення».
        lo = max(pos + 1, cut - 2 * OVERLAP)
        hi = max(lo + 1, cut - OVERLAP // 2)
        nxt = _cut_point(body, lo, hi)
        pos = nxt if pos < nxt < cut else cut
        part += 1
    return out


def segment(text, rule="union"):
    """-> (які маркери спрацювали, [dict(label, parent, char_start, char_end, text)])."""
    bounds = boundaries(text, rule)
    if not bounds:
        # Шлях «маркерів немає» ТЕЖ мусить іти через обрізання за довжиною.
        # Знайдено тестом деградації: документ без переносів рядків (типовий
        # поганий OCR) давав ОДНУ одиницю на 100 тисяч символів, ще й не
        # позначену як порізану. Вектор такої одиниці представляв би перші
        # ~2100 символів, а решта була б невидима семантичній гілці зовсім.
        # Це друга поява того самого недогляду -- перша була з преамбулою.
        return "без маркерів", [
            dict(label=lbl, base_label="весь документ", parent=None,
                 char_start=s, char_end=e, text=text[s:e])
            for s, e, lbl in split_long(text, 0, len(text), "весь документ")]

    # 1. Сегменти між межами, ДО будь-якого різання за довжиною.
    segs = []            # [start, end, label, parent]
    if bounds[0][0] > 0:
        segs.append([0, bounds[0][0], "преамбула", None])

    container = None
    for i, (pos, kind, label) in enumerate(bounds):
        end = bounds[i + 1][0] if i + 1 < len(bounds) else len(text)
        if kind in CONTAINERS:
            container = label
            segs.append([pos, end, label, None])
        else:
            full = f"{container} / {label}" if container else label
            segs.append([pos, end, full, container])

    # 2. Дрібні сегменти зливаємо з ПОПЕРЕДНІМ. Це елементи переліків на
    # 20-40 символів: окремо вони не цитата, а їхній вектор розмитий, бо
    # контексту в них немає. Мітка лишається батьківська -- «4) членів
    # добровольчих формувань» справді належить пункту, який його вводить.
    # Робиться ДО різання за довжиною, інакше злиття може дати завелику
    # одиницю, яку вже ніхто не поріже.
    merged = []
    for seg in segs:
        if merged and (seg[1] - seg[0]) < MIN_UNIT and merged[-1][3] == seg[3]:
            merged[-1][1] = seg[1]
        else:
            merged.append(seg)

    # 3. І лише тепер різання за довжиною.
    units = []
    for start, end, label, parent in merged:
        for s, e, lbl in split_long(text, start, end, label):
            units.append(dict(label=lbl, base_label=label, parent=parent,
                              char_start=s, char_end=e, text=text[s:e]))
    return "+".join(sorted({k for _, k, _ in bounds})), units


# ── перевірки, які не потребують розмітки ───────────────────────────────────


def check(name, text, family, units):
    """Механічні інваріанти: правильна відповідь визначається не судженням."""
    problems = []
    bad = [u["label"] for u in units
           if text[u["char_start"]:u["char_end"]] != u["text"]]
    if bad:
        problems.append(f"зсуви брешуть у {len(bad)}: {bad[:2]}")

    # Перекриття тепер ДОЗВОЛЕНЕ, але лише між сусідніми частинами ОДНІЄЇ
    # одиниці й не глибше за 2*OVERLAP -- саме стільку може дати split_long,
    # коли підтягує початок частини назад до межі речення. Перекриття між
    # РІЗНИМИ одиницями -- усе ще помилка.
    ordered = sorted(units, key=lambda u: u["char_start"])
    over = []
    for a, b in zip(ordered, ordered[1:]):
        if a["char_end"] <= b["char_start"]:
            continue
        same = a.get("base_label") == b.get("base_label")
        depth = a["char_end"] - b["char_start"]
        if not same or depth > 2 * OVERLAP + 2:
            over.append((a["label"], b["label"], depth))
    if over:
        problems.append(f"негодяще перекриття {len(over)}, напр. {over[0]}")

    # Покриття -- по ОБ'ЄДНАННЮ проміжків, а не по сумі довжин: із перекриттям
    # сума завжди більша за текст, і порівнювати її з len(text) безглуздо.
    covered, cursor = 0, 0
    for u in ordered:
        s, e = max(u["char_start"], cursor), u["char_end"]
        if e > s:
            covered += e - s
            cursor = e
    lost = len(text) - covered
    if lost != 0:
        problems.append(f"покриття розійшлось на {lost} симв.")

    labels = [u["label"] for u in units]
    dup = len(labels) - len(set(labels))
    sizes = sorted(u["char_end"] - u["char_start"] for u in units)
    tiny = sum(1 for s in sizes if s < MIN_UNIT)
    huge = sum(1 for s in sizes if s > MAX_UNIT)
    med = sizes[len(sizes) // 2] if sizes else 0
    toc = sum(1 for u in units if TOC_LINE.search(u["text"][:200]))

    print(f"  {str(name):>30} {family:<28} одиниць {len(units):>5} "
          f"медіана {med:>5} дрібних {tiny:>4} завеликих {huge:>3} "
          f"дублі {dup:>4} зміст {toc:>3}"
          + ("  ⚠ " + "; ".join(problems) if problems else ""))
    return dict(units=len(units), tiny=tiny, huge=huge, dup=dup, toc=toc,
                problems=problems)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--doc", type=int)
    ap.add_argument("--show", type=int, default=0)
    ap.add_argument("--rule", default="union",
                    choices=["union", "blank", "seq", "blank+seq", "nest"])
    ap.add_argument("--compare-rules", action="store_true",
                    help="прогнати всі правила й показати числа поруч")
    args = ap.parse_args(argv)

    items = []
    if args.files:
        for pattern in args.files:
            for path in sorted(glob.glob(pattern)) or [pattern]:
                with open(path, encoding="utf-8") as f:
                    items.append((os.path.basename(path), f.read()))
    else:
        import psycopg
        from dotenv import load_dotenv
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        load_dotenv(os.path.join(root, ".env"))
        dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            if args.doc:
                cur.execute("SELECT id, text_content FROM documents WHERE id=%s", (args.doc,))
            else:
                cur.execute("""SELECT id, text_content FROM documents
                                WHERE domain='normative' AND text_content IS NOT NULL
                                ORDER BY id""")
            items = cur.fetchall()

    if args.compare_rules:
        print(f"{'правило':<12} {'одиниць':>8} {'медіана':>8} {'дрібних':>8} "
              f"{'завеликих':>10} {'дублі':>7} {'цитовних':>9} {'проблем':>8}")
        for rule in ("union", "blank", "seq", "blank+seq", "nest"):
            tot = dict(u=0, tiny=0, huge=0, dup=0, ok=0, prob=0)
            meds = []
            for name, text in items:
                _, units = segment(text, rule)
                sizes = sorted(u["char_end"] - u["char_start"] for u in units)
                labels = [u["label"] for u in units]
                tot["u"] += len(units)
                tot["tiny"] += sum(1 for x in sizes if x < MIN_UNIT)
                tot["huge"] += sum(1 for x in sizes if x > MAX_UNIT)
                tot["dup"] += len(labels) - len(set(labels))
                # «цитовна» одиниця -- та, яку є сенс показати людині
                tot["ok"] += sum(1 for x in sizes if 200 <= x <= MAX_UNIT)
                if any(text[u["char_start"]:u["char_end"]] != u["text"] for u in units):
                    tot["prob"] += 1
                meds.append(sizes[len(sizes) // 2] if sizes else 0)
            med = sorted(meds)[len(meds) // 2]
            print(f"{rule:<12} {tot['u']:>8} {med:>8} {tot['tiny']:>8} "
                  f"{tot['huge']:>10} {tot['dup']:>7} {tot['ok']:>9} {tot['prob']:>8}")
        return 0

    stats = []
    for name, text in items:
        family, units = segment(text, args.rule)
        if args.show:
            print(f"\n=== {name}  маркери: {family}  одиниць: {len(units)}")
            for u in units[:args.show]:
                print(f"  [{u['label']}] батько={u['parent']} "
                      f"@{u['char_start']}-{u['char_end']} "
                      f"({u['char_end']-u['char_start']})")
                print(f"      {' '.join(u['text'].split())[:110]}")
            continue
        stats.append(check(name, text, family, units))

    if stats:
        print(f"\nдокументів: {len(stats)}, одиниць: {sum(s['units'] for s in stats)}")
        print(f"  з проблемами інваріантів: {sum(1 for s in stats if s['problems'])}")
        print(f"  дрібних (<{MIN_UNIT}): {sum(s['tiny'] for s in stats)}")
        print(f"  завеликих (>{MAX_UNIT}): {sum(s['huge'] for s in stats)}")
        print(f"  дублікатів міток: {sum(s['dup'] for s in stats)}")
        print(f"  одиниць зі змістом: {sum(s['toc'] for s in stats)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
