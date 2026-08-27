"use strict";
/* Перемикач світлої й темної теми. Один файл на всі три екрани (/, /stats,
 * /chat) -- інакше три копії розійшлися б на першій правці.
 *
 * ЧОМУ ВІН З'ЯВИВСЯ ЛИШЕ ЗАРАЗ. Темна тема в токенах була від початку, але
 * вмикалась ТІЛЬКИ з налаштування операційної системи (@media
 * prefers-color-scheme). Тобто людина зі світлою системою не мала способу її
 * побачити взагалі, і питання Ані «а де перемикач?» законне: його не було.
 *
 * ТРИ СТАНИ, А НЕ ДВА. «Як у системі» -- це окремий і найчастіше правильний
 * стан: телефон сам темніє ввечері, і забирати це в людини не варто. Тому
 * цикл: як у системі -> світла -> темна -> як у системі. У першому стані
 * атрибут не ставиться зовсім, і працює медіа-запит; у двох інших атрибут
 * data-theme перебиває його в обидві сторони.
 *
 * ІКОНКА, А НЕ ПІДПИС (правка Ані 27.08: «класично, кружечком угорі
 * праворуч»). Текстова пігулка «Тема: як у системі» їла місце в шапці й
 * називала стан, який людина й так бачить очима -- екран або світлий, або
 * темний. Кругла іконка -- стандартний, звичний людям вигляд. Слова
 * лишаються там, де їм і місце: у підказці при наведенні й у aria-label для
 * читача екрана.
 *
 * Іконки живуть ТУТ, а не в розмітці трьох сторінок: інакше три копії трьох
 * SVG розійшлися б із першою ж правкою. Розмітці лишається порожня кнопка.
 *
 * ЧОМУ СКРИПТ У <head> І БЕЗ defer. Він мусить поставити атрибут ДО першого
 * малювання. Із defer сторінка встигала б показатись у світлій темі й
 * блимнути в темну -- ця мить читається як поломка.
 *
 * Жодного зовнішнього запиту: вибір лежить у localStorage браузера й нікуди
 * не їде (правило «зі сторінок нічого не йде назовні»).
 */
(function () {
  var KEY = "ai-secretary-theme";
  var ORDER = ["system", "light", "dark"];
  var LABEL = {
    system: "Тема: як у системі",
    light: "Тема: світла",
    dark: "Тема: темна",
  };
  /* Підказка веде НЕ до поточного стану, а до наступного: людина натискає,
     щоб щось змінити, і мусить знати, що саме станеться. */
  var NEXT_HINT = {
    system: "перемкнути на світлу",
    light: "перемкнути на темну",
    dark: "повернути як у системі",
  };

  var SVG_OPEN = '<svg viewBox="0 0 24 24" width="17" height="17" '
    + 'fill="none" stroke="currentColor" stroke-width="1.7" '
    + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';

  /* Три знаки, по одному на стан. Півколо для «як у системі» -- звичний
     спосіб сказати «вирішує не я»: половина світла, половина темна. */
  var ICON = {
    system: SVG_OPEN
      + '<circle cx="12" cy="12" r="8.2" />'
      + '<path d="M12 3.8a8.2 8.2 0 0 0 0 16.4z" fill="currentColor" '
      + 'stroke="none" />'
      + '</svg>',
    light: SVG_OPEN
      + '<circle cx="12" cy="12" r="4.3" />'
      + '<path d="M12 2.4v2.2M12 19.4v2.2M2.4 12h2.2M19.4 12h2.2'
      + 'M5.2 5.2l1.6 1.6M17.2 17.2l1.6 1.6M18.8 5.2l-1.6 1.6'
      + 'M6.8 17.2l-1.6 1.6" />'
      + '</svg>',
    dark: SVG_OPEN
      + '<path d="M20.5 14.6A8.6 8.6 0 0 1 9.4 3.5a8.6 8.6 0 1 0 11.1 11.1z" />'
      + '</svg>',
  };

  function read() {
    try {
      var value = window.localStorage.getItem(KEY);
      return ORDER.indexOf(value) >= 0 ? value : "system";
    } catch (err) {
      /* Приватне вікно або заборонені дані сайту: не падаємо, просто
         працюємо як «за системою». */
      return "system";
    }
  }

  function apply(mode) {
    var root = document.documentElement;
    if (mode === "system") {
      root.removeAttribute("data-theme");
    } else {
      root.setAttribute("data-theme", mode);
    }
  }

  /* Застосувати ОДРАЗУ, ще до розбору <body>: див. коментар про блимання. */
  apply(read());

  function paintButton(btn, mode) {
    btn.innerHTML = ICON[mode];
    btn.setAttribute("aria-label", LABEL[mode] + ", " + NEXT_HINT[mode]);
    btn.setAttribute("title", LABEL[mode] + " — " + NEXT_HINT[mode]);
    btn.setAttribute("data-mode", mode);
  }

  function wire(btn) {
    if (!btn || btn.dataset.wired) { return; }
    btn.dataset.wired = "1";
    paintButton(btn, read());
    btn.addEventListener("click", function () {
      var next = ORDER[(ORDER.indexOf(read()) + 1) % ORDER.length];
      try { window.localStorage.setItem(KEY, next); } catch (err) { /* ок */ }
      apply(next);
      /* Кнопок може бути кілька (на сторінці одна, але Gradio перемальовує
         свої блоки) -- перефарбовуємо всі. */
      var all = document.querySelectorAll(".theme-toggle");
      for (var i = 0; i < all.length; i += 1) { paintButton(all[i], next); }
    });
  }

  function wireAll() {
    var all = document.querySelectorAll(".theme-toggle");
    for (var i = 0; i < all.length; i += 1) { wire(all[i]); }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wireAll);
  } else {
    wireAll();
  }
  /* Чат -- це Gradio: шапка з'являється не одразу й може бути перемальована.
     Тому кнопку доводиться дочікуватись, а не шукати один раз. Спостерігач
     дешевий і сам знімається, коли кнопку знайдено. */
  if (window.MutationObserver) {
    var seen = 0;
    var obs = new MutationObserver(function () {
      wireAll();
      var found = document.querySelectorAll(".theme-toggle[data-wired]").length;
      if (found && found === seen) { obs.disconnect(); }
      seen = found;
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
    /* Запобіжник: якщо кнопки немає взагалі, не спостерігаємо вічно. */
    window.setTimeout(function () { obs.disconnect(); }, 15000);
  }
}());
