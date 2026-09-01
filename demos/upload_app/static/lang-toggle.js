"use strict";
/* Перемикач мови інтерфейсу: українська ↔ англійська.
 *
 * ЗАВДАННЯ (Аня 28.08): «обдумай кнопку перекладу на англійську -- як це дешево
 * реалізувати». Дешево -- це головна вимога, тому спершу про підхід.
 *
 * ЧОМУ СЛОВНИК ПО DOM, А НЕ `t()` НА КОЖНОМУ РЯДКУ
 *
 * Підписи цих сторінок не лежать у розмітці -- їх будує JS (у розмітці видимого
 * тексту рівно один рядок на сторінку, решта збирається в скрипті). Заміряно:
 * 105 живих рядків у двох сторінках і трьох скриптах.
 *
 * Звичайний шлях -- обгорнути кожен рядок у `t("ключ")` -- це 105 правок у
 * місцях, де зараз працює перевірений код, і 105 шансів на одруківку в тексті,
 * який людина побачить. Тут же ЖОДНОЇ правки в тих місцях: сторінка малюється
 * як малювалась, а перекладач один раз проходить готовий DOM і підміняє текстові
 * вузли за словником.
 *
 * Ціна цього рішення названа чесно:
 *   - переклад працює по ТОЧНОМУ рядку, тому склеєні з числами підписи потрібні
 *     окремим списком шаблонів (`PATTERNS` нижче);
 *   - словник мусить покривати все, інакше сторінка стає напівукраїнською.
 *     Саме тому є тест, який порівнює словник із рядками сторінок і падає,
 *     коли з'явився новий підпис без перекладу. Прогалину видно, а не видно
 *     оком на демо.
 *
 * ЩО НЕ ПЕРЕКЛАДАЄТЬСЯ І ЦЕ РІШЕННЯ, А НЕ НЕДОРОБКА
 *
 * Відповіді чата лишаються українськими. Вони складаються з ДОСЛІВНИХ ЦИТАТ
 * нормативних документів і значень із бази: перекладена норма -- це вже не
 * норма, а наш переказ, і перевірити її по документу неможливо. Це прямо
 * ламало б правило продукту «цитата або відмова, не переказ». Так само
 * лишаються українськими ПІБ, назви підрозділів і номери документів.
 *
 * Тобто це двомовний ІНТЕРФЕЙС над українськими ДАНИМИ.
 *
 * МЕХАНІКА -- та сама, що в перемикача теми (`theme-toggle.js`), і навмисно:
 * `data-lang` на <html>, вибір у localStorage у try/catch, скрипт у <head> без
 * defer (інакше сторінка блимне українською й перемалюється), жодного
 * зовнішнього запиту. Дві кнопки поруч, однаковий вигляд.
 */
