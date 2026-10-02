"use strict";
// E se…: keeps the "Seu dinheiro rende" slider honest while it is dragged (the track fill and the figure
// beside it). The calculation itself runs on the server when the slider is released (htmx "change").
(function () {
  const pct = function (bps) { return (bps / 100).toFixed(2).replace(".", ","); };
  const annual = function (bps) { return ((Math.pow(1 + bps / 10000, 12) - 1) * 100).toFixed(2).replace(".", ","); };
  document.addEventListener("input", function (event) {
    const el = event.target;
    if (!(el instanceof HTMLInputElement) || el.id !== "rate") return;
    const bps = Number(el.value) || 0;
    const max = Number(el.max) || 200;
    el.style.setProperty("--p", (bps / max).toFixed(4));
    el.setAttribute("aria-valuetext", pct(bps) + "% ao mês");
    const num = document.getElementById("rate-num");
    const year = document.getElementById("rate-annual");
    if (num) num.textContent = pct(bps);
    if (year) year.textContent = "≈ " + annual(bps) + "% ao ano";
  });
})();
