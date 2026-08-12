"""Точка входу пайплайна: перетворює файл на (ocr_text, ocr_blocks) --
той самий формат, що очікує extract_document(), незалежно від джерела.

docx має вбудований текстовий шар -> пряма екстракція (без OCR).
Зображення (скан/фото) -> OCR (Surya, підключається ззовні через ocr_fn,
щоб цей модуль не тягнув важку ML-залежність, коли вона не потрібна).
"""
import hashlib
import os

DOCX_EXTS = (".docx",)
PDF_EXTS = (".pdf",)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")

# Нижче цього середнього числа символів на сторінку вважаємо, що текстового
# шару фактично немає (скан, загорнутий у PDF) -- тоді сторінки рендеряться
# в зображення й ідуть в OCR. Поріг свідомо низький: краще спробувати
# текстовий шар, ніж марно ганяти OCR по нормальному PDF.
MIN_TEXT_CHARS_PER_PAGE = 40
PDF_RENDER_DPI = 200


def extract_docx_blocks(path: str):
    """Абзаци + клітинки таблиць як блоки, у порядку появи в документі.
    Розбиття багаторядкових блоків на окремі рядки -- відповідальність
    extract_document()/flatten_blocks() (той самий дефект гранулярності
    трапляється і в блоках Surya, тому виправлено централізовано там, а не
    тут по джерелу).

    Імпорт python-docx -- лінивий, усередині функції: цей модуль
    використовується і для суто-OCR прогонів (зображення), де docx не
    встановлено й не потрібен -- те саме міркування, що для Surya (ocr_fn
    підключається ззовні саме щоб не тягнути важку залежність завжди)."""
    from docx import Document

    doc = Document(path)
    blocks = []

    # Об'єднана клітинка (merged) повертається python-docx ОКРЕМО для кожної
    # колонки сітки, під якою вона лежить, тому наївний обхід row.cells
    # дублював той самий текст 4-6 разів (підтверджено на реальному
    # "Посвідченні про відрядження": "рядовий БЕВЗЕНКО ..." з'являвся
    # чотири рази). Це не косметика: роздутий текст -- це довший промпт для
    # LLM, тобто прямі витрати часу на CPU. Дедуплікуємо за самим XML-
    # елементом клітинки (cell._tc), а не за текстом: дві РІЗНІ клітинки з
    # однаковим текстом (напр. дві порожні дати) лишаються обидві.
    # lxml перевикористовує id() звільнених проксі-об'єктів, тому набору
    # самих id НЕ достатньо: перший варіант цього коду через id() випадково
    # відкинув рядок з датами відрядження (елемент вважався "вже баченим").
    # Тримаємо сильні посилання на елементи -- поки посилання живе, lxml
    # гарантує той самий проксі, отже id стабільний і не перевикористаний.
    # Визначена ДО обходу header/footer, бо таблиці бувають і там (гриф
    # "ДЛЯ СЛУЖБОВОГО КОРИСТУВАННЯ" у клітинці поруч із номером примірника
    # -- реальна верстка офіційних листів), не лише в тілі документа.
    seen_ids, seen_refs = set(), []

    def walk_tables(tables):
        """Рекурсивно, бо таблиця може лежати ВСЕРЕДИНІ клітинки іншої
        таблиці (у бланках це звичайна річ), а `doc.tables` віддає лише
        верхній рівень -- вкладені раніше не читались узагалі й мовчки
        випадали з екстракції."""
        for table in tables:
            for row in table.rows:
                for cell in row.cells:
                    tc = cell._tc
                    if id(tc) in seen_ids:
                        continue
                    seen_ids.add(id(tc))
                    seen_refs.append(tc)
                    text = cell.text.strip()
                    if text:
                        blocks.append(text)
                    if cell.tables:
                        walk_tables(cell.tables)

    # Кутовий штамп/шапка реального заповненого документа може бути
    # надрукована в header, не в тілі -- python-docx не читає header/footer
    # разом з doc.paragraphs, це треба явно. is_linked_to_previous
    # перевіряємо, бо секція без власного header успадковує header
    # попередньої секції -- без цієї перевірки один і той самий текст
    # задублювався б для кожної секції документа.
    # Дедуплікація за id() тут була б тим самим багом, що вже знайдений для
    # клітинок вище (lxml перевикористовує id звільнених проксі), тому
    # порівнюємо за самим ВМІСТОМ header/footer: він короткий, а різні секції
    # з однаковою шапкою -- це справді один і той самий текст.
    # first_page_header/footer -- ОКРЕМИЙ від звичайного header/footer вміст
    # (реальна властивість python-docx, перевірено), який Word показує лише
    # на 1-й сторінці секції, коли увімкнено "Особлива перша сторінка". Раніше
    # не читався взагалі -- офіційний лист із повною шапкою (адресат, гриф,
    # вихідний номер) саме на 1-й сторінці губив цей текст мовчки. Читаємо
    # лише коли прапорець дійсно увімкнений -- інакше python-docx все одно
    # віддає "порожній" проксі-об'єкт цієї секції, який Word не показує.
    seen_header_texts = set()
    header_footer_parts = []
    for section in doc.sections:
        header_footer_parts.append(section.header)
        header_footer_parts.append(section.footer)
        if section.different_first_page_header_footer:
            header_footer_parts.append(section.first_page_header)
            header_footer_parts.append(section.first_page_footer)

    for part in header_footer_parts:
        if part.is_linked_to_previous:
            continue
        part_texts = tuple(p.text.strip() for p in part.paragraphs if p.text.strip())
        if part_texts and part_texts not in seen_header_texts:
            seen_header_texts.add(part_texts)
            blocks.extend(part_texts)
        walk_tables(part.tables)

    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            blocks.append(text)

    walk_tables(doc.tables)
    return "\n".join(blocks), blocks


