/* charts.js — <fp-patrimonio> chart island (SVG, vanilla Web Component). GENERATED FILE: do not edit, regenerate from the verified package.
 * Build   : esbuild 0.28.2 · bundle · format=esm · target=esnext · charset=utf8 · not minified (esbuild strips the comments of the sources).
 * Bundled : dataviz/fp-patrimonio.js, dataviz/lib/chart-math.js, dataviz/lib/format.js, dataviz/lib/points.js, dataviz/lib/rolling-number.js, dataviz/lib/spring.js
 * External: ./fp-money.js — serve it from the SAME directory and load it with the SAME URL as this file (module identity is the full URL:
 *            no ?v= on only one of the two, no other host). Two URLs = two instances of the currency layer.
 * Registers <fp-patrimonio> (customElements.define, idempotent). Exports: FpPatrimonio, parsePoints.
 *
 * Island contract (header of dataviz/fp-patrimonio.js, pt-BR):
 * <fp-patrimonio> — ilha de evolução do patrimônio líquido.
 *
 * Web Component nativo (light DOM) + SVG, em JavaScript puro: sem React, sem TypeScript, sem etapa de build.
 * Carrega com <script type="module"> direto do /static e funciona dentro de HTML/Jinja2/HTMX
 * (o upgrade acontece sozinho quando o HTMX insere o elemento; não há hook de pós-swap).
 *
 *   <fp-patrimonio src="/api/patrimonio" heading="Patrimônio" badge="parcial" footnote="…"></fp-patrimonio>
 *
 * Dados (centavos inteiros): `{"points":[["2026-10-01", 9511846], …]}` ou `[{ "date": "…", "value": … }, …]`,
 * por `src` (apenas mesma origem), por <script type="application/json"> dentro do elemento, ou por `el.points = […]`.
 *
 * Eventos: `fp-ready` {count} · `fp-range` {key} · `fp-scrub` {fraction,date,value,change,changePct} (null ao soltar) · `fp-error`.
 *
 * Moeda: símbolo, posição, separadores e sinal vêm de ../fp-money.js (window.APP_CONFIG + Intl.NumberFormat); aqui não há "R$"
 * escrito. A ilha se refaz sozinha quando FP.money.setConfig({ locale, currency }) dispara `fp:config`.
 *
 * Estilo: classes e variáveis do tokens.css (--dv-*, --surface-*, molas --k-* / --c-*). Só `transform`, `opacity` e atributos
 * SVG são escritos por quadro; nenhuma leitura de layout fora de medições pontuais (largura, retângulo ao tocar).
 */
// dataviz/lib/chart-math.js
var DAY = 864e5;
var clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
var lerp = (a, b, t) => a + (b - a) * t;
function sliceByDays(points, days) {
  if (points.length < 3 || !Number.isFinite(days)) return points.slice();
  const since = points[points.length - 1].t - days * DAY;
  let lo = 0;
  let hi = points.length;
  while (lo < hi) {
    const mid = lo + hi >> 1;
    if (points[mid].t < since) lo = mid + 1;
    else hi = mid;
  }
  return points.slice(Math.min(lo, points.length - 2));
}
function resampleByTime(points, n) {
  const out = new Float64Array(n);
  const last = points.length - 1;
  if (last < 0) return out;
  if (last === 0) return out.fill(points[0].v);
  const t0 = points[0].t;
  const span = points[last].t - t0;
  let k = 0;
  for (let j = 0; j < n; j++) {
    const t = t0 + span * j / (n - 1);
    while (k < last - 1 && points[k + 1].t < t) k++;
    const a = points[k];
    const b = points[k + 1];
    const dt = b.t - a.t;
    out[j] = dt <= 0 ? b.v : lerp(a.v, b.v, clamp((t - a.t) / dt, 0, 1));
  }
  return out;
}
function locate(points, t) {
  const last = points.length - 1;
  if (last < 0) return { i0: 0, i1: 0, frac: 0 };
  if (t <= points[0].t) return { i0: 0, i1: 0, frac: 0 };
  if (t >= points[last].t) return { i0: last, i1: last, frac: 0 };
  let lo = 0;
  let hi = last;
  while (hi - lo > 1) {
    const mid = lo + hi >> 1;
    if (points[mid].t <= t) lo = mid;
    else hi = mid;
  }
  const a = points[lo];
  const b = points[hi];
  return { i0: lo, i1: hi, frac: (t - a.t) / Math.max(1, b.t - a.t) };
}
function readoutAt(points, t, gapDays = 2) {
  if (points.length === 0) return { value: 0, index: -1, estimated: false };
  const { i0, i1, frac } = locate(points, t);
  const value = i0 === i1 ? points[i0].v : lerp(points[i0].v, points[i1].v, frac);
  const index = frac < 0.5 ? i0 : i1;
  const estimated = i0 !== i1 && points[i1].t - points[i0].t > gapDays * DAY && frac > 0 && frac < 1;
  return { value, index, estimated };
}
function monotoneTangents(y) {
  const n = y.length;
  const m = new Float64Array(n);
  if (n < 2) return m;
  const d = new Float64Array(n - 1);
  for (let i = 0; i < n - 1; i++) d[i] = y[i + 1] - y[i];
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
    const s2 = a * a + b * b;
    if (s2 > 9) {
      const k = 3 / Math.sqrt(s2);
      m[i] = k * a * d[i];
      m[i + 1] = k * b * d[i];
    }
  }
  return m;
}
function monotonePath(xs, ys, m) {
  const n = xs.length;
  if (n === 0) return "";
  let d = `M${xs[0].toFixed(2)} ${ys[0].toFixed(2)}`;
  for (let i = 0; i < n - 1; i++) {
    const dx = (xs[i + 1] - xs[i]) / 3;
    d += `C${(xs[i] + dx).toFixed(2)} ${(ys[i] + m[i] / 3).toFixed(2)} ${(xs[i + 1] - dx).toFixed(2)} ${(ys[i + 1] - m[i + 1] / 3).toFixed(2)} ${xs[i + 1].toFixed(2)} ${ys[i + 1].toFixed(2)}`;
  }
  return d;
}
function hermiteAt(y, m, p) {
  const last = y.length - 1;
  if (last < 1) return y[0] ?? 0;
  const k = Math.min(last - 1, Math.max(0, Math.floor(p)));
  const t = p - k;
  const t2 = t * t;
  const t3 = t2 * t;
  return (2 * t3 - 3 * t2 + 1) * y[k] + (t3 - 2 * t2 + t) * m[k] + (-2 * t3 + 3 * t2) * y[k + 1] + (t3 - t2) * m[k + 1];
}
var STEPS_BRL = [100, 200, 250, 500, 1e3, 2e3, 2500, 5e3, 1e4, 2e4, 25e3, 5e4, 1e5, 25e4, 5e5, 1e6];
function niceDomain(values, maxTicks = 3) {
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
function xAxisStops(startT, endT) {
  return [0, 1 / 3, 2 / 3, 1].map((fraction) => ({ fraction, t: startT + (endT - startT) * fraction }));
}
function snapStroke(x, dpr = 1) {
  const d = dpr > 0 ? dpr : 1;
  return Math.round((x - 0.5) * d) / d + 0.5;
}

// dataviz/lib/format.js
import { MINUS, affixes, formatCompact, formatCurrency, formatMoneyParts, getConfig } from "./fp-money.js";
var MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
var WEEKDAYS = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];
var two = (n) => String(n).padStart(2, "0");
var moneyParts = (cents) => formatMoneyParts(cents);
var formatMoney = (cents) => formatCurrency(cents);
var formatDelta = (cents) => formatCurrency(cents, { sign: "always" });
var formatTick = (cents) => formatCompact(cents);
var moneyAffixes = () => affixes();
var moneyConfigKey = () => {
  const c = getConfig();
  return `${c.locale}|${c.currency}`;
};
var percentFormatters = /* @__PURE__ */ new Map();
function percentFormatter(locale) {
  let f = percentFormatters.get(locale);
  if (!f) {
    f = new Intl.NumberFormat(locale, { style: "percent", minimumFractionDigits: 0, maximumFractionDigits: 1 });
    percentFormatters.set(locale, f);
  }
  return f;
}
function formatPercent(x, { sign = "auto" } = {}) {
  if (!Number.isFinite(x)) return "—";
  const rounded = Math.round(x * 10) / 10;
  const body = percentFormatter(getConfig().locale).format(Math.abs(rounded) / 100);
  if (rounded < 0) return MINUS + body;
  return sign === "always" && rounded > 0 ? `+${body}` : body;
}
var shortDate = (d) => `${two(d.getDate())} ${MONTHS[d.getMonth()]}`;
var monthYear = (d) => `${MONTHS[d.getMonth()]} ${String(d.getFullYear()).slice(2)}`;
var monthYearFull = (d) => `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
var fullDate = (d) => `${two(d.getDate())} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
var longDate = (d) => `${WEEKDAYS[d.getDay()]}, ${fullDate(d)}`;
function isSameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}
function toEpoch(date) {
  if (date instanceof Date) return date.getTime();
  if (typeof date === "number") return date;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(date));
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).getTime() : new Date(date).getTime();
}

