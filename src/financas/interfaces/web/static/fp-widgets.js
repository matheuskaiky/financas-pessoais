/**
 * fp-widgets.js — behaviour for components.css. One ES module, no build, no dependencies, strict-CSP safe (it writes ARIA attributes, `hidden`, `inert`, `data-*`
 * and CSS custom properties through the CSSOM; never a `style` attribute string, never inline script). Everything is found by delegation or by one
 * MutationObserver, so HTMX swaps need no re-initialisation. Each part is a no-op without its markup. Same directory and URL form as fp-ui.js / fp-money.js:
 *
 *   <script type="module" src="/static/fp-widgets.js"></script>
 *
 * ── 1  RADIO GROUP    <div class="seg" role="radiogroup" data-fp-name="conta"><i></i><button role="radio" aria-checked="true" data-value="bbc">…</button>…</div>
 *       click · ←↑ / →↓ move AND select (wrapping) · Home / End · roving tabindex · aria-checked. Mirrors data-value into <input type="hidden" name="conta"> inside the group
 *       (or the one named by data-fp-input="#id") and fires real `input` + `change` events on it, so <form hx-trigger="change"> works. Also fires `fp:change` on the group.
 *       data-fp-deselect: a second tap on the checked radio clears the group (category chips: "nenhuma vem marcada"; the mirror becomes "").
 *       data-fp-reveal: after a selection, and the first time the group is seen (an HTMX swap that arrives with a checked chip), the checked radio is scrolled into the
 *       visible part of its horizontal scroller (smooth, or instant under prefers-reduced-motion).
 *       Skipped: [data-fp-theme-group] (fp-ui.js owns it).
 * ── 2  TOGGLE        <button class="chipb" aria-pressed="false" data-fp-toggle data-value="x">  ·  exclusive set: data-fp-exclusive on the parent  ·  mirror: data-fp-input on the parent
 * ── 3  SWITCH        <button class="swrow" role="switch" aria-checked="true" data-fp-name="x"> … <span class="sw"><i></i></span></button> — click / Space / Enter; mirrors "1" / "0".
 * ── 4  STEPPER       <div class="stp" data-fp-min="1" data-fp-max="24" [data-fp-stepsize="1"] [data-fp-input="#id"]><button data-fp-step="-1">…</button><output>12</output><button data-fp-step="1">…</button></div>
 *       the bound buttons get aria-disabled (not disabled: focus is never lost); <output> is a live region by itself.
 * ── 5  DISCLOSURE    <button data-fp-fold aria-controls="x" aria-expanded="false"> + <div class="fold" id="x"><div>…</div></div>  (flips data-open; collapsed content is inert)
 * ── 6  TABS          <div role="tablist"><button role="tab" aria-selected="true" aria-controls="panel">…</button>…</div> — arrows / Home / End, roving tabindex; button tabs switch their
 *       panels (hidden) and select on arrow; <a role="tab" href> tabs only move focus (Enter follows the link). data-fp-manual on the tablist = never select on arrow.
 * ── 7  MASKS         <input data-fp-mask="cents" inputmode="numeric" data-fp-input="#valor_centavos">  integer minor units (FP.money decides separators and places);
 *                     <input data-fp-mask="date" inputmode="numeric" placeholder="dd/mm/aaaa" data-fp-input="#data_iso">  progressive dd/mm/aaaa, ISO in the mirror, aria-invalid when impossible.
 *       Optional: data-fp-cents="621745" (initial value; kept current as you type — NOT `data-cents`, which fp-money.js would hydrate as an amount), data-fp-signed (a leading − or - makes it negative), data-fp-max-digits="13".
 * ── 8  RANGE         <input type="range" class="rg"> — writes --p (0…1) on the input for the filled part of the track.
 * ── 9  DIALOG        <button data-fp-open="#id"> opens <dialog class="dlg" id="id"> with showModal(); [data-fp-close] closes; a click on the backdrop closes; focus goes back to the opener.
 *       A dialog that arrives in an htmx swap with data-fp-autoopen is opened as soon as it is in the DOM; with data-fp-remove it is deleted 320 ms after it closes.
 * ── 10 HOTKEYS       [data-fp-hotkey="mod+k"] (mod = ⌘ on macOS and iOS, Ctrl elsewhere): opens its dialog (data-fp-open) or clicks it; again while open closes. Bare keys are ignored while typing.
 *       [data-fp-kbd="mod+k"] elements get the platform label («⌘K» / «Ctrl K»).
 * ── 11 CHECKBOX      <button type="button" class="ckb" role="checkbox" aria-checked="false" data-fp-name="ack" data-fp-enables="#ativar">…</button> — click / Space / Enter flips aria-checked, mirrors "1" / "0" into
 *       <input type="hidden" name="ack"> (input + change events) and fires `fp:change`.  data-fp-enables="#a, #b": those controls get aria-disabled="false" while the box is checked and "true" while it is not
 *       (never `disabled`: they stay focusable); a target that carries data-fp-hold stays closed whatever the box says — the server sets it for the conditions the browser cannot know ("ligue ao menos uma função").
 *       A role="checkbox" that is not a <button> also answers to Space.
 * ── 12 GATE GUARD    a click on a button / link / role widget that says aria-disabled="true" is swallowed in the capture phase (preventDefault + stopImmediatePropagation): an hx-post on a gated button never fires.
 *
 * Pure helpers are exported for tests: maskCents, maskDate, parseDateBR, fillOf, stepValue, moveIndex, parseHotkey, hotkeyMatches, kbdLabel, gateOpen.
 * Public API: window.FP.widgets = { bind, scan, open, close, mask:{cents,date}, parseDateBR }.
 */

