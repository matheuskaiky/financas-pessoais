/* fp-theme.js — theme preference: Claro / Escuro / Automático. Vanilla JS, no dependencies, no build.
 *
 * LOADING  Classic script, blocking, FIRST script in <head> (before the stylesheets):  <script src="/static/fp-theme.js"></script>
 *   Not a module and not deferred on purpose: it runs before the first paint, so a stored choice never flashes the wrong theme.
 *   Strict-CSP safe (no inline script, no eval). Idempotent: a second load keeps the first instance.
 *
 * STATE  localStorage["fp_theme"] = "light" | "dark" | "system"   (anything else, or no entry, = "system"; nothing leaves the browser).
 *   Exception: with NO stored entry, a server-rendered <html data-theme="light|dark"> is kept as the default (the choice is "unset", not "system").
 *
 * WRITES ON <html>  (all synchronous, before first paint)
 *   data-theme="light"|"dark"      forces the theme. tokens.css: :root{color-scheme:light}, :root[data-theme="dark"]{…}.
 *   (no data-theme)                "system": tokens.css' @media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){…} } drives every token,
 *                                  so an OS change repaints with no JavaScript at all.
 *   data-theme-pref="light|dark|system"   the CHOICE. CSS draws the switcher from it (fp-theme.css), so the control never flashes either.
 *   data-theme-resolved="light|dark"      the EFFECTIVE theme (informational; follows the OS while the choice is "system").
 *   <meta name="theme-color" data-fp-theme-color>  (optional, put it BEFORE this script): content = --bg of the effective theme.
 *
 * API  globalThis.FP.theme  (also module.exports under Node, for tests)
 *   get() → "light"|"dark"|"system"          resolved() → "light"|"dark"        set(mode) → mode      cycle() → mode (Claro → Escuro → Automático)
 *   subscribe(fn) → unsubscribe; fn({mode, resolved})      refresh() re-reads storage      labels {light, dark, system} (pt-BR)
 *   document gets CustomEvent "fp:theme" { detail: { mode, resolved } } when the choice or the effective theme changes.
 *   The switcher UI (radiogroup keyboard, aria-checked, live region) lives in fp-ui.js and talks to this API.
 *
 * KEEPS IN STEP  other tabs (`storage` event), the back/forward cache (`pageshow`), and the OS (matchMedia change, while "system").
 */
(function (root) {
  'use strict';

  if (root.FP && root.FP.theme && root.FP.theme.KEY) return;   // loaded twice: keep the first instance

  var KEY = 'fp_theme';
  var MODES = Object.freeze(['light', 'dark', 'system']);
  var LABELS = Object.freeze({ light: 'Claro', dark: 'Escuro', system: 'Automático' });   // end-user copy (pt-BR)
  var BG = Object.freeze({ light: '#F3F6F2', dark: '#08100D' });                            // --bg of each theme in tokens.css (browser chrome colour only)

  /* ───────────── pure logic (no DOM; tested under Node) ───────────── */

  function normalize(v) { return v === 'light' || v === 'dark' ? v : 'system'; }
  function next(mode) { return MODES[(MODES.indexOf(normalize(mode)) + 1) % MODES.length]; }
  function resolve(mode, osDark) { var m = normalize(mode); return m === 'system' ? (osDark ? 'dark' : 'light') : m; }

  var api = { KEY: KEY, MODES: MODES, labels: LABELS, logic: { normalize: normalize, next: next, resolve: resolve } };

  var doc = root.document;
  if (!doc || !doc.documentElement) {      // Node / worker: the pure part only
    if (typeof module === 'object' && module.exports) module.exports = api;
    return;
  }

  /* ───────────── storage (never throws; falls back to memory) ───────────── */

  var memory = null;   // set only when localStorage is blocked or full

  function ls() {
    try { return root.localStorage || null; } catch (e) { return null; }   // reading the property itself can throw (blocked site data)
  }

  /** The stored choice, or null when there is none. A failed write earlier in this page's life (memory) wins: it is the user's last word. */
  function stored() {
    if (memory !== null) return memory;
    var s = ls();
    if (s) {
      try { var v = s.getItem(KEY); return v === null ? null : normalize(v); } catch (e) { /* unreadable: treat as unset */ }
    }
    return null;
  }

  function save(mode) {
    memory = mode;
    try { var s = ls(); if (s) { s.setItem(KEY, mode); memory = null; } } catch (e) { /* memory keeps it for this page's life */ }
  }

  /* ───────────── DOM ───────────── */

  var de = doc.documentElement;
  var mq = null;
  try { mq = root.matchMedia ? root.matchMedia('(prefers-color-scheme: dark)') : null; } catch (e) { mq = null; }

  function osDark() { return !!(mq && mq.matches); }

  function serverDefault() {
    var a = de.getAttribute('data-theme');
    return a === 'light' || a === 'dark' ? a : 'system';
  }

  function read() { var s = stored(); return s === null ? serverDefault() : s; }

  function paintMeta(resolved) {
    var meta = doc.querySelector('meta[name="theme-color"][data-fp-theme-color]');
    if (meta) meta.setAttribute('content', BG[resolved]);
  }

  /** Writes the three attributes; returns the effective theme. */
  function paint(mode) {
    var resolved = resolve(mode, osDark());
    if (mode === 'system') de.removeAttribute('data-theme'); else de.setAttribute('data-theme', mode);
    de.setAttribute('data-theme-pref', mode);
    de.setAttribute('data-theme-resolved', resolved);
    paintMeta(resolved);
    return resolved;
  }

  var mode = read();
  var effective = paint(mode);
  var listeners = [];

  function announce() {
    var detail = { mode: mode, resolved: effective };
    listeners.slice().forEach(function (fn) { try { fn(detail); } catch (e) { /* a listener must not break the others */ } });
    try { doc.dispatchEvent(new root.CustomEvent('fp:theme', { detail: detail })); } catch (e) { /* very old engines */ }
  }

  /** Re-reads storage and the OS, repaints, and tells everyone if something changed. */
  function sync() {
    var m = read();
    var r = paint(m);
    if (m !== mode || r !== effective) { mode = m; effective = r; announce(); }
  }

  api.get = function () { return mode; };
  api.resolved = function () { return effective; };
  api.set = function (m) { save(normalize(m)); sync(); return mode; };
  api.cycle = function () { return api.set(next(mode)); };
  api.refresh = sync;
  api.subscribe = function (fn) {
    if (typeof fn !== 'function') return function () {};
    listeners.push(fn);
    return function () { var i = listeners.indexOf(fn); if (i !== -1) listeners.splice(i, 1); };
  };

  root.FP = root.FP || {};
  root.FP.theme = api;

  /* the OS flipped while the choice is "system": tokens.css already repainted by itself; this refreshes the informational bits */
  if (mq) {
    var onOs = function () { if (mode === 'system') sync(); };
    if (mq.addEventListener) mq.addEventListener('change', onOs); else if (mq.addListener) mq.addListener(onOs);
  }

  /* other tabs, back/forward cache */
  root.addEventListener('storage', function (e) { if (e.key === null || e.key === KEY) sync(); });
  root.addEventListener('pageshow', function (e) { if (e.persisted) sync(); });

  /* a <meta data-fp-theme-color> placed after this script is only parsed later */
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', function () { paintMeta(effective); }, { once: true });

  if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : typeof window !== 'undefined' ? window : this);
