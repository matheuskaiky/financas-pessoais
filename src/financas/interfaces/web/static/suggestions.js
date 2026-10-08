// Smart suggestions for the quick entry form: the description field offers what the user habitually
// enters (page data block #suggestions-data: the last 180 days, scored by frequency and recency on the
// server) and picking one fills the category, the account, the payment method and, when the amount is
// still empty, the habitual amount. No request per key: the data is already in the page.
(function () {
  "use strict";
  var data = null;
  try { data = JSON.parse(document.getElementById("suggestions-data").textContent || "null"); } catch (e) { data = null; }
  if (!data) return;

  function fold(text) {
    return String(text || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
  }
  function flowOf(form) {
    var kind = form.querySelector('input[name="kind"]:checked');
    if (!kind) return null;
    return kind.value === "income" ? "income" : kind.value === "expense" ? "expense" : null;
  }
  // the best suggestion of each description (the list arrives scored, best first)
  function distinct(flow) {
    var seen = {}, out = [];
    (data[flow] || []).forEach(function (s) {
      var key = fold(s.description);
      if (!seen[key]) { seen[key] = true; out.push(s); }
    });
    return out;
  }
  function fillList(form) {
    var list = document.getElementById("description-suggestions");
    if (!list) return;
    var flow = flowOf(form);
    list.replaceChildren();
    (flow ? distinct(flow) : []).forEach(function (s) {
      var option = document.createElement("option");
      option.value = s.description;
      list.appendChild(option);
    });
  }
  function find(form, text) {
    var flow = flowOf(form), key = fold(text);
    if (!flow || !key) return null;
    return distinct(flow).filter(function (s) { return fold(s.description) === key; })[0] || null;
  }

  function usable(option) { return option && !option.disabled && !option.hidden; }

  function apply(form, s) {
    var category = form.querySelector('select[name="category_id"]');
    if (category && !category.disabled && s.category_id) {
      var wanted = Array.prototype.filter.call(category.options, function (o) { return o.value === s.category_id; })[0];
      if (usable(wanted)) category.value = s.category_id;
    }
    var account = form.querySelector('select[name="account_id"]');
    if (account && s.account_id) {
      var there = Array.prototype.filter.call(account.options, function (o) { return o.value === s.account_id; })[0];
      if (usable(there) && account.value !== s.account_id) {
        account.value = s.account_id;
        account.dispatchEvent(new Event("change", { bubbles: true }));  // the method field follows the account
      }
    }
    var method = s.payment_method && form.querySelector('input[name="payment_method"][value="' + s.payment_method + '"]');
    if (method && !method.disabled) method.checked = true;
    var amount = form.querySelector('input[name="amount"]');
    if (amount && s.habitual_amount && !amount.value.trim()) {
      amount.value = s.habitual_amount;
      amount.dispatchEvent(new Event("input", { bubbles: true }));  // the money mask and the items editor read it
    }
  }

  document.addEventListener("input", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    if (!target || !target.matches('form input[name="description"]')) return;
    // a datalist pick is an input without typing (insertReplacementText, or no inputType at all)
    if (event.inputType && event.inputType !== "insertReplacementText") return;
    var form = target.closest("form"), found = form && find(form, target.value);
    if (found) apply(form, found);
  });
  document.addEventListener("change", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    var form = target && target.closest("form");
    if (!form || !form.querySelector("#description-suggestions") && !document.getElementById("description-suggestions")) return;
    if (target.matches('input[name="kind"]')) fillList(form);
    else if (target.matches('input[name="description"]')) {
      var found = find(form, target.value);  // a typed, committed description that is a known habit
      if (found) apply(form, found);
    }
  });
  function init() {
    document.querySelectorAll("form").forEach(function (form) {
      if (form.querySelector('input[name="description"][list="description-suggestions"]')) fillList(form);
    });
  }
  document.addEventListener("htmx:afterSettle", init);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
