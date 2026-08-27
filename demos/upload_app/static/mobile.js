"use strict";
/* Телефон: висота під клавіатуру + бічна панель як шухляда.
 *
 * ЧОМУ ЦЕ ФАЙЛ, А НЕ ЛИШЕ CSS. Дві речі медіа-запитом не робляться:
 *
 * 1. КЛАВІАТУРА. `100dvh` на телефоні не те, що здається: різні браузери
 *    по-різному вирішують, чи змінювати viewport при появі клавіатури, і
 *    на iOS поле вводу з'їжджає під неї, лишаючись «у межах» 100dvh. Єдине,
 *    що знає правду про видиму частину екрана, -- `window.visualViewport`.
 *    Тому висоту колонки чата беремо з нього: `--vvh`. CSS лишається
 *    простим (`height: var(--vvh, 100dvh)`), а знання про клавіатуру живе
 *    в одному місці.
 * 2. ШУХЛЯДА. Відкриття/закриття -- це стан, а стан у CSS не тримається.
 *    Тримаємо атрибутом на <html>, як і тему: одне джерело правди, і CSS
 *    лише реагує.
 *
 * ДОСТУПНІСТЬ, і це не формальність: шухляда накриває екран, тому поки вона
 * відкрита, решта сторінки мусить бути недосяжною -- інакше Tab виводить
 * фокус «за» накладку, і людина з клавіатурою втрачає орієнтацію. Тому
 * `inert` на головній колонці (браузер сам блокує фокус, клік і доступ для
 * читача екрана) плюс Escape на закриття.
 */
(function () {
  var OPEN = "data-nav";
  var root = document.documentElement;

  /* ── 1. Висота видимої частини екрана ─────────────────────────────────── */
  function measure() {
    var vv = window.visualViewport;
    if (!vv) { return; }
    /* Висота видимої області й зсув від верху: другий потрібен, бо на iOS
       при відкритій клавіатурі сторінка ще й прокручується вгору. */
    root.style.setProperty("--vvh", Math.round(vv.height) + "px");
    root.style.setProperty("--vvtop", Math.round(vv.offsetTop) + "px");
  }

  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", measure);
    window.visualViewport.addEventListener("scroll", measure);
    measure();
  }

  /* ── 1b. Висоти шапки, поля вводу й підказки ──────────────────────────
   *
   * Розкладка чата виймає стрічку, поле й підказку з потоку (див. правило 18
   * у темі), і стрічці потрібні `top` і `bottom`. Числами їх зашивати не
   * можна: рівно на цьому вже двічі ламалось -- літерал 104px розійшовся з
   * реальністю, як тільки поле підросло. Тому міряємо справжні висоти й
   * кладемо у змінні CSS.
   *
   * ResizeObserver, а не одноразовий замір: поле вводу росте, коли людина
   * пише багаторядкове питання, і стрічка мусить одразу віддати йому місце.
   */
  function heights() {
    var map = {
      "--topbar-h": document.getElementById("topbar"),
      "--composer-h": document.getElementById("composer"),
      "--hint-h": document.getElementById("hint"),
    };
    for (var name in map) {
      if (!Object.prototype.hasOwnProperty.call(map, name)) { continue; }
      var el = map[name];
      if (!el) { continue; }
      var h = Math.round(el.getBoundingClientRect().height);
      /* Нуль означає «елемент ще не намалювався» -- запасне значення з CSS
         кращe за нуль: нуль зробив би стрічку на весь екран і сховав поле. */
      if (h > 0) { root.style.setProperty(name, h + "px"); }
    }
  }

  function watchHeights() {
    heights();
    if (!window.ResizeObserver) { return; }
    var ro = new ResizeObserver(heights);
    ["topbar", "composer", "hint"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el && !el.dataset.roWired) {
        el.dataset.roWired = "1";
        ro.observe(el);
      }
    });
  }

  /* ── 2. Шухляда ───────────────────────────────────────────────────────── */
  function main() {
    return document.getElementById("main-col");
  }

  function isOpen() {
    return root.getAttribute(OPEN) === "open";
  }

  function open() {
    root.setAttribute(OPEN, "open");
    var m = main();
    if (m) { m.setAttribute("inert", ""); }
    /* Фокус -- у шухляду, на першу дію: інакше читач екрана лишається там,
       де був, і про відкриття не дізнається. */
    var first = document.querySelector("#sidebar button, #sidebar a");
    if (first) { first.focus(); }
    sync();
  }

  function close(returnFocus) {
    root.removeAttribute(OPEN);
    var m = main();
    if (m) { m.removeAttribute("inert"); }
    if (returnFocus) {
      var btn = document.getElementById("nav-toggle");
      if (btn) { btn.focus(); }
    }
    sync();
  }

  function sync() {
    var btn = document.getElementById("nav-toggle");
    if (!btn) { return; }
    btn.setAttribute("aria-expanded", isOpen() ? "true" : "false");
    btn.setAttribute("aria-label", isOpen() ? "Закрити панель" : "Меню");
  }

  function wire() {
    watchHeights();
    var btn = document.getElementById("nav-toggle");
    if (btn && !btn.dataset.wired) {
      btn.dataset.wired = "1";
      btn.setAttribute("aria-controls", "sidebar");
      btn.addEventListener("click", function () {
        if (isOpen()) { close(true); } else { open(); }
      });
      sync();
    }
    var back = document.getElementById("nav-backdrop");
    if (back && !back.dataset.wired) {
      back.dataset.wired = "1";
      back.addEventListener("click", function () { close(true); });
    }
    /* Перехід на іншу сторінку з шухляди -- закривати не треба (сторінка
       перезавантажиться), але натиск «Новий чат» лишає нас тут, і відкрита
       шухляда над порожнім чатом виглядає як поломка. */
    var side = document.getElementById("sidebar");
    if (side && !side.dataset.wiredClose) {
      side.dataset.wiredClose = "1";
      side.addEventListener("click", function (ev) {
        if (ev.target.closest("button")) { close(false); }
      });
    }
  }

  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && isOpen()) { close(true); }
  });

  /* Повернулись на широкий екран -- шухляди більше не існує, стан треба
     зняти, інакше `inert` залишиться на головній колонці назавжди. */
  if (window.matchMedia) {
    var wide = window.matchMedia("(min-width: 861px)");
    var onWide = function (e) { if (e.matches && isOpen()) { close(false); } };
    if (wide.addEventListener) { wide.addEventListener("change", onWide); }
    onWide(wide);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
  /* Чат -- Gradio: шапка й панель з'являються не одразу. Той самий підхід,
     що в перемикача теми: дочікуємось і знімаємо спостерігача. */
  if (window.MutationObserver) {
    var obs = new MutationObserver(function () {
      wire();
      heights();
      /* Знімаємось, коли є і висоти, і (якщо вона взагалі є) кнопка меню.
         На широкому екрані кнопки немає, тому достатньо шапки. */
      var haveHeights = !!document.getElementById("composer");
      var btnEl = document.getElementById("nav-toggle");
      if (haveHeights && (!btnEl || btnEl.dataset.wired)) {
        obs.disconnect();
      }
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
    window.setTimeout(function () { obs.disconnect(); }, 15000);
  }
}());
