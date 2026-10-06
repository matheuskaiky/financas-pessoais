/*
 * fp-scrub.js — behaviour of the server-rendered charts (charts-extra.css + snippets/charts-extra.html): scrub, keyboard, hover / pin, rolling number.
 * ES module, no dependencies, no inline style: the cursor is moved with CSS variables written through the CSSOM (allowed by a strict CSP).
 *
 * The server sends, for every point, a <template data-p="i"> with the finished pieces (header number, chips, tooltip, aria text, cursor position as a %),
 * so this file does no money, date or percent formatting: it swaps what the server already wrote in the user's locale.
 *
 *   ritmo · saldo · fluxo   .cx-hit (role="slider") + template[data-p] → pointer scrub (mouse hover, touch drag), keyboard (←/→ 1 · PgUp/PgDn page · Home/End · Esc)
 *   donut                   .cx-seg + .cx-rw + template[data-g]        → hover (transient), click / Enter (pin), «Demais categorias» (open)
 *
 *   window.FP.charts = { scan(root?), init(el), get(el), scrubTo(el, i), release(el) }   (scrubTo/release are for tests and for pages that drive a chart)
 * Touch: the finger drags the cursor, lifting it releases. Reduced motion: the springs are zeroed by tokens.css; nothing here animates by itself.
 */

const instances = new WeakMap();
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/* ───────────── the rolling number ───────────── */

function el(tag, cls, text, attrs) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text) n.textContent = text;
  if (attrs) for (const k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
  return n;
}

/** Same DOM as the `_od` macro, built from the compact descriptor {s sign, y symbol, p b|a, g gap?, c amount, t tail index, l label}. */
function buildOdo(d, group) {
  const priv = group ? { 'data-private': group } : null;
  const root = el('span', 'cx-od');
  if (d.l) root.append(el('span', 'vh', d.l, priv));                 // the accessible name (see the _od macro: privacy mode swaps it, not us)
  if (d.s) root.append(el('span', 'cx-od-sign', d.s, { 'aria-hidden': 'true', ...priv }));
  const sym = (pos) => el('span', 'cx-od-sym', d.y, { 'aria-hidden': 'true', 'data-pos': pos, 'data-gap': d.g ? null : 'none' });
  if (d.y && d.p !== 'a') root.append(sym('before'));
  const num = el('span', 'cx-od-n', '', { 'aria-hidden': 'true', ...priv });
  let host = num;
  let k = 0;
  Array.from(d.c).forEach((ch, i) => {
    if (d.t >= 0 && i === d.t) { host = el('span', 'cx-od-tail'); num.append(host); }
    if (ch >= '0' && ch <= '9') {
      const cell = el('span', 'od-d', '', { 'data-n': ch, 'data-k': String(k++) });
      cell.append(el('span', 'od-sp', ch), el('span', 'od-s'));
      host.append(cell);
    } else host.append(el('span', 'cx-od-c', ch));
  });
  root.append(num);
  if (d.y && d.p === 'a') root.append(sym('after'));
  return root;
}

/** What makes two numbers roll into each other instead of being replaced: same sign, symbol, separators and digit count. */
const shape = (n) => Array.from(n.querySelectorAll('.cx-od-sign,.cx-od-sym,.cx-od-tail,.od-d,.cx-od-c')).map((x) => (x.classList.contains('od-d') ? '#' : x.classList.contains('cx-od-tail') ? '~' : x.textContent)).join('|');

/** Writes `next` (.cx-od) into the slot `live`: same shape → only data-n changes, so the strips slide (the spring is CSS); otherwise the cells are replaced and roll in from 0. */
function roll(live, next) {
  const cur = live.querySelector('.cx-od');
  if (cur && shape(cur) === shape(next)) {
    const a = cur.querySelectorAll('.od-d');
    const b = next.querySelectorAll('.od-d');
    for (let i = 0; i < a.length; i++) {
      if (a[i].dataset.n === b[i].dataset.n) continue;
      a[i].dataset.n = b[i].dataset.n;
      const sp = a[i].firstElementChild;                               // .od-sp: the spacer glyph that gives the cell its width (and the text if the strip is missing) follows the digit
      if (sp) sp.textContent = b[i].dataset.n;
    }
    const name = next.querySelector('.vh');
    const now = cur.querySelector('.vh');
    if (name && now && now.textContent !== name.textContent) now.textContent = name.textContent;
  } else live.replaceChildren(next);
}

