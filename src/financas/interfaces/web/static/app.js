"use strict";
// Small behaviors without inline handlers (the CSP only allows scripts from this origin).
document.addEventListener("change", function (event) {
  const el = event.target;
  if (el instanceof HTMLElement && el.matches("[data-autosubmit]") && el.form) el.form.submit();
});

// Confirmation dialog (instead of the browser's pop-up): forms with data-confirm and htmx requests
// with hx-confirm ask first. Optional data-confirm-title, data-confirm-ok and data-confirm-danger.
const confirmDialog = document.getElementById("confirm-dialog");
function askConfirm(message, source) {
  const data = (source && source.dataset) || {};
  if (!confirmDialog || typeof confirmDialog.showModal !== "function") {
    return Promise.resolve(window.confirm(message)); // very old browsers
  }
  document.getElementById("confirm-title").textContent = data.confirmTitle || "Confirmar";
  document.getElementById("confirm-message").textContent = message;
  const ok = document.getElementById("confirm-ok");
  ok.textContent = data.confirmOk || "Confirmar";
  ok.classList.toggle("danger", data.confirmDanger !== undefined);
  const cancel = document.getElementById("confirm-cancel");
  return new Promise(function (resolve) {
    let settled = false;
    const finish = function (answer) {
      if (settled) return;
      settled = true;
      ok.removeEventListener("click", yes);
      cancel.removeEventListener("click", no);
      confirmDialog.removeEventListener("close", no);
      if (confirmDialog.open) confirmDialog.close();
      resolve(answer);
    };
    const yes = function () { finish(true); };
    const no = function () { finish(false); }; // also Esc and the backdrop (they close the dialog)
    ok.addEventListener("click", yes);
    cancel.addEventListener("click", no);
    confirmDialog.addEventListener("close", no);
    confirmDialog.showModal();
    cancel.focus(); // the safe choice is the default
  });
}
if (confirmDialog) {
  confirmDialog.addEventListener("click", function (event) {
    if (event.target === confirmDialog) confirmDialog.close(); // a click on the backdrop
  });
}
document.addEventListener("submit", function (event) {
  const form = event.target;
  const message = form instanceof HTMLElement ? form.dataset.confirm : undefined;
  if (!message || form.dataset.confirmed) return;
  event.preventDefault();
  askConfirm(message, form).then(function (yes) {
    if (!yes) return;
    form.dataset.confirmed = "1";
    form.requestSubmit(event.submitter || undefined);
  });
});
document.body.addEventListener("htmx:confirm", function (event) {
  if (!event.detail.question) return;
  event.preventDefault();
  askConfirm(event.detail.question, event.detail.elt).then(function (yes) {
    if (yes) event.detail.issueRequest(true);
  });
});

// Investment valuation form: the URL carries the account id.
(function () {
  const form = document.querySelector("[data-valuation-form]");
  const select = document.querySelector("[data-valuation-account]");
  if (!form || !select) return;
  const sync = function () { form.action = "/investments/" + encodeURIComponent(select.value) + "/valuation"; };
  select.addEventListener("change", sync);
  sync();
})();

// Failure reports: the page tells the server what broke, so it lands in data/logs (Diagnóstico).
// Only the kind, the page path and a short technical message are sent; never field values.
(function () {
  const report = function (data) {
    try {
      data.path = window.location.pathname;
      fetch("/client-errors", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
        keepalive: true,
      }).catch(function () {});
    } catch (e) { /* reporting must never break the page */ }
  };
  window.addEventListener("error", function (event) {
    if (event.target && event.target !== window) return; // resource load errors are not script errors
    report({
      kind: "js_error",
      message: String(event.message || "").slice(0, 300),
      file: String(event.filename || "").replace(window.location.origin, "").slice(0, 200),
      line: event.lineno,
    });
  });
  window.addEventListener("unhandledrejection", function (event) {
    const reason = event.reason;
    report({ kind: "unhandled_rejection", message: String(reason && reason.message ? reason.message : reason).slice(0, 300) });
  });
  const htmx = function (name, describe) {
    document.body.addEventListener(name, function (event) {
      const info = (event.detail && event.detail.requestConfig) || {};
      const xhr = (event.detail && event.detail.xhr) || {};
      report({ kind: "htmx_error", method: info.verb ? String(info.verb).toUpperCase() : undefined,
               target: String(info.path || "").split("?")[0].slice(0, 200), status: xhr.status || 0, message: describe });
    });
  };
  htmx("htmx:responseError", "resposta com erro");
  htmx("htmx:sendError", "falha de conexão");
  htmx("htmx:swapError", "falha ao atualizar a tela");
})();

