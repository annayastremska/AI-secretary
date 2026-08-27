"use strict";
/* Показати людині, ЯКИМ РІВНЕМ вона зайшла — і дати спосіб піднятись.
 *
 * ЗАПИТ АНІ 27.08: «де у людини питають про рівень доступу чи просять
 * пароль?». Відповідь до цього була незручна: ніде. Пароль питає сам браузер,
 * коли заходиш без ключа; а хто зайшов за QR — не бачить нічого й дізнається
 * про свій рівень лише коли натисне «записати в базу» й отримає 403.
 *
 * Дізнатися про свою межу з відмови ПІСЛЯ дії — найгірший спосіб: людина вже
 * витратила час і вирішила, що система зламана. Тому рівень видно від початку,
 * а поруч — посилання «увійти як оператор».
 *
 * Чому JS, а не сервер: сторінки статичні (їх віддає FileResponse, без
 * шаблонів), а чат Gradio будує ОДИН раз при запуску — тобто вставити туди
 * рівень конкретної людини неможливо за побудовою. Один запит /api/whoami з
 * браузера вирішує це на всіх трьох екранах однаково.
 *
 * Жодного зовнішнього запиту: усе своє.
 */
(function () {
  var LABEL = {
    guest: "гість",
    operator: "оператор",
  };

  function place(badge) {
    /* Куди вставити. Порядок спроб — від найточнішого до запасного:
       брендова смуга звичайних сторінок, бічна панель чата, початок body. */
    var host = document.querySelector(".appbar")
      || document.getElementById("page-links")
      || document.body;
    if (host.classList && host.classList.contains("appbar")) {
      badge.classList.add("access-badge--bar");
    }
    host.appendChild(badge);
  }

  function build(info) {
    var wrap = document.createElement("div");
    wrap.className = "access-badge access-badge--" + info.level;

    var who = document.createElement("span");
    who.className = "access-who";
    who.textContent = LABEL[info.level] || info.level;
    wrap.appendChild(who);

    if (!info.can_write) {
      /* Межа названа ОДРАЗУ, а не після натискання кнопки. */
      var note = document.createElement("span");
      note.className = "access-note";
      note.textContent = "без запису в базу";
      note.title = info.reason || "";
      wrap.appendChild(note);
    }

    if (info.level !== "operator") {
      /* Спосіб піднятись. Маршрут /operator свідомо віддає 401, і браузер сам
         показує вікно пароля — інакше людина, яку гейт уже пустив, того вікна
         не побачить ніколи. */
      var up = document.createElement("a");
      up.className = "access-up";
      up.href = "/operator";
      up.textContent = "увійти як оператор";
      wrap.appendChild(up);
    }
    return wrap;
  }

  function show() {
    if (document.querySelector(".access-badge")) { return; }
    var req = new XMLHttpRequest();
    req.open("GET", "/api/whoami", true);
    req.onload = function () {
      if (req.status !== 200) { return; }
      var info;
      try { info = JSON.parse(req.responseText); } catch (err) { return; }
      if (!info || !info.level) { return; }
      place(build(info));
      markCommit(info);
    };
    req.send();
  }

  function markCommit(info) {
    /* Сторінка завантаження: сказати про межу БІЛЯ КНОПКИ, а не лише в
       смузі вгорі. Кнопку не вимикаємо — натиснути можна, і відмова прийде
       з сервера з поясненням. Вимкнена кнопка без причини гірша за кнопку,
       яка чесно відмовляє: людина не бачить, чого їй бракує. */
    var btn = document.getElementById("commit-btn");
    if (!btn || info.can_write) { return; }
    var hint = document.createElement("p");
    hint.className = "hint access-commit-note";
    hint.textContent = "Запис у базу робить оператор. "
      + (info.reason ? "Ваш доступ: " + info.reason + "." : "");
    btn.parentNode.appendChild(hint);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", show);
  } else {
    show();
  }
  /* Чат — Gradio: бічна панель з'являється не одразу. Той самий підхід, що в
     перемикача теми: дочікуємось, і спостерігач сам знімається. */
  if (window.MutationObserver) {
    var obs = new MutationObserver(function () {
      if (document.getElementById("page-links")
          || document.querySelector(".appbar")) {
        show();
        if (document.querySelector(".access-badge")) { obs.disconnect(); }
      }
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
    window.setTimeout(function () { obs.disconnect(); }, 15000);
  }
}());
