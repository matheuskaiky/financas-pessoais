// Smart suggestions for the quick entry form: under the description field a small list offers what the
// user habitually enters (page data block #suggestions-data: the last 180 days, scored by frequency and
// recency on the server). Picking one fills the category, the account, the payment method and, when the
// amount is still empty, the habitual amount. It is our own listbox (not the browser's <datalist>), so it
// looks and behaves the same everywhere and never depends on how a browser reports a pick. Mouse,
// touch and keyboard (↑ ↓ Enter Esc) work; no request per key: the data is already in the page.
(function () {
  "use strict";
  var MAX_ITEMS = 8;
  var cache = { node: null, text: null, value: null };

  function data() {  // parsed again only when the block changes (a swap may replace it)
    var node = document.getElementById("suggestions-data");
    if (!node) return null;
    if (cache.node !== node || cache.text !== node.textContent) {
      try { cache.value = JSON.parse(node.textContent || "null"); } catch (e) { cache.value = null; }
      cache.node = node; cache.text = node.textContent;
    }
    return cache.value;
  }
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
    var all = data(), seen = {}, out = [];
    ((all && all[flow]) || []).forEach(function (s) {
      var key = fold(s.description);
      if (!seen[key]) { seen[key] = true; out.push(s); }
    });
    return out;
  }
  function matches(form, text) {
    var flow = flowOf(form), key = fold(text);
    if (!flow) return [];
    return distinct(flow).filter(function (s) { return !key || fold(s.description).indexOf(key) > -1; })
      .filter(function (s) { return fold(s.description) !== key || !key; }).slice(0, MAX_ITEMS);
  }
  function find(form, text) {
    var flow = flowOf(form), key = fold(text);
    if (!flow || !key) return null;
    return distinct(flow).filter(function (s) { return fold(s.description) === key; })[0] || null;
  }

  // --- the listbox ---------------------------------------------------------------------------
  var state = { items: [], active: -1 };

  function box(input) { return input.parentElement && input.parentElement.querySelector(".suggest-list"); }
  function close(input) {
    var list = box(input);
    if (list) { list.hidden = true; list.replaceChildren(); }
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    state.items = []; state.active = -1;
  }
  function describe(s) {
    var parts = [s.category_name, s.account_name, s.payment_label];
    if (s.habitual_amount) parts.push("R$ " + s.habitual_amount);
    return parts.filter(Boolean).join(" · ");
  }
  function open(input) {
    var form = input.closest("form"), list = box(input);
    if (!form || !list) return;
    var items = matches(form, input.value);
    if (!items.length) { close(input); return; }
    state.items = items; state.active = -1;
    list.replaceChildren();
    items.forEach(function (s, index) {
      var li = document.createElement("li");
      li.id = "suggestion-" + index;
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", "false");
      li.setAttribute("data-index", String(index));
      var title = document.createElement("span");
      title.className = "suggest-title";
      title.textContent = s.description;
      var meta = document.createElement("small");
      meta.className = "suggest-meta";
      meta.textContent = describe(s);
      li.appendChild(title); li.appendChild(meta);
      list.appendChild(li);
    });
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }
  function highlight(input, index) {
    var list = box(input);
    if (!list) return;
    state.active = (index + state.items.length) % state.items.length;
    Array.prototype.forEach.call(list.children, function (li, i) {
      li.setAttribute("aria-selected", i === state.active ? "true" : "false");
      if (i === state.active) { input.setAttribute("aria-activedescendant", li.id); li.scrollIntoView({ block: "nearest" }); }
    });
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
  function pick(input, s) {
    var form = input.closest("form");
    input.value = s.description;
    close(input);
    if (form) apply(form, s);
    // the category field asks the server for its suggestion on "change", now with the category we set
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function isDescription(target) {
    return target instanceof Element && target.matches('input[name="description"][data-suggest]');
  }

  document.addEventListener("focusin", function (event) {
    if (isDescription(event.target)) open(event.target);
  });
  document.addEventListener("input", function (event) {
    if (isDescription(event.target)) open(event.target);
  });
  document.addEventListener("focusout", function (event) {
    if (isDescription(event.target)) close(event.target);
  });
  // mousedown (not click): it runs before the field loses focus, so the list is still there to read
  document.addEventListener("mousedown", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    var item = target && target.closest(".suggest-list li[data-index]");
    if (!item) return;
    event.preventDefault();
    var host = item.closest(".suggest-host"), input = host && host.querySelector("input[data-suggest]");
    var chosen = input && state.items[Number(item.getAttribute("data-index"))];
    if (input && chosen) pick(input, chosen);
  });
  document.addEventListener("keydown", function (event) {
    if (!isDescription(event.target)) return;
    var input = event.target, list = box(input), isOpen = list && !list.hidden;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!isOpen) open(input);
      if (state.items.length) { event.preventDefault(); highlight(input, state.active + (event.key === "ArrowDown" ? 1 : -1)); }
    } else if (event.key === "Enter" && isOpen && state.active > -1) {
      event.preventDefault();  // choose the highlighted suggestion instead of submitting the form
      pick(input, state.items[state.active]);
    } else if (event.key === "Escape" && isOpen) {
      event.preventDefault(); event.stopPropagation();
      close(input);
    }
  }, true);
  // a typed, committed description that is already a known habit fills the rest too
  document.addEventListener("change", function (event) {
    var target = event.target;
    if (!isDescription(target)) return;
    var form = target.closest("form"), found = form && find(form, target.value);
    if (found) apply(form, found);
  });
  // toggling Despesa/Receita: the open list (if any) shows the other flow's habits
  document.addEventListener("change", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    if (!target || !target.matches('input[name="kind"]')) return;
    var form = target.closest("form"), input = form && form.querySelector("input[data-suggest]");
    if (input) close(input);
  });
  // a swap or a reset must not leave a stale list behind
  function reset() {
    document.querySelectorAll("input[data-suggest]").forEach(close);
  }
  document.addEventListener("htmx:afterSwap", reset);
  document.addEventListener("reset", reset);
})();
