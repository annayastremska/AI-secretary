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

    /* ── сторінка статистики ───────────────────────────────────────────── */
    "Стан бази, з якої чат бере відповіді, і виміряна якість обробки.":
      "The state of the database the chat answers from, and the measured processing quality.",
    "База зараз": "The database right now",
    "Робота чата": "How the chat performs",
    "Виміряна якість обробки": "Measured processing quality",
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

  /* Підписи, склеєні з числами. Точний збіг тут неможливий, тому шаблон плюс
     заміна. Список короткий навмисно: усе, що можна перекласти словником,
     перекладається словником -- регулярка потрібна лише там, де число стоїть
     СЕРЕДИНІ рядка. */
  var PATTERNS = [
    [/^(\d+) з (\d+)$/, "$1 of $2"],
    [/^(\d+) з (\d+) відсутні$/, "$1 of $2 absent"],
    [/^Документів у базі: (\d+)\.$/, "Documents in the database: $1."],
    [/^Зріз: стан бази на (.+)\.$/, "As of: database state on $1."],
  ];

  /* Оригінали текстових вузлів, які ми підмінили. Потрібні, щоб повернути
     українську ТОЧНО, а не зворотним словником: два різні українські рядки
     могли б перекластись однаково, і зворотний шлях був би неоднозначний.
     Відірвані від документа вузли відсіюються при поверненні (`isConnected`). */
  var originals = [];

  function translateText(s) {
    var key = s.trim();
    if (!key) { return null; }
    if (Object.prototype.hasOwnProperty.call(DICT, key)) {
      return s.replace(key, DICT[key]);
    }
    for (var i = 0; i < PATTERNS.length; i += 1) {
      if (PATTERNS[i][0].test(key)) {
        return s.replace(key, key.replace(PATTERNS[i][0], PATTERNS[i][1]));
      }
    }
    return null;
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

  function apply(lang) {
    var root = document.documentElement;
    root.setAttribute("lang", lang);
    root.setAttribute("data-lang", lang);
    if (lang === "en") {
      walk(document.body);
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
  window.__aiLang = { dict: DICT, patterns: PATTERNS, apply: apply, read: read };
}());
