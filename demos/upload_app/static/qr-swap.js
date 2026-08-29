/* Перемикач двох QR: вхід у чат <-> пам'ятка журі.
 *
 * Перемикається В САМІЙ СТОРІНЦІ, без ходу на сервер: це показ картинки, а не
 * робота з даними, і затримка тут читалась би як «зависло».
 *
 * Блок малює Gradio, і малює його ПІСЛЯ завантаження скрипта, тому слухач
 * ставиться на document (делегування), а не на саму кнопку. Прямий
 * addEventListener на #qr-swap не спрацював би: у момент виконання скрипта
 * кнопки ще немає в DOM.
 */
(function () {
  "use strict";

  var TEXT = {
    guest: {
      title: "Гостьовий доступ",
      note: "Вхід без пароля. Без доступу до запису в базу.",
      button: "Показати пам'ятку журі"
    },
    jury: {
      title: "Пам'ятка журі",
      note: "Що спитати й на що дивитись у відповіді.",
      button: "Показати вхід у чат"
    }
  };

  function show(block, which) {
    var t = TEXT[which];
    if (!t) { return; }
    var imgs = block.querySelectorAll("img[data-qr]");
    for (var i = 0; i < imgs.length; i++) {
      imgs[i].hidden = imgs[i].getAttribute("data-qr") !== which;
    }
    var title = block.querySelector(".qr-title");
    var note = block.querySelector(".qr-note");
    var btn = block.querySelector("#qr-swap");
    if (title) { title.textContent = t.title; }
    if (note) { note.textContent = t.note; }
    if (btn) { btn.textContent = t.button; }
    block.setAttribute("data-shown", which);
  }

  document.addEventListener("click", function (ev) {
    var btn = ev.target && ev.target.closest
      ? ev.target.closest("#qr-swap") : null;
    if (!btn) { return; }
    ev.preventDefault();
    var block = document.getElementById("guest-qr");
    if (!block) { return; }
    /* Стан читаємо з РОЗМІТКИ, не з власної змінної: блок може бути
       перемальований Gradio, і тоді змінна розійшлася б із тим, що на екрані. */
    show(block, block.getAttribute("data-shown") === "jury" ? "guest" : "jury");
  }, false);
})();
