// "Compras Mais Caras" (/analises): a failed Top 5 / Top 10 request shows the banner with
// "Tentar novamente"; the control itself is plain HTMX (hx-get on change, hx-sync replaces a request).
(function () {
  "use strict";
  function panel() { return document.getElementById("rank-expensive"); }

  function failed(event) {
    var source = event.detail && event.detail.elt instanceof Element ? event.detail.elt : null;
    var box = panel();
    if (!box || !source || !source.closest("#rank-expensive")) return;
    var banner = box.querySelector(".rank-error");
    if (!banner) return;
    var path = event.detail.pathInfo && event.detail.pathInfo.requestPath;
    if (path) banner.setAttribute("data-url", path);
    banner.hidden = false;
    var button = banner.querySelector("[data-rank-retry]");
    if (button) button.focus();
  }
  document.addEventListener("htmx:responseError", failed);
  document.addEventListener("htmx:sendError", failed);

  document.addEventListener("click", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    var retry = target && target.closest("[data-rank-retry]");
    if (!retry) return;
    var banner = retry.closest(".rank-error");
    var url = banner && banner.getAttribute("data-url");
    if (banner) banner.hidden = true;
    if (url && window.htmx) window.htmx.ajax("GET", url, { target: "#rank-expensive", swap: "outerHTML" });
  });
})();