/* ═════════════ pure helpers ═════════════ */

/** Raw text → integer minor units + display text. `places` = decimals of the currency (2 for BRL). Never floats for the stored value. */
export function maskCents(raw, opts = {}) {
  const places = Number.isInteger(opts.places) ? opts.places : 2;
  const maxDigits = Math.min(15, Math.max(1, opts.maxDigits || 13));
  const text = String(raw == null ? '' : raw);
  const neg = !!opts.signed && /^\s*[-−]/.test(text);
  let digits = text.replace(/\D/g, '').replace(/^0+(?=\d)/, '');
  if (digits.length > maxDigits) digits = digits.slice(0, maxDigits);
  if (digits === '') return { minor: null, text: neg ? '−' : '', digits: '' };
  const minor = Number(digits) * (neg && Number(digits) !== 0 ? -1 : 1);
  return { minor, text: formatMinor(minor, places, opts.format), digits };
}

function formatMinor(minor, places, format) {
  if (typeof format === 'function') return format(minor);
  try {
    const nf = new Intl.NumberFormat((typeof window !== 'undefined' && window.APP_CONFIG && window.APP_CONFIG.locale) || 'pt-BR', { minimumFractionDigits: places, maximumFractionDigits: places });
    const s = nf.format(Math.abs(minor) / Math.pow(10, places));
    return (minor < 0 ? '−' : '') + s;
  } catch (e) { return String(minor); }
}

/** Progressive dd/mm/aaaa: the separator appears with the digit that needs it, so Backspace never gets stuck on a "/". */
export function maskDate(raw) {
  const d = String(raw == null ? '' : raw).replace(/\D/g, '').slice(0, 8);
  return d.slice(0, 2) + (d.length > 2 ? '/' + d.slice(2, 4) : '') + (d.length > 4 ? '/' + d.slice(4, 8) : '');
}

/** "dd/mm/aaaa" → "aaaa-mm-dd" for a REAL calendar date (31/02 is null), else null. */
export function parseDateBR(text) {
  const m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(String(text || ''));
  if (!m) return null;
  const d = +m[1], mo = +m[2], y = +m[3];
  if (y < 1900 || y > 2100 || mo < 1 || mo > 12 || d < 1) return null;
  const last = new Date(Date.UTC(y, mo, 0)).getUTCDate();
  if (d > last) return null;
  return `${m[3]}-${m[2]}-${m[1]}`;
}

/** 0…1 share of a range input (for --p). */
export function fillOf(min, max, value) {
  const lo = Number(min), hi = Number(max), v = Number(value);
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || !Number.isFinite(v) || hi <= lo) return 0;
  return Math.min(1, Math.max(0, (v - lo) / (hi - lo)));
}