// Contribution/withdrawal form: choosing where the money goes pre-selects the checking account of
// the same institution when there is exactly one (the user can still pick another or none).
(function () {
  const target = document.querySelector("[data-flow-target]");
  const other = document.querySelector("[data-flow-other]");
  if (!target || !other) return;
  target.addEventListener("change", function () {
    const chosen = target.selectedOptions[0];
    const institution = chosen ? chosen.dataset.institution : "";
    const same = Array.from(other.options).filter(function (o) { return o.value && o.dataset.institution === institution; });
    if (same.length === 1) other.value = same[0].value;
  });
})();

// Quick entry form: only the accounts that accept the chosen type are offered (a card never takes
// income), and a transfer cannot leave from and arrive at the same account.
(function () {
  const account = document.querySelector("[data-entry-account]");
  const kinds = document.querySelectorAll("input[name='kind']");
  if (account && kinds.length) {
    const sync = function () {
      const checked = document.querySelector("input[name='kind']:checked");
      const kind = checked ? checked.value : "expense";
      Array.from(account.options).forEach(function (option) {
        if (!option.dataset.kinds) return;
        const allowed = option.dataset.kinds.split(" ").indexOf(kind) !== -1;
        option.hidden = !allowed;
        option.disabled = !allowed;
        if (!allowed && option.selected) account.value = "";
      });
    };
    kinds.forEach(function (radio) { radio.addEventListener("change", sync); });
    sync();
  }
  const from = document.querySelector("[data-transfer-from]");
  const to = document.querySelector("[data-transfer-to]");
  if (from && to) {
    const exclude = function (source, other) {
      Array.from(other.options).forEach(function (option) {
        const same = option.value !== "" && option.value === source.value;
        option.disabled = same;
        option.hidden = same;
        if (same && option.selected) other.value = "";
      });
    };
    from.addEventListener("change", function () { exclude(from, to); });
    to.addEventListener("change", function () { exclude(to, from); });
    exclude(from, to);
    exclude(to, from);
  }
})();

// Statement tabs (/cards): open scrolled to the current statement instead of the oldest month. The tab row is scrolled
// directly (scrollLeft), not with scrollIntoView, so the page itself never moves; one read, then one write, and the
// same on every HTMX swap (the year filter) and once the fonts have set the final widths.
(function () {
  const center = function (root) {
    (root && root.querySelectorAll ? root : document).querySelectorAll(".statement-tabs").forEach(function (tabs) {
      const active = tabs.querySelector('[aria-current="true"], [aria-selected="true"], .is-active');
      if (!active || tabs.scrollWidth <= tabs.clientWidth) return;
      const box = tabs.getBoundingClientRect();
      const tab = active.getBoundingClientRect();
      tabs.scrollLeft = tabs.scrollLeft + (tab.left - box.left) - (box.width - tab.width) / 2;
    });
  };
  center(document);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { center(document); });
  document.body.addEventListener("htmx:afterSettle", function (event) { center(event.target); });
})();

// Notice for HTMX actions that update part of a page: the server sends HX-Trigger {"fp:notice": text}
// and the message appears in the page's flash area (#flash), the same banner a redirect shows.
document.body.addEventListener("fp:notice", function (event) {
  var flash = document.getElementById("flash");
  if (!flash) return;
  var banner = document.createElement("div");
  banner.className = "banner ok";
  banner.setAttribute("role", "status");
  banner.textContent = event.detail && event.detail.value ? event.detail.value : "";
  flash.replaceChildren(banner);
  banner.scrollIntoView({ block: "nearest" });
});

