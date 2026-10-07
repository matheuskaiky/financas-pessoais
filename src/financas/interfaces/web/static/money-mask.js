// ATM-style currency mask for every money field (".money-field input").
// Digits push in from the right, like a cash machine: 1 -> 0,01 · 10 -> 0,10 · 1000 -> 10,00 ·
// 100000 -> 1.000,00. Backspace drops the last digit. The "R$" is the field's own prefix, so the
// submitted text is "1.234,56", which the server's parse_brl already accepts. A "-" keeps the
// value negative (informed balances can be). Opt out with data-money-free on the input.
(function () {
  "use strict";
  var SELECTOR = ".money-field input:not([data-money-free])";
  var MAX_DIGITS = 13; // up to 99.999.999.999,99: far past anything a person types here

  // "123456" -> "1.234,56"; "" -> ""; a lone "-" is kept so the minus can be typed first.
  function format(raw, deleting) {
    var text = String(raw == null ? "" : raw);
    var negative = text.indexOf("-") !== -1;
    var digits = text.replace(/\D/g, "").replace(/^0+/, "").slice(0, MAX_DIGITS);
    var typed = /\d/.test(text);
    if (!typed) return negative ? "-" : "";
    if (digits === "" && deleting) return negative ? "-" : ""; // backspaced down to zero: empty
    digits = digits.padStart(3, "0");
    var whole = digits.slice(0, -2).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    var out = whole + "," + digits.slice(-2);
    return negative && out !== "0,00" ? "-" + out : out;
  }

  // A decimal text the server or the user produced ("12,34", "1.234,5", "12.5", "100") as the
  // digits-only string of its cents. Plain integers are whole reais.
  function centsDigits(text) {
    var t = String(text).replace(/[^\d.,-]/g, "");
    var negative = t.indexOf("-") !== -1;
    t = t.replace(/-/g, "");
    var comma = t.lastIndexOf(","), dot = t.lastIndexOf(".");
    var at = Math.max(comma, dot);
    var whole = t, frac = "";
    // the last separator is the decimal one when it has 1-2 digits after it (or it is a comma)
    if (at !== -1 && (t.length - at - 1 <= 2 || at === comma)) {
      whole = t.slice(0, at);
      frac = t.slice(at + 1);
    }
    whole = whole.replace(/[.,]/g, "");
    var digits = whole + (frac + "00").slice(0, 2);
    return (negative ? "-" : "") + digits;
  }

  function apply(input, deleting) {
    var next = format(input.value, deleting);
    if (next !== input.value) input.value = next;
    try { input.setSelectionRange(next.length, next.length); } catch (e) { /* not supported */ }
    // Whoever reads the value (the items editor's "Restam R$ …") must wait for this: the native
    // `input` event reaches other listeners with the text as typed, before it is masked.
    input.dispatchEvent(new CustomEvent("fp:money", { bubbles: true }));
  }

  function prepare(root) {
    var inputs = (root || document).querySelectorAll(SELECTOR);
    for (var i = 0; i < inputs.length; i++) {
      var input = inputs[i];
      if (input.dataset.moneyReady) continue;
      input.dataset.moneyReady = "1";
      input.setAttribute("inputmode", "numeric");
      if (input.value) input.value = format(centsDigits(input.value), false);
    }
  }

  document.addEventListener("input", function (event) {
    var input = event.target;
    if (!(input instanceof HTMLInputElement) || !input.matches(SELECTOR)) return;
    if (!input.dataset.moneyReady) { input.dataset.moneyReady = "1"; input.setAttribute("inputmode", "numeric"); }
    apply(input, !!event.inputType && event.inputType.indexOf("delete") === 0);
  });

  document.addEventListener("paste", function (event) {
    var input = event.target;
    if (!(input instanceof HTMLInputElement) || !input.matches(SELECTOR)) return;
    var text = event.clipboardData && event.clipboardData.getData("text");
    if (!text) return;
    event.preventDefault();
    input.value = format(centsDigits(text), false);
    apply(input, false);
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });

  document.addEventListener("focusin", function (event) {
    var input = event.target;
    if (input instanceof HTMLInputElement && input.matches(SELECTOR)) apply(input, false);
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { prepare(); });
  else prepare();
  document.addEventListener("htmx:afterSettle", function (event) { prepare(event.target); });

  window.fpMoneyMask = { format: format, centsDigits: centsDigits };
})();