// dataviz/lib/points.js
function parsePoints(input) {
  const list = Array.isArray(input) ? input : Array.isArray(input?.points) ? input.points : [];
  const rows = [];
  for (const item of list) {
    const date = Array.isArray(item) ? item[0] : item?.date;
    const value = Array.isArray(item) ? item[1] : item?.value;
    if (value == null || value === "" || date == null) continue;
    const t = toEpoch(date);
    const v = Number(value);
    if (Number.isFinite(t) && Number.isFinite(v)) rows.push({ t, v: Math.round(v) });
  }
  rows.sort((a, b) => a.t - b.t);
  const out = [];
  for (const r of rows) {
    if (out.length && out[out.length - 1].t === r.t) out[out.length - 1] = r;
    else out.push(r);
  }
  return out;
}

// dataviz/lib/spring.js
function fromKC(k, c) {
  const omega = Math.sqrt(k);
  return { omega, zeta: c / (2 * omega) };
}
function springSolve(x0, v0, target, zeta, omega, t) {
  const u0 = x0 - target;
  if (t <= 0) return [x0, v0];
  if (Math.abs(zeta - 1) < 1e-6) {
    const e = Math.exp(-omega * t);
    const b = v0 + omega * u0;
    return [target + e * (u0 + b * t), e * (v0 - omega * b * t)];
  }
  if (zeta < 1) {
    const zw = zeta * omega;
    const wd = omega * Math.sqrt(1 - zeta * zeta);
    const e = Math.exp(-zw * t);
    const c = Math.cos(wd * t);
    const s2 = Math.sin(wd * t);
    const B = (v0 + zw * u0) / wd;
    return [target + e * (u0 * c + B * s2), e * ((-zw * u0 + wd * B) * c + (-zw * B - wd * u0) * s2)];
  }
  const q = Math.sqrt(zeta * zeta - 1);
  const r1 = -omega * (zeta - q);
  const r2 = -omega * (zeta + q);
  const c2 = (v0 - r1 * u0) / (r2 - r1);
  const c1 = u0 - c2;
  const e1 = Math.exp(r1 * t);
  const e2 = Math.exp(r2 * t);
  return [target + c1 * e1 + c2 * e2, c1 * r1 * e1 + c2 * r2 * e2];
}
function settleTime(zeta, omega) {
  if (zeta < 1) return 8 / (zeta * omega);
  if (Math.abs(zeta - 1) < 1e-6) return 9.3 / omega;
  return 8 / (omega * (zeta - Math.sqrt(zeta * zeta - 1)));
}
var SpringField = class {
  constructor(n, zeta, omega) {
    this.n = n;
    this.zeta = zeta;
    this.omega = omega;
    this.tSettle = settleTime(zeta, omega);
    this.x0 = new Float64Array(n);
    this.v0 = new Float64Array(n);
    this.target = new Float64Array(n);
    this.cur = new Float64Array(n);
    this.vel = new Float64Array(n);
    this.t0 = 0;
    this.settled = true;
  }
  /** Vai direto ao alvo, sem animar (primeiro desenho, movimento reduzido). */
  jump(target) {
    this.x0.set(target);
    this.target.set(target);
    this.cur.set(target);
    this.v0.fill(0);
    this.vel.fill(0);
    this.settled = true;
  }
  /** Novo alvo no instante `now` (ms), continuando de onde cada componente está agora. */
  retarget(target, now) {
    if (!this.settled) this.sample(now);
    this.x0.set(this.cur);
    this.v0.set(this.vel);
    this.target.set(target);
    this.t0 = now;
    this.settled = false;
  }
  /** Estado no instante `now` (ms). Marca `settled` ao fim e crava o alvo exato. */
  sample(now) {
    if (this.settled) return this.cur;
    const t = Math.max(0, (now - this.t0) / 1e3);
    if (t >= this.tSettle) {
      this.cur.set(this.target);
      this.vel.fill(0);
      this.settled = true;
      return this.cur;
    }
    const { zeta, omega, n } = this;
    const { x0, v0, target, cur, vel } = this;
    if (Math.abs(zeta - 1) < 1e-6) {
      const e = Math.exp(-omega * t);
      for (let i = 0; i < n; i++) {
        const u0 = x0[i] - target[i];
        const b = v0[i] + omega * u0;
        cur[i] = target[i] + e * (u0 + b * t);
        vel[i] = e * (v0[i] - omega * b * t);
      }
    } else if (zeta < 1) {
      const zw = zeta * omega;
      const wd = omega * Math.sqrt(1 - zeta * zeta);
      const e = Math.exp(-zw * t);
      const c = Math.cos(wd * t);
      const s2 = Math.sin(wd * t);
      for (let i = 0; i < n; i++) {
        const u0 = x0[i] - target[i];
        const B = (v0[i] + zw * u0) / wd;
        cur[i] = target[i] + e * (u0 * c + B * s2);
        vel[i] = e * ((-zw * u0 + wd * B) * c + (-zw * B - wd * u0) * s2);
      }
    } else {
      for (let i = 0; i < n; i++) {
        const r = springSolve(x0[i], v0[i], target[i], zeta, omega, t);
        cur[i] = r[0];
        vel[i] = r[1];
      }
    }
    return cur;
  }
};
function readNumber(el, prop, fallback) {
  try {
    const raw = getComputedStyle(el).getPropertyValue(prop).trim();
    const v = parseFloat(raw);
    return Number.isFinite(v) ? v : fallback;
  } catch {
    return fallback;
  }
}
function readSpring(el, name, fallback) {
  const k = readNumber(el, `--k-${name}`, fallback.k);
  const c = readNumber(el, `--c-${name}`, fallback.c);
  return fromKC(k, c);
}
var ticker = /* @__PURE__ */ (() => {
  const subs = /* @__PURE__ */ new Set();
  let raf = 0;
  const loop = (now) => {
    raf = 0;
    for (const fn of [...subs]) if (!fn(now)) subs.delete(fn);
    if (subs.size) raf = requestAnimationFrame(loop);
  };
  return {
    add(fn) {
      subs.add(fn);
      if (!raf && typeof requestAnimationFrame === "function") raf = requestAnimationFrame(loop);
    },
    remove(fn) {
      subs.delete(fn);
    },
    get size() {
      return subs.size;
    }
  };
})();

