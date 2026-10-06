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
