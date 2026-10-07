/**
 * fp-ui.js — small interface behaviours, no build and no dependencies. One ES module, three independent parts (each is a no-op without its markup):
 *
 *   1. .sx scroll edges ......... marks the horizontally scrollable areas that still have content beyond the edge.
 *   2. theme switcher ........... Claro / Escuro / Automático radiogroup, bound by delegation to FP.theme (fp-theme.js, a blocking <head> script).
 *   3. card tilt + glare ........ 3D parallax tilt and specular highlight on [data-card-tilt] / .card-face (cards.css draws it).
 *
 * Load as a module, same directory and same URL form as fp-money.js and charts.js:  <script type="module" src="/static/fp-ui.js"></script>
 * Everything is found by delegation or by a MutationObserver, so HTMX swaps and island renders need no re-initialisation.
 *
 * ── 1. .sx ─────────────────────────────────────────────────────────────────────────────────────────────────────────
 *   <div class="sx sx-col" role="region" aria-label="Lançamentos" tabindex="0"> …wide table… </div>
 *   Where the browser has scroll-driven animations (Chromium, Safari 26) tokens.css fades the edges with CSS alone and this part is redundant
 *   (harmless). Elsewhere (Firefox) it writes data-sx-l="1" (content hidden on the left) and data-sx-r="1" (on the right), which the same CSS reads.
 *
 * ── 2. Theme switcher ──────────────────────────────────────────────────────────────────────────────────────────────
 *   <div class="fp-theme" role="radiogroup" aria-label="Tema" data-fp-theme-group>
 *     <i aria-hidden="true"></i>
 *     <button type="button" role="radio" aria-checked="false" aria-label="Claro" data-fp-theme-option="light">…</button> … (escuro, system)
 *   (markup: snippets/theme-switcher.html; styles: fp-theme.css). State lives in FP.theme; this part only wires the controls:
 *   click · ←/↑ and →/↓ (move AND select, wrapping) · Home / End · roving tabindex (the checked radio is the one tab stop) · aria-checked kept in step
 *   (also when another tab or the OS changes the theme) · a polite live region says «Tema: Escuro» after a USER choice (never at load, never cross-tab)
 *   · the page cross-fades through the View Transitions API when it exists and prefers-reduced-motion allows it.
 *   Without FP.theme the controls stay inert and a console warning names the missing <head> script (only if a switcher is on the page).
 *
 * ── 3. Card tilt + glare ───────────────────────────────────────────────────────────────────────────────────────────
 *   <span class="card-face card-face--forest" data-card-tilt> …content… <span class="card-glare" aria-hidden="true"></span></span>
 *   (markup: snippets/card-markup.html; styles: cards.css). One passive pointermove on `document`, coalesced into ONE requestAnimationFrame per
 *   frame. While a mouse/pen is over a card the frame does only writes: it sets --tilt-x / --tilt-y (−1…1 across the card) and --glare-x / --glare-y
 *   (% of the card) with CSSOM setProperty (allowed under a strict CSP) and cards.css turns them into transform and gradient. The card's layout box
 *   is measured ONCE, when the pointer enters, and cached: no layout read per move. Hysteresis: the card lets go only 3 px outside that box, and the
 *   hit-test uses the cached box, not the tilted shape, so the far edge of a rotated card cannot flicker enter/leave.
 *   data-tilt-state on the element drives the CSS:  track (pointer over)  →  leave (spring back, 0.4 s)  →  [attribute removed].
 *   Touch: no tilt, ever. A touch press sets data-tilt-state="press" (a small lift, no rotation) until release or cancel.
 *   prefers-reduced-motion: reduce → nothing is written (and the CSS only defines the tilt inside prefers-reduced-motion: no-preference);
 *   turning the setting on while a card is tilted lets it go at once.
 *   Opt out per card: data-card-tilt="off" on the card or any ancestor.
 */

/* ═════════════ 1. .sx scroll edges ═════════════ */

const SEEN = new WeakSet();
let ro = null;
let mo = null;