def sort_blocks_by_geometry(blocks):
    """v2: layout-aware впорядкування замість довіри до порядку списку.

    blocks: або list[str] (немає геометрії -- напр. docx, де порядок
    параграфів документа й так надійний, повертається без змін), або
    list[{"text": str, "bbox": (x1, y1, x2, y2)}] -- формат, який тепер
    повертає Surya для кожного блоку (bbox у координатах сторінки).

    Сортує за (y1, x1): зверху-вниз, зліва-направо за РЕАЛЬНОЮ позицією на
    сторінці, а не за тим, у якому порядку detection-модель внутрішньо
    згенерувала блоки. Дешева, детерміністична заміна повноцінних
    layout-моделей (LayoutLM/LiLT) -- без нової моделі й без GPU, лише
    геометрія, яку OCR вже рахує.

    Повертає list[{"text","bbox"}] (bbox БІЛЬШЕ НЕ відкидається -- раніше
    тут `[b["text"] for b in ordered]` губило геометрію одразу після
    сортування, і вона не доходила до extract_document взагалі; саме це
    не давало прив'язати значення до лейбла ГЕОМЕТРИЧНО, коли порядок
    блоків уводить в оману -- research-round-2026-08-12.md). list[str]
    повертається без змін, коли геометрії немає (docx)."""
    if not blocks or isinstance(blocks[0], str):
        return blocks
    missing = [b for b in blocks if not b.get("bbox")]
    if missing:
        # Явна помилка, а не тиха деградація до природного порядку: якщо
        # bbox зник (напр. інша версія/режим Surya), це має бути ПОМІЧЕНО,
        # бо без геометрії ми повертаємось до того самого класу багів
        # (сплутаний порядок блоків), який ця функція існує, щоб виправити.
        raise ValueError(
            f"{len(missing)} з {len(blocks)} блоків без bbox -- геометричне "
            "сортування неможливе. Перевірте версію/режим OCR-виклику."
        )
    return sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))


def _block_text(block) -> str:
    """block -- str або {"text","bbox"}; повертає сам текст незалежно від
    представлення. Потрібно, відколи sort_blocks_by_geometry перестала
    зрізати bbox перед поверненням."""
    return block["text"] if isinstance(block, dict) else block


def join_block_texts(blocks) -> str:
    return "\n".join(_block_text(b) for b in blocks)


