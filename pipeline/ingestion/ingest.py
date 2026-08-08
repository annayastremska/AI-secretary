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

    # Кутовий штамп/шапка реального заповненого документа може бути
    # надрукована в header, не в тілі -- python-docx не читає header/footer
    # разом з doc.paragraphs, це треба явно. is_linked_to_previous
    # перевіряємо, бо секція без власного header успадковує header
    # попередньої секції -- без цієї перевірки один і той самий текст
    # задублювався б для кожної секції документа.
    seen_headers = set()
    for section in doc.sections:
        for part in (section.header, section.footer):
            if part.is_linked_to_previous or id(part) in seen_headers:
                continue
            seen_headers.add(id(part))
            for para in part.paragraphs:
                text = para.text.strip()
                if text:
                    blocks.append(text)

    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            blocks.append(text)
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
    seen_ids, seen_refs = set(), []
    for table in doc.tables:
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
    геометрія, яку OCR вже рахує і яку попередня версія відкидала."""
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
    ordered = sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))
    return [b["text"] for b in ordered]


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


def extract_pdf_blocks(path: str, ocr_fn=None):
    """PDF двома шляхами, залежно від того, чи є текстовий шар:

    - є текст -> `page.get_text("blocks")` дає блоки РАЗОМ з координатами,
      тому PDF отримує таку саму геометричну обробку, як OCR, без OCR;
    - тексту фактично немає (скан у PDF-обгортці) -> сторінки рендеряться в
      зображення й ідуть через ocr_fn.

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

    blocks = []
    with fitz.open(path) as doc:
        total_chars = sum(len(page.get_text() or "") for page in doc)
        has_text_layer = total_chars >= MIN_TEXT_CHARS_PER_PAGE * max(1, doc.page_count)

        if has_text_layer:
            for page in doc:
                page_blocks = []
                for b in page.get_text("blocks"):
                    # (x0, y0, x1, y1, text, block_no, block_type); type 1 -- зображення
                    if len(b) >= 7 and b[6] != 0:
                        continue
                    text = (b[4] or "").strip()
                    if text:
                        page_blocks.append({"text": text, "bbox": (b[0], b[1], b[2], b[3])})
                blocks.extend(sort_blocks_by_geometry(page_blocks))
            return "\n".join(blocks), blocks

        if ocr_fn is None:
            raise ValueError(
                f"У PDF {path} немає текстового шару (скан), а OCR не налаштовано "
                "(ocr.engine: none у конфізі)."
            )

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            for i, page in enumerate(doc):
                image_path = os.path.join(tmpdir, f"page_{i:03d}.png")
                page.get_pixmap(dpi=PDF_RENDER_DPI).save(image_path)
                blocks.extend(sort_blocks_by_geometry(ocr_fn(image_path)))
    return "\n".join(blocks), blocks


def load_document_blocks(path: str, ocr_fn=None):
    """ocr_fn: (image_path) -> list[{"text","bbox"}] або list[str];
    обов'язковий лише для зображень. Невідоме розширення -- явна помилка,
    без мовчазного fallback.

    Повертає (text, blocks) -- той самий формат, що очікує extract_document,
    незалежно від джерела."""
    ext = os.path.splitext(path)[1].lower()

    if ext in DOCX_EXTS:
        return extract_docx_blocks(path)

    if ext in PDF_EXTS:
        return extract_pdf_blocks(path, ocr_fn=ocr_fn)

    if ext in IMAGE_EXTS:
        if ocr_fn is None:
            raise ValueError(
                f"Файл {path} -- зображення, але OCR не налаштовано "
                "(ocr.engine: none у конфізі). Для зображень потрібен ocr.engine: surya."
            )
        # Сортування за геометрією централізоване тут, а не в ocr_fn, щоб
        # OCR-виклик лишався простим "зібрати блоки", не турбуючись про порядок.
        ordered_blocks = sort_blocks_by_geometry(ocr_fn(path))
        return "\n".join(ordered_blocks), ordered_blocks

    raise ValueError(f"Непідтримуване розширення файлу: {ext} ({path})")