/** Lê (sem escrever nada) as duas bordas de uma área `.sx`: [esquerda, direita]. */
function readEdges(el) {
  // Com barra de rolagem vertical (extrato com cabeçalho fixo) o Chromium deixa o fim da rolagem horizontal até a largura da barra
  // antes de scrollWidth - clientWidth; descontar a barra faz a borda direita apagar no fim real em qualquer navegador.
  const bar = Math.max(0, (el.offsetWidth || 0) - el.clientWidth - 2 * (el.clientLeft || 0));
  const max = el.scrollWidth - el.clientWidth;
  const x = Math.abs(el.scrollLeft);
  return [x > 1 ? '1' : '0', max > 1 && x < max - bar - 1 ? '1' : '0'];
}

function writeEdges(el, [l, r]) {
  if (el.getAttribute('data-sx-l') !== l) el.setAttribute('data-sx-l', l);
  if (el.getAttribute('data-sx-r') !== r) el.setAttribute('data-sx-r', r);
}

/** Recalcula as duas bordas de uma área `.sx`. */
export function updateEdges(el) { writeEdges(el, readEdges(el)); }

/** Várias áreas de uma vez: todas as leituras primeiro, depois as escritas (uma única passagem de layout, não uma por área). */
function updateAllEdges(list) {
  const edges = list.map(readEdges);
  list.forEach((el, i) => writeEdges(el, edges[i]));
}

const onSxScroll = (e) => updateEdges(e.currentTarget);   // a single function: adding it twice to the same area is a no-op

/** Liga o acompanhamento em toda `.sx` dentro de `root` (idempotente). */
export function scrollEdges(root) {
  const scope = root || (typeof document !== 'undefined' ? document : null);
  if (!scope || !scope.querySelectorAll) return 0;
  if (!ro && typeof ResizeObserver !== 'undefined') ro = new ResizeObserver((entries) => updateAllEdges([...new Set(entries.map((e) => (e.target.closest ? (e.target.closest('.sx') || e.target) : e.target)))].filter((el) => el.isConnected)));
  const fresh = [];
  for (const el of scope.querySelectorAll('.sx')) {
    if (SEEN.has(el)) continue;
    SEEN.add(el);
    el.addEventListener('scroll', onSxScroll, { passive: true });
    if (ro) { ro.observe(el); for (const c of el.children) ro.observe(c); }
    fresh.push(el);
  }
  updateAllEdges(fresh);
  return fresh.length;
}

/** Uma área `.sx` saiu do DOM: o ResizeObserver deixa de segurá-la (se voltar, scrollEdges a vê de novo). */
function forgetEdges(root) {
  if (!ro || !root.querySelectorAll) return;
  const gone = root.matches && root.matches('.sx') ? [root, ...root.querySelectorAll('.sx')] : [...root.querySelectorAll('.sx')];
  for (const el of gone) {
    if (el.isConnected) continue;
    SEEN.delete(el);
    ro.unobserve(el);
    for (const c of el.children) ro.unobserve(c);
  }
}

/** scrollEdges agora e para tudo que o DOM ganhar depois (HTMX, re-render). Chamar duas vezes não duplica. */
export function observe() {
  if (typeof document === 'undefined' || mo) return;
  scrollEdges(document);
  syncThemeControls(document);
  let queued = false;
  // Only new ELEMENTS can bring a new .sx or a new theme switcher: text updates (tooltips, figures) are ignored, so scrubbing a chart costs nothing here.
  mo = new MutationObserver((records) => {
    let added = false;
    for (const rec of records) {
      for (const n of rec.removedNodes) if (n.nodeType === 1) forgetEdges(n);
      if (!added) for (const n of rec.addedNodes) if (n.nodeType === 1) { added = true; break; }
    }
    if (!added || queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; scrollEdges(document); syncThemeControls(document); });
  });
  mo.observe(document.documentElement, { childList: true, subtree: true });
}

/* ═════════════ 2. Theme switcher ═════════════ */