/* ───────────── pieces shared by the controllers ───────────── */

/** Writes the finished pieces of a source (template content, or the captured idle clones) into the live slots. */
function write(slots, group, src) {
  for (const s of src.querySelectorAll('[data-slot]')) {
    const live = slots[s.dataset.slot];
    if (!live) continue;
    if (s.dataset.slot === 'value') {
      roll(live, s.dataset.o ? buildOdo(JSON.parse(s.dataset.o), group) : s.querySelector('.cx-od').cloneNode(true));
      if (s.dataset.tone != null) live.dataset.tone = s.dataset.tone;
    } else {
      if (s.dataset.slot === 'c1' || s.dataset.slot === 'c2') live.className = s.className;
      live.replaceChildren(...Array.from(s.childNodes, (c) => c.cloneNode(true)));
    }
  }
}

/** Live slots of a chart (templates are inert and are not reached by querySelectorAll), and a frozen copy of each: the "idle" state. */
function capture(root) {
  const slots = {};
  const idle = document.createDocumentFragment();
  for (const s of root.querySelectorAll('[data-slot]')) {
    if (s.closest('template')) continue;
    slots[s.dataset.slot] = s;
    idle.append(s.cloneNode(true));
  }
  return { slots, idle };
}

/* ───────────── scrub: ritmo · saldo · fluxo ───────────── */

class Scrub {
  constructor(root) {
    this.root = root;
    this.kind = root.dataset.cx;
    this.group = root.dataset.pg || '';
    this.plot = root.querySelector('.cx-plot');
    this.hit = this.plot.querySelector('.cx-hit');
    this.tip = this.plot.querySelector('.cx-tip');
    const d = this.plot.dataset;
    this.n = +d.n;
    this.W = +d.w;
    this.padl = +(d.padl || 0);
    this.padr = +(d.padr || 0);
    this.days = +(d.days || d.n);
    this.page = +(d.page || 7);
    this.first = +(d.first || 0);
    this.tpl = new Map();
    for (const t of this.plot.querySelectorAll(':scope > template[data-p]')) this.tpl.set(+t.dataset.p, t);
    const ids = Array.from(this.tpl.keys()).sort((a, b) => a - b);
    this.lo = ids[0];
    this.hi = ids[ids.length - 1];
    const live = capture(root);
    this.slots = live.slots;
    this.idle = live.idle;
    this.vNow = this.hit.getAttribute('aria-valuenow');
    this.vText = this.hit.getAttribute('aria-valuetext');
    this.home = this.kind === 'saldo' ? +d.today : this.kind === 'fluxo' ? this.sel0() : this.hi;   // the index the idle header shows
    this.cur = null;          // index the cursor is on (null = idle)
    this.shown = null;        // index whose pieces are written (null = idle pieces)
    this.pending = undefined; // coalesced by requestAnimationFrame
    this.raf = 0;
    this.rect = null;
    this.half = new Map();
    if (this.kind === 'fluxo') this.plot.style.setProperty('--st', d.step);
    this.place(this.home);
    this.bind();
  }

  sel0() {
    const g = this.plot.querySelector('.cx-col[data-sel]');
    return g ? +g.dataset.i : this.hi;
  }

  bind() {
    const h = this.hit;
    h.addEventListener('pointerenter', (e) => { this.rect = null; this.pointer(e); });
    h.addEventListener('pointermove', (e) => this.pointer(e));
    h.addEventListener('pointerdown', (e) => { this.rect = null; this.root.dataset.pressed = 'true'; this.pointer(e); });
    h.addEventListener('pointerup', (e) => { delete this.root.dataset.pressed; if (e.pointerType === 'touch') this.queue(null); });
    h.addEventListener('pointercancel', () => { delete this.root.dataset.pressed; this.queue(null); });
    h.addEventListener('pointerleave', () => { delete this.root.dataset.pressed; this.queue(null); });
    h.addEventListener('keydown', (e) => this.key(e));
    h.addEventListener('focus', () => { if (h.matches(':focus-visible')) this.queue(this.cur == null ? this.home : this.cur); });
    h.addEventListener('blur', () => this.queue(null));
    if (typeof ResizeObserver === 'function') new ResizeObserver(() => { this.rect = null; this.half.clear(); }).observe(this.plot);
  }