(function () {
  var KEY = "ai-secretary-lang";
  var ORDER = ["uk", "en"];

  /* Підказка веде до НАСТУПНОГО стану: людина натискає, щоб щось змінити. */
  var HINT = { uk: "Switch to English", en: "Перемкнути на українську" };

  /* Словник. Ключ -- рядок, як він стоїть у сторінці; значення -- переклад.
     Порядок не важить: підміна йде за точним збігом тексту вузла. */
  var DICT = {
    /* ── сторінка ЧАТА: перший екран демо ────────────────────────────────
       Ці підписи виправлені руками попри те, що машина їх теж переклала:
       заміряно, що на коротких підписах вона помиляється в термінах --
       «облік особового складу» стало «personal account», а «Зріз» -- «Cut».
       Словник тут перебиває машину, і саме тому шари стоять у цьому порядку. */
    "Чат обліку особового складу": "Personnel records chat",
    "Поставте питання про особовий склад…": "Ask a question about personnel…",
    "Поставте питання.": "Please ask a question.",
    "Очистити чат": "Clear the chat",
    "Надіслати": "Send",
    "Зупинити": "Stop",
    "Приклади питань": "Example questions",
    "Відповідь формується з документів, завантажених у базу.":
      "The answer is built from the documents loaded into the database.",
    "MamayLM 27B — на нашому сервері": "MamayLM 27B — on our own server",

    /* ── шапка, навігація, доступ ─────────────────────────────────────── */
    "AI-секретар": "AI Secretary",
    "облік особового складу": "personnel records",
    "Статистика": "Statistics",
    "Чат": "Chat",
    "Завантажити документ": "Upload a document",
    "Тема": "Theme",
    "Мова": "Language",
    "Меню": "Menu",
    "Закрити панель": "Close panel",
    "Ваш доступ:": "Your access:",
    "оператор": "operator",
    "гість": "guest",
    "невідома": "unknown",
    "увійти як оператор": "sign in as operator",
    "Запис у базу робить оператор.": "Only an operator can write to the database.",
    "Можна записувати в базу.": "You can write to the database.",

    /* ── перемикач теми ────────────────────────────────────────────────── */
    "Тема: як у системі": "Theme: follow system",
    "Тема: світла": "Theme: light",
    "Тема: темна": "Theme: dark",
    "перемкнути на світлу": "switch to light",
    "перемкнути на темну": "switch to dark",
    "повернути як у системі": "back to system",

    /* ── сторінка завантаження ─────────────────────────────────────────── */
    "Завантаження документа": "Document upload",
    "Покажемо, що витягли з документа. У базу — лише після вашого підтвердження.":
      "We will show what was extracted from the document. Nothing is written to the database without your confirmation.",
    "Перетягніть файл документа сюди": "Drag a document file here",
    "docx, pdf або фото документа": "docx, pdf or a photo of the document",
    "Вибрати файл": "Choose a file",
    "Обробка": "Processing",
    "Розпізнавання фото триває до кількох хвилин.":
      "Recognising a photo takes up to a few minutes.",
    "Витягнуто з документа": "Extracted from the document",
    "Технічні деталі обробки": "Technical processing details",
    "Запис у базу": "Writing to the database",
    "Записати в базу": "Write to the database",
    "або натисніть, щоб обрати (фото, скан, docx, pdf)":
      "or click to choose (photo, scan, docx, pdf)",
    "Зняти камерою": "Take a photo",
    "Файл": "File",
    "Що витягли": "What we extracted",
    "У базу": "To the database",
    "Про кого документ": "Who the document is about",
    "Поле": "Field",
    "Значення": "Value",
    "Стан": "Status",
    "Факти": "Facts",
    "Прогалини": "Gaps",
    "Джерело": "Source",
    "Шаблон:": "Template:",
    "Розпізнавання:": "Recognition:",
    "Причина:": "Reason:",
    "Причина перевірки:": "Review reason:",
    "Технічні деталі": "Technical details",
    "Що саме записано": "What exactly was written",
    "№ запису": "Record no.",
    "Підтверджено": "Confirmed",
    "підтверджено": "confirmed",
    "Потребує перевірки": "Needs review",
    "потребує перевірки": "needs review",
    "Не розпізнано": "Not recognised",
    "не знайдено": "not found",
    "не знайдено — обов'язкове поле": "not found — required field",
    "порожньо": "empty",
    "порожньо в самому документі": "empty in the document itself",
    "з тексту документа": "from the document text",
    "невідомо": "unknown",
    "невідома причина": "reason unknown",
    "символів": "characters",
    "блоків,": "blocks,",
    "виконується": "in progress",
    "хв": "min",
    "без запису в базу": "without writing to the database",
    "запис не вдався": "the write failed",
    "Збій обробки:": "Processing failure:",
    "Сервер недоступний:": "Server unavailable:",
    "завантажте файл ще раз.": "upload the file again.",
    "Документ у базі під номером": "The document is in the database under no.",
    "Цей документ уже в базі під номером":
      "This document is already in the database under no.",
    "— копію не створено.": "— no copy was created.",
    "Уже оброблявся": "Already processed",
    "Нижче — наявний запис; повторний запис у базу не створить копію.":
      "Below is the existing record; re-uploading will not create a copy.",
    "Повторна обробка: попередня версія фактів позначена застарілою.":
      "Reprocessed: the previous version of the facts is marked outdated.",
    "Документ позначено для ручного розбору.":
      "The document is flagged for manual handling.",
    "У базу піде лише сам документ і позначка для ручного розбору.":
      "Only the document itself and the manual-handling flag will be stored.",
    "Фактів не витягнуто — у базу піде лише сам документ.":
      "No facts were extracted — only the document itself will be stored.",
    "Запишеться як непідтверджене й стане в чергу перевірки людиною.":
      "It will be stored as unconfirmed and queued for human review.",
    "Факти записані як непідтверджені й документ у черзі перевірки — у підсумки вони не входять, доки людина не підтвердить.":
      "The facts are stored as unconfirmed and the document is queued for review — they are excluded from totals until a human confirms them.",
    "Неповне ім'я — запис особи в базу не пройде.":
      "Incomplete name — the person cannot be written to the database.",
    "Суперечливі дати:": "Conflicting dates:",
    "Обробку перервано: апку перезапустили, і ця задача втрачена":
      "Processing was interrupted: the app restarted and this task was lost",
    "(задачі живуть у пам'яті процесу). Документ у базу НЕ записано —":
      "(tasks live in the process memory). The document was NOT written to the database —",
    ", записано фактів:": ", facts written:",
    ", без фактів.": ", no facts.",
    ". Це не «нуль у базі»: невідомо.":
      ". This is not «zero in the database»: it is unknown.",

    /* ── власні речення чата (статичні) ──────────────────────────────── */
    "На це система відповісти не може, бо питання не лягає на жодну з її доріг: підрахунок відсутностей за документами, довідник, цитати чи діагностика.":
      "The system cannot answer this: the question does not fit any of its roads — counting absences from documents, the reference list, quotes, or diagnostics.",
    "Такого підрозділу в штатці немає, тому порахувати по ньому не можу — нуль тут означав би «нікого немає», а насправді немає самого підрозділу. У базі є: 1-ша, 2-га і 3-тя механізовані роти, взвод забезпечення, управління батальйону.":
      "There is no such subdivision in the roster, so I cannot count for it — a zero here would mean «nobody is absent», while in fact the subdivision itself does not exist. The database has: 1st, 2nd and 3rd mechanized companies, the support platoon, and the battalion HQ.",
    "Це доповнення до складу ЗА ШТАТКОЮ: чи людина сьогодні фізично в частині, база не знає.":
      "This is the complement of the roster strength: the database does not know whether a person is physically in the unit today.",
    "щорічна основна відпустка": "annual basic leave",
    "відпустка за сімейними обставинами": "leave for family reasons",
    "відпустка для лікування": "leave for medical treatment",

    "Такої дати не існує, тому порахувати на неї не можу. Дату не виправляю на найближчу дійсну й не вгадую, яку саме ви мали на увазі — вкажіть її, будь ласка, ще раз.":
      "This date does not exist, so I cannot count for it. I do not correct the date to the nearest valid one and do not guess which one you meant — please state it again.",
    "Це склад ЗА ШТАТКОЮ: хто сьогодні в частині фізично, база не знає.":
      "This is the roster strength: the database does not know who is physically present in the unit today.",
    "Це дослівна цитата з чинного документа; перевірено, що вона є в тексті.":
      "This is a verbatim quote from a valid document; it was verified to exist in the text. The quote itself is left in Ukrainian on purpose — a translated rule would be our paraphrase, not the rule.",
    "Знайшла схожі за темою місця, але жодне не відповідає на питання прямо. Показувати їх як відповідь не буду.":
      "I found passages on a similar topic, but none answers the question directly. I will not present them as an answer.",
    "Що саме відкинуто:": "What exactly was rejected:",
    "Враховані лише підтверджені факти; чернетки в підрахунок не входять.":
      "Only confirmed facts are counted; drafts are excluded from the total.",
    "Переліку не даю навмисно: кількасот прізвищ — це не відповідь. Можу перелічити тих, хто в стані.":
      "I deliberately do not give the list: several hundred surnames are not an answer. I can list those in the state.",

    /* ── сторінка статистики ───────────────────────────────────────────── */
    "Стан бази, з якої чат бере відповіді, і виміряна якість обробки.":
      "The state of the database the chat answers from, and the measured processing quality.",
    "База зараз": "The database right now",
    "Робота чата": "How the chat performs",
    "Виміряна якість обробки": "Measured processing quality",

    // Розділ метрик Андрія (28.08). Рядки додані ТИМ САМИМ рухом, що й сама
    // розмітка: тест повноти інакше падає, і це правильно -- він і стоїть,
    // щоб нові рядки не з'являлись без перекладу.
    //
    // Ключі -- БЕЗ пробілів на краях: витяжка тесту рядки обрізає, і перша
    // моя версія з пробілами не збіглася жодним ключем.
    "Замір бази й нормативного пошуку":
      "Database and normative search: measured",
    // Стан «перетин» при завантаженні (робота Андрія, 29.08). Рядки
    // додані ТИМ САМИМ рухом, що й розмітка: сторінка двомовна, і
    // новий стан без перекладу став би українським островом.
    "Старий документ втратив чинність":
      "The old document is no longer valid",
    "Скасовую старий документ і додаю новий…":
      "Superseding the old document and adding the new one...",
    "скасування не вдалось":
      "superseding failed",
    "Скасувати старий:":
      "Supersede the old one:",
    "Переглянути поля":
      "Review the fields",
    "Перетин періодів у тієї самої особи — новий документ у базу НЕ додано.":
      "Overlapping periods for the same person - the new document was NOT added to the database.",
    "Накладається на:": "Overlaps with:",
    "чим зміряно": "how it was measured",
    "· замір": "· measured",
    "метрик охоплення не показано окремо:":
      "coverage metrics are not shown separately:",
    "ті самі величини є вище, у розділі «База зараз», і там":
      "the same quantities appear above, in «The database right now», and there",
    "вони живі": "they are live",
    // Згорнутий дубль перетинів (29.08, пункт 13 переліку Ані). Рядки
    // розбиті так само, як у рендері: ключем має бути рівно той шматок, що
    // потрапляє на екран, інакше перемикач мови його не бачить.
    "метрику про перетини не показано окремо:":
      "one overlap metric is not shown separately:",
    "те саме число є нижче, у розділі «Конфлікти», і воно живе":
      "the same number appears below, in «Overlaps», and it is live",
    "метрик відкинуто: без поля «чим зміряно»":
      "metrics were dropped: without the «how it was measured» field",
    "цифра не показується": "a number is not shown",
    // Плитка «Конфлікти» (28.08). Рядки додані ТИМ САМИМ рухом, що й розмітка.
    //
    // Кожен ключ -- ЦІЛА ФРАЗА. Перша версія рендера складала речення з
    // уламків («ще», «) і №», «двічі»), і цей тест упав по праву: уламок не є
    // одиницею перекладу, тобто англійська сторінка вийшла б
    // напівукраїнською. Рендер переписаний, а не словник розширений уламками.
    "Конфлікти: перетини відсутностей":
      "Conflicts: overlapping absences",
    "пар, що перетинаються": "overlapping pairs",
    "з них із записаною підставою заміни":
      "of these, with a recorded ground for replacement",
    "які саме": "which ones exactly",
    "Перетинів немає.": "There are no overlaps.",
    "Перетини не зміряні: база недоступна.":
      "Overlaps not measured: the database is unavailable.",
    "пар без подробиць:": "pairs shown without details:",
    "підстава заміни записана": "a ground for replacement is recorded",
    "різні люди, відсутні одночасно, перетином не є":
      "different people absent at the same time are not an overlap",
    "це перетини, для яких у базі записано, що один документ виданий замість іншого; решта — не підтверджена заміна":
      "these are the overlaps where the database records that one document was issued to replace another; the rest are not a confirmed replacement",
    "накладання періодів двох відсутностей однієї особи; запит по базі при відкритті сторінки":
      "overlapping periods of two absences of one person; queried from the database when the page opens",
    // Комбінації видів відсутності: їх рівно три, і вони приходять готовою
    // фразою з Python (`stats._kinds_phrase`) саме для того, щоб перекладались
    // цілком, а не через ключ «і».
    "відпустка двічі": "leave twice",
    "відрядження двічі": "deployment twice",
    "відпустка і відрядження": "leave and deployment",
    "відрядження і відпустка": "deployment and leave",

    "База недоступна — цифр показати не можу.":
      "The database is unavailable — I cannot show any numbers.",
    "Сторінка не змогла отримати цифри:": "The page could not fetch the numbers:",
    "Цифри якості не читаються:": "The quality numbers cannot be read:",
    "Цифри зняті": "Numbers taken",
    "· сталі числа — із заміру приладів":
      "· fixed numbers — from the instrument run",
    "Цифри, проти яких щодня йде перевірка «не ламає».":
      "The numbers the daily «nothing broke» check runs against.",
    "фактів витягнуто впевнено": "facts extracted confidently",
    "входять у підрахунки": "included in the totals",
    "чернеток": "drafts",
    "непевне поле, не в підрахунку": "uncertain field, excluded from totals",
    "осіб не зійшлися зі штаткою": "people not matched to the roster",
    "один навмисно — для показу черги":
      "one deliberately — to demonstrate the review queue",
    "документів усього": "documents in total",
    "з них кадрові": "of them personnel documents",
    "осіб у реєстрі": "people in the registry",
    "Типи документів:": "Document types:",
    "документів чекає людини": "documents awaiting a human",
    "· усього завдань у черзі": "· tasks in the queue in total",
    "Черга перевірки:": "Review queue:",
    "медіана часу відповіді": "median answer time",
    "половина відповідей швидше, половина довше":
      "half of the answers are faster, half slower",
    "найгірші 10%": "worst 10%",
    "9 з 10 відповідей швидші за це": "9 out of 10 answers are faster than this",
    "питань з запуску сервісу": "questions since the service started",
    "Ще не міряли": "Not measured yet",
    "ще не міряємо": "not measured yet",
    "Лічильник заповнюється з першого питання в чаті":
      "The counter starts filling from the first question in the chat",
    "і рахує з моменту запуску сервісу.": "and counts from the service start.",
    "перевірок чат проходить": "checks the chat passes",
    "цифри у відповідях збігаються з обробленими документами":
      "the numbers in the answers match the processed documents",
    "помилок швидкого розпізнавання": "fast-recognition errors",
    "швидке розпізнавання — відповідь за схожістю з уже відомими":
      "fast recognition — an answer by similarity to already known",
    "питаннями, без моделі": "questions, without the model",
    "підходящих цитат із норм-актів": "suitable quotes from normative acts",
    "нормативних класифіковано": "normative documents classified",
    "тип документа визначено правильно": "the document type was identified correctly",
    "тестів пайплайна проходить": "pipeline tests pass",
    "один тест відкладено навмисно": "one test is deferred deliberately",
    "· не зіставлено з довідником": "· not matched to the reference list",
    "· домен:": "· domain:",
    "модель": "model",
  };

  /* ФРАГМЕНТИ -- для сторінки ЧАТА.
   *
   * Чому для чата потрібен інший режим. Рядок відповіді чата -- це переважно
   * ДАНІ плюс наші короткі зв'язки:
   *
   *   «- Ґоляш Богодар Святославович — відпустка, 2026-09-21 — 2026-10-10
   *     (документ №1077 (запис №64 у базі))»
   *
   * Перекласти такий рядок цілим неможливо: ПІБ, дати й номери щоразу інші.
   * Тому тут підміняються ФРАГМЕНТИ всередині рядка, а дані лишаються як є.
   *
   * Порядок важить: довші фрази стоять ПЕРЕД коротшими, інакше «відпустка»
   * з'їла б «відпускний квиток». Список замкнений навмисно -- це наша
   * службова лексика, а не мова документів.
   */
  var PHRASES = [
    /* Фрагмент може бути й РЕГУЛЯРКОЮ -- для форм, які не є фіксованим
       рядком: порядкові числівники підрозділів («3-тя механізована рота»)
       і числа всередині фрази. Без цього в англійському тексті лишались би
       українські закінчення. */
    [/1-ша\s+механізована рота/g, "1st mechanized company"],
    [/2-га\s+механізована рота/g, "2nd mechanized company"],
    [/3-тя\s+механізована рота/g, "3rd mechanized company"],
    [/(\d+)-(?:ша|га|тя|тє|те)\s+механізована рота/g, "$1 mechanized company"],
    /* Найчастіший рядок усього демо -- знаменник у дужках. Фрагментами він
       склеювався в «of the registry total 303 people», тобто зрозуміло, але
       кострубато; тут ціла форма. */
    [/\(усього в реєстрі (\d+) ос\S+\)/g, "(of $1 in the registry)"],
    ["(відпустка або відрядження)", "(leave or deployment)"],
    /* «на <дата>» -- прив'язано до ISO-дати навмисно: слово «на» саме по собі
       перекладати не можна, воно значить різне в різних місцях. */
    [/ на (\d{4}-\d{2}-\d{2})/g, " on $1"],
    [/ з (\d{4}-\d{2}-\d{2}) по (\d{4}-\d{2}-\d{2})/g, " from $1 to $2"],
    [/ з (\d{4}-\d{2}-\d{2})/g, " from $1"],
    [/ до (\d{4}-\d{2}-\d{2})/g, " until $1"],
    ["про відсутність немає", "there are none"],
    ["документів про це", "documents about it"],
    ["у когось їх кілька", "some people have several"],
    ["наприклад, квиток і виданий замість нього новий",
     "for example a ticket and the new one issued to replace it"],
    ["Це розбіжність між двома запитами", "This is a disagreement between two queries"],
    /* Блок «джерело» -- те, чим відповідь доводить себе. На демо його читають
       уважніше за саму цифру, тому підписи тут перекладаються. Назва шаблона
       лишається українською: поруч із нею стоїть її код (`count_by_state_on_date`),
       який і є однозначним іменем, а перекладати 29 назв означало б завести
       другий список, що розійдеться з каталогом. */
    ["технічний запит шаблону:", "the template SQL:"],
    ["шаблон каталогу:", "catalogue template:"],
    ["каталог шаблонів", "template catalogue"],
    ["обрано моделлю-класифікатором", "chosen by the classifier model"],
    ["вільний SQL під рейками", "free SQL under rails"],
    ["нормативний ланцюг", "normative chain"],
    ["зріз бази:", "database snapshot:"],
    ["джерело:", "source:"],
    ["джерело", "source"],
    ["дорога:", "road:"],
    ["правила", "rules"],
    ["вектори", "vectors"],
    ["відмова", "refusal"],
    ["уточнення", "clarification"],
    ["потрібне уточнення", "clarification needed"],
    ["збій доступу до бази", "database access failure"],
    ["полів у відповіді:", "fields used in the answer:"],
    [/ від (\d{4}-\d{2}-\d{2})/g, " dated $1"],
    ["Відпускний квиток", "Leave ticket"],
    ["Посвідчення про відрядження", "Deployment certificate"],
    ["Чернетки (не в підрахунку):", "Drafts (excluded from the total):"],
    ["Враховані лише підтверджені факти; чернетки в підрахунок не входять.",
     "Only confirmed facts are counted; drafts are excluded."],
    ["за підтвердженими фактами", "by confirmed facts"],
    ["усього в реєстрі", "in the registry"],
    ["за штаткою", "per the roster"],
    ["полів у відповіді:", "fields used in the answer:"],
    ["документів-джерел:", "source documents:"],
    ["і ще", "and"],
    ["документів", "documents"],
    ["документ №", "document no. "],
    ["запис №", "record no. "],
    ["у базі", "in the database"],
    ["Доповідаю:", "Report:"],
    ["Документи (", "Documents ("],
    ["Зріз:", "As of:"],
    ["на дату", "on date"],
    ["звернення:", "request:"],
    ["звернення", "request"],
    ["Поіменно:", "By name:"],
    ["непідтверджено", "unconfirmed"],
    ["підтверджений", "confirmed"],
    ["у відпустці", "on leave"],
    ["у відрядженні", "on deployment"],
    ["відпустка", "leave"],
    ["відрядження", "deployment"],
    ["поза частиною", "away from the unit"],
    ["особи", "people"],
    ["осіб", "people"],
    ["особа", "person"],
    ["механізована рота", "mechanized company"],
    ["Взвод забезпечення", "Support platoon"],
    ["Управління батальйону", "Battalion HQ"],
    ["нова особа в реєстрі", "new person in the registry"],
    ["невідомий тип бланка", "unknown form type"],
    ["непідтверджений факт", "unconfirmed fact"],
    ["вибіркова перевірка якості", "quality sampling"],
    [/ за (\d{4}) рік/g, " for $1"],
    [/about one: дата /g, "about one: date "],
    [/about one: початок періоду /g, "about one: period start "],
    [/about one: документ /g, "about one: document "],
    [/about one: підрозділ /g, "about one: subdivision "],
    [/about one: особа /g, "about one: person "],
    ["прибути до", "report to"],
    ["механізовані роти", "mechanized companies"],
    ["взвод забезпечення", "support platoon"],
    ["управління батальйону", "battalion HQ"],
    [/(^|[^а-яіїєґ])днів(?=[^а-яіїєґ]|$)/g, "$1days"],
    ["У базі є:", "The database has:"],
    ["щорічна основна відпустка", "annual basic leave"],
    ["відпустка за сімейними обставинами", "leave for family reasons"],
    ["відпустки", "leave"],
    ["нормативні акти", "normative acts"],
    ["штатна книжка", "roster book"],
    ["без домену", "no domain"],
    ["Найближча:", "Next one:"],
    ["За документами у частині", "By the unit documents"],
    ["У частині", "In the unit"],
    ["за документами зараз відсутності немає",
     "by the documents there is no absence right now"],
    ["документів про відсутність немає", "there are no absence documents"],
  ];

  /* ПОРЯДОК ФРАЗ ЗАДАЄТЬСЯ КОДОМ, А НЕ РУКАМИ.
   *
   * У комментарі вище я написала «довші стоять перед коротшими» -- і сама ж
   * цього не витримала. Наслідок зловив замір на справжніх відповідях:
   *
   *   «- непідтверджений факт: 20»   -> «- неconfirmed факт: 20»
   *   «нова особа в реєстрі»          -> «нова person в реєстрі»
   *
   * Тобто коротка фраза з'їла частину довшої. Правило, яке тримається на
   * уважності при кожній правці списку, не тримається взагалі -- тому список
   * сортується сам: спершу регулярки (вони прив'язані до форми), далі рядки
   * від довших до коротших.
   */
  PHRASES.sort(function (a, b) {
    var ra = a[0] instanceof RegExp, rb = b[0] instanceof RegExp;
    if (ra !== rb) { return ra ? -1 : 1; }
    if (ra) { return 0; }
    return b[0].length - a[0].length;
  });

  /* Підписи, склеєні з числами. Точний збіг тут неможливий, тому шаблон плюс
     заміна. Список короткий навмисно: усе, що можна перекласти словником,
     перекладається словником -- регулярка потрібна лише там, де число стоїть
     СЕРЕДИНІ рядка. */
  var PATTERNS = [
    [/^(\d+) з (\d+)$/, "$1 of $2"],
    /* Рядки відповіді чата, які краще перекласти ЦІЛИМИ: у них службові
       слова стоять між даними («Зріз: на <дата>»), і склейка з фрагментів
       лишила б українські прийменники всередині англійського рядка. */
    [/^Зріз: на (.+?) \(за підтвердженими фактами\)\.$/,
     "As of: $1 (by confirmed facts)."],
    [/^Зріз: період (.+?) — (.+?) \(за підтвердженими фактами\)\.$/,
     "As of: period $1 — $2 (by confirmed facts)."],
    [/^Зріз: стан бази на (.+?)\.$/, "As of: database state on $1."],
    [/^Зріз: (.+?) — (.+?) \(усього в реєстрі (\d+) ос\S+\)\.$/,
     "As of: $1 — $2 (of $3 in the registry)."],
    [/^Покриття даних у базі: (.+?) — (.+?)\.$/,
     "Data coverage in the database: $1 — $2."],
    [/^Чернетки \(не в підрахунку\): (\d+)\.$/,
     "Drafts (excluded from the total): $1."],
    [/^Документи \((\d+)\):$/, "Documents ($1):"],
    /* Наші власні речення у відповідях. Вони параметризовані числами й
       датами, тому це шаблони, а не словник. Кожне -- текст, який писала я,
       тобто перекладати його можна й треба (на відміну від цитат норм). */
    [/^0 — на (.+?) чинних документів про стан «(.+?)» немає\.$/,
     "0 — on $1 there are no valid documents for the state «$2»."],
    [/^0 — на (.+?) чинних документів про відсутність немає\.$/,
     "0 — on $1 there are no valid absence documents."],
    [/^⚠️ у питанні кінець періоду \((.+?)\) раніше за початок \((.+?)\)\. Межі я не міняю — порахувала як написано, тому нуль тут означає «межі перевернуті», а не «нікого не було»\.$/,
     "⚠️ in your question the period ends ($1) before it starts ($2). I do not "
     + "swap the bounds — I counted exactly as written, so a zero here means "
     + "«the bounds are reversed», not «nobody was absent»."],
    [/^⚠️ узято з попереднього питання: (.+?)\. Якщо мали на увазі інше — напишіть це в питанні\.$/,
     "⚠️ taken from your previous question: $1. If you meant something else, "
     + "please say so in the question."],
    /* По одному шаблону на назву параметра. Шаблон повертається ОДРАЗУ, тому
       фрагменти після нього не працюють -- і назва лишалась українською
       всередині англійського речення («about one: дата 2026-09-01»). Знайдено
       заміром на справжніх відповідях. */
    [/^⚠️ у питанні дві речі для порівняння, а я відповідаю про одну: дата (.+?)\. Порівнювати два значення я поки не вмію — спитайте окремо про друге\.$/,
     "⚠️ your question compares two things, but I answer about one: date $1. "
     + "I cannot compare two values yet — please ask about the second one "
     + "separately."],
    [/^⚠️ у питанні дві речі для порівняння, а я відповідаю про одну: (?:початок періоду|документ|підрозділ|особа) (.+?)\. Порівнювати два значення я поки не вмію — спитайте окремо про друге\.$/,
     "⚠️ your question compares two things, but I answer about one: $1. "
     + "I cannot compare two values yet — please ask about the second one "
     + "separately."],
    [/^⚠️ у питанні дві речі для порівняння, а я відповідаю про одну: (.+?)\. Порівнювати два значення я поки не вмію — спитайте окремо про друге\.$/,
     "⚠️ your question compares two things, but I answer about one: $1. "
     + "I cannot compare two values yet — please ask about the second one "
     + "separately."],
    [/^⚠️ у переліку (.+?), а число вище — (\d+)\. Це розбіжність між двома запитами, і я її не приховую: перевірте документи-джерела нижче\.$/,
     "⚠️ the list shows $1, while the number above is $2. This is a "
     + "disagreement between two queries and I am not hiding it: check the "
     + "source documents below."],
    [/^Поіменно не показую: (.+?) — це задовгий перелік\. Спитайте «покажи поіменно», якщо потрібні прізвища\.$/,
     "I am not listing names: $1 — the list is too long. Ask «покажи "
     + "поіменно» if you need the surnames."],
    [/^За цю дату в базі даних НЕМАЄ: документи покривають (.+?) — (.+?)\. Нуль тут означає «немає даних», а не «нікого не було»\.$/,
     "There is NO data for this date: the documents cover $1 — $2. A zero "
     + "here means «no data», not «nobody was absent»."],
    [/^Не знайшла в нормативних документах нічого по цьому питанню\. Це не «немає такої норми» — це означає, що в нашому корпусі \((.+?)\) відповіді немає\.$/,
     "I found nothing on this question in the normative documents. This is "
     + "not «no such rule» — it means our corpus ($1) has no answer."],
    [/^Документів у базі: (\d+)\.$/, "Documents in the database: $1."],
    [/^Чому це відповідь: (.+)$/, "Why this is the answer: $1"],
    /* ПРЕФІКСНІ шаблони блоку «джерело». Тут важливий не лише переклад, а й
       те, що ХВІСТ рядка лишається недоторканим: у ньому назва шаблона
       каталогу, адреса пункту, назва документа. Замір показав, що без цього
       фрагменти робили назви НАПІВперекладеними («Скільки documents in the
       database (за доменами)»), а це гірше за українську назву цілком. */
    [/^шаблон каталогу: (.+)$/, "catalogue template: $1"],
    /* Назви ДОРІГ -- наші, тому перекладаються; код шаблона в дужках лишається
       як є, він і є однозначним іменем. Стоять ПЕРЕД загальним «дорога: …»,
       інакше той забрав би рядок собі й лишив назву українською. */
    [/^дорога: каталог шаблонів, обрано моделлю-класифікатором \((.+)\)$/,
     "road: template catalogue, chosen by the classifier model ($1)"],
    [/^дорога: каталог шаблонів \((.+)\)$/, "road: template catalogue ($1)"],
    [/^дорога: підрахунок \(потрібне уточнення\)$/,
     "road: counting (clarification needed)"],
    [/^дорога: підрахунок$/, "road: counting"],
    [/^дорога: відмова(.*)$/, "road: refusal$1"],
    [/^дорога: довідник(.*)$/, "road: reference$1"],
    [/^дорога: цитата$/, "road: quote"],
    [/^дорога: збій доступу до бази$/, "road: database access failure"],
    /* Назва документа у відповіді -- НЕ перекладається (юридична назва), але
       рядок навколо неї перекладається. Без цього шаблону фрагменти псували
       назву: «Порядок оформлення leave у військовій частині А0000». */
    [/^Доповідаю: \*\*(.+?)\*\*, (.+)$/, "Report: **$1**, $2"],
    [/^нормативний ланцюг: одиниці → реранкер → ворота → перевірка цитати$/,
     "normative chain: units → reranker → gate → quote check"],
    /* Відмови приходять ЗАГОРНУТИМИ в «Доповідаю: », тому словник по цілому
       рядку їх не ловив -- знайдено заміром. */
    [/^Доповідаю: на це система відповісти не може, бо питання не лягає на жодну з її доріг: підрахунок відсутностей за документами, довідник, цитати чи діагностика\.$/,
     "Report: the system cannot answer this — the question does not fit any of "
     + "its roads: counting absences from documents, the reference list, "
     + "quotes, or diagnostics."],
    [/^Доповідаю: такого підрозділу в штатці немає, тому порахувати по ньому не можу — нуль тут означав би «нікого немає», а насправді немає самого підрозділу\. (.+)$/,
     "Report: there is no such subdivision in the roster, so I cannot count "
     + "for it — a zero here would mean «nobody is absent», while in fact the "
     + "subdivision itself does not exist. $1"],
    [/^У базі є: (.+)$/, "The database has: $1"],
    [/^Доповідаю: такої дати не існує, тому порахувати на неї не можу\. Дату не виправляю на найближчу дійсну й не вгадую, яку саме ви мали на увазі — вкажіть її, будь ласка, ще раз\.$/,
     "Report: this date does not exist, so I cannot count for it. I do not "
     + "correct the date to the nearest valid one and do not guess which one "
     + "you meant — please state it again."],
    [/^Непідтверджених записів \(у відпустці\) — у підрахунок не входять: (\d+)\.$/,
     "Unconfirmed entries (on leave) — excluded from the total: $1."],
    [/^Непідтверджених записів \(у відрядженні\) — у підрахунок не входять: (\d+)\.$/,
     "Unconfirmed entries (on deployment) — excluded from the total: $1."],
    [/^джерело: №(.+?) \(запис №(\d+) у базі \((.+?)\)\)$/,
     "source: no. $1 (record no. $2 in the database ($3))"],
    [/^документ: запис №(\d+) у базі, адреса (.+)$/,
     "document: record no. $1 in the database, address $2"],
    [/^дорога: (.+)$/, "road: $1"],
    [/^джерело: (.+)$/, "source: $1"],
    [/^документ: (.+)$/, "document: $1"],
    [/^технічний запит шаблону:$/, "the template SQL:"],
    [/^заблоковано: SQL немає, відповідь — дослівний refusal шаблону$/,
     "blocked: there is no SQL, the answer is the template refusal verbatim"],
    [/^збіг лем питання й цитати: (.+)$/,
     "lemma overlap between question and quote: $1"],
    [/^нормативний ланцюг: (.+)$/, "normative chain: $1"],
    [/^зріз бази: (.+)$/, "database snapshot: $1"],
    [/^звернення:? ([0-9a-f]{6})$/, "request $1"],
    [/^документів-джерел: (\d+)$/, "source documents: $1"],

    /* Решта моїх власних речень у відповідях. */
    [/^Доповідаю: у реєстрі (\d+) ос\S+\.$/,
     "Report: $1 people in the registry."],
    [/^Доповідаю: у черзі перевірки (\d+) запис\S*:$/,
     "Report: $1 entries in the review queue:"],
    [/^Доповідаю: розклад по підрозділах — відсутні \(відпустка або відрядження\):$/,
     "Report: breakdown by subdivision — away from the unit (leave or deployment):"],
    [/^Доповідаю: (\d+) ос\S+ відсутні \(відпустка або відрядження\) \(усього в реєстрі (\d+) ос\S+\)\.$/,
     "Report: $1 people away from the unit (leave or deployment) (of $2 in the registry)."],
    [/^Доповідаю: (\d+) ос\S+ НЕ у відпустці: (\d+) у реєстрі мінус (\d+) у відпустці\.$/,
     "Report: $1 people NOT on leave: $2 in the registry minus $3 on leave."],
    [/^Доповідаю: (\d+) ос\S+ НЕ у відрядженні: (\d+) у реєстрі мінус (\d+) у відрядженні\.$/,
     "Report: $1 people NOT on deployment: $2 in the registry minus $3 on deployment."],
    [/^Доповідаю: у реєстрі частини людини за «(.+?)» немає\.$/,
     "Report: the unit registry has no person matching «$1»."],
    [/^Доповідаю: документа №(.+?) у базі стенду немає\. Номер не виправляємо і схожих не підставляємо\.$/,
     "Report: document no. $1 is not in the demo database. We do not correct "
     + "the number and do not substitute similar ones."],
    [/^Склад підрозділу за штаткою: (\d+) ос\S+\.$/,
     "Subdivision strength per the roster: $1 people."],
    [/^Непідтверджених записів \((.+?)\) — у підрахунок не входять: (\d+)\.$/,
     "Unconfirmed entries ($1) — excluded from the total: $2."],
    [/^Непідтверджених записів за цей період: (\d+)\.$/,
     "Unconfirmed entries for this period: $1."],
    [/^Окремо непідтверджені \((?:потребують|чекають) перевірки людиною, у підсумок не входять\): (\d+)$/,
     "Unconfirmed, listed separately (awaiting human review, excluded from the "
     + "total): $1"],
    [/^З них (\d+) мають відповідник у штатці; решта (\d+) — особи з документів, які чекають підтвердження людиною\.$/,
     "Of these, $1 match the roster; the remaining $2 are people from "
     + "documents awaiting human confirmation."],
    [/^⚠️ у номері документа нецифровий символ \(можливий шум розпізнавання\): №(.+?) — місце, де система не впевнена$/,
     "⚠️ the document number contains a non-digit character (possible OCR "
     + "noise): no. $1 — a place where the system is not confident"],
    [/^(\d+) з (\d+) відсутні$/, "$1 of $2 absent"],
    [/^Документів у базі: (\d+)\.$/, "Documents in the database: $1."],
    [/^Зріз: стан бази на (.+)\.$/, "As of: database state on $1."],
  ];

  /* Оригінали текстових вузлів, які ми підмінили. Потрібні, щоб повернути
     українську ТОЧНО, а не зворотним словником: два різні українські рядки
     могли б перекластись однаково, і зворотний шлях був би неоднозначний.
     Відірвані від документа вузли відсіюються при поверненні (`isConnected`). */
  var originals = [];

  /* Вузли, яких перекладач НЕ ЧІПАЄ. Це не оптимізація, а правило продукту й
     здоровий глузд:

       - ДОСЛІВНА ЦИТАТА (у лапках «...»): перекласти норму означає підмінити
         її нашим переказом. Гірше за відсутність перекладу: людина вважатиме,
         що читає документ;
       - НАЗВА нормативного акта («Про оборону України»): це юридична назва
         закону, і вигадувати їй переклад ми не маємо права;
       - SQL у блоці «джерело»: технічний текст, який мусить лишитись тим
         самим, щоб відповідь і запит можна було звірити. */
  /* ЩО НЕ ПЕРЕКЛАДАЄТЬСЯ -- лишилось рівно дві речі.
   *
   * Спершу тут стояв ще й захист дослівних цитат і назв законів: перекладена
   * норма -- це вже наш переказ, і перевірити її по документу неможливо.
   * Аня 28.08 це рішення СКАСУВАЛА для демо, і аргументи назвала прямо: дані
   * синтетичні, сторінка вже відкрита, а на демо будуть іноземці -- тобто
   * незрозуміла сторінка шкодить більше, ніж неточний переклад норми.
   * Записано тут, а не заховано: для пілота з реальними документами це
   * рішення треба переглянути.
   *
   * Лишились ті два випадки, де переклад ЗЛАМАВ БИ роботу:
   *   - SQL у блоці «джерело» мусить збігатися з виконаним запитом, інакше
   *     звірити відповідь із запитом стає неможливо;
   *   - номер звернення -- ключ, за яким хід знаходиться в журналі.
   */
  function isUntouchable(s) {
    var t = s.trim();
    if (!t) { return true; }
    if (/SELECT|FROM|JOIN|WHERE|%\(|::/.test(t)) {
      return true;                       // SQL
    }
    if (/^[0-9a-f]{6}$/.test(t)) {
      return true;                       // номер звернення
    }
    return false;
  }

  function translateText(s) {
    var key = s.trim();
    if (!key || isUntouchable(s)) { return null; }
    if (Object.prototype.hasOwnProperty.call(DICT, key)) {
      return s.replace(key, DICT[key]);
    }
    for (var i = 0; i < PATTERNS.length; i += 1) {
      if (PATTERNS[i][0].test(key)) {
        return s.replace(key, key.replace(PATTERNS[i][0], PATTERNS[i][1]));
      }
    }
    /* Фрагменти -- останніми: спершу пробуємо перекласти рядок цілим, бо
       цілісний переклад завжди кращий за склейку з частин. */
    var out = s, hit = false;
    for (var k = 0; k < PHRASES.length; k += 1) {
      var from = PHRASES[k][0];
      if (from instanceof RegExp) {
        var replaced = out.replace(from, PHRASES[k][1]);
        if (replaced !== out) { out = replaced; hit = true; }
      } else if (out.indexOf(from) >= 0) {
        out = out.split(from).join(PHRASES[k][1]);
        hit = true;
      }
    }
    return hit ? out : null;
  }

  /* Атрибути, які людина теж читає: підказка при наведенні й підпис для
     читача екрана. Без них переклад був би половинчастим саме там, де він
     найпотрібніший -- у доступності. */
  var ATTRS = ["title", "aria-label", "placeholder"];

  function walk(root) {
    if (!root) { return; }
    var it = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var node;
    while ((node = it.nextNode())) {
      /* Скрипти й стилі -- не текст для людини. */
      var tag = node.parentNode && node.parentNode.nodeName;
      if (tag === "SCRIPT" || tag === "STYLE") { continue; }
      var next = translateText(node.nodeValue);
      if (next !== null && next !== node.nodeValue) {
        originals.push([node, node.nodeValue]);
        node.nodeValue = next;
      }
    }
    var all = root.querySelectorAll ? root.querySelectorAll("*") : [];
    for (var i = 0; i < all.length; i += 1) {
      for (var a = 0; a < ATTRS.length; a += 1) {
        var v = all[i].getAttribute(ATTRS[a]);
        if (!v) { continue; }
        var t = translateText(v);
        if (t !== null && t !== v) {
          originals.push([all[i], v, ATTRS[a]]);
          all[i].setAttribute(ATTRS[a], t);
        }
      }
    }
  }

  function restore() {
    for (var i = originals.length - 1; i >= 0; i -= 1) {
      var rec = originals[i];
      if (rec.length === 3) {
        if (rec[0].isConnected) { rec[0].setAttribute(rec[2], rec[1]); }
      } else if (rec[0].isConnected) {
        rec[0].nodeValue = rec[1];
      }
    }
    originals = [];
  }

  function read() {
    try {
      var v = window.localStorage.getItem(KEY);
      return ORDER.indexOf(v) >= 0 ? v : "uk";
    } catch (err) {
      return "uk";
    }
  }

  /* ДРУГИЙ ШАР: машинний переклад решти.
   *
   * Словник і шаблони вище дають ПРАВИЛЬНІ терміни («Зріз» -> «As of»,
   * «відпустка» -> «leave»), але лише там, де я їх написала. Машина дає
   * ПОВНЕ покриття, включно з цитатами норм і назвами законів, але на коротких
   * підписах помиляється: заміряно -- «Зріз» вона перекладає як «Cut», а
   * «відпустка» як «vacation».
   *
   * Тому шари саме в такому порядку: спершу словник, потім машина на решту.
   * Це не компроміс із лінощів -- це те, що показав замір.
   *
   * Переклад приходить із сервера (`/api/translate`): у браузері вбудованого
   * перекладача немає, бо він працює лише в захищеному контексті, а сторінка
   * роздається по HTTP. На сервері це КЕШ на диску, тобто відповідь за
   * мілісекунди й без моделі.
   */
  var CYRILLIC = /[А-ЯІЇЄҐа-яіїєґ]/;

  function machinePass() {
    var nodes = [], seen = {}, list = [];
    var it = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
    var node;
    while ((node = it.nextNode())) {
      var tag = node.parentNode && node.parentNode.nodeName;
      if (tag === "SCRIPT" || tag === "STYLE") { continue; }
      var v = node.nodeValue;
      if (!CYRILLIC.test(v) || isUntouchable(v)) { continue; }
      nodes.push(node);
      var key = v.trim();
      if (!seen[key]) { seen[key] = 1; list.push(key); }
    }
    if (!list.length) { return; }
    try {
      var req = new XMLHttpRequest();
      req.open("POST", "/api/translate", true);
      req.setRequestHeader("Content-Type", "application/json");
      req.onload = function () {
        if (req.status !== 200) { return; }
        var map;
        try { map = JSON.parse(req.responseText).texts || {}; }
        catch (err) { return; }
        for (var i = 0; i < nodes.length; i += 1) {
          if (!nodes[i].isConnected) { continue; }
          var t = nodes[i].nodeValue, k = t.trim();
          if (map[k]) {
            originals.push([nodes[i], t]);
            nodes[i].nodeValue = t.replace(k, map[k]);
          }
        }
      };
      req.send(JSON.stringify({ texts: list }));
    } catch (err) {
      /* Сервер недоступний -- сторінка лишається як є. Переклад це зручність,
         і вона не має права зламати сторінку. */
    }
  }

  function apply(lang) {
    var root = document.documentElement;
    root.setAttribute("lang", lang);
    root.setAttribute("data-lang", lang);
    if (lang === "en") {
      walk(document.body);
      machinePass();
    } else {
      restore();
    }
  }

  /* Атрибут ставимо ОДРАЗУ (до першого малювання), а переклад -- коли є body. */
  document.documentElement.setAttribute("data-lang", read());
  document.documentElement.setAttribute("lang", read());

  function paint(btn, lang) {
    /* Підпис -- код мови, а не прапорець: прапорець позначає країну, а не
       мову, і для української це окрема чутлива різниця. */
    btn.textContent = lang === "uk" ? "EN" : "УКР";
    btn.setAttribute("aria-label", HINT[lang]);
    btn.setAttribute("title", HINT[lang]);
    btn.setAttribute("data-lang-mode", lang);
  }

  function wire(btn) {
    if (!btn || btn.dataset.wired) { return; }
    btn.dataset.wired = "1";
    paint(btn, read());
    btn.addEventListener("click", function () {
      var next = read() === "uk" ? "en" : "uk";
      try { window.localStorage.setItem(KEY, next); } catch (err) { /* ок */ }
      apply(next);
      var all = document.querySelectorAll(".lang-toggle");
      for (var i = 0; i < all.length; i += 1) { paint(all[i], next); }
    });
  }

  function wireAll() {
    var all = document.querySelectorAll(".lang-toggle");
    for (var i = 0; i < all.length; i += 1) { wire(all[i]); }
  }

  function start() {
    wireAll();
    if (read() === "en") { walk(document.body); }
    /* Сторінка домальовує себе після запиту до /api/stats, і кнопки Gradio
       з'являються не одразу. Тому перекладаємо ще й те, що додалось. Наглядач
       дешевий: він дивиться лише на нові вузли. */
    if (window.MutationObserver) {
      var obs = new MutationObserver(function (recs) {
        wireAll();
        if (read() !== "en") { return; }
        for (var i = 0; i < recs.length; i += 1) {
          for (var j = 0; j < recs[i].addedNodes.length; j += 1) {
            var n = recs[i].addedNodes[j];
            if (n.nodeType === 3) {
              var t = translateText(n.nodeValue);
              if (t !== null && t !== n.nodeValue) {
                originals.push([n, n.nodeValue]);
                n.nodeValue = t;
              }
            } else if (n.nodeType === 1) {
              walk(n);
            }
          }
        }
      });
      obs.observe(document.body, { childList: true, subtree: true });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  /* Назовні -- лише для тестів і для сторінок, які домальовують себе самі. */
  window.__aiLang = { dict: DICT, patterns: PATTERNS, phrases: PHRASES,
                      translate: translateText, apply: apply, read: read };
}());
