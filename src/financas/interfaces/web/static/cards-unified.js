// The unified cards view (/cards?card=all): the last included card cannot be excluded (its chip
// explains itself instead), the view is busy while a chip's request runs, and a failed request shows
// the banner with "Tentar novamente". The chips themselves are plain HTMX (hx-get + hx-push-url).
(function () {
  "use strict";
  var KEEP_ONE = "Mantenha ao menos um cartão selecionado.";

  function unified() { return document.getElementById("cards-unified"); }
  function banner() { return document.getElementById("cards-unified-error"); }
  function say(text) {
    var live = document.getElementById("cards-live");
    if (!live) return;
    live.textContent = "";
    setTimeout(function () { live.textContent = text; }, 30);
  }

  document.addEventListener("click", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.closest(".card-chip[data-keep-one]")) { say(KEEP_ONE); return; }
    var retry = target.closest("[data-retry]");
    if (retry && retry.closest("#cards-unified-error")) {
      var box = banner();
      var url = box.getAttribute("data-url") || (window.location.pathname + window.location.search);
      box.hidden = true;
      if (window.htmx) window.htmx.ajax("GET", url, { target: "#cards-unified", swap: "outerHTML" });
    }
  });

  document.addEventListener("htmx:beforeRequest", function (event) {
    var source = event.detail && event.detail.elt instanceof Element ? event.detail.elt : null;
    var view = unified();
    if (view && source && source.closest("#cards-unified")) view.setAttribute("aria-busy", "true");
  });

  function failed(event) {
    var source = event.detail && event.detail.elt instanceof Element ? event.detail.elt : null;
    var view = unified(), box = banner();
    if (!view || !box || !source || !source.closest("#cards-unified")) return;
    view.removeAttribute("aria-busy");
    var path = event.detail.pathInfo && event.detail.pathInfo.requestPath;
    if (path) box.setAttribute("data-url", path);
    box.hidden = false;
    var button = box.querySelector("[data-retry]");
    if (button) button.focus();
  }
  document.addEventListener("htmx:responseError", failed);
  document.addEventListener("htmx:sendError", failed);
})();