/** Stepper arithmetic: the value after `dir` steps, clamped; plus whether each direction is still possible. */
export function stepValue(value, dir, min, max, size = 1) {
  const lo = Number.isFinite(min) ? min : -Infinity, hi = Number.isFinite(max) ? max : Infinity;
  const v = Math.min(hi, Math.max(lo, (Number.isFinite(value) ? value : lo === -Infinity ? 0 : lo) + dir * size));
  return { value: v, canDec: v - size >= lo, canInc: v + size <= hi };
}

/** Roving index for arrow keys: wraps; null when the key is not a navigation key. */
export function moveIndex(i, n, key, vertical = true) {
  if (n <= 0) return null;
  switch (key) {
    case 'ArrowRight': return (i + 1) % n;
    case 'ArrowLeft': return (i - 1 + n) % n;
    case 'ArrowDown': return vertical ? (i + 1) % n : null;
    case 'ArrowUp': return vertical ? (i - 1 + n) % n : null;
    case 'Home': return 0;
    case 'End': return n - 1;
    default: return null;
  }
}

/** "mod+shift+k" → { mod, shift, alt, key } */
export function parseHotkey(spec) {
  const parts = String(spec || '').toLowerCase().split('+').map((s) => s.trim()).filter(Boolean);
  const key = parts.pop() || '';
  return { mod: parts.includes('mod'), shift: parts.includes('shift'), alt: parts.includes('alt'), key };
}

/** Does the keyboard event match the spec? `mac` picks ⌘ vs Ctrl for "mod". */
export function hotkeyMatches(spec, e, mac) {
  const h = parseHotkey(spec);
  if (!h.key || String(e.key || '').toLowerCase() !== h.key) return false;
  const modDown = mac ? !!e.metaKey : !!e.ctrlKey;
  const otherMod = mac ? !!e.ctrlKey : !!e.metaKey;
  return modDown === h.mod && !otherMod && !!e.shiftKey === h.shift && !!e.altKey === h.alt;
}

/** Platform label for a hotkey: «⌘K» on macOS and iOS, «Ctrl K» elsewhere. */
export function kbdLabel(spec, mac) {
  const h = parseHotkey(spec);
  const k = h.key.length === 1 ? h.key.toUpperCase() : h.key.charAt(0).toUpperCase() + h.key.slice(1);
  if (mac) return (h.alt ? '⌥' : '') + (h.shift ? '⇧' : '') + (h.mod ? '⌘' : '') + k;
  return [h.mod ? 'Ctrl' : '', h.alt ? 'Alt' : '', h.shift ? 'Shift' : '', k].filter(Boolean).join(' ');
}

/** The gate rule: a gated control is open when its checkbox is checked and the server has not put it on hold. */
export function gateOpen(checked, hold) { return !!checked && !hold; }

/* ═════════════ DOM plumbing ═════════════ */

