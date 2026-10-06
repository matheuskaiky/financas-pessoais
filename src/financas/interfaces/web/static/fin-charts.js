// Charts (design v3, package 2): one vanilla ES module, no libraries. Port of docs/design-v3 chartMath.ts,
// format.ts, useSpringTween.ts and useNetWorthChart.ts, plus the three sibling charts of the board
// (month pace, cash flow, categories). The server computes every figure (integer cents, ISO dates) and
// serves it as JSON under /api/charts/...; the browser only draws, interpolates and reads values back.
// Jinja prints the card, the header, the skeleton and the fixed-height box (no layout shift), and the
// <template> blocks of the states (few data, error); this file adds scrubbing, the spring morph, the glass
// tooltip and the keyboard. Nothing is inline (CSP), nothing leaves this origin.
//
// Public API (also on window.FinCharts):
//   math      pure functions: clamp, lerp, lerpArray, sameArray, sliceByDays, resampleByTime, valueAtTime,
//             monotoneTangents, monotonePath, hermiteAt, niceDomain, niceTicks, niceStep, springProgress,
//             xAxisStops, stepPath
//   fmt       pt-BR: cents (1.234,56), signedCents, percent, thousands (9,3 mil), shortDate, monthYear,
//             fullDate, longDate, toEpoch
//   mount(root)       turns a [data-chart] element into a live chart; returns the controller
//   mountAll()        mounts every [data-chart] not mounted yet (runs on DOMContentLoaded)
// Chart kinds (data-chart): pace | flow | donut (the net worth is the <fp-patrimonio> element of charts.js). Text shown to the user lives in T below and in
// the page templates (states); this file belongs to interfaces/.

import { formatAmount, formatCurrency } from "./fp-money.js";

const MONTH_ABBR = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
const MONTH_FULL = [
  "janeiro", "fevereiro", "março", "abril", "maio", "junho",
  "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
];
const WEEKDAY_ABBR = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];

const T = {
  today: "hoje",
  on: "em",
  since: "desde",
  entriesOne: "lançamento",
  entriesMany: "lançamentos",
  noSpending: "sem gastos",
  day: "dia",
  aboveAverage: "acima da média",
  belowAverage: "abaixo da média",
  noAverage: "sem meses anteriores para comparar",
  overCeiling: "passou",
  ofCeiling: "do teto",
  untilCeiling: "restam",
  untilCeilingTail: "até o teto",
  noCeiling: "sem metas de orçamento",
  crossed: "passou do teto no dia",
  ceiling: "Teto",
  averageOf: "Média",
  spentIn: "gasto em",
  daysWord: "dias",
  ofIncomeSaved: "da renda poupados",
  noIncome: "sem receitas no mês",
  firstMonth: "primeiro mês com dados",
  versus: "em relação a",
  balanceOf: "saldo de",
  incomeLabel: "Entradas",
  expenseLabel: "Saídas",
  ofTotal: "do total",
  rest: "Demais categorias",
  thousand: "mil",
};

// ───────────────────────────── pure maths (no DOM) ─────────────────────────────

export const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
export const lerp = (a, b, t) => a + (b - a) * t;

/** Interpolates two vectors; a missing slot in `a` takes the target. */
export function lerpArray(a, b, t) {
  const out = new Array(b.length);
  for (let i = 0; i < b.length; i++) out[i] = lerp(i < a.length ? a[i] : b[i], b[i], t);
  return out;
}

export function sameArray(a, b) {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

const DAY = 86400000;

/** Points of the window of `days` ending at the last point; always at least two when they exist. */
export function sliceByDays(points, days) {
  if (points.length < 3 || !Number.isFinite(days)) return points.slice();
  const since = points[points.length - 1].t - days * DAY;
  let i = 0;
  while (i < points.length && points[i].t < since) i++;
  return points.slice(Math.min(i, points.length - 2));
}

/** Resamples in `n` instants equally spaced IN TIME (linear between records), so any two curves interpolate. */
export function resampleByTime(points, n) {
  const out = new Array(n);
  const last = points.length - 1;
  if (last < 0) return out.fill(0);
  if (last === 0) return out.fill(points[0].v);
  const t0 = points[0].t;
  const span = points[last].t - t0;
  let k = 0;
  for (let j = 0; j < n; j++) {
    const t = t0 + (span * j) / (n - 1);
    while (k < last - 1 && points[k + 1].t < t) k++;
    const a = points[k];
    const b = points[k + 1];
    const dt = b.t - a.t;
    out[j] = dt <= 0 ? b.v : lerp(a.v, b.v, clamp((t - a.t) / dt, 0, 1));
  }
  return out;
}

/** Value of the series at any instant (linear between records, clamped at the ends). */
export function valueAtTime(points, t) {
  const last = points.length - 1;
  if (last < 0) return 0;
  if (t <= points[0].t) return points[0].v;
  if (t >= points[last].t) return points[last].v;
  let lo = 0;
  let hi = last;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (points[mid].t <= t) lo = mid;
    else hi = mid;
  }
  const a = points[lo];
  const b = points[hi];
  return lerp(a.v, b.v, (t - a.t) / Math.max(1, b.t - a.t));
}

/** Fritsch–Carlson monotone tangents for equidistant samples: no false waves around a step. */
export function monotoneTangents(y) {
  const n = y.length;
  if (n < 2) return new Array(n).fill(0);
  const d = new Array(n - 1);
  for (let i = 0; i < n - 1; i++) d[i] = y[i + 1] - y[i];
  const m = new Array(n);
  m[0] = d[0];
  m[n - 1] = d[n - 2];
  for (let i = 1; i < n - 1; i++) m[i] = d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (d[i] === 0) {
      m[i] = 0;
      m[i + 1] = 0;
      continue;
    }
    const a = m[i] / d[i];
    const b = m[i + 1] / d[i];
    const s = a * a + b * b;
    if (s > 9) {
      const k = 3 / Math.sqrt(s);
      m[i] = k * a * d[i];
      m[i + 1] = k * b * d[i];
    }
  }
  return m;
}

/** SVG path (cubic Béziers) through the points with the given tangents. */
export function monotonePath(xs, ys, m) {
  if (xs.length === 0) return "";
  let d = "M" + xs[0].toFixed(2) + " " + ys[0].toFixed(2);
  for (let i = 0; i < xs.length - 1; i++) {
    const dx = (xs[i + 1] - xs[i]) / 3;
    d +=
      "C" + (xs[i] + dx).toFixed(2) + " " + (ys[i] + m[i] / 3).toFixed(2) + " " +
      (xs[i + 1] - dx).toFixed(2) + " " + (ys[i + 1] - m[i + 1] / 3).toFixed(2) + " " +
      xs[i + 1].toFixed(2) + " " + ys[i + 1].toFixed(2);
  }
  return d;
}

