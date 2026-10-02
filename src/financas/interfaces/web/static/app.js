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
