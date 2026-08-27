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
    /* Куди вставити: брендова смуга звичайних сторінок або бічна панель чата.
       ЖОДНОГО запасного `document.body`.

       Було з body -- і на телефоні позначка опинилась унизу сторінки злитим
       рядком «гістьбез запису в базуувійти як оператор»: вона потрапила в
       body, бо на той момент ні смуги, ні панелі ще не існувало (Gradio
       будує їх пізніше), а в body для неї немає ні розкладки, ні проміжків.

       Тепер, якщо місця ще немає, ми просто не вставляємо -- спостерігач за
       деревом покличе ще раз, коли панель з'явиться. Не показати позначку
       на секунду довше краще, ніж показати її в чужому місці зіпсованою. */
    var host = document.querySelector(".appbar")
      || document.getElementById("page-links");
    if (!host) { return false; }
    if (host.classList && host.classList.contains("appbar")) {
      badge.classList.add("access-badge--bar");
    }
    host.appendChild(badge);
    return true;
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

  /* Стан запиту -- ОКРЕМОЮ змінною, а не «чи є вже позначка в розмітці».
   *
   * Причина -- живий дефект: при оновленні сторінки позначка іноді
   * виводилась ТРИ рази. Це гонка, і вона була в цій функції. `show()`
   * кличеться і з DOMContentLoaded, і зі спостерігача за деревом (Gradio
   * будує панель не одразу, тому спостерігач зривається кілька разів). А
   * перевірка стояла на РЕЗУЛЬТАТ -- «чи є вже позначка», -- і результат
   * з'являється лише після відповіді сервера. Отже три виклики встигали
   * пройти перевірку до першої відповіді, кожен посилав свій запит, і кожен
   * домальовував свою позначку.
   *
   * Правило, яке з цього варто запам'ятати: прапорець мусить ставитись
   * СИНХРОННО, у ту саму мить, коли починається асинхронна робота. Перевірка
   * «чи вже зроблено» ловить лише те, що вже завершилось, а не те, що вже
   * почалось.
   */
  var state = "idle";   // idle -> loading -> done

  function show() {
    if (state !== "idle") { return; }
    state = "loading";
    var req = new XMLHttpRequest();
    req.open("GET", "/api/whoami", true);
    req.onload = function () {
      /* Невдача повертає стан у `idle`: спостерігач спробує ще раз, коли
         дерево зміниться. Інакше один невдалий запит назавжди лишив би
         людину без позначки рівня. */
      if (req.status !== 200) { state = "idle"; return; }
      var info;
      try { info = JSON.parse(req.responseText); } catch (err) {
        state = "idle";
        return;
      }
      if (!info || !info.level) { state = "idle"; return; }
      if (!place(build(info))) {
        /* Місця ще немає -- вертаємо стан, спостерігач покличе ще раз. */
        state = "idle";
        return;
      }
      state = "done";
      markCommit(info);
    };
    req.onerror = function () { state = "idle"; };
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
        /* Знімаємось за СТАНОМ, а не за наявністю позначки в розмітці: та
           сама причина, що вище -- розмітка з'являється пізніше за рішення. */
        if (state === "done") { obs.disconnect(); }
      }
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
    window.setTimeout(function () { obs.disconnect(); }, 15000);
  }
}());
