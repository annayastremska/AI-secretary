// Мінімальна заглушка DOM: рівно те, чим користується lang-toggle.js.
// Мета -- прогнати САМ перекладач, а не повірити, що він працює.
const fs = require("fs");

function makeText(v) { return { nodeType: 3, nodeValue: v, isConnected: true }; }
function makeEl(tag, children, attrs) {
  return { nodeType: 1, nodeName: tag.toUpperCase(), childNodes: children || [],
           _attrs: attrs || {}, isConnected: true, dataset: {},
           getAttribute(n) { return this._attrs[n] || null; },
           setAttribute(n, v) { this._attrs[n] = v; },
           removeAttribute(n) { delete this._attrs[n]; },
           querySelectorAll(sel) { return collectEls(this); },
           addEventListener() {}, appendChild(c) { this.childNodes.push(c); } };
}
function collectText(node, out) {
  out = out || [];
  for (const c of node.childNodes || []) {
    if (c.nodeType === 3) { c.parentNode = node; out.push(c); }
    else collectText(c, out);
  }
  return out;
}
function collectEls(node, out) {
  out = out || [];
  for (const c of node.childNodes || []) {
    if (c.nodeType === 1) { out.push(c); collectEls(c, out); }
  }
  return out;
}

// Сторінка: те, що справді малює stats.html
const body = makeEl("body", [
  makeEl("h1", [makeText("Статистика")]),
  makeEl("p", [makeText("Стан бази, з якої чат бере відповіді, і виміряна якість обробки.")]),
  makeEl("div", [makeText("фактів витягнуто впевнено")]),
  makeEl("div", [makeText("35 з 35")]),
  makeEl("div", [makeText("перевірок чат проходить")]),
  makeEl("div", [makeText("медіана часу відповіді")]),
  makeEl("div", [makeText("Доповідаю: 1 особа у відпустці.")]),   // відповідь чата -- НЕ перекладати
  makeEl("button", [], { "aria-label": "Тема", title: "Тема: темна" }),
], {});

const store = {};
global.window = {
  localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } },
  MutationObserver: null,
};
global.NodeFilter = { SHOW_TEXT: 4 };
global.document = {
  documentElement: makeEl("html", [body]),
  body: body,
  readyState: "complete",
  addEventListener() {},
  querySelectorAll() { return []; },
  createTreeWalker(root) {
    const nodes = collectText(root);
    let i = -1;
    return { nextNode() { i += 1; return i < nodes.length ? nodes[i] : null; } };
  },
};
document.documentElement.setAttribute = function (n, v) { this._attrs[n] = v; };

const src = fs.readFileSync(process.argv[2], "utf8");
eval(src);

const api = global.window.__aiLang;
function texts() { return collectText(body).map((n) => n.nodeValue); }

console.log("до перекладу:", JSON.stringify(texts().slice(0, 3), null, 0));
api.apply("en");
const en = texts();
console.log("після EN:");
en.forEach((t) => console.log("   ", t));
const btn = collectEls(body).find((e) => e.nodeName === "BUTTON");
// Значення атрибута знімаємо ПОКИ АКТИВНА англійська. Перша версія читала
// його в кінці, тобто ПІСЛЯ повернення до української -- і вердикт казав
// «атрибут не переклався», хоч переклад працював. Помилка була в тесті.
const attrAfterEn = btn.getAttribute("title");
console.log("атрибут title після EN:", attrAfterEn);
api.apply("uk");
const uk = texts();
console.log("повернення до UK збіглося:", JSON.stringify(uk) === JSON.stringify(texts()));
console.log("рядок 0 знову українською:", uk[0]);

// ВЕРДИКТ машинним рядком: тест на Python не мусить розбирати людський друк.
const verdict = {
  translated: en[0] === "Statistics",
  pattern: en.includes("35 of 35"),
  attr: attrAfterEn === "Theme: dark",
  chat_untouched: en.some((t) => t.indexOf("Доповідаю:") === 0),
  restored: uk[0] === "Статистика",
};
console.log("VERDICT " + JSON.stringify(verdict));