const THEME_GROUP = '[data-fp-theme-group]';
const THEME_OPT = '[data-fp-theme-option]';

let themeBound = false;
let userPick = false;       // true between a click/key choice and the fp-theme event it causes: only then is the choice announced
let themeLive = null;

function themeApi() {
  return (typeof window !== 'undefined' && window.FP && window.FP.theme && typeof window.FP.theme.set === 'function') ? window.FP.theme : null;
}

function reducedMotion() {
  return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function setAttr(el, name, value) { if (el.getAttribute(name) !== value) el.setAttribute(name, value); }

/** aria-checked + roving tabindex on every switcher under `root`, from the current choice. Idempotent: writes only what changed. Returns the groups seen. */
export function syncThemeControls(root) {
  const T = themeApi();
  const scope = root || (typeof document !== 'undefined' ? document : null);
  if (!T || !scope || !scope.querySelectorAll) return 0;
  const mode = T.get();
  let n = 0;
  for (const g of scope.querySelectorAll(THEME_GROUP)) {
    const opts = [...g.querySelectorAll(THEME_OPT)];
    let any = false;
    for (const b of opts) {
      const on = b.getAttribute('data-fp-theme-option') === mode;
      any = any || on;
      setAttr(b, 'aria-checked', on ? 'true' : 'false');
      setAttr(b, 'tabindex', on ? '0' : '-1');
    }
    if (!any && opts[0]) setAttr(opts[0], 'tabindex', '0');   // a choice this group does not offer: keep one tab stop
    n++;
  }
  return n;
}

/** pt-BR sentence for the live region. */
export function themeMessage(detail, labels) {
  const L = labels || { light: 'Claro', dark: 'Escuro', system: 'Automático' };
  const m = detail && detail.mode;
  if (m === 'system') return `Tema: ${L.system}, ${detail.resolved === 'dark' ? 'escuro' : 'claro'} agora`;
  return `Tema: ${L[m] || ''}`.trim();
}

function announceTheme(text) {
  if (!text || typeof document === 'undefined') return;
  themeLive = (themeLive && themeLive.isConnected) ? themeLive : document.querySelector('[data-fp-theme-live]');
  if (!themeLive && document.body) {
    themeLive = document.createElement('div');
    themeLive.setAttribute('data-fp-theme-live', '');
    themeLive.setAttribute('role', 'status');
    themeLive.setAttribute('aria-live', 'polite');
    themeLive.className = 'fp-theme-live';
    document.body.appendChild(themeLive);
  }
  if (themeLive) { themeLive.textContent = ''; themeLive.textContent = text; }   // clear first: the same sentence twice is still announced
}

const noop = () => {};

function choose(mode) {
  const T = themeApi();
  if (!T || T.MODES.indexOf(mode) === -1 || mode === T.get()) return;
  userPick = true;
  const run = () => T.set(mode);
  if (typeof document.startViewTransition === 'function' && !reducedMotion()) {
    try {
      const vt = document.startViewTransition(run);
      if (vt) for (const k of ['ready', 'finished', 'updateCallbackDone']) if (vt[k] && vt[k].catch) vt[k].catch(noop);   // a skipped transition rejects: not an error here
      return;
    } catch (e) { /* fall back to the plain change */ }
  }
  run();
}

function onThemeClick(e) {
  const b = e.target && e.target.closest ? e.target.closest(THEME_OPT) : null;
  if (b) choose(b.getAttribute('data-fp-theme-option'));
}

function onThemeKey(e) {
  const b = e.target && e.target.closest ? e.target.closest(THEME_OPT) : null;
  if (!b || e.altKey || e.ctrlKey || e.metaKey) return;
  const g = b.closest(THEME_GROUP);
  if (!g) return;
  const opts = [...g.querySelectorAll(THEME_OPT)];
  const i = opts.indexOf(b);
  let to = -1;
  switch (e.key) {
    case 'ArrowRight': case 'ArrowDown': to = (i + 1) % opts.length; break;
    case 'ArrowLeft': case 'ArrowUp': to = (i - 1 + opts.length) % opts.length; break;
    case 'Home': to = 0; break;
    case 'End': to = opts.length - 1; break;
    default: return;                                 // Space / Enter on a <button> already click
  }
  e.preventDefault();
  opts[to].focus();
  choose(opts[to].getAttribute('data-fp-theme-option'));
}

function onThemeChange(detail) {
  syncThemeControls(document);
  if (userPick) { userPick = false; announceTheme(themeMessage(detail, themeApi() && themeApi().labels)); }
}

/** Binds every switcher on the page, now and later (delegation). Returns false when FP.theme is missing. Idempotent. */
export function bindTheme() {
  if (themeBound || typeof document === 'undefined') return themeBound;
  const T = themeApi();
  if (!T) {
    if (document.querySelector(THEME_GROUP) && typeof console !== 'undefined') console.warn('[fp-ui] theme switcher found but FP.theme is missing: load fp-theme.js as a blocking script in <head>.');
    return false;
  }
  themeBound = true;
  document.addEventListener('click', onThemeClick);
  document.addEventListener('keydown', onThemeKey);
  T.subscribe(onThemeChange);
  syncThemeControls(document);
  return true;
}

/* ═════════════ 3. Card tilt + glare ═════════════ */

const TILT_SEL = '[data-card-tilt],.card-face';
const TILT_EXIT = 3;          // px beyond the cached layout box before the card lets go (hysteresis against edge flicker)
const TILT_SETTLE = 700;      // ms: safety net when transitionend never fires (display:none mid-spring, detached node)

/**
 * Pointer position → tilt and glare values for a layout box. Pure.
 * tx, ty: −1…1 from the box's left/top edge to its right/bottom edge (0 = centre), clamped.
 * gx, gy: the pointer as a percentage of the box, clamped to 0…100.
 */
export function tiltFrom(rect, x, y) {
  const w = rect.width || 1;
  const h = rect.height || 1;
  const cl = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
  const nx = (x - rect.left) / w;
  const ny = (y - rect.top) / h;
  return { tx: cl(nx * 2 - 1, -1, 1), ty: cl(ny * 2 - 1, -1, 1), gx: cl(nx, 0, 1) * 100, gy: cl(ny, 0, 1) * 100 };
}

/** Is (x, y) inside the box grown by `margin` px on every side? Pure. */
export function within(rect, x, y, margin = 0) {
  return x >= rect.left - margin && x <= rect.left + rect.width + margin && y >= rect.top - margin && y <= rect.top + rect.height + margin;
}

let tiltBound = false;
let cur = null;               // { el, rect } — the card under a mouse/pen pointer
let pressed = null;           // the card under a touch press
let frame = 0;
let px = 0, py = 0, ptarget = null;
const layout = new WeakMap(); // last measured layout box per card (re-used when the pointer comes back mid-spring)
const timers = new WeakMap();
let mqReduce = null;

function motionOk() { return !(mqReduce && mqReduce.matches); }

function optedOut(el) { return !!el.closest('[data-card-tilt="off"]'); }

function related(el, t) { return !!t && (t === el || (t.nodeType === 1 && (el.contains(t) || t.contains(el)))); }

function measure(el) {
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 ? { left: r.left, top: r.top, width: r.width, height: r.height } : null;
}

function clearTimer(el) { const t = timers.get(el); if (t) { clearTimeout(t); timers.delete(el); } }

/** End of the spring: drop the state and the inline variables so the element is exactly as authored again. */
function settle(el) {
  clearTimer(el);
  el.removeEventListener('transitionend', onSpringEnd);
  if (el.getAttribute('data-tilt-state') !== 'leave') return;       // came back (track/press) meanwhile
  el.removeAttribute('data-tilt-state');
  for (const p of ['--tilt-x', '--tilt-y', '--glare-x', '--glare-y']) el.style.removeProperty(p);
  if (!el.getAttribute('style')) el.removeAttribute('style');
}

function onSpringEnd(e) {
  if (e.target === e.currentTarget && e.propertyName === 'transform') settle(e.currentTarget);
}

function startLeave(el) {
  el.setAttribute('data-tilt-state', 'leave');
  el.addEventListener('transitionend', onSpringEnd);
  clearTimer(el);
  timers.set(el, setTimeout(() => settle(el), TILT_SETTLE));
}

/** The box of a card at rest. While it is tracked or springing back it is tilted: then the last at-rest measurement is trusted instead. */
function restBox(el) {
  const tilted = el.getAttribute('data-tilt-state') === 'leave' || (cur !== null && cur.el === el);
  return tilted && layout.get(el) ? layout.get(el) : measure(el);
}

/** The card under the pointer that may be entered now, or null. */
function candidate(t) {
  if (!t || !t.closest || pressed) return null;
  const el = t.closest(TILT_SEL);
  return el && !optedOut(el) ? el : null;
}

/** Hysteresis, entry side: the pointer must be inside the layout box itself (leaving needs TILT_EXIT px beyond it). `rect` was read before any write of the frame. */
function enterTilt(el, rect) {
  if (!rect || !within(rect, px, py, 0)) return;
  layout.set(el, rect);
  clearTimer(el);
  el.removeEventListener('transitionend', onSpringEnd);
  cur = { el, rect };
  el.setAttribute('data-tilt-state', 'track');
}

function leaveTilt() {
  if (!cur) return;
  const { el } = cur;
  cur = null;
  startLeave(el);
}

/** Let go of everything now (reduced motion switched on, window blurred, scroll, resize). `now` = skip the spring. */
export function releaseTilt(now) {
  if (cur) { const { el } = cur; cur = null; if (now) { clearTimer(el); el.setAttribute('data-tilt-state', 'leave'); settle(el); } else startLeave(el); }
  if (pressed) { const el = pressed; pressed = null; if (now) { clearTimer(el); el.setAttribute('data-tilt-state', 'leave'); settle(el); } else startLeave(el); }
}

function paintTilt() {
  const { el, rect } = cur;
  const v = tiltFrom(rect, px, py);
  const s = el.style;
  s.setProperty('--tilt-x', v.tx.toFixed(3));
  s.setProperty('--tilt-y', v.ty.toFixed(3));
  s.setProperty('--glare-x', v.gx.toFixed(1) + '%');   // the unit lives in the value: `var(--x)%` is not valid CSS
  s.setProperty('--glare-y', v.gy.toFixed(1) + '%');
}

/** One animation frame: the only layout read is the box of a card being ENTERED, taken before the frame writes anything; a card already
 *  tracked reads nothing (its box was cached on enter) and the frame just writes four custom properties. */
function onFrame() {
  frame = 0;
  if (!motionOk()) { releaseTilt(true); return; }
  const t = ptarget;
  const leaving = cur && !(cur.el.isConnected && within(cur.rect, px, py, TILT_EXIT) && related(cur.el, t));
  const next = !cur || leaving ? candidate(t) : null;
  const nextRect = next ? restBox(next) : null;   // read first: leaving the old card writes, and a read after a write forces a layout
  if (leaving) leaveTilt();
  if (!cur && next) enterTilt(next, nextRect);
  if (cur) paintTilt();
}

function cancelFrame() { if (frame) { cancelAnimationFrame(frame); frame = 0; } }

function onMove(e) {
  if (e.pointerType === 'touch') return;
  px = e.clientX; py = e.clientY; ptarget = e.target;
  if (!frame) frame = requestAnimationFrame(onFrame);
}

function onDown(e) {
  if (e.pointerType !== 'touch' || !e.target || !e.target.closest || !motionOk()) return;
  const el = e.target.closest(TILT_SEL);
  if (!el || optedOut(el)) return;
  releaseTilt(true);
  pressed = el;
  clearTimer(el);
  el.removeEventListener('transitionend', onSpringEnd);
  el.setAttribute('data-tilt-state', 'press');
}

function onUp() {
  if (!pressed) return;
  const el = pressed;
  pressed = null;
  if (el.getAttribute('data-tilt-state') === 'press') startLeave(el);
}

function onOut(e) { if (!e.relatedTarget && cur) leaveTilt(); }   // the pointer left the window

/** Binds the tilt for every card on the page, now and later (delegation). Idempotent. */
export function bindTilt() {
  if (tiltBound || typeof document === 'undefined' || typeof window === 'undefined') return tiltBound;
  tiltBound = true;
  mqReduce = typeof matchMedia === 'function' ? matchMedia('(prefers-reduced-motion: reduce)') : null;
  const passive = { passive: true };
  document.addEventListener('pointermove', onMove, passive);
  document.addEventListener('pointerdown', onDown, passive);
  document.addEventListener('pointerup', onUp, passive);
  document.addEventListener('pointercancel', onUp, passive);
  document.addEventListener('pointerout', onOut, passive);
  window.addEventListener('blur', () => { cancelFrame(); releaseTilt(false); });
  document.addEventListener('visibilitychange', () => { if (document.hidden) { cancelFrame(); releaseTilt(true); } });   // a frame queued before the tab hid must not replay a stale pointer
  window.addEventListener('resize', () => releaseTilt(true), passive);
  window.addEventListener('scroll', () => releaseTilt(false), { passive: true, capture: true });   // the card moved under a still pointer
  if (mqReduce) {
    const onMotion = () => { if (mqReduce.matches) releaseTilt(true); };
    if (mqReduce.addEventListener) mqReduce.addEventListener('change', onMotion); else if (mqReduce.addListener) mqReduce.addListener(onMotion);
  }
  return true;
}

/* Dialogs: while one opens it gets data-opening, which turns on will-change (components.css); it is removed when the opening
   animation or transition ends (or after a fallback) so the layer is released. Only opacity and transform are ever animated. */
const OPENING_FALLBACK_MS = 400;
function settleDialog(dlg) {
  if (!dlg.hasAttribute('data-opening')) return;
  dlg.removeAttribute('data-opening');
}
function markOpening(dlg) {
  if (!dlg.open || dlg.hasAttribute('data-opening')) return;
  dlg.setAttribute('data-opening', '');
  const done = (ev) => {
    if (ev && ev.target !== dlg) return;      // a child's animation ending is not the dialog's
    dlg.removeEventListener('animationend', done);
    dlg.removeEventListener('transitionend', done);
    dlg.removeEventListener('animationcancel', done);
    settleDialog(dlg);
  };
  dlg.addEventListener('animationend', done);
  dlg.addEventListener('transitionend', done);
  dlg.addEventListener('animationcancel', done);
  setTimeout(() => done(null), OPENING_FALLBACK_MS);
}
function bindDialogs() {
  if (typeof MutationObserver === 'undefined' || document.documentElement.hasAttribute('data-fp-dialogs')) return false;
  document.documentElement.setAttribute('data-fp-dialogs', '');
  document.querySelectorAll('dialog[open]').forEach(markOpening);
  new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === 'attributes') {
        const dlg = r.target;
        if (dlg.open) markOpening(dlg); else settleDialog(dlg);
      }
    }
  }).observe(document.documentElement, { subtree: true, attributes: true, attributeFilter: ['open'] });
  return true;
}

export const ui = Object.freeze({
  updateEdges, scrollEdges, observe, dialogs: Object.freeze({ bind: bindDialogs, opening: markOpening }),
  theme: Object.freeze({ bind: bindTheme, sync: syncThemeControls, message: themeMessage }),
  tilt: Object.freeze({ bind: bindTilt, release: releaseTilt, from: tiltFrom, within }),
});

if (typeof window !== 'undefined') {
  window.FP = window.FP || {};
  window.FP.ui = ui;
}
if (typeof document !== 'undefined') {
  const boot = () => { observe(); bindTheme(); bindTilt(); bindDialogs(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
}