// dataviz/lib/rolling-number.js
var mod = (n, m) => (n % m + m) % m;
var isDigit = (ch) => ch >= "0" && ch <= "9";
var kindOf = (ch) => isDigit(ch) ? "d" : "c";
function nearestEquivalent(pos, digit, bias = 0) {
  const k = Math.round((pos - digit) / 10);
  let target = digit + 10 * k;
  if (bias !== 0 && Math.abs(Math.abs(target - pos) - 5) < 1e-9) target = pos + (bias > 0 ? 5 : -5);
  return target;
}
function alignRight(oldKinds, chars) {
  const plan = new Array(chars.length).fill(-1);
  const used = /* @__PURE__ */ new Set();
  let i = oldKinds.length - 1;
  for (let j = chars.length - 1; j >= 0; j--) {
    if (i >= 0 && oldKinds[i] === kindOf(chars[j])) {
      plan[j] = i;
      used.add(i);
    }
    i--;
  }
  const remove = [];
  for (let k = 0; k < oldKinds.length; k++) if (!used.has(k)) remove.push(k);
  return { plan, remove };
}
function makeStripTemplate() {
  const strip = document.createElement("span");
  strip.className = "od-s";
  for (let k = 0; k < 30; k++) {
    const i = document.createElement("i");
    i.textContent = String(k % 10);
    strip.appendChild(i);
  }
  return strip;
}
var RollingNumber = class {
  #host;
  #cells = [];
  #text = "";
  #zeta;
  #omega;
  #settleT;
  #reduced;
  #template = null;
  /**
   * @param {HTMLElement} host   contêiner (recebe as colunas como filhos)
   * @param {object} [opts]
   * @param {number} [opts.zeta=1] amortecimento
   * @param {number} [opts.omega=24] frequência natural (rad/s)
   * @param {() => boolean} [opts.reduced] true quando o usuário pede movimento reduzido
   */
  constructor(host, { zeta = 1, omega = 24, reduced = () => false } = {}) {
    this.#host = host;
    this.#zeta = zeta;
    this.#omega = omega;
    this.#settleT = settleTime(zeta, omega);
    this.#reduced = reduced;
  }
  get text() {
    return this.#text;
  }
  /**
   * Mostra `text` (ex.: `95.118,46` ou `+4.483,97 · +4,4%`).
   * @param {object} [o]
   * @param {boolean} [o.immediate] sem animar
   * @param {number} [o.bias] sentido preferido nos empates (+1 sobe)
   * @param {boolean} [o.intro] primeira aparição: TODOS os dígitos voltam ao 0 e rolam até o valor, em cascata
   * @param {number} [o.tail] fator de escala dos centavos (ex.: 0.62); 1 = sem destaque
   * @param {string} [o.decimal] separador decimal do texto (vem de `affixes().dec` do fp-money.js: "," no pt-BR, "." no en-US).
   *   Sem ele não há destaque dos centavos: o separador nunca é presumido.
   */
  set(text, { immediate = false, bias = 0, intro = false, tail = 1, decimal = "" } = {}) {
    if (text === this.#text && !this.#needsTail(tail, decimal)) return;
    const chars = Array.from(text);
    const instant = immediate || this.#reduced();
    const rollIn = intro && !instant;
    const now = performance.now();
    const { plan, remove } = alignRight(this.#cells.map((c) => c.kind), chars);
    const next = chars.map((ch, j) => {
      const reuse = plan[j] >= 0 ? this.#cells[plan[j]] : null;
      if (isDigit(ch)) {
        const d = ch.charCodeAt(0) - 48;
        if (reuse) {
          if (rollIn) this.#restart(reuse, d, now + j * 38);
          else this.#retarget(reuse, d, bias, now, instant);
          return reuse;
        }
        const cell = this.#makeDigit(rollIn ? 0 : d);
        if (rollIn) this.#retarget(cell, d, 1, now + j * 38, false, true);
        return cell;
      }
      if (reuse) {
        if (reuse.ch !== ch) {
          reuse.el.textContent = ch;
          reuse.ch = ch;
        }
        return reuse;
      }
      const el = document.createElement("span");
      el.className = "od-c";
      el.textContent = ch;
      return { kind: "c", el, ch };
    });
    const unchanged = remove.length === 0 && next.length === this.#cells.length && next.every((c, i) => c === this.#cells[i]);
    if (!unchanged) this.#host.replaceChildren(...next.map((c) => c.el));
    this.#cells = next;
    this.#text = text;
    const dec = decimal ? chars.lastIndexOf(decimal) : -1;
    next.forEach((c, i) => c.el.classList.toggle("od-tail", tail !== 1 && dec >= 0 && i >= dec));
    this.#tail = tail;
    this.#decimal = decimal;
    if (next.some((c) => c.moving)) ticker.add(this.#tick);
  }
  #tail = 1;
  #decimal = "";
  #needsTail(tail, decimal) {
    return tail !== this.#tail || decimal !== this.#decimal;
  }
  destroy() {
    ticker.remove(this.#tick);
    this.#host.replaceChildren();
    this.#cells = [];
    this.#text = "";
  }
  #makeDigit(d) {
    this.#template ??= makeStripTemplate();
    const el = document.createElement("span");
    el.className = "od-d";
    const sp = document.createElement("span");
    sp.className = "od-sp";
    sp.textContent = "0";
    const strip = this.#template.cloneNode(true);
    el.append(sp, strip);
    const cell = { kind: "d", el, strip, pos: d, vel: 0, p0: d, v0: 0, target: d, t0: 0, moving: false };
    this.#write(cell);
    return cell;
  }
  /** Entrada: volta a coluna ao 0 e conta para cima até o dígito, em cascata (a esquerda começa antes). */
  #restart(c, digit, startAt) {
    c.pos = c.p0 = c.target = 0;
    c.vel = c.v0 = 0;
    c.moving = false;
    this.#write(c);
    this.#retarget(c, digit, 1, startAt, false, true);
  }
  #retarget(c, digit, bias, now, instant, forceUp = false) {
    const target = forceUp ? digit : nearestEquivalent(c.pos, digit, bias);
    if (instant) {
      c.pos = c.target = c.p0 = target;
      c.vel = c.v0 = 0;
      c.moving = false;
      this.#write(c);
      return;
    }
    if (target === c.target && c.moving) return;
    if (target === c.pos && !c.moving) return;
    c.p0 = c.pos;
    c.v0 = c.vel;
    c.target = target;
    c.t0 = now;
    c.moving = true;
  }
  #write(c) {
    c.strip.style.transform = `translate3d(0,${(-(10 + mod(c.pos, 10))).toFixed(4)}em,0)`;
  }
  #tick = (now) => {
    let alive = false;
    for (const c of this.#cells) {
      if (!c.moving) continue;
      const t = (now - c.t0) / 1e3;
      if (t < 0) {
        alive = true;
        continue;
      }
      if (t >= this.#settleT) {
        c.pos = c.target;
        c.vel = 0;
        c.moving = false;
        c.pos = mod(c.pos, 10);
        c.target = c.pos;
        this.#write(c);
        continue;
      }
      const [x, v] = springSolve(c.p0, c.v0, c.target, this.#zeta, this.#omega, t);
      c.pos = x;
      c.vel = v;
      this.#write(c);
      alive = true;
    }
    return alive;
  };
};