// Itemized expenses: the breakdown toggle on a row, the items editor of the edit form ("Restam R$ …")
// and the merge toolbar ("Mesclar em um só lançamento"). Everything is delegated from the document, so
// it keeps working for rows and forms HTMX swaps in.
(function () {
  "use strict";
  // The cents of a masked amount ("R$ 1.250,00", "-R$ 25,52" -> 125000, 2552): the sign never
  // counts and "." is a thousands mark, so only the digits matter. Masked text always has its two
  // decimals; never a float (it would also lose cents).
  // Fields whose value changes what the items must add up to.
  var WATCHED = 'input[name="amount"], input[name="installments"], input[name="current_installment"], ' +
    'input[name="amount_mode"], input[name="propagate"]';
  function cents(text) {
    var n = parseInt(String(text || "").replace(/\D/g, ""), 10);
    return isNaN(n) ? 0 : n;
  }
  function brl(c) {
    return "R$ " + window.fpMoneyMask.format(String(Math.abs(c)));
  }

  // The parent's own category field while the editor is on: cleared, locked and labelled, because
  // its items carry the categories (an itemized entry has none). Off again, it gets its value back.
  function lockParent(form, locked) {
    var select = form.querySelector('select[name="category_id"]');
    if (!select) return;
    var placeholder = select.querySelector("option[data-split-placeholder]");
    if (select.dataset.wasRequired === undefined) select.dataset.wasRequired = select.required ? "1" : "";
    if (locked) {
      if (!select.disabled) select.dataset.prev = select.value;
      if (!placeholder) {
        placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.setAttribute("data-split-placeholder", "");
        placeholder.textContent = "Categorizado por item abaixo";
        select.insertBefore(placeholder, select.firstChild);
      }
      placeholder.selected = true;
      select.value = "";
      select.required = false;
      select.disabled = true;
    } else if (select.disabled || placeholder) {
      if (placeholder) placeholder.remove();
      select.disabled = false;
      var back = select.dataset.prev !== undefined ? select.dataset.prev : select.dataset.restore;
      if (back !== undefined && select.querySelector('option[value="' + back + '"]')) select.value = back;
      select.required = select.dataset.wasRequired === "1";
    }
  }
  // What the items must add up to, in cents, and a note for the user about it.
  //  - the card purchase form: the whole purchase (an installment value times the installments);
  //  - an installment's edit form: its own amount, or, with "apply to every installment" on, the
  //    installments the plan-wide items cover (the others are history) adjusted by this amount;
  //  - any other entry: the amount field.
  function expectedTotal(editor, form) {
    var field = form.querySelector('input[name="amount"]');
    var typed = field ? cents(field.value) : 0;
    var note = "";
    var total = typed;
    if (editor.hasAttribute("data-split-purchase")) {
      var count = parseInt((form.querySelector('input[name="installments"]') || {}).value, 10) || 1;
      var mode = form.querySelector('input[name="amount_mode"]:checked');
      if (mode && mode.value === "installment") {
        total = typed * count;
        note = "Total da compra: " + brl(total) + " (" + count + " x " + brl(typed) + ")";
      }
      if (count > 1) {
        note += (note ? " · " : "") + "Os itens serão distribuídos proporcionalmente entre as " + count + " faturas.";
      }
    } else if (editor.hasAttribute("data-plan-total")) {
      var wide = form.querySelector('input[name="propagate"]');
      var planTotal = parseInt(editor.getAttribute("data-plan-total"), 10) || 0;
      var own = parseInt(editor.getAttribute("data-target-amount"), 10) || 0;
      var many = parseInt(editor.getAttribute("data-plan-count"), 10) || 1;
      if (wide && wide.checked) {
        total = planTotal + (typed - own);
        note = "Itens do parcelamento: valem para as " + many + " parcela(s) ainda abertas (total " + brl(total) +
          ") e são distribuídos proporcionalmente entre elas.";
      } else {
        note = "Itens só desta parcela (" + brl(total) + "); as outras parcelas não mudam.";
      }
    }
    return { total: total, note: note };
  }

  function refreshEditor(editor) {
    var form = editor.closest("form");
    var on = editor.querySelector("[data-split-toggle-items]").checked;
    lockParent(form, on);
    var box = editor.querySelector("[data-split-items]");
    var amounts = editor.querySelectorAll("[data-split-rows] .split-amount");
    var out = editor.querySelector("[data-split-remaining]");
    box.hidden = !on;
    editor.querySelectorAll("[data-split-rows] input, [data-split-rows] select").forEach(function (el) { el.disabled = !on; });
    amounts.forEach(function (el) { el.setCustomValidity(""); });
    var save0 = form.querySelector('button[type="submit"]');
    if (!on) { out.textContent = ""; if (save0) save0.disabled = false; return; }
    var expected = expectedTotal(editor, form);
    var total = expected.total;
    var noteBox = editor.querySelector("[data-split-note]");
    if (noteBox) noteBox.textContent = expected.note;
    var sum = 0;
    amounts.forEach(function (el) { sum += cents(el.value); });
    var remaining = total - sum;
    var enough = amounts.length >= 2;
    if (remaining > 0) out.textContent = "Restam " + brl(remaining);
    else if (remaining < 0) out.textContent = "Ultrapassou " + brl(remaining);
    else out.textContent = "Total distribuído com sucesso";
    out.dataset.state = remaining === 0 && enough ? "ok" : "bad";
    var valid = remaining === 0 && enough;
    if (amounts.length) {
      // native validation blocks the submit (plain form and HTMX alike) until the items add up
      amounts[0].setCustomValidity(!enough ? "Use pelo menos dois itens."
        : remaining !== 0 ? "Os itens precisam somar o valor do lançamento." : "");
    }
    var save = form.querySelector('button[type="submit"]');
    if (save) save.disabled = !valid;
  }
  function refreshAll(root) {
    (root || document).querySelectorAll("[data-split-editor]").forEach(refreshEditor);
  }
  function addRow(editor) {
    var template = editor.querySelector("[data-split-template]");
    editor.querySelector("[data-split-rows]").appendChild(template.content.firstElementChild.cloneNode(true));
    refreshEditor(editor);
  }

  document.addEventListener("click", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    var toggle = target.closest("[data-split-toggle]");
    if (toggle) {  // a row's "▾ N categorias"
      var detail = document.getElementById(toggle.getAttribute("aria-controls"));
      var open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(open));
      toggle.textContent = toggle.textContent.replace(/^[▾▴]/, open ? "▴" : "▾");
      if (detail) detail.hidden = !open;
      return;
    }
    var editor = target.closest("[data-split-editor]");
    if (editor && target.closest("[data-split-add]")) addRow(editor);
    else if (editor && target.closest("[data-split-remove]")) {
      target.closest(".split-row").remove();
      refreshEditor(editor);
    }
    if (target.closest("[data-merge-open]")) openMerge();
    else if (target.closest("[data-merge-clear]")) { clearPicks(); syncBar(); }
    else if (target.closest("[data-merge-cancel]")) { var d = document.getElementById("merge-dialog"); if (d) d.close(); }
  });

  document.addEventListener("change", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    var ownForm = target.closest("form");
    if (ownForm && target.matches(WATCHED)) refreshAll(ownForm);  // purchase and installment forms
    var editor = target.closest("[data-split-editor]");
    if (editor && target.matches("[data-split-toggle-items]")) {
      if (target.checked && editor.querySelectorAll("[data-split-rows] .split-row").length === 0) {
        addRow(editor); addRow(editor);
      }
      refreshEditor(editor);
    }
    if (target.matches(".merge-pick")) syncBar();
  });

  // Money fields announce themselves once their text is masked (money-mask.js): read them then.
  document.addEventListener("fp:money", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    var editor = target.closest("[data-split-editor]");
    if (editor) { refreshEditor(editor); return; }
    var form = target.closest("form");
    if (form && target.matches('input[name="amount"]')) refreshAll(form);  // the total moved
  });
  // Without the mask (or loaded after this file) the typed text is all there is: look once the
  // other listeners of the same event have run.
  document.addEventListener("input", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    if (!target || !target.closest("[data-split-editor], form")) return;
    setTimeout(function () {
      var editor = target.closest("[data-split-editor]");
      if (editor) refreshEditor(editor);
      else if (target.matches(WATCHED) && target.closest("form")) refreshAll(target.closest("form"));
    }, 0);
  });

  // merge toolbar
  function picked() { return Array.prototype.slice.call(document.querySelectorAll(".merge-pick:checked")); }
  function clearPicks() { picked().forEach(function (el) { el.checked = false; }); }
  function syncBar() {
    var bar = document.getElementById("merge-bar");
    if (!bar) return;
    var n = picked().length;
    bar.hidden = n < 2;
    var count = bar.querySelector("[data-merge-count]");
    if (count) count.textContent = String(n);
  }
  function openMerge() {
    var dialog = document.getElementById("merge-dialog");
    var chosen = picked();
    if (!dialog || chosen.length < 2 || typeof dialog.showModal !== "function") return;
    var ids = dialog.querySelector("[data-merge-ids]");
    ids.replaceChildren();
    chosen.forEach(function (el) {
      var input = document.createElement("input");
      input.type = "hidden"; input.name = "ids"; input.value = el.getAttribute("data-merge-id");
      ids.appendChild(input);
    });
    var form = dialog.querySelector("form");
    form.elements["description"].value = chosen[0].getAttribute("data-merge-desc") || "";
    form.elements["date"].value = chosen.map(function (el) { return el.getAttribute("data-merge-date") || ""; }).sort().pop();
    form.elements["ack"].checked = false;
    dialog.querySelector("#merge-error").replaceChildren();
    dialog.showModal();
  }

  document.addEventListener("htmx:afterSettle", function (event) { refreshAll(event.target); syncBar(); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { refreshAll(); syncBar(); });
  else { refreshAll(); syncBar(); }
})();

