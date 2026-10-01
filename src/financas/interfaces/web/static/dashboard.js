"use strict";
// Chart data comes from data- attributes written by the server (no inline scripts, see the CSP).
(function () {
  if (typeof Chart === "undefined") return;
  const brl = (v) => v.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  const months = document.getElementById("chart-months");
  if (months) {
    new Chart(months, {
      type: "bar",
      data: {
        labels: JSON.parse(months.dataset.months),
        datasets: [
          { label: "Receitas", data: JSON.parse(months.dataset.income), backgroundColor: "#0E6151" },
          { label: "Despesas (líquidas)", data: JSON.parse(months.dataset.expenses), backgroundColor: "#1E395F" },
        ],
      },
      options: {
        maintainAspectRatio: false,
        scales: { y: { ticks: { callback: (v) => brl(v) } } },
        plugins: { tooltip: { callbacks: { label: (c) => c.dataset.label + ": " + brl(c.parsed.y) } } },
      },
    });
  }
})();