/** The same curve at a fractional index `p`: the scrub marker sits exactly on the stroke. */
export function hermiteAt(y, m, p) {
  const last = y.length - 1;
  if (last < 1) return y.length ? y[0] : 0;
  const k = Math.min(last - 1, Math.max(0, Math.floor(p)));
  const t = p - k;
  const t2 = t * t;
  const t3 = t2 * t;
  return (
    (2 * t3 - 3 * t2 + 1) * y[k] + (t3 - 2 * t2 + t) * m[k] +
    (-2 * t3 + 3 * t2) * y[k + 1] + (t3 - t2) * m[k + 1]
  );
}

/** Candidate steps in reais; times 100 they are cents. */
const STEPS_BRL = [100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 25000, 50000, 100000, 250000, 500000, 1000000];

/** Domain with room (10% below, 12% above) and at most `maxTicks` round reference lines. In cents. */
export function niceDomain(values, maxTicks = 3) {
  let mn = Infinity;
  let mx = -Infinity;
  for (const v of values) {
    if (v < mn) mn = v;
    if (v > mx) mx = v;
  }
  if (!Number.isFinite(mn)) return { min: 0, max: 1, ticks: [] };
  const span = Math.max(mx - mn, Math.abs(mx) * 0.02, 100);
  const lo = mn - span * 0.1;
  const hi = mx + span * 0.12;
  const a = lo + (hi - lo) * 0.06;
  const b = hi - (hi - lo) * 0.1;
  let ticks = [];
  for (const brl of STEPS_BRL) {
    const step = brl * 100;
    const found = [];
    for (let v = Math.ceil(a / step) * step; v <= b; v += step) found.push(v);
    ticks = found;
    if (found.length <= maxTicks) break;
  }
  return { min: lo, max: hi, ticks: ticks.length <= maxTicks ? ticks : [] };
}

/** Zero-based axis for bars and cumulative lines: a round top and at most `maxTicks` lines below it. */
export function niceTicks(maxCents, maxTicks = 3) {
  const target = Math.max(maxCents, 100);
  for (const brl of STEPS_BRL) {
    const step = brl * 100;
    const count = Math.ceil(target / step);
    if (count <= maxTicks + 0.2 || brl === STEPS_BRL[STEPS_BRL.length - 1]) {
      const top = Math.max(1, count) * step;
      const ticks = [];
      for (let v = step; v < top; v += step) ticks.push(v);
      return { top, step, ticks: ticks.slice(0, maxTicks) };
    }
  }
  return { top: target, step: target, ticks: [] };
}

/** The biggest round step (cents) that fits under `maxCents`: one reference line for the bars. */
export function niceStep(maxCents) {
  let best = STEPS_BRL[0] * 100;
  for (const brl of STEPS_BRL) if (brl * 100 <= maxCents * 0.9) best = brl * 100;
  return best;
}

/** Closed-form step response of a damped spring (ζ < 1): rises to ~1 without a visible bounce. */
export function springProgress(seconds, zeta = 0.88, omega = 11) {
  if (seconds <= 0) return 0;
  const wd = omega * Math.sqrt(1 - zeta * zeta);
  return (
    1 - Math.exp(-zeta * omega * seconds) * (Math.cos(wd * seconds) + ((zeta * omega) / wd) * Math.sin(wd * seconds))
  );
}

/** Four X labels (start, 1/3, 2/3, end) with their horizontal fraction. */
export function xAxisStops(startT, endT) {
  return [0, 1 / 3, 2 / 3, 1].map((fraction) => ({ fraction, t: startT + (endT - startT) * fraction }));
}

/** Step-after path: each day's spending is a jump on that day, flat until the next one. */
export function stepPath(xs, ys, y0) {
  if (xs.length === 0) return "";
  let d = "M" + xs[0].toFixed(2) + " " + y0.toFixed(2) + "V" + ys[0].toFixed(2);
  for (let i = 1; i < xs.length; i++) d += "H" + xs[i].toFixed(2) + "V" + ys[i].toFixed(2);
  return d;
}

export const math = {
  clamp, lerp, lerpArray, sameArray, sliceByDays, resampleByTime, valueAtTime, monotoneTangents,
  monotonePath, hermiteAt, niceDomain, niceTicks, niceStep, springProgress, xAxisStops, stepPath,
};

// ───────────────────────────── pt-BR formatting ─────────────────────────────

const two = (n) => String(n).padStart(2, "0");

/** 9511846 → "95.118,46"; negative → "−…" (U+2212). With `sign`, positive gets "+". One formatter: fp-money.js. */
export function cents(value, opts = {}) {
  return formatAmount(Math.round(value), { sign: opts.sign ? "always" : "auto" });
}

/** Amount with the currency symbol of the page ("R$ 95.118,46"), for the accessible texts. */
const money = (value) => formatCurrency(Math.round(value));

/** 4.43 → "4,4" (no "%": the caller decides). */
export function percent(x) {
  return (Math.round(Math.abs(x) * 10) / 10).toFixed(1).replace(".", ",").replace(/,0$/, "");
}

/** Axis label: 9250000 cents → "92,5 mil" (thousands of reais). */
export function thousands(value) {
  const x = Math.round((value / 100000) * 10) / 10;
  return String(x).replace(".", ",") + " " + T.thousand;
}

export const shortDate = (d) => two(d.getDate()) + " " + MONTH_ABBR[d.getMonth()];
export const monthYear = (d) => MONTH_ABBR[d.getMonth()] + " " + String(d.getFullYear()).slice(2);
export const monthYearFull = (d) => MONTH_ABBR[d.getMonth()] + " " + d.getFullYear();
export const fullDate = (d) => shortDate(d) + " " + d.getFullYear();
export const longDate = (d) => WEEKDAY_ABBR[d.getDay()] + ", " + fullDate(d);
const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

/** ISO "YYYY-MM-DD" as a LOCAL day (no time-zone shift). */
export function toEpoch(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).getTime() : new Date(iso).getTime();
}

export const fmt = { cents, signedCents: (v) => cents(v, { sign: true }), percent, thousands, shortDate, monthYear, fullDate, longDate, toEpoch };

// ───────────────────────────── small DOM helpers ─────────────────────────────

const SVG_NS = "http://www.w3.org/2000/svg";

