"use strict";
// Small behaviors without inline handlers (the CSP only allows scripts from this origin).
document.addEventListener("change", function (event) {
  const el = event.target;
  if (el instanceof HTMLElement && el.matches("[data-autosubmit]") && el.form) el.form.submit();
});
document.addEventListener("submit", function (event) {
  const message = event.target instanceof HTMLElement ? event.target.dataset.confirm : undefined;
  if (message && !window.confirm(message)) event.preventDefault();
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