  /** Pointer x → index, as the canvas does it (Ritmo/Saldo: nearest day inside the padded plot; Fluxo: the column under the pointer). */
  index(clientX) {
    const r = this.rect || (this.rect = this.plot.getBoundingClientRect());
    const raw = (clientX - r.left) / Math.max(1, r.width);
    if (this.kind === 'fluxo') return clamp(Math.floor(raw * this.n), 0, this.n - 1);
    const f = clamp((raw * this.W - this.padl) / (this.W - this.padl - this.padr), 0, 1);
    return Math.min(this.hi, Math.round(f * (this.days - 1)));
  }

  pointer(e) {
    this.queue(this.index(e.clientX));
  }

  key(e) {
    const base = this.cur == null ? this.home : this.cur;
    const step = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1, PageUp: this.page, PageDown: -this.page }[e.key];
    let to;
    if (step != null) to = base + step;
    else if (e.key === 'Home') to = this.lo;
    else if (e.key === 'End') to = this.hi;
    else if (e.key === 'Escape') { if (this.cur != null) { e.preventDefault(); this.queue(null); } return; } else return;
    e.preventDefault();
    this.queue(clamp(to, this.lo, this.hi));
  }

  /** One DOM write per frame, whatever the pointer rate. */
  queue(i) {
    this.pending = i;
    if (!this.raf) this.raf = requestAnimationFrame(() => { this.raf = 0; if (this.root.isConnected) this.apply(this.pending); });
  }

  /** The cursor and the tooltip follow the point (CSS variables on the plot), the header takes its pieces. */
  place(i) {
    const t = this.tpl.get(i);
    if (!t) return null;
    const p = this.plot.style;
    if (this.kind === 'fluxo') { p.setProperty('--i', String(i)); p.setProperty('--x', t.dataset.x + '%'); } else {
      p.setProperty('--x', t.dataset.x + '%');
      p.setProperty('--y', t.dataset.y + '%');
      p.setProperty('--xu', (parseFloat(t.dataset.x) * this.W / 100).toFixed(2));
      if (t.dataset.y2) p.setProperty('--y2', t.dataset.y2 + '%');
    }
    return t;
  }

  apply(i) {
    const R = this.root;
    if (i != null && (i < this.lo || i > this.hi || !this.tpl.has(i))) i = null;   // Fluxo: a column without history is not a target
    if (i === this.cur && (i != null || this.shown == null)) return;
    this.cur = i;
    if (i == null) {
      delete R.dataset.active;
      this.mark(null);
      if (this.shown != null) { write(this.slots, this.group, this.idle); this.shown = null; }
      this.hit.setAttribute('aria-valuenow', this.vNow);
      this.hit.setAttribute('aria-valuetext', this.vText);
      if (this.kind === 'saldo') delete R.dataset.proj;
      return;
    }
    const t = this.place(i);
    write(this.slots, this.group, t.content);
    this.shown = i;
    this.mark(i);
    if (this.kind === 'saldo') { if (t.dataset.proj) R.dataset.proj = 'true'; else delete R.dataset.proj; }
    this.hit.setAttribute('aria-valuenow', String(i + 1));
    this.hit.setAttribute('aria-valuetext', t.dataset.vt || '');
    if (this.tip) {
      let w = this.half.get(i);
      if (w == null) { w = Math.ceil(this.tip.offsetWidth / 2) + 2; this.half.set(i, w); }
      this.plot.style.setProperty('--half', w + 'px');
    }
    R.dataset.active = 'true';
  }

  /** Fluxo: the column under the pointer is lit, the others dimmed; the month name is bold. Other charts: nothing to mark. */
  mark(i) {
    if (this.kind !== 'fluxo') return;
    const on = i == null ? null : i;
    for (const n of this.plot.querySelectorAll('.cx-col,.cx-fl-lab')) {
      if (on != null && +n.dataset.i === on) n.dataset.on = ''; else delete n.dataset.on;
    }
    const sel = on == null ? this.home : on;
    for (const n of this.plot.querySelectorAll('.cx-fm')) {
      if (+n.dataset.i === sel && !n.hasAttribute('data-ghost')) n.dataset.sel = ''; else delete n.dataset.sel;
    }
  }
}

/* ───────────── rosca: hover (transient), pin (sticky), open «Demais categorias» ───────────── */