function h(tag, props, ...children) {
  const node = document.createElement(tag);
  if (props) {
    for (const key of Object.keys(props)) {
      const value = props[key];
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "style") node.setAttribute("style", value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

function s(tag, props) {
  const node = document.createElementNS(SVG_NS, tag);
  if (props) for (const key of Object.keys(props)) if (props[key] != null) node.setAttribute(key, String(props[key]));
  return node;
}

const reduced = () => typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

let uid = 0;
const nextId = (prefix) => prefix + "-" + ++uid;

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

/** Springs between two vectors. A new target starts from where the curve is now (no jump). */
function createTween(onFrame, opts = {}) {
  const zeta = opts.zeta || 0.88;
  const omega = opts.omega || 11;
  const maxSeconds = opts.maxSeconds || 0.95;
  const alive = opts.alive || (() => true);
  let from = [];
  let to = [];
  let p = 1;
  let raf = 0;
  const current = () => (p >= 1 ? to : lerpArray(from, to, p));
  return {
    get values() { return current(); },
    get from() { return from; },
    get to() { return to; },
    get progress() { return p; },
    get settled() { return p >= 1; },
    set(target, immediate) {
      if (sameArray(to, target) && p >= 1) return;
      from = to.length === target.length ? current() : target;
      to = target;
      cancelAnimationFrame(raf);
      if (immediate || reduced() || from === target) {
        p = 1;
        onFrame();
        return;
      }
      p = 0;
      const t0 = performance.now();
      const tick = (now) => {
        if (!alive()) return; // the chart left the page: stop the loop (nobody is left to see it)
        const t = Math.max(0, (now - t0) / 1000);
        const e = springProgress(t, zeta, omega);
        const done = t > maxSeconds || (t > 0.25 && Math.abs(1 - e) < 0.0008);
        p = done ? 1 : e;
        onFrame();
        if (!done) raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    },
    stop() { cancelAnimationFrame(raf); },
  };
}

// ───────────────────────────── base: fetch, states, size ─────────────────────────────

// One ResizeObserver for every chart: a callback batch reads all the boxes first and only then lets the charts rebuild (a rebuild writes,
// and a read after a write forces a layout). A chart whose box left the page is let go of here.
const chartOf = new WeakMap();
const sizeObserver =
  typeof ResizeObserver === "undefined"
    ? null
    : new ResizeObserver((entries) => {
        const sizes = [];
        for (const entry of entries) {
          const chart = chartOf.get(entry.target);
          if (!chart) continue;
          if (!entry.target.isConnected) {
            sizeObserver.unobserve(entry.target);
            chartOf.delete(entry.target);
            continue;
          }
          sizes.push([chart, Math.round(entry.target.clientWidth), Math.round(entry.target.clientHeight)]);
        }
        for (const [chart, w, hgt] of sizes) {
          if (w > 0 && (w !== chart.width || hgt !== chart.height)) {
            chart.width = w;
            chart.height = hgt;
            chart.onResize();
          }
        }
      });

// Cached layout boxes (the scrub layer's left edge and width) stay valid until the page scrolls or resizes: the epoch moves then.
let layoutEpoch = 0;
const bumpLayout = () => { layoutEpoch++; };
window.addEventListener("scroll", bumpLayout, { passive: true, capture: true });
window.addEventListener("resize", bumpLayout, { passive: true });

const PLOT_TOP = 34; // band reserved for the tooltip: it never covers data
const PLOT_PAD_X = 8;
const MIN_POINTS = 8;
const SAMPLES = 120;

class Chart {
  constructor(root) {
    this.root = root;
    this.src = root.dataset.src || "";
    this.box = root.querySelector('[data-fc="plot"]');
    this.data = null;
    this.state = "loading";
    this.width = 0;
    this.height = 0;
    this.observer = false;
    this.rectCache = null;
    root.classList.add("fc");
  }

  qs(name) { return this.root.querySelector('[data-fc="' + name + '"]'); }

  /** Appends a clone of <template data-tpl="name"> (the states written in Jinja) to the plot box. */
  appendTemplate(name) {
    const tpl = this.root.querySelector('template[data-tpl="' + name + '"]');
    if (!tpl) return null;
    const node = tpl.content.firstElementChild.cloneNode(true);
    this.box.append(node);
    return node;
  }

  showTemplate(name) {
    clear(this.box);
    return this.appendTemplate(name);
  }

  setBusy(on) { this.box.setAttribute("aria-busy", on ? "true" : "false"); }

  async load() {
    this.setBusy(true);
    try {
      const response = await fetch(this.src, { headers: { Accept: "application/json" }, credentials: "same-origin" });
      if (!response.ok) throw new Error(String(response.status));
      this.data = await response.json();
    } catch (error) {
      this.fail(error);
      return;
    }
    this.measure(); // before the first write below: the box height is fixed by CSS, so this is the only read of the mount
    this.setBusy(false);
    this.watchSize();
    this.start();
  }

  fail() {
    this.state = "error";
    this.root.classList.remove("is-ready");
    this.setBusy(false);
    const node = this.showTemplate("error");
    if (node) {
      const retry = node.querySelector("[data-fc-retry]");
      if (retry) retry.addEventListener("click", () => this.retry());
    }
  }

  retry() {
    this.root.classList.remove("is-ready");
    clear(this.box);
    const sk = this.root.querySelector('template[data-tpl="skeleton"]');
    if (sk) this.box.append(sk.content.cloneNode(true));
    this.load();
  }

  /** Measures the box (its height is fixed by CSS, so measuring never moves the page). */
  watchSize() {
    if (this.observer || !sizeObserver) return;
    this.observer = true;
    chartOf.set(this.box, this);
    sizeObserver.observe(this.box);
  }

  measure() {
    this.width = Math.round(this.box.clientWidth);
    this.height = Math.round(this.box.clientHeight);
  }

  onResize() {}
  start() {}

  /** Left edge and width of the scrub layer, read once and reused until the pointer re-enters, the page scrolls or it resizes. */
  rectOf(el) {
    const c = this.rectCache;
    if (c && c.el === el && c.epoch === layoutEpoch) return c.rect;
    const r = el.getBoundingClientRect();
    const rect = { left: r.left, width: r.width };
    this.rectCache = { el, epoch: layoutEpoch, rect };
    return rect;
  }

  /** Pointer position as a fraction of the drawing width. */
  fractionOf(clientX, el) {
    const r = this.rectOf(el);
    return clamp((clientX - r.left - PLOT_PAD_X) / Math.max(1, r.width - 2 * PLOT_PAD_X), 0, 1);
  }

  /** Shared slider layer: ARIA slider over the plot, with pointer (mouse hover, touch drag) and keyboard. */
  makeHit(label, handlers) {
    const hit = h("div", {
      class: "fc-hit", role: "slider", tabindex: "0", "aria-label": label, "aria-orientation": "horizontal",
    });
    const forget = () => { this.rectCache = null; };
    hit.addEventListener("pointerenter", forget);
    hit.addEventListener("pointermove", (e) => handlers.move(e.clientX, hit, e));
    hit.addEventListener("pointerdown", (e) => {
      forget();
      try { hit.setPointerCapture(e.pointerId); } catch (_) { /* synthetic events */ }
      handlers.press(true);
      handlers.move(e.clientX, hit, e);
    });
    hit.addEventListener("pointerup", (e) => {
      handlers.press(false);
      if (e.pointerType !== "mouse") handlers.release();
    });
    hit.addEventListener("pointercancel", () => { forget(); handlers.release(); });
    hit.addEventListener("pointerleave", (e) => {
      forget();
      if (e.pointerType === "mouse" && !(hit.hasPointerCapture && hit.hasPointerCapture(e.pointerId))) handlers.release();
    });
    hit.addEventListener("blur", () => handlers.release());
    hit.addEventListener("keydown", (e) => {
      const many = e.shiftKey ? 7 : 1;
      switch (e.key) {
        case "ArrowLeft": case "ArrowDown": handlers.nudge(-many); break;
        case "ArrowRight": case "ArrowUp": handlers.nudge(many); break;
        case "PageDown": handlers.nudge(-30); break;
        case "PageUp": handlers.nudge(30); break;
        case "Home": handlers.jump(0); break;
        case "End": handlers.jump(1); break;
        case "Escape": handlers.release(); return;
        default: return;
      }
      e.preventDefault();
    });
    return hit;
  }

  /** Floating glass note; `x` is the pointer abscissa in the box, `html` pieces are text nodes (no markup). */
  placeTip(tip, x, visible) {
    // a note's width depends only on its text: measure each text once (the tip is rebuilt on resize, which drops the cache)
    const text = tip.textContent;
    const widths = tip.__widths || (tip.__widths = new Map());
    let w = widths.get(text);
    if (w === undefined) {
      w = tip.offsetWidth;
      if (widths.size > 400) widths.clear();
      if (w) widths.set(text, w);
    }
    w = w || 150;
    tip.style.opacity = visible ? "1" : "0";
    const left = clamp(x, w / 2 + 2, Math.max(w / 2 + 2, this.width - w / 2 - 2));
    tip.style.left = left + "px";
  }
}

function setChip(el, text, tone, direction) {
  if (!el) return;
  el.className = "dl dl-chip dl-" + tone + (direction === "up" ? " dl-up" : direction === "down" ? " dl-dn" : "");
  el.textContent = text;
}

/** The card's big figure as a rolling odometer (static/ui.js, same markup as templates/_ui.html). The symbol stays in .fc-cur;
 *  the odometer keeps the full "R$ …" text as its accessible name. Falls back to plain text when ui.js is not loaded. */
function setFigure(el, value, negativeRed) {
  if (!el) return;
  const rolling = window.FinUI && window.FinUI.odometer;
  if (rolling) {
    const total = Math.round(value);
    let od = el.querySelector(":scope > .od");
    if (!od) {
      el.textContent = "";
      od = h("span", { class: "od od-md", role: "img" });
      od.dataset.odometer = "0";
      od.dataset.odPrefix = "0";
      el.append(od);
      if (rolling.armOnView) rolling.armOnView(od); // below the fold: roll when it scrolls into view
    }
    // the server already printed this value (templates/_charts.html): leave the first-paint roll running
    if (od.dataset.odometer !== String(total)) rolling.set(od, total);
    od.setAttribute("aria-label", money(total));
  } else {
    el.textContent = cents(value);
  }
  el.classList.toggle("is-negative", Boolean(negativeRed) && value < 0);
  el.classList.remove("sk-inline");
  el.style.width = "";
  el.style.height = "";
}

function gridPath(ticks, yOf, width) {
  return ticks.map((v) => "M" + PLOT_PAD_X + " " + yOf(v).toFixed(1) + "H" + (width - PLOT_PAD_X).toFixed(1)).join("");
}

function marker(cls) { return h("div", { class: cls, "aria-hidden": "true" }); }

/** Text labels that fade between two sets (period change): keyed spans positioned by `place(key)`. */
class LabelLayer {
  constructor(parent, cls) {
    this.parent = parent;
    this.cls = cls;
    this.nodes = new Map();
  }

  /** items: [{ key, text, left?, top?, shift? , opacity }] */
  update(items) {
    const seen = new Set();
    for (const item of items) {
      if (item.opacity < 0.002) continue;
      seen.add(item.key);
      let node = this.nodes.get(item.key);
      if (!node) {
        node = h("span", { class: this.cls, "aria-hidden": "true", text: item.text });
        this.parent.append(node);
        this.nodes.set(item.key, node);
      }
      if (item.left != null) node.style.left = item.left + "px";
      if (item.top != null) node.style.top = item.top + "px";
      node.style.transform = item.shift || "";
      node.style.opacity = String(item.opacity);
    }
    for (const [key, node] of this.nodes) {
      if (!seen.has(key)) {
        node.remove();
        this.nodes.delete(key);
      }
    }
  }
}

// ───────────────────────────── month pace ─────────────────────────────

class PaceChart extends Chart {
  constructor(root) {
    super(root);
    this.idx = null;
    this.pressed = false;
    this.load();
  }

  start() {
    const d = this.data;
    this.monthIndex = Number(d.month.slice(5, 7)) - 1;
    this.year = Number(d.month.slice(0, 4));
    const title = this.qs("title");
    if (title) title.textContent = (title.dataset.prefix || "") + " " + MONTH_FULL[this.monthIndex];
    this.root.classList.add("is-ready");
    this.fillLegend();
    if (d.total_cents === 0 || d.elapsed_days === 0) {
      this.state = "sparse";
      this.build(true);
    } else {
      this.state = "ready";
      this.build(false);
    }
    this.header();
  }

  fillLegend() {
    const d = this.data;
    const set = (name, text, show) => {
      const el = this.qs(name);
      if (!el) return;
      el.hidden = !show;
      const target = el.querySelector("[data-fc-text]");
      if (target && text != null) target.textContent = text;
    };
    set("legend-month", MONTH_FULL[this.monthIndex][0].toUpperCase() + MONTH_FULL[this.monthIndex].slice(1), true);
    const months = (d.average_months || []).map((m) => MONTH_ABBR[Number(m.slice(5, 7)) - 1]);
    set("legend-average", months.length ? (months.length > 1 ? months[0] + "–" + months[months.length - 1] : months[0]) : null, Boolean(d.average_cents && d.average_cents.length));
    set("legend-ceiling", d.ceiling_cents != null ? cents(d.ceiling_cents) : null, d.ceiling_cents != null);
  }

  onResize() {
    if (this.state === "loading" || this.state === "error") return;
    this.build(this.state === "sparse");
    this.header();
  }

  build(sparse) {
    const d = this.data;
    const w = this.width;
    if (!w) return;
    clear(this.box);
    this.plotH = Math.max(40, this.height - PLOT_TOP - 30);
    const days = d.days;
    const cum = d.cumulative_cents;
    const avg = d.average_cents && d.average_cents.length ? d.average_cents : null;
    const ceiling = d.ceiling_cents;
    const maxVal = Math.max(cum.length ? cum[cum.length - 1] : 0, avg ? avg[avg.length - 1] : 0, ceiling || 0, 1);
    const axis = niceTicks(maxVal * 1.04, 3);
    this.axisTop = axis.top;
    const inner = w - 2 * PLOT_PAD_X;
    const xOf = (i) => PLOT_PAD_X + (i / Math.max(1, days - 1)) * inner;
    const yOf = (v) => PLOT_TOP + (1 - v / axis.top) * this.plotH;
    this.xOf = xOf;
    this.yOf = yOf;
    this.avgYs = avg ? avg.map(yOf) : [];
    this.cumYs = cum.map(yOf);
    const layer = h("div", { class: "fc-layer " + (sparse ? "fade" : "wipe") });
    const gid = nextId("fc-fill");
    const svg = s("svg", { width: w, height: this.height, viewBox: "0 0 " + w + " " + this.height, "aria-hidden": "true", class: "fc-svg" });
    const defs = s("defs");
    const grad = s("linearGradient", { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 });
    grad.append(s("stop", { offset: 0, class: "fc-stop-top fc-stop-out" }), s("stop", { offset: 1, class: "fc-stop-bottom" }));
    defs.append(grad);
    svg.append(defs, s("path", { class: "fc-grid", d: gridPath(axis.ticks, yOf, w) }));
    if (ceiling != null) svg.append(s("path", { class: "fc-ceiling", d: "M" + PLOT_PAD_X + " " + yOf(ceiling).toFixed(1) + "H" + (w - PLOT_PAD_X) }));
    if (cum.length && !sparse) {
      const xs = cum.map((_, i) => xOf(i));
      const step = stepPath(xs, this.cumYs, yOf(0));
      svg.append(s("path", { fill: "url(#" + gid + ")", stroke: "none", d: step + "L" + xs[xs.length - 1].toFixed(2) + " " + yOf(0).toFixed(2) + "L" + xs[0].toFixed(2) + " " + yOf(0).toFixed(2) + "Z" }));
    }
    if (avg) svg.append(s("path", { class: "fc-avg", d: monotonePath(avg.map((_, i) => xOf(i)), this.avgYs, monotoneTangents(this.avgYs)) }));
    if (cum.length && !sparse) svg.append(s("path", { class: "fc-line fc-line-out", d: stepPath(cum.map((_, i) => xOf(i)), this.cumYs, yOf(0)) }));
    layer.append(svg);
    const ylabels = new LabelLayer(layer, "fc-ylabel");
    ylabels.update(axis.ticks.map((v) => ({ key: "y" + v, text: thousands(v), top: yOf(v) - 17, opacity: 1 })));
    const xlabels = new LabelLayer(layer, "fc-xlabel");
    xlabels.update(
      [0, 9, 19, days - 1].filter((n, k, all) => all.indexOf(n) === k).map((n, k, all) => ({
        key: "x" + n, text: k === 0 ? T.day + " 1" : String(n + 1), left: (n / Math.max(1, days - 1)) * w,
        shift: k === 0 ? "translateX(0)" : k === all.length - 1 ? "translateX(-100%)" : "translateX(-50%)", opacity: 1,
      })),
    );
    if (ceiling != null) {
      layer.append(h("span", { class: "fc-ceil-label", "aria-hidden": "true", text: T.ceiling, style: "top:" + (yOf(ceiling) - 18) + "px" }));
    }
    this.box.append(layer);
    if (sparse) {
      this.guide = this.halo = this.dotEl = this.avgDot = this.tip = null;
      const live = h("div", { class: "fc-dot is-on live", "aria-hidden": "true" });
      live.style.transform = "translate(" + (xOf(0) - 6.5) + "px," + (yOf(0) - 6.5) + "px)";
      this.box.append(live);
      const node = this.appendTemplate("sparse");
      if (node) {
        const hint = node.querySelector("[data-fc-avg-hint]");
        if (hint) hint.hidden = !avg;
      }
      return;
    }
    if (d.crossed_day != null && ceiling != null) {
      const i = d.crossed_day - 1;
      const x = xOf(i);
      const y = this.cumYs[i];
      const dot = h("div", { class: "fc-cross-dot pop", "aria-hidden": "true" });
      dot.style.left = x + "px";
      dot.style.top = y + "px";
      const label = h("span", { class: "fc-cross-label fade", "aria-hidden": "true", text: T.crossed + " " + d.crossed_day });
      label.style.left = x + "px";
      label.style.top = y + "px";
      this.box.append(dot, label);
    }
    this.guide = marker("fc-guide");
    this.guide.style.top = PLOT_TOP + "px";
    this.guide.style.height = this.plotH + "px";
    this.avgDot = marker("fc-dot fc-dot-avg");
    this.halo = marker("fc-halo fc-halo-out");
    this.dotEl = marker("fc-dot fc-dot-out");
    this.tip = h("div", { class: "fc-tip dv-tip", "aria-hidden": "true" });
    this.tipDate = h("span", { class: "fc-tip-soft" });
    this.tipValue = h("span");
    this.tip.append(this.tipDate, h("span", { class: "fc-tip-sep", text: "·" }), this.tipValue);
    this.hit = this.makeHit(this.root.dataset.sliderLabel || "Gasto acumulado no mês", {
      move: (x, el) => this.scrubTo(Math.round(this.fractionOf(x, el) * (days - 1))),
      press: (on) => { this.pressed = on; this.cursor(); },
      release: () => { this.idx = null; this.pressed = false; this.cursor(); this.header(); },
      nudge: (n) => this.scrubTo((this.idx == null ? this.lastIndex() : this.idx) + n),
      jump: (f) => this.scrubTo(f === 0 ? 0 : this.lastIndex()),
    });
    const desc = this.qs("desc");
    if (desc) this.hit.setAttribute("aria-describedby", desc.id);
    this.box.append(this.guide, this.avgDot, this.halo, this.dotEl, this.tip, this.hit);
    this.cursor();
  }

  lastIndex() { return Math.max(0, this.data.elapsed_days - 1); }

  scrubTo(i) {
    this.idx = clamp(i, 0, this.lastIndex());
    this.cursor();
    this.header();
  }

  dateOf(i) { return new Date(this.year, this.monthIndex, i + 1); }

  header() {
    const d = this.data;
    if (!d) return;
    const last = this.lastIndex();
    const i = this.idx != null ? Math.min(this.idx, last) : last;
    const cum = d.cumulative_cents;
    const value = cum.length ? cum[i] : 0;
    const avg = d.average_cents && d.average_cents.length ? d.average_cents : null;
    setFigure(this.qs("value"), value);
    const sub = this.qs("sub");
    const date = this.dateOf(i);
    if (sub) {
      if (this.state === "sparse") sub.textContent = T.noSpending;
      else if (this.idx != null) sub.textContent = T.day + " " + (i + 1) + " · " + WEEKDAY_ABBR[date.getDay()] + ", " + shortDate(date);
      else sub.textContent = T.spentIn + " " + d.elapsed_days + " " + T.daysWord + " · " + d.entry_count + " " + (d.entry_count === 1 ? T.entriesOne : T.entriesMany);
    }
    if (this.state === "sparse") {
      setChip(this.qs("chip-average"), T.day + " " + Math.max(1, d.elapsed_days) + " de " + d.days, "neu");
    } else if (avg) {
      const diff = value - avg[Math.min(i, avg.length - 1)];
      setChip(this.qs("chip-average"), cents(Math.abs(diff)) + " " + (diff > 0 ? T.aboveAverage : T.belowAverage), diff > 0 ? "neg" : "pos", diff > 0 ? "up" : "down");
    } else {
      setChip(this.qs("chip-average"), T.noAverage, "neu");
    }
    if (d.ceiling_cents != null) {
      const diff = value - d.ceiling_cents;
      setChip(
        this.qs("chip-ceiling"),
        diff > 0 ? T.overCeiling + " " + cents(diff) + " " + T.ofCeiling : T.untilCeiling + " " + cents(-diff) + " " + T.untilCeilingTail,
        diff > 0 ? "neg" : "neu", diff > 0 ? "up" : null,
      );
    } else {
      setChip(this.qs("chip-ceiling"), T.noCeiling, "neu");
    }
    const desc = this.qs("desc");
    if (desc) {
      const pre = desc.dataset.prefix || "";
      desc.textContent = pre + " " + MONTH_FULL[this.monthIndex] + ": " + money(d.total_cents) +
        (avg ? ". " + T.averageOf + ": " + money(avg[avg.length - 1]) : "") +
        (d.ceiling_cents != null ? ". " + T.ceiling + ": " + money(d.ceiling_cents) + (d.crossed_day ? ", " + T.crossed + " " + d.crossed_day : "") : "") + ".";
    }
    if (this.hit) {
      this.hit.setAttribute("aria-valuemin", "0");
      this.hit.setAttribute("aria-valuemax", String(last));
      this.hit.setAttribute("aria-valuenow", String(i));
      this.hit.setAttribute("aria-valuetext", T.day + " " + (i + 1) + ", " + WEEKDAY_ABBR[date.getDay()] + " " + shortDate(date) + ": " + money(value));
    }
  }

  cursor() {
    if (this.state !== "ready" || !this.guide) return;
    const d = this.data;
    const active = this.idx != null;
    const i = active ? this.idx : this.lastIndex();
    const x = this.xOf(i);
    const y = this.cumYs[i];
    this.guide.style.left = x - 0.5 + "px";
    this.guide.style.opacity = active ? "1" : "0";
    const scale = this.pressed ? 1.15 : 1;
    this.halo.style.transform = "translate3d(" + x + "px," + y + "px,0) scale(" + (active ? scale : 0.4) + ")";
    this.dotEl.style.transform = "translate3d(" + x + "px," + y + "px,0) scale(" + (active ? (this.pressed ? 1.18 : 1) : 0.4) + ")";
    this.halo.style.opacity = this.dotEl.style.opacity = active ? "1" : "0";
    const avg = d.average_cents && d.average_cents.length ? this.avgYs : null;
    if (avg) {
      this.avgDot.style.transform = "translate3d(" + x + "px," + avg[Math.min(i, avg.length - 1)] + "px,0)";
      this.avgDot.style.opacity = active ? "1" : "0";
    } else {
      this.avgDot.style.opacity = "0";
    }
    if (active) {
      const date = this.dateOf(i);
      this.tipDate.textContent = WEEKDAY_ABBR[date.getDay()] + " " + shortDate(date);
      const n = d.daily_counts[i];
      this.tipValue.textContent = d.daily_cents[i] ? cents(d.daily_cents[i]) + " em " + n + " " + (n === 1 ? T.entriesOne : T.entriesMany) : T.noSpending;
    }
    this.placeTip(this.tip, x, active);
  }
}

// ───────────────────────────── cash flow ─────────────────────────────

class FlowChart extends Chart {
  constructor(root) {
    super(root);
    this.idx = null;
    this.load();
  }

  start() {
    const d = this.data;
    this.months = d.months;
    this.year = d.year;
    this.n = Math.max(1, d.months.length);
    this.first = d.months.findIndex((m) => m.income_cents || m.expenses_cents);
    if (this.first < 0) this.first = this.n;
    const real = this.n - this.first;
    this.root.classList.add("is-ready");
    if (!d.months.length || real < 3) {
      this.state = "sparse";
    } else {
      this.state = "ready";
    }
    this.build();
    this.header();
  }

  onResize() {
    if (this.state === "loading" || this.state === "error") return;
    this.build();
    this.header();
  }

  selected() {
    if (this.idx != null && this.idx >= this.first) return this.idx;
    return this.n - 1;
  }

  build() {
    const w = this.width;
    if (!w || !this.months.length) {
      clear(this.box);
      if (this.state === "sparse") this.showSparseOnly();
      return;
    }
    clear(this.box);
    const BOT = 46;
    const plotH = Math.max(60, this.height - PLOT_TOP - BOT);
    const hup = Math.round(plotH * 0.56);
    const hdn = plotH - hup;
    const maxIncome = Math.max(...this.months.map((m) => m.income_cents), 1);
    const maxExpense = Math.max(...this.months.map((m) => Math.max(m.expenses_cents, 0)), 1);
    const scale = Math.min(hup / (maxIncome * 1.08), hdn / (maxExpense * 1.08));
    this.scale = scale;
    const n = this.n;
    const gap = 8;
    const y0 = PLOT_TOP + hup;
    const top = niceStep(Math.floor(hup / scale));
    const grid = h("div", { class: "fc-flow" });
    grid.style.top = PLOT_TOP + "px";
    grid.style.height = plotH + "px";
    grid.style.gridTemplateColumns = "repeat(" + n + ", minmax(0, 1fr))";
    grid.style.gap = gap + "px";
    this.band = h("div", { class: "fc-band", "aria-hidden": "true" });
    this.band.style.top = PLOT_TOP + "px";
    this.band.style.height = plotH + "px";
    this.box.append(
      h("div", { class: "fc-flow-line", style: "top:" + (y0 - top * scale) + "px", "aria-hidden": "true" }),
      h("div", { class: "fc-flow-line", style: "top:" + (y0 + top * scale) + "px", "aria-hidden": "true" }),
      this.band,
    );
    this.cols = [];
    this.labels = [];
    const labelRow = h("div", { class: "fc-flow-labels" });
    labelRow.style.height = BOT + "px";
    labelRow.style.gridTemplateColumns = "repeat(" + n + ", minmax(0, 1fr))";
    labelRow.style.gap = gap + "px";
    this.months.forEach((m, i) => {
      const realCol = i >= this.first;
      const col = h("div", { class: "fc-col" });
      if (realCol) {
        const up = h("div", { class: "fc-bar fc-bar-in grow" });
        const dn = h("div", { class: "fc-bar fc-bar-out grow" });
        up.style.height = Math.max(0, m.income_cents * scale) + "px";
        up.style.bottom = hdn + 1 + "px";
        dn.style.height = Math.max(0, m.expenses_cents * scale) + "px";
        dn.style.top = hup + 1 + "px";
        up.style.animationDelay = dn.style.animationDelay = 60 + (i - this.first) * 55 + "ms";
        col.append(up, dn);
      } else {
        const up = h("div", { class: "fc-ghost-bar fc-ghost-up" });
        const dn = h("div", { class: "fc-ghost-bar fc-ghost-dn" });
        up.style.height = Math.round(hup * 0.36) + "px";
        up.style.bottom = hdn + 1 + "px";
        dn.style.height = Math.round(hdn * 0.26) + "px";
        dn.style.top = hup + 1 + "px";
        col.append(up, dn);
      }
      grid.append(col);
      this.cols.push(col);
      const net = m.income_cents - m.expenses_cents;
      const label = h("div", { class: "fc-flow-label" },
        h("span", { class: "fc-flow-month", text: MONTH_ABBR[Number(m.month.slice(5, 7)) - 1] }),
        h("span", { class: "fc-flow-net " + (net >= 0 ? "is-pos" : "is-neg"), text: realCol ? (net >= 0 ? "+" : "−") + (Math.round(Math.abs(net) / 10000) / 10).toFixed(1).replace(".", ",") : "" }));
      labelRow.append(label);
      this.labels.push(label);
    });
    this.box.append(grid, h("div", { class: "fc-flow-zero", style: "top:" + y0 + "px", "aria-hidden": "true" }));
    this.box.append(
      h("span", { class: "fc-ylabel", "aria-hidden": "true", style: "top:" + (y0 - top * scale - 16) + "px", text: thousands(top) }),
      h("span", { class: "fc-ylabel", "aria-hidden": "true", style: "top:" + (y0 + top * scale + 4) + "px", text: thousands(top) })
    );
    this.box.append(labelRow);
    this.colW = (w - (n - 1) * gap) / n;
    this.gap = gap;
    this.tip = h("div", { class: "fc-tip dv-tip", "aria-hidden": "true" });
    this.tipDate = h("span", { class: "fc-tip-soft" });
    this.tipValue = h("span");
    this.tip.append(this.tipDate, h("span", { class: "fc-tip-sep", text: "·" }), this.tipValue);
    this.hit = null;
    if (this.state === "sparse") {
      const node = this.appendTemplate("sparse");
      if (node) node.style.zIndex = "2";
    }
    this.hit = this.makeHit(this.root.dataset.sliderLabel || "Fluxo de caixa por mês", {
      move: (x, el) => {
        const r = this.rectOf(el);
        this.pick(Math.floor(((x - r.left) / Math.max(1, r.width)) * n));
      },
      press: () => {},
      release: () => { this.idx = null; this.refresh(); },
      nudge: (k) => this.pick((this.idx == null ? n - 1 : this.idx) + k),
      jump: (f) => this.pick(f === 0 ? this.first : n - 1),
    });
    if (this.state === "sparse") this.hit.style.display = "none";
    const desc = this.qs("desc");
    if (desc) this.hit.setAttribute("aria-describedby", desc.id);
    this.box.append(this.tip, this.hit);
    this.refresh();
  }

  showSparseOnly() {
    clear(this.box);
    this.appendTemplate("sparse");
  }

  pick(i) {
    this.idx = clamp(i, this.first, this.n - 1);
    this.refresh();
  }

  refresh() {
    this.header();
    if (!this.cols) return;
    const hovering = this.idx != null && this.state === "ready";
    this.cols.forEach((col, i) => { col.style.opacity = hovering && i !== this.idx ? "0.5" : "1"; });
    this.labels.forEach((l, i) => {
      l.style.opacity = hovering && i !== this.idx ? "0.55" : "1";
      l.classList.toggle("is-selected", i === this.selected() && this.state !== "sparse");
    });
    if (this.band) {
      const i = this.idx != null ? this.idx : 0;
      this.band.style.width = this.colW + "px";
      this.band.style.transform = "translateX(" + i * (this.colW + this.gap) + "px)";
      this.band.style.opacity = hovering ? "1" : "0";
    }
    if (this.tip) {
      const sel = this.selected();
      const m = this.months[sel];
      this.tipDate.textContent = MONTH_ABBR[Number(m.month.slice(5, 7)) - 1] + " " + this.year;
      this.tipValue.textContent = cents(m.income_cents) + " − " + cents(m.expenses_cents);
      this.placeTip(this.tip, sel * (this.colW + this.gap) + this.colW / 2, hovering);
    }
  }

  header() {
    const d = this.data;
    if (!d || !this.months.length) {
      setFigure(this.qs("value"), 0);
      return;
    }
    const sel = this.selected();
    const m = this.months[sel];
    const net = m.income_cents - m.expenses_cents;
    const monthName = MONTH_FULL[Number(m.month.slice(5, 7)) - 1];
    setFigure(this.qs("value"), net, true);
    const sub = this.qs("sub");
    if (sub) sub.textContent = T.balanceOf + " " + monthName;
    const rate = m.income_cents > 0 ? (net / m.income_cents) * 100 : null;
    setChip(this.qs("chip-rate"), rate == null ? T.noIncome : percent(rate) + "% " + T.ofIncomeSaved, rate == null ? "neu" : rate >= 25 ? "pos" : rate >= 10 ? "warn" : "neg");
    const prev = sel > this.first ? this.months[sel - 1] : null;
    if (prev) {
      const delta = net - (prev.income_cents - prev.expenses_cents);
      setChip(this.qs("chip-delta"), cents(delta, { sign: true }) + " " + T.versus + " " + MONTH_FULL[Number(prev.month.slice(5, 7)) - 1], delta >= 0 ? "pos" : "neg", delta >= 0 ? "up" : "down");
    } else {
      setChip(this.qs("chip-delta"), T.firstMonth, "neu");
    }
    const inc = this.qs("legend-in");
    const out = this.qs("legend-out");
    if (inc) inc.textContent = cents(m.income_cents);
    if (out) out.textContent = cents(m.expenses_cents);
    const desc = this.qs("desc");
    if (desc) {
      desc.textContent = (desc.dataset.prefix || "") + " " + monthName + " " + this.year + ": " + T.incomeLabel.toLowerCase() + " " +
        money(m.income_cents) + ", " + T.expenseLabel.toLowerCase() + " " + money(m.expenses_cents) + ", " + T.balanceOf + " " + money(net) + ".";
    }
    if (this.hit) {
      this.hit.setAttribute("aria-valuemin", String(this.first));
      this.hit.setAttribute("aria-valuemax", String(this.n - 1));
      this.hit.setAttribute("aria-valuenow", String(sel));
      this.hit.setAttribute("aria-valuetext", monthName + " " + this.year + ": " + T.incomeLabel.toLowerCase() + " " + money(m.income_cents) + ", " + T.expenseLabel.toLowerCase() + " " + money(m.expenses_cents) + ", " + T.balanceOf + " " + money(net));
    }
  }
}

// ───────────────────────────── categories (ring + list) ─────────────────────────────

class DonutChart extends Chart {
  constructor(root) {
    super(root);
    this.box = root.querySelector('[data-fc="ring"]');
    this.hover = null;
    this.pin = null;
    this.open = false;
    this.size = Number(root.dataset.size) || 220;
    this.load();
  }

  start() {
    const d = this.data;
    this.slices = d.slices.slice();
    if (d.rest) this.slices.push({ key: "__rest", name: T.rest, cents: d.rest.cents, count: d.rest.entries, color: "#C9D4CC" });
    this.total = d.total_cents;
    this.root.classList.add("is-ready");
    this.setBusy(false);
    this.defaultCenter = {
      label: this.root.dataset.centerLabel || "",
      sub: this.root.dataset.centerSub || "",
    };
    this.build();
    this.bindRows();
    this.refresh();
  }

  // The ring has a fixed size (data-size) and nothing is redrawn on resize: no box to measure, no observer.
  measure() {}
  watchSize() {}

  fail() {
    this.setBusy(false);
    clear(this.box);
    const msg = h("p", { class: "fc-donut-error", text: this.root.dataset.errorText || "" });
    this.box.append(msg);
  }

  build() {
    clear(this.box);
    const S = this.size;
    const SW = 20;
    const SWH = 25;
    const c = S / 2;
    const r = S / 2 - 14;
    const C = 2 * Math.PI * r;
    const GAP = 6;
    this.geo = { S, SW, SWH, c, r, C, GAP };
    const svg = s("svg", { viewBox: "0 0 " + S + " " + S, width: S, height: S, role: "img", class: "fc-ring", "aria-label": this.root.dataset.ringLabel || "" });
    svg.append(s("circle", { cx: c, cy: c, r: r, fill: "none", class: "fc-ring-track", "stroke-width": SW }));
    this.segs = [];
    let acc = 0;
    this.slices.forEach((slice, i) => {
      const f = this.total ? slice.cents / this.total : 0;
      const circle = s("circle", { cx: c, cy: c, r: r, fill: "none", class: "fc-seg fade", stroke: slice.color, "data-key": slice.key });
      circle.style.animationDelay = 60 + i * 80 + "ms";
      circle.style.transformOrigin = c + "px " + c + "px";
      slice.f = f;
      slice.start = acc;
      acc += f;
      circle.addEventListener("pointermove", () => this.setHover(slice.key));
      circle.addEventListener("pointerleave", () => this.setHover(null));
      circle.addEventListener("click", () => this.togglePin(slice.key));
      svg.append(circle);
      this.segs.push(circle);
    });
    const center = h("div", { class: "fc-center" });
    this.cLabel = h("span", { class: "fc-center-label" });
    this.cValue = h("span", { class: "fc-center-value" });
    this.cSub = h("span", { class: "fc-center-sub" });
    center.append(this.cLabel, this.cValue, this.cSub);
    center.style.width = S - 2 * (SWH + 14) + "px";
    this.box.append(svg, center);
  }

  bindRows() {
    this.rows = Array.from(this.root.querySelectorAll("[data-row]"));
    this.rows.forEach((row) => {
      const key = row.dataset.row;
      row.addEventListener("pointermove", () => this.setHover(key));
      row.addEventListener("pointerleave", () => this.setHover(null));
      row.addEventListener("focus", () => this.setHover(key));
      row.addEventListener("blur", () => this.setHover(null));
      row.addEventListener("click", () => {
        if (key === "__rest") {
          this.open = !this.open;
          this.pin = this.open ? key : null;
          this.hover = null;
        } else {
          this.pin = this.pin === key ? null : key;
          this.hover = null;
        }
        this.refresh();
      });
    });
    this.more = this.root.querySelector("[data-more]");
  }

  setHover(key) { if (this.hover !== key) { this.hover = key; this.refresh(); } }
  togglePin(key) { this.pin = this.pin === key ? null : key; this.hover = null; this.refresh(); }

  refresh() {
    const hot = this.hover != null ? this.hover : this.pin;
    const g = this.geo;
    this.slices.forEach((slice, i) => {
      const circle = this.segs[i];
      const sw = hot === slice.key ? g.SWH : g.SW;
      const length = slice.f * g.C;
      const dash = Math.max(0.5, length - g.GAP - sw);
      const off = (((g.GAP / 2 + sw / 2) / g.r) * 180) / Math.PI;
      const rot = -90 + slice.start * 360 + off;
      circle.style.strokeWidth = sw + "px";
      circle.style.strokeDasharray = dash.toFixed(2) + " " + (g.C - dash).toFixed(2);
      circle.style.transform = "rotate(" + rot.toFixed(2) + "deg)";
      circle.style.opacity = hot != null && hot !== slice.key ? "0.32" : "1";
      circle.style.pointerEvents = slice.f > 0 ? "auto" : "none";
    });
    const slice = this.slices.find((x) => x.key === hot);
    if (slice) {
      this.cLabel.textContent = slice.name;
      this.cValue.textContent = cents(slice.cents);
      this.cSub.textContent = percent(this.total ? (slice.cents / this.total) * 100 : 0) + "% " + T.ofTotal;
    } else {
      this.cLabel.textContent = this.defaultCenter.label;
      this.cValue.textContent = cents(this.total);
      this.cSub.textContent = this.defaultCenter.sub;
    }
    this.rows.forEach((row) => {
      const key = row.dataset.row;
      const on = hot === key;
      row.classList.toggle("is-hot", on);
      row.classList.toggle("is-dim", hot != null && !on);
      row.setAttribute(key === "__rest" ? "aria-expanded" : "aria-pressed", String(key === "__rest" ? this.open : this.pin === key));
    });
    if (this.more) {
      this.more.classList.toggle("is-open", this.open);
      this.more.setAttribute("aria-hidden", this.open ? "false" : "true");
    }
  }
}

// ───────────────────────────── mounting ─────────────────────────────

const KINDS = { pace: PaceChart, flow: FlowChart, donut: DonutChart };

export function mount(root) {
  if (!root || root.__fc) return root && root.__fc;
  const Kind = KINDS[root.dataset.chart];
  if (!Kind) return null;
  root.__fc = new Kind(root);
  return root.__fc;
}

export function mountAll() {
  document.querySelectorAll("[data-chart]").forEach((el) => mount(el));
}

window.FinCharts = { math, fmt, mount, mountAll };

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mountAll);
else mountAll();