const hasDOM = typeof document !== 'undefined' && typeof window !== 'undefined';
const attr = (el, n, v) => { if (el.getAttribute(n) !== v) el.setAttribute(n, v); };
const isMac = () => hasDOM && /Mac|iPhone|iPad|iPod/i.test((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || navigator.userAgent || '');

function mirrorTarget(host) {
  const sel = host.getAttribute('data-fp-input');
  if (sel) { try { return document.querySelector(sel); } catch (e) { return null; } }
  const name = host.getAttribute('data-fp-name');
  return name ? host.querySelector('input[type="hidden"][name="' + name.replace(/"/g, '') + '"]') : null;
}

/** Writes `value` to the mirror input and fires input + change (bubbling) so forms and HTMX notice. */
function mirror(host, value) {
  const t = mirrorTarget(host);
  if (!t || t.value === String(value)) return;
  t.value = String(value);
  t.dispatchEvent(new Event('input', { bubbles: true }));
  t.dispatchEvent(new Event('change', { bubbles: true }));
}

function announce(host, type, detail) {
  host.dispatchEvent(new CustomEvent(type, { bubbles: true, detail }));
}

/* ── 1 radio group ── */
const RADIOS = '[role="radio"]';
const groupOf = (b) => { const g = b.closest('[role="radiogroup"]'); return g && !g.hasAttribute('data-fp-theme-group') ? g : null; };
const radiosOf = (g) => [...g.querySelectorAll(RADIOS)].filter((b) => b.closest('[role="radiogroup"]') === g);
const valueOf = (b) => (b.hasAttribute('data-value') ? b.getAttribute('data-value') : b.hasAttribute('value') ? b.getAttribute('value') : b.textContent.trim());

function selectRadio(g, b, fromKey) {
  if (b.getAttribute('aria-disabled') === 'true' || b.disabled) return;
  const all = radiosOf(g);
  const was = all.find((x) => x.getAttribute('aria-checked') === 'true');
  const anyOn = all.some((x) => x.classList.contains('on'));
  for (const x of all) {
    const on = x === b;
    attr(x, 'aria-checked', on ? 'true' : 'false');
    attr(x, 'tabindex', on ? '0' : '-1');
    if (anyOn || x.classList.contains('on')) x.classList.toggle('on', on);
  }
  if (fromKey) b.focus();
  if (g.hasAttribute('data-fp-reveal')) reveal(g);
  if (was !== b) {
    mirror(g, valueOf(b));
    announce(g, 'fp:change', { value: valueOf(b), name: g.getAttribute('data-fp-name'), index: all.indexOf(b) });
  }
}

/** data-fp-deselect: nothing checked. The first radio stays the one tab stop. */
function clearRadios(g) {
  const all = radiosOf(g);
  const was = all.some((x) => x.getAttribute('aria-checked') === 'true');
  for (const x of all) { attr(x, 'aria-checked', 'false'); if (x.classList.contains('on')) x.classList.remove('on'); }
  rove(g);
  if (was) {
    mirror(g, '');
    announce(g, 'fp:change', { value: '', name: g.getAttribute('data-fp-name'), index: -1 });
  }
}

/** data-fp-reveal: bring the checked radio into view inside its horizontal scroller (the group itself or an ancestor up to it). Measures only on demand. */
const seenReveal = new WeakSet();
function reveal(g) {
  const on = radiosOf(g).find((x) => x.getAttribute('aria-checked') === 'true');
  if (!on) return;
  let row = on.parentElement;
  while (row && row.scrollWidth <= row.clientWidth + 1 && row !== g) row = row.parentElement;
  if (!row || row.scrollWidth <= row.clientWidth + 1) return;
  const a = on.getBoundingClientRect(), b = row.getBoundingClientRect();
  if (a.left >= b.left && a.right <= b.right) return;
  const calm = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  row.scrollTo({ left: Math.max(0, row.scrollLeft + (a.left - b.left) - 24), behavior: calm ? 'auto' : 'smooth' });
}

/** Roving tabindex for groups that did not render it: the checked radio (or the first) is the one tab stop. */
function rove(g) {
  const all = radiosOf(g);
  if (!all.length) return;
  const on = all.find((x) => x.getAttribute('aria-checked') === 'true') || all[0];
  for (const x of all) attr(x, 'tabindex', x === on ? '0' : '-1');
}

/* ── 4 stepper ── */
function stepperState(st) {
  const out = st.querySelector('output');
  const min = st.hasAttribute('data-fp-min') ? Number(st.getAttribute('data-fp-min')) : -Infinity;
  const max = st.hasAttribute('data-fp-max') ? Number(st.getAttribute('data-fp-max')) : Infinity;
  const size = Number(st.getAttribute('data-fp-stepsize')) || 1;
  const cur = out ? Number(out.getAttribute('data-value') != null ? out.getAttribute('data-value') : out.textContent.replace(/[^\d.-]/g, '')) : NaN;
  return { out, min, max, size, cur };
}
function paintStepper(st, v, quiet) {
  const s = stepperState(st);
  const r = stepValue(v, 0, s.min, s.max, s.size);
  if (s.out) { const txt = String(r.value); if (s.out.textContent !== txt) s.out.textContent = txt; attr(s.out, 'data-value', txt); }   // write only on change: a rewrite would wake the observer again
  for (const b of st.querySelectorAll('[data-fp-step]')) {
    const dir = Number(b.getAttribute('data-fp-step'));
    attr(b, 'aria-disabled', (dir < 0 ? !r.canDec : !r.canInc) ? 'true' : 'false');
  }
  if (!quiet) { mirror(st, r.value); announce(st, 'fp:change', { value: r.value, name: st.getAttribute('data-fp-name') }); }
  return r.value;
}

/* ── 5 disclosure ── */
function setFold(btn, open) {
  const id = btn.getAttribute('aria-controls');
  const panel = id ? document.getElementById(id) : null;
  attr(btn, 'aria-expanded', open ? 'true' : 'false');
  if (!panel) return;
  attr(panel, 'data-open', open ? 'true' : 'false');
  if (open) panel.removeAttribute('inert'); else panel.setAttribute('inert', '');
  announce(btn, 'fp:fold', { open, id });
}

/* ── 6 tabs ── */
const tabsOf = (tl) => [...tl.querySelectorAll('[role="tab"]')].filter((t) => t.closest('[role="tablist"]') === tl);
function selectTab(tl, tab) {
  const tabs = tabsOf(tl);
  for (const t of tabs) {
    const on = t === tab;
    attr(t, 'aria-selected', on ? 'true' : 'false');
    attr(t, 'tabindex', on ? '0' : '-1');
    const pid = t.getAttribute('aria-controls');
    const panel = pid ? document.getElementById(pid) : null;
    if (panel) panel.hidden = !on;
  }
  announce(tl, 'fp:change', { value: tab.getAttribute('data-value') || tab.id || tab.textContent.trim(), index: tabs.indexOf(tab) });
}

/* ── 7 masks ── */
function moneyApi() { return hasDOM && window.FP && window.FP.money ? window.FP.money : null; }
function placesNow() { const M = moneyApi(); try { return M && typeof M.minorDigits === 'function' ? M.minorDigits() : 2; } catch (e) { return 2; } }
function fmtNow(minor) { const M = moneyApi(); return M && typeof M.formatAmount === 'function' ? M.formatAmount(minor) : null; }

function applyCents(input) {
  const r = maskCents(input.value, { places: placesNow(), signed: input.hasAttribute('data-fp-signed'), maxDigits: Number(input.getAttribute('data-fp-max-digits')) || 13, format: fmtNow(0) != null ? fmtNow : undefined });
  if (input.value !== r.text) input.value = r.text;
  if (r.minor == null) input.removeAttribute('data-fp-cents'); else input.setAttribute('data-fp-cents', String(r.minor));
  mirror(input, r.minor == null ? '' : r.minor);
  announce(input, 'fp:mask', { kind: 'cents', minor: r.minor });
}
function applyDate(input) {
  const t = maskDate(input.value);
  if (input.value !== t) input.value = t;
  const iso = parseDateBR(t);
  if (t.length === 10 && !iso) attr(input, 'aria-invalid', 'true'); else input.removeAttribute('aria-invalid');   // only a complete, impossible date is an error
  mirror(input, iso || '');
  announce(input, 'fp:mask', { kind: 'date', iso });
}
function initMask(input) {
  const kind = input.getAttribute('data-fp-mask');
  if (kind === 'cents') {
    if (input.hasAttribute('data-fp-cents') && input.value === '') {
      const minor = Number(input.getAttribute('data-fp-cents'));
      if (Number.isFinite(minor)) input.value = fmtNow(minor) != null ? fmtNow(minor) : formatMinor(minor, placesNow());
    } else if (input.value !== '') applyCents(input);
  } else if (kind === 'date' && input.value !== '') applyDate(input);
}

/* ── 8 range ── */
function paintRange(r) {
  const f = fillOf(r.min === '' ? 0 : r.min, r.max === '' ? 100 : r.max, r.value);
  r.style.setProperty('--p', f.toFixed(4));
}

/* ── 9 dialog ── */
const openers = new WeakMap();
function dlgOf(sel) { try { return sel ? document.querySelector(sel) : null; } catch (e) { return null; } }
export function open(target, opener) {
  const d = typeof target === 'string' ? dlgOf(target.charAt(0) === '#' || target.charAt(0) === '.' ? target : '#' + target) : target;
  if (!d || typeof d.showModal !== 'function' || d.open) return false;
  if (opener) openers.set(d, opener);
  d.showModal();
  announce(d, 'fp:dialog', { open: true });
  return true;
}
export function close(target, value) {
  const d = typeof target === 'string' ? dlgOf(target.charAt(0) === '#' || target.charAt(0) === '.' ? target : '#' + target) : target;
  if (!d || !d.open) return false;
  d.close(value);
  return true;
}
function onDialogClose(e) {
  const d = e.target;
  if (!d || d.tagName !== 'DIALOG') return;
  const o = openers.get(d);
  openers.delete(d);
  if (o && o.isConnected && typeof o.focus === 'function') o.focus({ preventScroll: true });
  announce(d, 'fp:dialog', { open: false, value: d.returnValue });
  if (d.hasAttribute('data-fp-remove')) setTimeout(() => { if (!d.open) d.remove(); }, 320);   // after the exit transition
}

/* ═════════════ event handlers ═════════════ */

/* ── 11 checkbox + 12 gate guard ── */
const GATED = 'button[aria-disabled="true"],a[aria-disabled="true"],[role="button"][aria-disabled="true"],[role="checkbox"][aria-disabled="true"],[role="switch"][aria-disabled="true"],[role="radio"][aria-disabled="true"],[role="tab"][aria-disabled="true"]';

function applyGate(cb) {
  const sel = cb.getAttribute('data-fp-enables');
  if (!sel) return;
  let list;
  try { list = document.querySelectorAll(sel); } catch (e) { return; }
  const on = cb.getAttribute('aria-checked') === 'true';
  for (const t of list) attr(t, 'aria-disabled', gateOpen(on, t.hasAttribute('data-fp-hold')) ? 'false' : 'true');
}

function toggleCheckbox(el) {
  if (el.getAttribute('aria-disabled') === 'true' || el.disabled) return;
  const on = el.getAttribute('aria-checked') !== 'true';
  attr(el, 'aria-checked', on ? 'true' : 'false');
  mirror(el, on ? '1' : '0');
  applyGate(el);
  announce(el, 'fp:change', { value: on, name: el.getAttribute('data-fp-name') });
}

function onGuard(e) {
  const g = e.target && e.target.closest ? e.target.closest(GATED) : null;
  if (g) { e.preventDefault(); e.stopImmediatePropagation(); }
}

function onClick(e) {
  const t = e.target && e.target.closest ? e.target : null;
  if (!t) return;
  let el;
  if ((el = t.closest(RADIOS))) {
    const g = groupOf(el);
    if (g) {
      if (g.hasAttribute('data-fp-deselect') && el.getAttribute('aria-checked') === 'true') clearRadios(g); else selectRadio(g, el);
      return;
    }
  }
  if ((el = t.closest('[role="switch"]'))) {
    if (el.hasAttribute('data-fp-privacy')) return;   // owned by fp-privacy.js, which keeps aria-checked in step with its own state: flipping it here would invert it
    if (el.getAttribute('aria-disabled') === 'true' || el.disabled) return;
    const on = el.getAttribute('aria-checked') !== 'true';
    attr(el, 'aria-checked', on ? 'true' : 'false');
    const sw = el.querySelector('.sw'); if (sw && sw.classList.contains('on') !== on) sw.classList.toggle('on', on);
    mirror(el, on ? '1' : '0');
    announce(el, 'fp:change', { value: on, name: el.getAttribute('data-fp-name') });
    return;
  }
  if ((el = t.closest('[role="checkbox"]'))) { toggleCheckbox(el); return; }
  if ((el = t.closest('[data-fp-toggle]'))) {
    if (el.getAttribute('aria-disabled') === 'true' || el.disabled) return;
    const on = el.getAttribute('aria-pressed') !== 'true';
    const parent = el.closest('[data-fp-exclusive]');
    if (parent) for (const b of parent.querySelectorAll('[data-fp-toggle]')) attr(b, 'aria-pressed', b === el && on ? 'true' : 'false');
    else attr(el, 'aria-pressed', on ? 'true' : 'false');
    const host = el.closest('[data-fp-input],[data-fp-name]') || parent;
    if (host && mirrorTarget(host)) mirror(host, [...host.querySelectorAll('[data-fp-toggle][aria-pressed="true"]')].map((b) => valueOf(b)).join(','));
    announce(el, 'fp:change', { value: valueOf(el), pressed: el.getAttribute('aria-pressed') === 'true' });
    return;
  }
  if ((el = t.closest('[data-fp-step]'))) {
    const st = el.closest('.stp,[data-fp-min],[data-fp-max]');
    if (!st || el.getAttribute('aria-disabled') === 'true') return;
    const s = stepperState(st);
    paintStepper(st, stepValue(s.cur, Number(el.getAttribute('data-fp-step')) || 0, s.min, s.max, s.size).value);
    return;
  }
  if ((el = t.closest('.fld')) && !t.closest('input,button,a,select,textarea')) {   // the whole big-number field is the click target, not just the digits
    const i = el.querySelector('input:not([type="hidden"])'); if (i && !i.disabled) i.focus();
    return;
  }
  if ((el = t.closest('[data-fp-fold]'))) { setFold(el, el.getAttribute('aria-expanded') !== 'true'); return; }
  if ((el = t.closest('button[role="tab"]'))) { const tl = el.closest('[role="tablist"]'); if (tl) { selectTab(tl, el); return; } }
  if ((el = t.closest('[data-fp-open]'))) {
    const d = dlgOf(el.getAttribute('data-fp-open'));
    if (d) { e.preventDefault(); open(d, el); return; }
  }
  if ((el = t.closest('[data-fp-close]'))) {
    const d = el.closest('dialog');
    if (d) { e.preventDefault(); d.close(el.getAttribute('data-fp-close') || undefined); return; }
  }
  if (t.tagName === 'DIALOG' && t.open && t.classList.contains('dlg')) {   // a click that lands on the dialog element itself is a click on the backdrop
    const r = t.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) t.close();
  }
}

function onKeydown(e) {
  const t = e.target && e.target.closest ? e.target : null;
  if (!t) return;
  if (e.key === ' ' && !e.altKey && !e.ctrlKey && !e.metaKey && t.matches && t.matches('[role="checkbox"]:not(button):not(input)')) { e.preventDefault(); toggleCheckbox(t); return; }
  // arrows inside radio groups and tab lists
  const radio = t.closest(RADIOS);
  if (radio && !e.altKey && !e.ctrlKey && !e.metaKey) {
    const g = groupOf(radio);
    if (g) {
      const all = radiosOf(g).filter((x) => x.getAttribute('aria-disabled') !== 'true' && !x.disabled);
      const to = moveIndex(all.indexOf(radio), all.length, e.key, true);
      if (to != null) { e.preventDefault(); selectRadio(g, all[to], true); }
      return;
    }
  }
  const tab = t.closest('[role="tab"]');
  if (tab && !e.altKey && !e.ctrlKey && !e.metaKey) {
    const tl = tab.closest('[role="tablist"]');
    if (tl) {
      const all = tabsOf(tl);
      const to = moveIndex(all.indexOf(tab), all.length, e.key, tl.getAttribute('aria-orientation') === 'vertical');
      if (to != null) {
        e.preventDefault();
        all[to].focus();
        const link = all[to].tagName === 'A' && all[to].hasAttribute('href');
        if (!link && !tl.hasAttribute('data-fp-manual')) selectTab(tl, all[to]);
        else for (const x of all) attr(x, 'tabindex', x === all[to] ? '0' : '-1');
      }
      return;
    }
  }
  // hotkeys
  if (e.defaultPrevented || e.repeat) return;
  const typing = t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName);
  for (const el of document.querySelectorAll('[data-fp-hotkey]')) {
    const spec = el.getAttribute('data-fp-hotkey');
    if (!hotkeyMatches(spec, e, isMac())) continue;
    if (typing && !parseHotkey(spec).mod) continue;
    const dlg = el.hasAttribute('data-fp-open') ? dlgOf(el.getAttribute('data-fp-open')) : null;
    if (document.querySelector('dialog[open]') && !(dlg && dlg.open)) return;   // another modal is up: leave the shortcut alone
    e.preventDefault();
    if (dlg) { if (dlg.open) dlg.close(); else open(dlg, el); } else el.click();
    return;
  }
}