class Donut {
  constructor(root) {
    this.root = root;
    this.group = root.dataset.pg || '';
    this.segs = Array.from(root.querySelectorAll('.cx-seg'));
    this.rows = Array.from(root.querySelectorAll('.cx-rw'));
    this.more = root.querySelector('.cx-more');
    this.tpl = new Map(Array.from(root.querySelectorAll(':scope > template[data-g]'), (t) => [t.dataset.g, t]));
    const live = capture(root);
    this.slots = live.slots;
    this.idle = live.idle;
    this.hover = null;
    this.pin = null;
    this.open = false;
    this.hot = null;
    const toggle = (id, rest) => {
      if (rest) { this.open = !this.open; this.pin = this.open ? id : null; } else this.pin = this.pin === id ? null : id;
      this.hover = null;
      this.render();
    };
    const over = (id) => { if (this.hover !== id) { this.hover = id; this.render(); } };
    const out = () => { if (this.hover != null) { this.hover = null; this.render(); } };
    for (const s of this.segs) {
      const id = s.dataset.id;
      s.addEventListener('pointermove', (e) => { if (e.pointerType !== 'touch') over(id); });
      s.addEventListener('pointerleave', out);
      s.addEventListener('click', () => toggle(id, false));
    }
    for (const r of this.rows) {
      const id = r.dataset.id;
      r.addEventListener('pointermove', (e) => { if (e.pointerType !== 'touch') over(id); });
      r.addEventListener('pointerleave', out);
      r.addEventListener('focus', () => over(id));
      r.addEventListener('blur', out);
      r.addEventListener('click', () => toggle(id, r.hasAttribute('data-rest')));
    }
    this.render();
  }

  render() {
    const hot = this.hover != null ? this.hover : this.pin;
    const R = this.root;
    if (hot !== this.hot) {
      this.hot = hot;
      if (hot != null) R.dataset.hot = ''; else delete R.dataset.hot;
      for (const s of this.segs) {
        const on = s.dataset.id === hot;
        const g = (on ? s.dataset.gh : s.dataset.gr).split(' ');
        s.setAttribute('stroke-width', g[0]);
        s.setAttribute('stroke-dasharray', g[1] + ' ' + g[2]);
        s.setAttribute('stroke-dashoffset', g[3]);
        if (on) s.dataset.on = ''; else delete s.dataset.on;
      }
      for (const r of this.rows) { if (r.dataset.id === hot) r.dataset.on = ''; else delete r.dataset.on; }
      const t = hot != null ? this.tpl.get(hot) : null;
      write(this.slots, this.group, t ? t.content : this.idle);
    }
    for (const r of this.rows) {
      const rest = r.hasAttribute('data-rest');
      r.setAttribute('aria-pressed', String(rest ? this.open : this.pin === r.dataset.id));
    }
    if (this.more) { this.more.dataset.open = String(this.open); this.more.setAttribute('aria-hidden', String(!this.open)); }
  }
}

/* ───────────── wiring ───────────── */

function init(root) {
  if (!root || instances.has(root)) return instances.get(root) || null;
  const kind = root.dataset.cx;
  let c = null;
  try {
    if (kind === 'donut' && root.querySelector('.cx-seg')) c = new Donut(root);
    else if ((kind === 'ritmo' || kind === 'fluxo' || kind === 'saldo') && root.querySelector('.cx-hit')) c = new Scrub(root);
  } catch (err) {
    console.error('fp-scrub:', err);
  }
  if (c) instances.set(root, c);
  return c;
}

function scan(node) {
  const base = node && node.querySelectorAll ? node : document;
  if (base.matches && base.matches('.cx[data-cx]')) init(base);
  for (const r of base.querySelectorAll('.cx[data-cx]')) init(r);
}

const api = {
  scan,
  init,
  get: (r) => instances.get(r) || null,
  /** Puts the cursor of a chart on point i and applies it right away (no frame wait). */
  scrubTo(r, i) { const c = init(r); if (c && c.apply) c.apply(i); return !!c; },
  release(r) { const c = instances.get(r); if (c && c.apply) c.apply(null); },
};
const FP = (window.FP = window.FP || {});
FP.charts = api;

function boot() {
  scan(document);
  new MutationObserver((list) => {
    for (const m of list) for (const n of m.addedNodes) if (n.nodeType === 1) scan(n);
  }).observe(document.body, { childList: true, subtree: true });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
else boot();

export { api as charts, buildOdo };