// dataviz/fp-patrimonio.js
var TAG = "fp-patrimonio";
var SVG_NS = "http://www.w3.org/2000/svg";
var GEOM = { top: 34, bottom: 30, padX: 0, samples: 96 };
var DEFAULT_RANGES = [
  { key: "1M", label: "1M", days: 30, aria: "Último mês" },
  { key: "3M", label: "3M", days: 92, aria: "Últimos 3 meses" },
  { key: "1A", label: "1A", days: 365, aria: "Último ano" },
  { key: "Tudo", label: "Tudo", days: Infinity, aria: "Todo o período" }
];
var FALLBACK = { morph: { k: 121, c: 19.36 }, scrub: { k: 576, c: 48 } };
function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else e.setAttribute(k, v === true ? "" : String(v));
    }
  }
  for (const c of kids.flat()) if (c != null) e.append(c);
  return e;
}
function s(tag, props, ...kids) {
  const e = document.createElementNS(SVG_NS, tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) if (v != null) e.setAttribute(k, String(v));
  }
  for (const c of kids.flat()) if (c != null) e.append(c);
  return e;
}
var uid = 0;
var HttpError = class extends Error {
  constructor(status) {
    super(`HTTP ${status}`);
    this.status = status;
  }
};
var FpPatrimonio = class extends HTMLElement {
  static observedAttributes = ["range", "status", "heading", "badge", "footnote", "plot-height", "min-points", "src"];
  // dados e estado
  #series = [];
  #ranges = DEFAULT_RANGES;
  #rangeKey = "1A";
  #state = "loading";
  #statusAttr = "ready";
  #slice = [];
  #toTicks = [];
  #fromTicks = [];
  #toAxis = [];
  #fromAxis = [];
  #width = 0;
  #plotH = 236;
  #minPoints = 8;
  // geometria da curva (px)
  #xs = new Float64Array(GEOM.samples);
  #ys = new Float64Array(GEOM.samples);
  #my = new Float64Array(GEOM.samples);
  // molas
  #field = new SpringField(2 + GEOM.samples, 0.88, 11);
  #prog = new SpringField(1, 0.88, 11);
  // interação
  #fraction = null;
  #lastFraction = 1;
  #pressed = false;
  #pendingX = null;
  #rectLeft = 0;
  #dirtyCursor = false;
  #nearest = -1;
  #lastValue = null;
  // infraestrutura
  #built = false;
  #dom = null;
  #roll = null;
  #rollChip = null;
  #ro = null;
  #mq = null;
  #reduced = false;
  #abort = null;
  #ticks = /* @__PURE__ */ new Map();
  #xlabels = /* @__PURE__ */ new Map();
  #desc = "";
  #pendingProps = {};
  #cfgKey = "";
  // locale|divisa com que o DOM foi desenhado
  #dec = "";
  // separador decimal em vigor (para destacar os centavos do odômetro)
  #snap = false;
  // próximo quadro do cursor sem rolagem: a divisa mudou e símbolo e separadores trocaram
  #ariaStale = false;
  // reescrever aria-valuetext mesmo sem mudar de registro
  #rollEmpty = true;
  // o cabeçalho mostra "sem valor" (—), não um saldo
  #firstReady = true;
  #hasCurve = false;
  #springsReady = false;
  #id = ++uid;
  // ───────────── API pública ─────────────
  get points() {
    return this.#series.map((p) => ({ date: p.t, value: p.v }));
  }
  set points(list) {
    if (!this.#built) {
      this.#pendingProps.points = list;
      return;
    }
    this.#setSeries(parsePoints(list));
  }
  get range() {
    return this.#rangeKey;
  }
  set range(key) {
    if (!this.#built) {
      this.#pendingProps.range = key;
      return;
    }
    this.#selectRange(String(key), false);
  }
  get status() {
    return this.#statusAttr;
  }
  set status(v) {
    this.setAttribute("status", v === "loading" ? "loading" : "ready");
  }
  /** Períodos do controle: `[{ key, label, days, aria }]`. */
  set ranges(list) {
    if (Array.isArray(list) && list.length) {
      this.#ranges = list;
      if (this.#built) {
        this.#buildRange();
        this.#selectRange(this.#rangeKey, false, true);
      }
    }
  }
  /** Lê `src` de novo. Com o gráfico já na tela a atualização é silenciosa: a curva INTERPOLA para os dados novos. */
  reload() {
    if (this.getAttribute("src")) this.#fetch(this.#state === "ready");
  }
  // ───────────── ciclo de vida ─────────────
  connectedCallback() {
    for (const k of ["points", "range", "status", "ranges"]) {
      if (Object.prototype.hasOwnProperty.call(this, k)) {
        const v = this[k];
        delete this[k];
        this.#pendingProps[k] = v;
      }
    }
    if (this.#built) {
      this.#attach();
      return;
    }
    const inline = this.querySelector('script[type="application/json"]');
    let inlineData = null;
    if (inline) {
      try {
        inlineData = JSON.parse(inline.textContent || "null");
      } catch {
        this.#fail("JSON inline inválido");
      }
    }
    this.#minPoints = Math.max(2, parseInt(this.getAttribute("min-points") || "8", 10) || 8);
    this.#plotH = clamp(parseInt(this.getAttribute("plot-height") || "236", 10) || 236, 120, 600);
    this.#rangeKey = this.getAttribute("range") || "1A";
    this.#statusAttr = this.getAttribute("status") === "loading" ? "loading" : "ready";
    this.#build();
    this.#attach();
    this.#built = true;
    const p = this.#pendingProps;
    if (p.ranges) this.ranges = p.ranges;
    if (p.range) this.#rangeKey = String(p.range);
    if (p.status) this.#statusAttr = p.status === "loading" ? "loading" : "ready";
    if (p.points) this.#setSeries(parsePoints(p.points));
    else if (inlineData) this.#setSeries(parsePoints(inlineData));
    else if (this.getAttribute("src")) this.#fetch();
    else this.#setState(this.#statusAttr === "loading" ? "loading" : "empty");
    this.#pendingProps = {};
  }
  disconnectedCallback() {
    this.#ro?.disconnect();
    this.#mq?.removeEventListener("change", this.#onMotionPref);
    window.removeEventListener("fp:config", this.#onConfig);
    document.removeEventListener("fp:privacy", this.#onPrivacy);
    this.#abort?.abort();
    ticker.remove(this.#tick);
  }
  attributeChangedCallback(name, _old, value) {
    if (!this.#built) return;
    switch (name) {
      case "range":
        if (value && value !== this.#rangeKey) this.#selectRange(value, false);
        break;
      case "status":
        this.#statusAttr = value === "loading" ? "loading" : "ready";
        this.#setState(this.#decideState());
        break;
      case "heading":
      case "badge":
      case "footnote":
        this.#renderTexts();
        break;
      case "plot-height":
        this.#plotH = clamp(parseInt(value || "236", 10) || 236, 120, 600);
        this.style.setProperty("--fp-plot-h", `${this.#plotH}px`);
        this.#layout();
        break;
      case "min-points":
        this.#minPoints = Math.max(2, parseInt(value || "8", 10) || 8);
        this.#setState(this.#decideState());
        break;
      case "src":
        this.#fetch();
        break;
    }
  }
  // ───────────── construção do DOM (uma vez) ─────────────
  #build() {
    this.replaceChildren();
    this.style.setProperty("--fp-top", `${GEOM.top}px`);
    this.style.setProperty("--fp-bottom", `${GEOM.bottom}px`);
    this.style.setProperty("--fp-plot-h", `${this.#plotH}px`);
    this.setAttribute("role", "group");
    this.setAttribute("aria-label", "Evolução do patrimônio");
    if (this.getAttribute("private") && !this.closest("[data-peek-scope]")) this.setAttribute("data-peek-scope", "");
    const gid = `fp-fill-${this.#id}`;
    const d = {};
    this.#dom = d;
    d.title = h("div", { class: "fp-title overline" });
    d.titleText = document.createTextNode("");
    d.badge = h("span", { class: "fp-badge" });
    d.title.append(d.titleText, d.badge);
    const pv = this.getAttribute("private") ? { "data-private": this.getAttribute("private") } : null;
    d.sign = h("span", { class: "fp-sign", "aria-hidden": "true", hidden: true, ...pv });
    d.cur = h("span", { class: "fp-cur", "aria-hidden": "true" });
    d.num = h("span", { class: "fp-num", "aria-hidden": "true", ...pv });
    d.value = h("div", { class: "fp-value display" });
    d.srValue = h("span", { class: "sr-only", ...pv });
    d.chipNum = h("span", { class: "fp-chip-num", ...pv });
    d.chip = h("span", { class: "fp-chip dl dl-pos dl-up nums", "aria-hidden": "true" }, d.chipNum);
    d.when = h("span", { class: "fp-when nums" });
    d.sub = h("div", { class: "fp-sub" }, d.chip, d.when);
    d.meta = h("div", { class: "fp-meta" }, d.title, d.value, d.srValue, d.sub);
    d.skMeta = h("div", { class: "fp-skm", "aria-hidden": "true" }, h("div", { class: "sk fp-sk1" }), h("div", { class: "sk fp-sk2" }), h("div", { class: "fp-sk3" }, h("div", { class: "sk fp-sk3a" }), h("div", { class: "sk fp-sk3b" })));
    d.skRange = h("div", { class: "sk fp-skr", "aria-hidden": "true" });
    d.range = h("div", { class: "fp-range", role: "radiogroup", "aria-label": "Período do gráfico" });
    d.head = h("div", { class: "fp-head" }, d.meta, d.skMeta, d.range, d.skRange);
    d.fillTop = s("stop", { offset: "0" });
    d.fillTop.setAttribute("class", "fp-g0");
    d.fillBottom = s("stop", { offset: "1" });
    d.fillBottom.setAttribute("class", "fp-g1");
    d.grid = s("g", { class: "fp-grid" });
    d.area = s("path", { class: "fp-area", fill: `url(#${gid})` });
    d.line = s("path", { class: "fp-line", fill: "none" });
    d.dots = s("g", { class: "fp-recs" });
    d.guide = s("line", { class: "fp-guide" });
    d.svg = s(
      "svg",
      { class: "fp-svg", width: "100%", height: "100%", "aria-hidden": "true", focusable: "false" },
      s("defs", null, s("linearGradient", { id: gid, x1: "0", y1: "0", x2: "0", y2: "1" }, d.fillTop, d.fillBottom)),
      d.grid,
      d.area,
      d.line,
      d.dots,
      d.guide
    );
    d.labels = h("div", { class: "fp-labels", "aria-hidden": "true" });
    d.draw = h("div", { class: "fp-draw wipe" }, d.svg, d.labels);
    d.halo = h("span", { class: "fp-halo" });
    d.dot = h("span", { class: "fp-dot" });
    d.marker = h("div", { class: "fp-marker", "aria-hidden": "true" }, d.halo, d.dot);
    d.tipDate = h("span", { class: "fp-tip-d" });
    d.tipVal = h("span", { class: "fp-tip-v", ...pv });
    d.tipEst = h("span", { class: "fp-tip-e", text: "estimado", hidden: true });
    d.tip = h("div", { class: "fp-tip dv-tip", "aria-hidden": "true" }, d.tipDate, h("span", { class: "fp-tip-s", text: "·" }), d.tipVal, d.tipEst);
    d.hit = h("div", {
      class: "fp-hit",
      role: "slider",
      tabindex: "0",
      "aria-label": "Patrimônio ao longo do período",
      "aria-orientation": "horizontal",
      "aria-valuemin": "0",
      "aria-valuemax": "0",
      "aria-valuenow": "0",
      "aria-describedby": `fp-desc-${this.#id}`
    });
    d.skPlot = h("div", { class: "fp-skp", role: "status", "aria-busy": "true", "aria-label": "Carregando o gráfico de patrimônio" }, h("div", { class: "sk fp-skp-area" }), h("div", { class: "sk fp-skp-y" }), h("div", { class: "sk fp-skp-x1" }), h("div", { class: "sk fp-skp-x2" }), h("div", { class: "sk fp-skp-x3" }));
    d.sparse = h("div", { class: "fp-sparse" });
    d.err = h("div", { class: "fp-err" });
    d.plot = h("div", { class: "fp-plot" }, d.draw, d.marker, d.tip, d.hit, d.skPlot, d.sparse, d.err);
    d.foot = h("p", { class: "fp-foot nums" });
    d.desc = h("p", { class: "sr-only", id: `fp-desc-${this.#id}` });
    d.live = h("p", { class: "sr-only", "aria-live": "polite" });
    this.append(d.head, d.plot, d.foot, d.desc, d.live);
    this.#applyCurrency();
    this.#roll = this.#makeRoll(d.num, { zeta: 1, omega: 24 });
    this.#rollChip = this.#makeRoll(d.chipNum, { zeta: 1, omega: 24 });
    this.#buildRange();
    this.#renderTexts();
    this.#bindEvents();
  }
  #attach() {
    this.#mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    this.#reduced = this.#mq.matches;
    this.#mq.addEventListener("change", this.#onMotionPref);
    window.addEventListener("fp:config", this.#onConfig);
    document.addEventListener("fp:privacy", this.#onPrivacy);
    this.#onConfig();
    if (!this.#springsReady) {
      this.#springsReady = true;
      const morph = readSpring(this, "morph", FALLBACK.morph);
      const scrub = readSpring(this, "scrub", FALLBACK.scrub);
      this.#field = new SpringField(2 + GEOM.samples, morph.zeta, morph.omega);
      this.#prog = new SpringField(1, morph.zeta, morph.omega);
      this.#roll?.destroy();
      this.#rollChip?.destroy();
      this.#roll = this.#makeRoll(this.#dom.num, scrub);
      this.#rollChip = this.#makeRoll(this.#dom.chipNum, scrub);
    }
    this.#ro?.disconnect();
    this.#ro = new ResizeObserver((entries) => {
      const w = Math.round(entries[0].contentRect.width);
      if (w > 0 && w !== this.#width) {
        this.#width = w;
        this.#layout();
      }
    });
    this.#ro.observe(this.#dom.plot);
  }
  #makeRoll(host, sp) {
    return new RollingNumber(host, { zeta: sp.zeta, omega: sp.omega, reduced: () => this.#reduced });
  }
  #onMotionPref = (e) => {
    this.#reduced = e.matches;
  };
  // ───────────── moeda (tudo de ../fp-money.js) ─────────────
  /** Símbolo, posição, respiro e separador decimal da divisa em vigor. Refaz a ordem sinal · símbolo · número. */
  #applyCurrency() {
    const d = this.#dom;
    const a = moneyAffixes();
    d.cur.textContent = a.symbol;
    d.cur.dataset.pos = a.position;
    d.cur.dataset.gap = a.gap ? "text" : "none";
    d.value.replaceChildren(...a.position === "after" ? [d.sign, d.num, d.cur] : [d.sign, d.cur, d.num]);
    this.#dec = a.dec;
    this.#cfgKey = moneyConfigKey();
  }
  /** `fp:config`: a divisa ou o locale mudou. Eixos, cabeçalho, tooltip e textos de leitura se refazem, sem rolagem. */
  #onConfig = () => {
    if (!this.#dom || moneyConfigKey() === this.#cfgKey) return;
    this.#applyCurrency();
    for (const [val, t] of this.#ticks) t.label.textContent = formatTick(val);
    if (this.#state === "ready") {
      this.#renderDescription();
      this.#snap = true;
      this.#ariaStale = true;
      this.#dirtyCursor = true;
      ticker.add(this.#tick);
    } else {
      this.#renderHeaderIdle();
    }
  };
  /** Atributo data-private dos nós de valor (vazio sem o atributo `private`: nada muda no DOM). */
  #pv() {
    const g = this.getAttribute("private");
    return g ? { "data-private": g } : null;
  }
  /** Modo privacidade (fp-privacy.js, opcional): com o grupo desfocado, nenhum texto de leitor de tela repete o valor. */
  #hidden() {
    const g = this.getAttribute("private");
    return !!g && globalThis.FP?.privacy?.isMasked?.(g) === true;
  }
  /** `fp:privacy`: o usuário ligou ou desligou o desfoque. Só os textos de leitura mudam (o desfoque em si é CSS). */
  #onPrivacy = () => {
    if (!this.#dom || !this.getAttribute("private")) return;
    if (this.#state === "ready") {
      this.#renderDescription();
      this.#ariaStale = true;
      this.#dirtyCursor = true;
      ticker.add(this.#tick);
    } else {
      this.#renderHeaderIdle();
    }
  };
  /** Cabeçalho: sinal e símbolo parados, número em dígitos vivos. `mp` nulo = sem valor: "—", sem símbolo (nunca um zero que pareça saldo). */
  #setHeader(mp, opts = {}) {
    const d = this.#dom;
    d.value.toggleAttribute("data-empty", !mp);
    d.sign.textContent = mp ? mp.sign : "";
    d.sign.hidden = !mp || !mp.sign;
    this.#roll.set(mp ? mp.amount : "—", { ...opts, tail: mp ? 0.62 : 1, decimal: this.#dec });
    this.#rollEmpty = !mp;
  }
  #renderTexts() {
    const d = this.#dom;
    d.titleText.textContent = this.getAttribute("heading") || "Patrimônio";
    const badge = this.getAttribute("badge");
    d.badge.textContent = badge || "";
    d.badge.hidden = !badge;
    const foot = this.getAttribute("footnote");
    d.foot.textContent = foot || "";
    d.foot.hidden = !foot;
  }
  // ───────────── dados ─────────────
  async #fetch(silent = false) {
    const raw = this.getAttribute("src");
    if (!raw) return;
    let url;
    try {
      url = new URL(raw, location.href);
    } catch {
      this.#fail("Endereço de dados inválido");
      return;
    }
    if (url.origin !== location.origin) {
      this.#fail("Origem externa bloqueada: os dados vêm só deste computador");
      return;
    }
    this.#abort?.abort();
    this.#abort = new AbortController();
    if (!silent) this.#setState("loading");
    try {
      const res = await fetch(url, { signal: this.#abort.signal, headers: { Accept: "application/json" }, credentials: "same-origin", cache: "no-cache", redirect: "error" });
      if (!res.ok) throw new HttpError(res.status);
      this.#setSeries(parsePoints(await res.json()));
    } catch (err) {
      if (err?.name === "AbortError") return;
      const why = err instanceof HttpError ? `HTTP ${err.status}` : err?.name === "SyntaxError" ? "resposta que não é JSON" : "sem resposta direta do servidor local";
      const message = `Não consegui ler os saldos (${why})`;
      if (silent) this.dispatchEvent(new CustomEvent("fp-error", { bubbles: true, detail: { message } }));
      else this.#fail(message);
    }
  }
  #fail(message) {
    this.#dom.err.replaceChildren(
      h("span", { class: "fp-card-t", text: "Não foi possível mostrar o gráfico" }),
      h("span", { class: "fp-card-p", text: message }),
      h("button", { type: "button", class: "fp-link", text: "Tentar de novo" })
    );
    this.#dom.err.querySelector("button").addEventListener("click", () => this.reload());
    this.#setState("error");
    this.dispatchEvent(new CustomEvent("fp-error", { bubbles: true, detail: { message } }));
  }
  #setSeries(series) {
    this.#series = series;
    this.#setState(this.#decideState());
  }
  #decideState() {
    if (this.#statusAttr === "loading") return "loading";
    if (this.#series.length === 0) return "empty";
    if (this.#series.length < this.#minPoints) return "sparse";
    return "ready";
  }
  #setState(state) {
    const prev = this.#state;
    this.#state = state;
    this.setAttribute("data-state", state);
    this.setAttribute("aria-busy", String(state === "loading"));
    this.#dom.range.setAttribute("aria-disabled", String(state !== "ready"));
    for (const b of this.#dom.range.querySelectorAll("button")) b.disabled = state !== "ready";
    if (state === "ready") {
      this.#selectRange(this.#rangeKey, prev !== "ready" ? false : true, true);
      if (this.#firstReady) {
        this.#firstReady = false;
        this.dispatchEvent(new CustomEvent("fp-ready", { bubbles: true, detail: { count: this.#series.length } }));
      }
    } else {
      this.#release(true);
      if (state === "sparse" || state === "empty") this.#renderSparse();
      this.#renderHeaderIdle();
    }
  }
  // ───────────── período e curva ─────────────
  #rangeDef(key) {
    return this.#ranges.find((r) => r.key === key) ?? this.#ranges[0];
  }
  #selectRange(key, animate, force = false) {
    const def = this.#rangeDef(key);
    const changed = def.key !== this.#rangeKey;
    if (!changed && !force) return;
    this.#rangeKey = def.key;
    this.#syncRangeUI();
    if (this.#state !== "ready") return;
    this.#release(true);
    this.#applyRange(animate && !this.#reduced && this.#width > 0);
    if (changed) {
      this.dispatchEvent(new CustomEvent("fp-range", { bubbles: true, detail: { key: def.key } }));
      const first = this.#slice[0];
      const last = this.#slice[this.#slice.length - 1];
      if (first && last) this.#dom.live.textContent = this.#hidden() ? `Período: ${def.aria}. Valores ocultos.` : `Período: ${def.aria}. Patrimônio de ${formatMoney(first.v)} a ${formatMoney(last.v)}.`;
    }
  }
  #axisOf(slice) {
    if (slice.length === 0) return [];
    const a = slice[0].t;
    const b = slice[slice.length - 1].t;
    const long = (b - a) / DAY > 100;
    const stops = xAxisStops(a, b);
    return stops.map((stop, i) => ({
      fraction: stop.fraction,
      text: i === stops.length - 1 && isSameDay(new Date(b), /* @__PURE__ */ new Date()) ? "hoje" : long ? monthYear(new Date(stop.t)) : shortDate(new Date(stop.t))
    }));
  }
  #applyRange(animate) {
    const def = this.#rangeDef(this.#rangeKey);
    const slice = sliceByDays(this.#series, def.days);
    const samples = resampleByTime(slice, GEOM.samples);
    const domain = niceDomain(samples);
    const target = new Float64Array(2 + GEOM.samples);
    target[0] = domain.min;
    target[1] = domain.max;
    target.set(samples, 2);
    const prevAxis = this.#toAxis;
    this.#slice = slice;
    this.#toTicks = domain.ticks;
    this.#toAxis = this.#axisOf(slice);
    const hadCurve = this.#hasCurve;
    this.#hasCurve = true;
    if (!animate || !hadCurve) {
      this.#field.jump(target);
      this.#prog.jump([1]);
      this.#fromTicks = this.#toTicks;
      this.#fromAxis = this.#toAxis;
    } else {
      const now = performance.now();
      this.#fromTicks = niceDomain(Array.from(this.#field.cur.subarray(2))).ticks;
      this.#fromAxis = prevAxis;
      this.#field.retarget(target, now);
      this.#prog.jump([0]);
      this.#prog.retarget([1], now);
    }
    this.#renderRecords(false);
    this.#renderDescription();
    this.#draw();
    this.#dirtyCursor = true;
    ticker.add(this.#tick);
  }
  #renderDescription() {
    const first = this.#slice[0];
    const last = this.#slice[this.#slice.length - 1];
    if (!first || !last) return;
    const long = (last.t - first.t) / DAY > 100;
    const since = long ? `desde ${monthYearFull(new Date(first.t))}` : `desde ${shortDate(new Date(first.t))}`;
    this.#desc = since;
    this.#dom.desc.textContent = this.#hidden() ? `Patrimônio ${since}: valores ocultos.` : `Patrimônio ${since}: de ${formatMoney(first.v)} a ${formatMoney(last.v)}, variação de ${formatDelta(last.v - first.v)}.`;
  }
  // ───────────── desenho ─────────────
  #layout() {
    const d = this.#dom;
    const n = GEOM.samples;
    const innerW = Math.max(1, this.#width - 2 * GEOM.padX);
    for (let i = 0; i < n; i++) this.#xs[i] = GEOM.padX + i / (n - 1) * innerW;
    d.svg.setAttribute("viewBox", `0 0 ${this.#width} ${GEOM.top + this.#plotH + GEOM.bottom}`);
    if (this.#state === "ready") {
      for (const [, l] of this.#xlabels) this.#placeXLabel(l);
      this.#draw();
      this.#renderRecords(false);
      this.#dirtyCursor = true;
      ticker.add(this.#tick);
    } else if (this.#state === "sparse" || this.#state === "empty") {
      this.#renderSparse();
    }
  }
  #yOf(v, min, range) {
    return GEOM.top + (1 - (v - min) / range) * this.#plotH;
  }
  #draw() {
    if (this.#state !== "ready" || this.#width <= 0) return;
    const d = this.#dom;
    const n = GEOM.samples;
    const v = this.#field.cur;
    const min = v[0];
    const range = v[1] - v[0] || 1;
    for (let i = 0; i < n; i++) this.#ys[i] = GEOM.top + (1 - (v[2 + i] - min) / range) * this.#plotH;
    this.#my = monotoneTangents(this.#ys);
    const line = monotonePath(this.#xs, this.#ys, this.#my);
    const base = GEOM.top + this.#plotH;
    d.line.setAttribute("d", line);
    d.area.setAttribute("d", `${line}L${this.#xs[n - 1].toFixed(2)} ${base}L${this.#xs[0].toFixed(2)} ${base}Z`);
    this.#updateTicks(min, range);
    this.#updateXLabels();
  }
  /** Linhas de referência (no máximo 3, pontilhadas): o que existe nos dois períodos fica parado; o resto faz crossfade. */
  #updateTicks(min, range) {
    const p = clamp(this.#prog.cur[0], 0, 1);
    const want = /* @__PURE__ */ new Map();
    for (const val of this.#toTicks) want.set(val, this.#fromTicks.includes(val) ? 1 : p);
    for (const val of this.#fromTicks) if (!want.has(val)) want.set(val, 1 - p);
    const x2 = Math.max(0, this.#width - GEOM.padX);
    for (const [val, op] of want) {
      let t = this.#ticks.get(val);
      if (!t) {
        t = { line: s("line", { class: "fp-ref" }), label: h("span", { class: "fp-tl", text: formatTick(val), ...this.#pv() }) };
        this.#dom.grid.append(t.line);
        this.#dom.labels.append(t.label);
        this.#ticks.set(val, t);
      }
      const y = this.#yOf(val, min, range);
      t.line.setAttribute("x1", GEOM.padX);
      t.line.setAttribute("x2", x2.toFixed(1));
      t.line.setAttribute("y1", y.toFixed(1));
      t.line.setAttribute("y2", y.toFixed(1));
      t.line.setAttribute("opacity", op.toFixed(3));
      t.label.style.transform = `translate3d(0,${(y - 17).toFixed(1)}px,0)`;
      t.label.style.opacity = op.toFixed(3);
    }
    for (const [val, t] of this.#ticks) {
      if (!want.has(val) || want.get(val) < 2e-3) {
        t.line.remove();
        t.label.remove();
        this.#ticks.delete(val);
      }
    }
  }
  #placeXLabel(l) {
    const f = l.fraction;
    l.el.style.left = `${(f * this.#width).toFixed(1)}px`;
    l.el.style.translate = f === 0 ? "0 0" : f === 1 ? "-100% 0" : "-50% 0";
  }
  #updateXLabels() {
    const p = clamp(this.#prog.cur[0], 0, 1);
    const key = (l) => `${l.fraction}|${l.text}`;
    const to = new Map(this.#toAxis.map((l) => [key(l), l]));
    const from = new Map(this.#fromAxis.map((l) => [key(l), l]));
    const want = /* @__PURE__ */ new Map();
    for (const [k, l] of to) want.set(k, { l, op: from.has(k) ? 1 : p });
    for (const [k, l] of from) if (!want.has(k)) want.set(k, { l, op: 1 - p });
    for (const [k, { l, op }] of want) {
      let x = this.#xlabels.get(k);
      if (!x) {
        x = { fraction: l.fraction, el: h("span", { class: "fp-xl", text: l.text }) };
        this.#placeXLabel(x);
        this.#dom.labels.append(x.el);
        this.#xlabels.set(k, x);
      }
      x.el.style.opacity = op.toFixed(3);
    }
    for (const [k, x] of this.#xlabels) {
      if (!want.has(k) || want.get(k).op < 2e-3) {
        x.el.remove();
        this.#xlabels.delete(k);
      }
    }
  }
  /** Série esparsa (≤ 45 registros no período): pontos pequenos sobre os registros reais, só quando a curva está parada. */
  #renderRecords(show) {
    const d = this.#dom;
    d.dots.replaceChildren();
    if (!show || this.#slice.length > 45 || this.#slice.length < 2) return;
    const v = this.#field.cur;
    const min = v[0];
    const range = v[1] - v[0] || 1;
    const first = this.#slice[0].t;
    const span = this.#slice[this.#slice.length - 1].t - first || 1;
    const innerW = Math.max(1, this.#width - 2 * GEOM.padX);
    for (const r of this.#slice) {
      d.dots.append(s("circle", { cx: (GEOM.padX + (r.t - first) / span * innerW).toFixed(1), cy: this.#yOf(r.v, min, range).toFixed(1), r: 2.5 }));
    }
  }
  // ───────────── laço de animação ─────────────
  #tick = (now) => {
    let alive = false;
    if (!this.#field.settled || !this.#prog.settled) {
      this.#field.sample(now);
      this.#prog.sample(now);
      this.#draw();
      alive = !this.#field.settled || !this.#prog.settled;
      if (!alive) this.#renderRecords(true);
    } else if (this.#dom.dots.childElementCount === 0 && this.#state === "ready") {
      this.#renderRecords(true);
    }
    if (this.#pendingX != null) {
      const innerW = Math.max(1, this.#width - 2 * GEOM.padX);
      this.#fraction = clamp((this.#pendingX - this.#rectLeft - GEOM.padX) / innerW, 0, 1);
      this.#pendingX = null;
      this.#dirtyCursor = true;
    }
    if (this.#dirtyCursor) {
      this.#dirtyCursor = false;
      this.#renderCursor();
    }
    return alive;
  };
  // ───────────── cursor, cabeçalho e leitura ─────────────
  #renderHeaderIdle() {
    if (this.#state === "loading") {
      this.#roll.destroy();
      this.#rollChip.destroy();
      this.#firstHeader = true;
      this.#lastValue = null;
      return;
    }
    const last = this.#series[this.#series.length - 1];
    const mp = last ? moneyParts(last.v) : null;
    this.#setHeader(mp, { immediate: true });
    this.#dom.chip.hidden = true;
    this.#dom.when.textContent = last ? `em ${fullDate(new Date(last.t))}` : "sem registros";
    this.#dom.srValue.textContent = mp ? `Patrimônio: ${mp.text}` : "";
  }
  #renderCursor() {
    if (this.#state !== "ready" || this.#slice.length === 0) return;
    const d = this.#dom;
    const n = GEOM.samples;
    if (d.chip.hidden) d.chip.hidden = false;
    const active = this.#fraction != null;
    const f = active ? this.#fraction : this.#lastFraction;
    if (active) this.#lastFraction = f;
    this.#flag("data-active", active);
    this.#flag("data-pressed", active && this.#pressed);
    const innerW = Math.max(1, this.#width - 2 * GEOM.padX);
    const x = GEOM.padX + f * innerW;
    const y = hermiteAt(this.#ys, this.#my, f * (n - 1));
    d.marker.style.transform = `translate3d(${x.toFixed(2)}px,${y.toFixed(2)}px,0)`;
    const gx = snapStroke(x, window.devicePixelRatio || 1);
    d.guide.setAttribute("x1", gx.toFixed(2));
    d.guide.setAttribute("x2", gx.toFixed(2));
    d.guide.setAttribute("y1", GEOM.top);
    d.guide.setAttribute("y2", GEOM.top + this.#plotH);
    const first = this.#slice[0];
    const last = this.#slice[this.#slice.length - 1];
    const at = active ? f : 1;
    const t = first.t + (last.t - first.t) * at;
    const r = readoutAt(this.#slice, t);
    const date = new Date(active ? t : last.t);
    const value = active ? r.value : last.v;
    const change = value - first.v;
    const pct = first.v ? change / Math.abs(first.v) * 100 : 0;
    const bias = this.#lastValue == null ? 0 : Math.sign(value - this.#lastValue);
    this.#lastValue = value;
    d.tip.style.setProperty("--tx", `${x.toFixed(1)}px`);
    const tipDate = `${shortDate(date)} ${String(date.getFullYear()).slice(2)}`;
    if (d.tipDate.textContent !== tipDate) d.tipDate.textContent = tipDate;
    const mp = moneyParts(value);
    const money = mp.text;
    if (d.tipVal.textContent !== money) d.tipVal.textContent = money;
    d.tipEst.hidden = !(active && r.estimated);
    const intro = this.#firstHeader && (!this.#roll.text || this.#rollEmpty);
    const immediate = this.#snap;
    this.#snap = false;
    this.#setHeader(mp, { bias, intro, immediate });
    this.#firstHeader = false;
    const deltaText = `${formatDelta(change)} · ${formatPercent(pct, { sign: "always" })}`;
    this.#rollChip.set(deltaText, { bias, intro, immediate });
    const pos = change >= 0;
    const cls = `fp-chip dl ${pos ? "dl-pos dl-up" : "dl-neg dl-dn"} nums`;
    if (d.chip.className !== cls) d.chip.className = cls;
    const spanDays = (last.t - first.t) / DAY;
    const since = spanDays > 100 ? `desde ${monthYearFull(new Date(first.t))}` : `desde ${shortDate(new Date(first.t))}`;
    const when = active ? `${longDate(date)} · ${since}` : `${isSameDay(new Date(last.t), /* @__PURE__ */ new Date()) ? "hoje" : "em"} ${fullDate(new Date(last.t))} · ${since}`;
    if (d.when.textContent !== when) d.when.textContent = when;
    d.srValue.textContent = `Patrimônio: ${money}, ${when}`;
    const idx = active ? r.index : -1;
    const moved = idx !== this.#nearest;
    if (moved || this.#ariaStale) {
      this.#nearest = idx;
      this.#ariaStale = false;
      d.hit.setAttribute("aria-valuemax", String(Math.max(0, this.#slice.length - 1)));
      d.hit.setAttribute("aria-valuenow", String(idx < 0 ? this.#slice.length - 1 : idx));
      d.hit.setAttribute("aria-valuetext", this.#hidden() ? `${longDate(date)}: valor oculto` : `${longDate(date)}: ${money}`);
      if (moved) {
        this.dispatchEvent(
          new CustomEvent("fp-scrub", { bubbles: true, detail: active ? { fraction: f, date, value, change, changePct: pct } : null })
        );
      }
    }
  }
  #firstHeader = true;
  /** Atributo de estado booleano no traçado (o CSS cuida do resto), só quando muda. */
  #flag(name, on) {
    const v = String(on);
    if (this.#dom.plot.getAttribute(name) !== v) this.#dom.plot.setAttribute(name, v);
  }
  // ───────────── interação ─────────────
  #bindEvents() {
    const hit = this.#dom.hit;
    hit.addEventListener("pointerenter", this.#measure);
    hit.addEventListener("pointerdown", this.#onDown);
    hit.addEventListener("pointermove", this.#onMove);
    hit.addEventListener("pointerup", this.#onUp);
    hit.addEventListener("pointercancel", () => this.#release());
    hit.addEventListener("pointerleave", (e) => {
      if (e.pointerType === "mouse" && !this.#pressed) this.#release();
    });
    hit.addEventListener("keydown", this.#onKey);
    hit.addEventListener("blur", () => this.#release());
    hit.addEventListener("lostpointercapture", () => {
      this.#pressed = false;
      this.#dirtyCursor = true;
      ticker.add(this.#tick);
    });
  }
  #measure = () => {
    this.#rectLeft = this.#dom.hit.getBoundingClientRect().left;
  };
  #onDown = (e) => {
    if (this.#state !== "ready" || e.pointerType === "mouse" && e.button !== 0) return;
    this.#measure();
    try {
      this.#dom.hit.setPointerCapture(e.pointerId);
    } catch {
    }
    this.#pressed = true;
    this.#flag("data-pressed", true);
    this.#pendingX = e.clientX;
    ticker.add(this.#tick);
  };
  #onMove = (e) => {
    if (this.#state !== "ready") return;
    if (e.pointerType === "mouse" || this.#pressed) {
      this.#pendingX = e.clientX;
      ticker.add(this.#tick);
    }
  };
  #onUp = (e) => {
    this.#pressed = false;
    this.#flag("data-pressed", false);
    if (e.pointerType !== "mouse") {
      this.#release();
      return;
    }
    const r = this.#dom.hit.getBoundingClientRect();
    const inside = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
    if (!inside) this.#release();
    else {
      this.#dirtyCursor = true;
      ticker.add(this.#tick);
    }
  };
  #release(silent = false) {
    this.#pressed = false;
    this.#flag("data-pressed", false);
    this.#pendingX = null;
    if (this.#fraction == null && silent) return;
    this.#fraction = null;
    this.#dirtyCursor = true;
    if (!silent) ticker.add(this.#tick);
    else if (this.#state === "ready") this.#renderCursor();
  }
  #onKey = (e) => {
    if (this.#state !== "ready") return;
    const last = Math.max(1, this.#slice.length - 1);
    const mult = e.shiftKey ? 7 : 1;
    let f = this.#fraction ?? 1;
    switch (e.key) {
      case "ArrowLeft":
      case "ArrowDown":
        f -= mult / last;
        break;
      case "ArrowRight":
      case "ArrowUp":
        f += mult / last;
        break;
      case "PageDown":
        f -= 30 / last;
        break;
      case "PageUp":
        f += 30 / last;
        break;
      case "Home":
        f = 0;
        break;
      case "End":
        f = 1;
        break;
      case "Escape":
        this.#release();
        return;
      default:
        return;
    }
    e.preventDefault();
    this.#fraction = clamp(f, 0, 1);
    this.#dirtyCursor = true;
    ticker.add(this.#tick);
  };
  // ───────────── controle de período ─────────────
  #buildRange() {
    const r = this.#dom.range;
    r.replaceChildren();
    r.style.setProperty("--n", String(this.#ranges.length));
    r.append(h("span", { class: "fp-pill", "aria-hidden": "true" }));
    this.#ranges.forEach((def, i) => {
      const b = h("button", { type: "button", role: "radio", class: "fp-opt", "aria-label": def.aria, text: def.label });
      b.addEventListener("click", () => this.#selectRange(def.key, true));
      b.addEventListener("keydown", (e) => {
        const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
        if (!step || this.#state !== "ready") return;
        e.preventDefault();
        const next = (i + step + this.#ranges.length) % this.#ranges.length;
        this.#selectRange(this.#ranges[next].key, true);
        r.querySelectorAll("button")[next]?.focus();
      });
      r.append(b);
    });
    this.#syncRangeUI();
  }
  #syncRangeUI() {
    const r = this.#dom.range;
    const idx = Math.max(0, this.#ranges.findIndex((x) => x.key === this.#rangeKey));
    r.style.setProperty("--i", String(idx));
    r.querySelectorAll("button").forEach((b, i) => {
      b.setAttribute("aria-checked", String(i === idx));
      b.tabIndex = i === idx ? 0 : -1;
    });
  }
  // ───────────── "poucos dados" e vazio ─────────────
  /** Nada de curva inventada: os registros que existem sobre uma trilha pontilhada, quanto falta e a ação que resolve. */
  #renderSparse() {
    const d = this.#dom;
    const w = this.#width || 600;
    const tail = this.#series.slice(-3);
    const empty = tail.length === 0;
    const lo = Math.min(...tail.map((q) => q.v));
    const hi = Math.max(...tail.map((q) => q.v));
    const narrow = w < 480;
    const stops = (narrow ? [0.72, 0.855, 0.97] : [0.6, 0.79, 0.96]).slice(3 - tail.length);
    const innerW = w - 2 * GEOM.padX;
    const dots = tail.map((q, i) => ({
      x: GEOM.padX + stops[i] * innerW,
      y: GEOM.top + this.#plotH * (0.86 - 0.3 * (hi === lo ? 0.5 : (q.v - lo) / (hi - lo)))
    }));
    const track = dots.length ? `M${dots.map((p) => `${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join("L")}` : "";
    const ghost = dots.length ? `M${GEOM.padX} ${dots[0].y.toFixed(1)}H${dots[0].x.toFixed(1)}` : "";
    const have = this.#series.length;
    const need = this.#minPoints;
    const meter = h("div", { class: "fp-meter", role: "progressbar", "aria-label": "Registros de patrimônio", "aria-valuemin": "0", "aria-valuemax": String(need), "aria-valuenow": String(Math.min(have, need)) });
    for (let i = 0; i < need; i++) meter.append(h("i", { class: i < have ? "on" : "" }));
    const href = this.getAttribute("empty-href");
    const label = this.getAttribute("empty-label") || "Registrar um saldo";
    const cta = href ? h("a", { class: "fp-link", href, text: label }) : h("button", { type: "button", class: "fp-link", text: label });
    if (!href) cta.addEventListener("click", () => this.dispatchEvent(new CustomEvent("fp-empty-action", { bubbles: true })));
    const card = h(
      "div",
      { class: "fp-card dv-tip" },
      h("span", { class: "fp-card-t", text: empty ? "Nenhum saldo registrado" : "Poucos dados ainda" }),
      h("span", {
        class: "fp-card-p",
        text: narrow ? `Com ${need} saldos registrados aparece a tendência.` : `Registre o saldo das contas e dos investimentos de tempos em tempos. Com ${need} pontos o gráfico mostra a tendência.`
      }),
      h("div", { class: "fp-card-m" }, meter, h("span", { class: "fp-card-n nums", text: `${have} de ${need}` })),
      cta
    );
    card.style.width = narrow ? "62%" : "52%";
    const svg = s(
      "svg",
      { class: "fp-svg", width: "100%", height: "100%", viewBox: `0 0 ${w} ${GEOM.top + this.#plotH + GEOM.bottom}`, "aria-hidden": "true", focusable: "false" },
      s("path", { class: "fp-ghost", d: ghost, fill: "none" }),
      s("path", { class: "fp-track", d: track, fill: "none" })
    );
    const marks = dots.map((p, i) => {
      const m = h("span", { class: "fp-spot pop", "aria-hidden": "true" });
      m.style.translate = `${p.x.toFixed(1)}px ${p.y.toFixed(1)}px`;
      m.style.animationDelay = `${200 + i * 140}ms`;
      return m;
    });
    d.sparse.replaceChildren(svg, ...marks, card);
  }
};
if (!customElements.get(TAG)) customElements.define(TAG, FpPatrimonio);
export {
  FpPatrimonio,
  parsePoints
};