function onInput(e) {
  const t = e.target;
  if (!t || !t.getAttribute) return;
  const kind = t.getAttribute('data-fp-mask');
  if (kind === 'cents') applyCents(t);
  else if (kind === 'date') applyDate(t);
  else if (t.matches && t.matches('input[type="range"].rg')) paintRange(t);
}

/** Initial paint of everything that needs a first write: stepper bounds, range fill, tab roving, radio roving, kbd labels, masks. Idempotent. */
export function scan(root) {
  const scope = root && root.querySelectorAll ? root : document;
  for (const st of scope.querySelectorAll('.stp[data-fp-min],.stp[data-fp-max],[data-fp-min] [data-fp-step]')) {
    const host = st.matches && st.matches('[data-fp-step]') ? st.closest('[data-fp-min],[data-fp-max]') : st;
    if (host) { const s = stepperState(host); paintStepper(host, Number.isFinite(s.cur) ? s.cur : s.min, true); }
  }
  for (const r of scope.querySelectorAll('input[type="range"].rg')) paintRange(r);
  for (const g of scope.querySelectorAll('[role="radiogroup"]')) {
    if (g.hasAttribute('data-fp-theme-group')) continue;
    rove(g);
    if (g.hasAttribute('data-fp-reveal') && !seenReveal.has(g)) { seenReveal.add(g); reveal(g); }
  }
  for (const tl of scope.querySelectorAll('[role="tablist"]')) {
    const tabs = tabsOf(tl);
    const on = tabs.find((x) => x.getAttribute('aria-selected') === 'true') || tabs[0];
    for (const x of tabs) attr(x, 'tabindex', x === on ? '0' : '-1');
  }
  for (const k of scope.querySelectorAll('[data-fp-kbd]')) { const l = kbdLabel(k.getAttribute('data-fp-kbd'), isMac()); if (k.textContent !== l) k.textContent = l; }
  for (const i of scope.querySelectorAll('input[data-fp-mask]')) initMask(i);
  for (const d of scope.querySelectorAll('dialog.dlg[data-fp-autoopen]')) {   // a dialog delivered by htmx: open it as a modal, once
    d.removeAttribute('data-fp-autoopen');
    const a = document.activeElement;
    open(d, a && a !== document.body ? a : null);
  }
  for (const c of scope.querySelectorAll('[role="checkbox"][data-fp-enables]')) applyGate(c);
  for (const b of scope.querySelectorAll('[data-fp-fold]')) { const p = document.getElementById(b.getAttribute('aria-controls') || ''); if (p && p.getAttribute('data-open') !== 'true') p.setAttribute('inert', ''); }
}

let bound = false;
let mo = null;

/** Binds the delegated listeners and the observer. Idempotent. */
export function bind() {
  if (bound || !hasDOM) return bound;
  bound = true;
  document.addEventListener('click', onGuard, true);   // capture: before htmx (or any other listener) sees a click on a gated control
  document.addEventListener('click', onClick);
  document.addEventListener('keydown', onKeydown);
  document.addEventListener('input', onInput);
  document.addEventListener('close', onDialogClose, true);   // `close` does not bubble: capture it
  scan(document);
  let queued = false;
  mo = new MutationObserver((ms) => {
    if (queued) return;
    if (!ms.some((m) => { for (const n of m.addedNodes) if (n.nodeType === 1) return true; return false; })) return;   // only new ELEMENTS can bring new widgets (text updates never do)
    queued = true;
    requestAnimationFrame(() => { queued = false; scan(document); });
  });
  mo.observe(document.documentElement, { childList: true, subtree: true });
  return true;
}

export const widgets = Object.freeze({ bind, scan, open, close, mask: Object.freeze({ cents: maskCents, date: maskDate }), parseDateBR });

if (hasDOM) {
  window.FP = window.FP || {};
  window.FP.widgets = widgets;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind, { once: true }); else bind();
}
