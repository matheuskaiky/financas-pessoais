"use strict";
// Carta do mês: hovering (or focusing) a figure in the text highlights the note that explains it, and
// the month selector submits on change. Progressive: without this file the letter still reads fine.
(function () {
  const notes = function () { return document.querySelectorAll(".lt-note[data-note-id]"); };
  const mark = function (id, on) {
    if (!id) return;
    notes().forEach(function (note) {
      if (note.getAttribute("data-note-id") === id) note.classList.toggle("is-hot", on);
    });
  };
  const target = function (event) {
    const el = event.target instanceof Element ? event.target.closest(".num-v[data-note]") : null;
    return el ? el.getAttribute("data-note") : null;
  };
  document.addEventListener("mouseover", function (event) { mark(target(event), true); });
  document.addEventListener("mouseout", function (event) { mark(target(event), false); });
  document.addEventListener("focusin", function (event) { mark(target(event), true); });
  document.addEventListener("focusout", function (event) { mark(target(event), false); });
  document.addEventListener("change", function (event) {
    const el = event.target;
    if (el instanceof HTMLSelectElement && el.hasAttribute("data-autosubmit") && el.form) el.form.submit();
  });
})();
