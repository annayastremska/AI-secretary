"use strict";
/* Перемикач світлої й темної теми. Один файл на всі три екрани (/, /stats,
 * /chat) -- інакше три копії розійшлися б на першій правці.
 *
 * ЧОМУ ВІН З'ЯВИВСЯ ЛИШЕ ЗАРАЗ. Темна тема в токенах була від початку, але
 * вмикалась ТІЛЬКИ з налаштування операційної системи (@media
 * prefers-color-scheme). Тобто людина з світлою системою не мала способу її
 * побачити взагалі, і питання Ані «а де перемикач?» законне: його не було.
 *
 * ТРИ СТАНИ, А НЕ ДВА. «Як у системі» -- це окремий і найчастіше правильний
 * стан: телефон сам темніє ввечері, і забирати це в людини не варто. Тому
 * цикл: як у системі -> світла -> темна -> як у системі. У першому стані
 * атрибут не ставиться зовсім, і працює медіа-запит; у двох інших атрибут
 * data-theme перебиває його в обидві сторони.
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
  /* Коротка підказка, куди веде натиск: людина мусить бачити не лише
     поточний стан, а й наступний. */
  var NEXT_HINT = {
    system: "перемкнути на світлу",
    light: "перемкнути на темну",
    dark: "повернути як у системі",
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
    btn.textContent = LABEL[mode];
    btn.setAttribute("aria-label", LABEL[mode] + ", " + NEXT_HINT[mode]);
    btn.setAttribute("title", NEXT_HINT[mode]);
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
      /* Кнопок може бути кілька (на сторінці одна, але в чаті вона живе в
         бічній панелі, яку Gradio перемальовує) -- перефарбовуємо всі. */
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
  /* Чат -- це Gradio: бічна панель з'являється не одразу й може бути
     перемальована. Тому кнопку доводиться дочікуватись, а не шукати один
     раз. Спостерігач дешевий і сам знімається, коли кнопку знайдено. */
  if (window.MutationObserver) {
    var seen = 0;
    var obs = new MutationObserver(function () {
      wireAll();
      var found = document.querySelectorAll(".theme-toggle[data-wired]").length;
      if (found && found === seen) { obs.disconnect(); }
      seen = found;
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
    /* Запобіжник: якщо кнопки немає взагалі (чужа сторінка), не спостерігаємо
       за деревом вічно. */
    window.setTimeout(function () { obs.disconnect(); }, 15000);
  }
}());