// Payment date of a statement (the "Pagar fatura" and "Editar pagamento" forms): between the previous
// statement's due date (data-min) and today (data-max). Outside it, the matching alert shows and the
// form cannot be sent; the server enforces the same range. ISO dates compare as plain strings.
(function () {
  "use strict";
  function check(input) {
    var form = input.closest("form");
    var box = form && form.querySelector("[data-payment-date-warning]");
    if (!box) return;
    var value = input.value;
    var future = !!value && value > (input.getAttribute("data-max") || "9999-12-31");
    var before = !!value && !future && value < (input.getAttribute("data-min") || "0000-01-01");
    var show = function (selector, on) { var el = box.querySelector(selector); if (el) el.hidden = !on; };
    show("[data-pd-future]", future);
    show("[data-pd-before]", before);
    input.setCustomValidity(future || before ? "Data fora do intervalo permitido." : "");
    form.querySelectorAll('button[type="submit"]').forEach(function (button) { button.disabled = future || before; });
  }
  function checkAll(root) {
    (root instanceof Element || root instanceof Document ? root : document)
      .querySelectorAll("input[data-payment-date]").forEach(check);
  }
  document.addEventListener("input", function (event) {
    if (event.target instanceof Element && event.target.matches("input[data-payment-date]")) check(event.target);
  });
  document.addEventListener("change", function (event) {
    if (event.target instanceof Element && event.target.matches("input[data-payment-date]")) check(event.target);
  });
  document.addEventListener("htmx:afterSettle", function (event) { checkAll(event.target); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { checkAll(document); });
  else checkAll(document);
})();

// Revisão Rápida (/revisar): ← skips, → or Enter saves and moves on. The arrows only work while the
// focus is not in a field (there they move the caret or the radio), Enter only when the focus is on
// the page itself (in a field it submits the form natively, on a button it presses it).
(function () {
  "use strict";
  function card() { return document.getElementById("review-card"); }
  function leaving(direction) {
    var current = card();
    if (current) current.classList.add(direction === "left" ? "is-leaving-left" : "is-leaving-right");
  }
  document.addEventListener("click", function (event) {
    if (event.target instanceof Element && event.target.closest("[data-review-skip]")) leaving("left");
  });
  document.addEventListener("submit", function (event) {
    if (event.target instanceof Element && event.target.id === "review-form") leaving("right");
  }, true);
  document.addEventListener("keydown", function (event) {
    if (!card() || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
    var target = event.target instanceof Element ? event.target : null;
    var typing = !!target && target.matches("input, select, textarea, [contenteditable]");
    var onPage = target === document.body || target === document.documentElement;
    var skip = document.querySelector("[data-review-skip]");
    var form = document.getElementById("review-form");
    if (event.key === "ArrowLeft" && !typing && skip) { event.preventDefault(); skip.click(); }
    else if (event.key === "ArrowRight" && !typing && form) { event.preventDefault(); form.requestSubmit(); }
    else if (event.key === "Enter" && onPage && form) { event.preventDefault(); form.requestSubmit(); }
  });
})();