def file_sha256(path: str) -> str:
    """Ідентичність документа за вмістом, не за назвою файлу -- основа
    дедуплікації: ручний експорт людиною з АСКОД/Армія+ гарантує, що той
    самий документ рано чи пізно завантажать двічі, а подвійні факти
    подвоюють саме ті підрахунки, заради яких будується система."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_pdf_blocks(path: str, ocr_fn=None, warnings=None, info=None):
    """PDF, рішення "текстовий шар чи скан" ПО КОЖНІЙ СТОРІНЦІ окремо:

    - сторінка з текстом -> `page.get_text("blocks")` дає блоки РАЗОМ з
      координатами, тому PDF отримує таку саму геометричну обробку, як OCR,
      без OCR;
    - сторінка без тексту (скан, вклеєне фото) -> рендериться в зображення й
      іде через ocr_fn.

    Раніше рішення приймалось один раз на весь документ за середнім числом
    символів, тому сторінка-скан усередині текстового PDF ніколи не
    потрапляла в OCR і мовчки випадала.

    Блоки сортуються ВСЕРЕДИНІ кожної сторінки, а сторінки склеюються за
    порядком: спільне сортування по всьому документу перемішало б сторінки
    між собою, бо y-координати на кожній сторінці починаються з нуля.

    PyMuPDF імпортується ліниво -- решта пайплайна працює без нього.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise ValueError(
            f"Для PDF потрібен PyMuPDF (pip install pymupdf): {path}"
        ) from exc

    import tempfile

    blocks = []
    pages_needing_ocr = []
    ocr_pages = 0
    with fitz.open(path) as doc:
        with tempfile.TemporaryDirectory() as tmpdir:
            for i, page in enumerate(doc):
                page_text = page.get_text() or ""
                if len(page_text.strip()) >= MIN_TEXT_CHARS_PER_PAGE:
                    page_blocks = []
                    for b in page.get_text("blocks"):
                        # (x0, y0, x1, y1, text, block_no, block_type); 1 -- зображення
                        if len(b) >= 7 and b[6] != 0:
                            continue
                        text = (b[4] or "").strip()
                        if text:
                            # `page: i` -- кожна сторінка PDF рахує bbox з
                            # нуля (коментар нижче), тому геометричне
                            # порівняння МІЖ сторінками безглузде -- виміряний
                            # реальний баг: блок із СТОРІНКИ 2 "вирівнювався"
                            # з лейблом на СТОРІНЦІ 1 лише тому, що обидві
                            # сторінки рахують y з нуля (LEAVE-003.pdf,
                            # 2 сторінки). find_block_before_label/
                            # _geometric_candidate фільтрують за цим полем.
                            page_blocks.append({"text": text, "bbox": (b[0], b[1], b[2], b[3]), "page": i})
                    blocks.extend(sort_blocks_by_geometry(page_blocks))
                    continue

                # Сторінка без текстового шару
                if ocr_fn is None:
                    pages_needing_ocr.append(i + 1)
                    continue
                image_path = os.path.join(tmpdir, f"page_{i:03d}.png")
                page.get_pixmap(dpi=PDF_RENDER_DPI).save(image_path)
                ocr_blocks = ocr_fn(image_path)
                for block in ocr_blocks:
                    if isinstance(block, dict):
                        block["page"] = i
                blocks.extend(sort_blocks_by_geometry(ocr_blocks))
                ocr_pages += 1

    # Скільки сторінок пройшло через OCR -- визначає source_kind у БД-споживача
    # (CHECK electronic/photo): PDF з текстовим шаром це electronic, а
    # сканований PDF за суттю такий самий photo, як jpg зі смартфона, і
    # оцінювати його якість треба за тією самою планкою.
    # `scan_pages_detected` -- ОКРЕМО від ocr_pages: якщо `ocr.engine: none`,
    # скан-сторінки потрапляють у pages_needing_ocr, а НЕ проганяються через
    # OCR, тому ocr_pages лишається 0 і source_kind раніше хибно виходив
    # "electronic" для документа, що ЧАСТКОВО скан -- прямо суперечило
    # коментарю вище. Виявлення скан-сторінки не залежить від того, чи
    # налаштований OCR для її обробки.
    if info is not None:
        info["ocr_pages"] = ocr_pages
        info["scan_pages_detected"] = ocr_pages > 0 or bool(pages_needing_ocr)

    if pages_needing_ocr:
        if not blocks:
            raise ValueError(
                f"У PDF {path} немає текстового шару (скан), а OCR не налаштовано "
                "(ocr.engine: none у конфізі)."
            )
        # Частина сторінок -- скан, решта з текстом. Раніше умова була
        # `and not blocks`, тому за наявності хоч однієї текстової сторінки
        # скановані МОВЧКИ зникали: зворотний бік бланка (дата повернення,
        # зупинки) губився без жодного сліду. Тепер це попередження, яке
        # доходить до звіту й до запису.
        if warnings is not None:
            warnings.append(
                f"пропущено {len(pages_needing_ocr)} сторінок без текстового шару "
                f"(№ {', '.join(map(str, pages_needing_ocr))}) -- OCR не налаштовано")
    return join_block_texts(blocks), blocks


def load_document_blocks(path: str, ocr_fn=None, warnings=None, info=None):
    """ocr_fn: (image_path) -> list[{"text","bbox"}] або list[str];
    обов'язковий лише для зображень. Невідоме розширення -- явна помилка,
    без мовчазного fallback.

    Повертає (text, blocks) -- той самий формат, що очікує extract_document,
    незалежно від джерела. info (необов'язковий словник) наповнюється
    метаданими самого інжесту: ocr_pages -- скільки сторінок довелось
    розпізнавати, source_kind -- electronic/photo у термінах БД-споживача."""
    ext = os.path.splitext(path)[1].lower()

    if ext in DOCX_EXTS:
        if info is not None:
            info["ocr_pages"] = 0
            info["source_kind"] = "electronic"
        return extract_docx_blocks(path)

    if ext in PDF_EXTS:
        result = extract_pdf_blocks(path, ocr_fn=ocr_fn, warnings=warnings, info=info)
        if info is not None:
            # НЕ info.get("ocr_pages"): скан-сторінка, пропущена через
            # ocr.engine: none, ніколи не збільшує ocr_pages, але документ
            # усе одно частково скан -- source_kind має це відображати.
            info["source_kind"] = "photo" if info.get("scan_pages_detected") else "electronic"
        return result

    if ext in IMAGE_EXTS:
        if info is not None:
            info["ocr_pages"] = 1
            info["source_kind"] = "photo"
        if ocr_fn is None:
            raise ValueError(
                f"Файл {path} -- зображення, але OCR не налаштовано "
                "(ocr.engine: none у конфізі). Для зображень потрібен ocr.engine: surya."
            )
        # Сортування за геометрією централізоване тут, а не в ocr_fn, щоб
        # OCR-виклик лишався простим "зібрати блоки", не турбуючись про порядок.
        ordered_blocks = sort_blocks_by_geometry(ocr_fn(path))
        return join_block_texts(ordered_blocks), ordered_blocks

    raise ValueError(f"Непідтримуване розширення файлу: {ext} ({path})")
