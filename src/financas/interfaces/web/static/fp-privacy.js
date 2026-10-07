/* fp-privacy.js — optional privacy mode: frosted blur masking of money values. Vanilla JS, no dependencies, no build.
 *
 * WHAT IT DOES
 *   Writes <html data-privacy-mask="networth cards …"> from the user's choices. tokens.css does the masking (filter: blur), so a value
 *   never changes its layout box (CLS 0) and no placeholder characters are ever drawn. Default = everything visible: nothing is masked
 *   until the user turns it on. State is stored in localStorage under `fp_privacy_settings` and nowhere else (nothing leaves the browser).
 *
 * LOADING  Classic script, blocking, in <head>:  <script src="/static/fp-privacy.js"></script>
 *   Not a module and not deferred on purpose: it runs before the first paint, so a user who hid the values never sees them flash on load.
 *   Works under a strict CSP (no inline script, no eval). API: globalThis.FP.privacy (also module.exports under Node, for tests).
 *
 * MARKUP CONTRACT (templates/macros/privacidade.html writes all of it)
 *   value       data-private="networth|accounts|investments|cards|transactions" on an inline element (a .money wrapper, a number, an SVG label)
 *   peek scope  data-peek-scope on a card or a row: hovering / focusing / holding it reveals every value inside, and only those
 *   eye         <button data-fp-privacy="toggle" data-state="visible|masked" aria-keyshortcuts="P"> … [data-fp-privacy-tip]
 *   gear        <button data-fp-privacy="settings" aria-haspopup="dialog">
 *   sheet       <dialog data-fp-privacy="dialog"> with input[data-fp-privacy-group], [data-fp-privacy="master"] (role=switch),
 *               [data-fp-privacy="done"], [data-fp-privacy="reset"]
 *   Everything is found by delegation and by a MutationObserver, so HTMX swaps need no re-initialisation.
 *
 * BEHAVIOUR
 *   eye / P        toggles. Turning it on with no group selected selects all of them (there is nothing to hide otherwise).
 *   checkboxes     live: they apply and persist on change. Unchecking the last group while it is on turns it off.
 *   Concluir       closes the sheet (everything is already saved).        Restaurar padrão   re-selects the five groups; the on/off state
 *                  is left as it is, so the button can never reveal values by surprise.
 *   peek           Keyboard: CSS :focus-visible. Touch / pen: press and hold ~350 ms (sets data-peek="true" on the scope or the value;
 *                  it ends on release, move or scroll). Mouse: hovering a blurred value does NOT reveal it by default; the user can opt in
 *                  with "Revelar valores temporariamente ao passar o cursor" (state.reveal, written to <html data-privacy-reveal-hover="true">).
 *   screen reader  a masked value is exposed as role="img" aria-label="Valor oculto" and gets its own name back when it is revealed.
 *   cross-tab      the `storage` event keeps other tabs in step; the back/forward cache is re-read on pageshow.
 *   events         document gets CustomEvent "fp:privacy" { detail: { on, mask, groups } } after every change.
 *
 * LIMITS  A blurred value is still in the DOM: this is a visual deterrent (shoulder-surfing, screen sharing), not access control.
 *         Layout must not move, so the width of a masked number (how many digits it has) stays visible by design.
 */
(function (root) {
  'use strict';

  if (root.FP && root.FP.privacy && root.FP.privacy.KEY) return;   // loaded twice: keep the first instance (two sets of listeners would cancel each other)

  var KEY = 'fp_privacy_settings';
  var VERSION = 1;
  var GROUPS = Object.freeze(['networth', 'accounts', 'investments', 'cards', 'transactions']);
  var HOLD_MS = 350;       // tap-and-hold threshold
  var MOVE_TOL = 10;       // px of drift that cancels a hold
  var LABELS = {           // end-user copy (pt-BR); the controls' own text lives in the markup
    hide: 'Ocultar valores',
    show: 'Mostrar valores',
    masked: 'Valor oculto',
    announceOn: 'Valores ocultos',
    announceOff: 'Valores visíveis',
    announceReset: 'Padrão restaurado: os cinco grupos selecionados'
  };

  /* ───────────── pure logic (no DOM; tested under Node) ───────────── */

  function defaults() {
    var g = {};
    GROUPS.forEach(function (k) { g[k] = true; });
    return { v: VERSION, on: false, reveal: false, groups: g };
  }

  /** Any value → a valid state. Unknown keys are dropped, missing or mistyped ones fall back to the default. Never throws. */
  function normalize(raw) {
    var s = defaults();
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return s;
    if (typeof raw.on === 'boolean') s.on = raw.on;
    if (typeof raw.reveal === 'boolean') s.reveal = raw.reveal;   // the hover opt-in: off unless the user turned it on
    var g = raw.groups;
    if (g && typeof g === 'object' && !Array.isArray(g)) {
      GROUPS.forEach(function (k) { if (typeof g[k] === 'boolean') s.groups[k] = g[k]; });
    }
    return s;
  }

  function parse(text) {
    try { return normalize(JSON.parse(text)); } catch (e) { return defaults(); }
  }

  /** The groups masked right now: none unless the master switch is on. */
  function maskList(s) {
    return s.on ? GROUPS.filter(function (k) { return s.groups[k]; }) : [];
  }

  function isOn(s) { return maskList(s).length > 0; }

  function toggled(s, force) {
    var n = normalize(s);
    var want = typeof force === 'boolean' ? force : !isOn(s);
    n.on = want;
    if (want && !maskList(n).length) GROUPS.forEach(function (k) { n.groups[k] = true; });   // nothing selected: there would be nothing to hide
    return n;
  }

  function withGroup(s, group, value) {
    var n = normalize(s);
    if (GROUPS.indexOf(group) === -1) return n;
    n.groups[group] = !!value;
    if (n.on && !maskList(n).length) n.on = false;
    return n;
  }

  function withReveal(s, value) {
    var n = normalize(s);
    n.reveal = !!value;
    return n;
  }

  function restored(s) {
    var n = normalize(s);
    GROUPS.forEach(function (k) { n.groups[k] = true; });
    n.reveal = false;       // back to the default: a hover never reveals (this can only hide more, never reveal by surprise)
    return n;
  }

  /* ───────────── storage (never throws; falls back to memory) ───────────── */

  var storage = 'localStorage';
  var memory = null;

  function ls() {
    try { return root.localStorage || null; } catch (e) { return null; }   // reading the property itself can throw (blocked site data)
  }

  function read() {
    var store = ls();
    if (store) {
      try {
        var text = store.getItem(KEY);
        storage = 'localStorage';
        return text ? parse(text) : defaults();
      } catch (e) { /* fall through to memory */ }
    }
    storage = 'memory';
    return memory ? normalize(memory) : defaults();
  }

  function write(s) {
    memory = normalize(s);
    try {
      var store = ls();
      if (!store) { storage = 'memory'; return; }
      store.setItem(KEY, JSON.stringify(s));
      storage = 'localStorage';
    } catch (e) { storage = 'memory'; }
  }

  /* ───────────── controller ───────────── */

  var state = read();
  var listeners = [];
  var view = null;          // the DOM layer, when there is a document

  function render(message) {
    if (view) view.render(message);
    var detail = { on: isOn(state), mask: maskList(state), groups: normalize(state).groups };
    listeners.slice().forEach(function (fn) { try { fn(detail); } catch (e) { /* a listener must not break the others */ } });
    if (view) view.emit(detail);
  }

  function commit(next, message) {
    state = normalize(next);
    write(state);
    render(message);
  }

  function refresh() {      // another tab wrote the key, or the page came back from the back/forward cache
    if (storage === 'memory') return;
    var next = read();
    if (JSON.stringify(next) === JSON.stringify(state)) return;
    state = next;
    render();
  }

  var api = {
    KEY: KEY,
    VERSION: VERSION,
    GROUPS: GROUPS,
    labels: LABELS,
    logic: { defaults: defaults, normalize: normalize, parse: parse, maskList: maskList, isOn: isOn, toggled: toggled, withGroup: withGroup, withReveal: withReveal, restored: restored },
    get: function () { return normalize(state); },
    isMasked: function (group) {
      var m = maskList(state);
      return group ? m.indexOf(group) !== -1 : m.length > 0;
    },
    get storage() { return storage; },
    toggle: function (force) {
      var next = toggled(state, force);
      commit(next, isOn(next) ? LABELS.announceOn : LABELS.announceOff);
    },
    setGroup: function (group, value) { commit(withGroup(state, group, value)); },
    /** The hover opt-in. Persisted with the rest of the state (localStorage `fp_privacy_settings`, field `reveal`). */
    setReveal: function (value) { commit(withReveal(state, value)); },
    get revealOnHover() { return !!state.reveal; },
    reset: function () { commit(restored(state), LABELS.announceReset); },
    openSettings: function (trigger) { if (view) view.open(trigger); },
    closeSettings: function () { if (view) view.close(); },
    subscribe: function (fn) {
      listeners.push(fn);
      return function () { listeners = listeners.filter(function (f) { return f !== fn; }); };
    }
  };

  root.FP = root.FP || {};
  root.FP.privacy = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;

  if (!root.document || !root.document.documentElement) return;      // Node (tests) or a host without a DOM: logic and state only

  /* ───────────── DOM layer ───────────── */

  view = (function (doc) {
    var de = doc.documentElement;
    var saved = typeof WeakMap === 'function' ? new WeakMap() : null;   // a masked value's own role / aria-label, restored on reveal
    var lastTrigger = null;
    var ready = false;                                                  // no announcement for the state read at load
    var live = null;
    var hold = null;
    var swallowClickUntil = 0;

    function each(sel, fn, scope) {
      var list = (scope || doc).querySelectorAll(sel);
      for (var i = 0; i < list.length; i++) fn(list[i]);
    }

    /** <html data-privacy-mask>: the one write that masks the page. Absent = nothing masked. */
    function applyMask() {
      var m = maskList(state).join(' ');
      if (m) { if (de.getAttribute('data-privacy-mask') !== m) de.setAttribute('data-privacy-mask', m); }
      else if (de.hasAttribute('data-privacy-mask')) de.removeAttribute('data-privacy-mask');
      // the hover opt-in is its own attribute, so the CSS can scope the :hover rules to it
      if (state.reveal) { if (de.getAttribute('data-privacy-reveal-hover') !== 'true') de.setAttribute('data-privacy-reveal-hover', 'true'); }
      else if (de.hasAttribute('data-privacy-reveal-hover')) de.removeAttribute('data-privacy-reveal-hover');
    }

    function syncControls() {
      var on = isOn(state);
      each('[data-fp-privacy="toggle"]', function (b) {
        var label = (on ? b.getAttribute('data-label-show') : b.getAttribute('data-label-hide')) || (on ? LABELS.show : LABELS.hide);
        var st = on ? 'masked' : 'visible';
        if (b.getAttribute('data-state') !== st) b.setAttribute('data-state', st);
        if (b.getAttribute('aria-label') !== label) b.setAttribute('aria-label', label);
        var tip = b.querySelector('[data-fp-privacy-tip]');
        if (tip && tip.textContent !== label) tip.textContent = label;
      });
      each('[data-fp-privacy="master"]', function (b) { b.setAttribute('aria-checked', on ? 'true' : 'false'); });
      each('input[data-fp-privacy-reveal]', function (i) { if (i.checked !== !!state.reveal) i.checked = !!state.reveal; });
      each('input[data-fp-privacy-group]', function (i) {
        var v = !!state.groups[i.getAttribute('data-fp-privacy-group')];
        if (i.checked !== v) i.checked = v;
      });
      // inside the sheet the samples preview the selection even while the master switch is off
      var picked = GROUPS.filter(function (k) { return state.groups[k]; }).join(' ');
      each('dialog[data-fp-privacy="dialog"]', function (d) { d.setAttribute('data-privacy-mask', picked); });
    }

    function syncOne(el, masked) {
      if (masked) {
        if (saved && !saved.has(el)) saved.set(el, { label: el.getAttribute('aria-label'), role: el.getAttribute('role') });
        if (el.getAttribute('role') !== 'img') el.setAttribute('role', 'img');
        if (el.getAttribute('aria-label') !== LABELS.masked) el.setAttribute('aria-label', LABELS.masked);
      } else if (saved && saved.has(el)) {
        var was = saved.get(el);
        saved.delete(el);
        if (was.label === null) el.removeAttribute('aria-label'); else el.setAttribute('aria-label', was.label);
        if (was.role === null) el.removeAttribute('role'); else el.setAttribute('role', was.role);
      }
    }

    /** Accessible names follow the mask: a hidden value is announced as hidden. `scope` = a node just added to the page. */
    function syncA11y(scope) {
      var mask = maskList(state);
      var check = function (el) {
        if (el.closest('[aria-hidden="true"]')) return;       // decorative samples (the sheet's previews)
        syncOne(el, mask.indexOf(el.getAttribute('data-private')) !== -1);
      };
      if (scope && scope.nodeType === 1) {
        if (scope.hasAttribute('data-private')) check(scope);
        each('[data-private]', check, scope);
      } else {
        each('[data-private]', check);
      }
    }

    function clearPeeks() {
      cancelHold();
      each('[data-peek]', function (el) { el.removeAttribute('data-peek'); });
    }

    function announce(text) {
      if (!text || !ready) return;
      live = (live && live.isConnected) ? live : doc.querySelector('[data-fp-privacy="live"]');
      if (!live && doc.body) {
        live = doc.createElement('div');
        live.setAttribute('data-fp-privacy', 'live');
        live.setAttribute('role', 'status');
        live.setAttribute('aria-live', 'polite');
        live.className = 'fp-priv-sr';
        doc.body.appendChild(live);
      }
      if (live) { live.textContent = ''; live.textContent = text; }   // clear first: the same sentence twice is still announced
    }

    /* settings sheet */
    function sheet() { return doc.querySelector('dialog[data-fp-privacy="dialog"]'); }

    function open(trigger) {
      var d = sheet();
      if (!d || d.open) return;
      lastTrigger = trigger || doc.activeElement;
      syncControls();
      if (typeof d.showModal === 'function') d.showModal(); else d.setAttribute('open', '');
    }

    function close() {
      var d = sheet();
      if (!d || !d.open) return;
      if (typeof d.close === 'function') d.close(); else d.removeAttribute('open');
      if (lastTrigger && lastTrigger.focus && lastTrigger.isConnected) { try { lastTrigger.focus(); } catch (e) { /* detached */ } }
    }

    /* peek: tap-and-hold for touch and pen (mouse = CSS :hover, keyboard = CSS :focus-visible) */
    var CONTROL = 'a[href], button, input, select, textarea, summary, [role="button"], [role="radio"], [role="slider"], [role="switch"], [role="checkbox"], [role="link"], [role="tab"]';

    /** What a press reveals: the scope of a value that was pressed (or the value itself), or the scope when the press landed on its
        plain content. A press on a control (a link, the range buttons of a chart) never arms a hold: a slow tap must still work. */
    function peekTarget(node) {
      if (!node || !node.closest) return null;
      var value = node.closest('[data-private]');
      if (value) return value.closest('[data-peek-scope]') || value;
      return node.closest(CONTROL) ? null : node.closest('[data-peek-scope]');
    }

    function holdsMasked(el) {
      var mask = maskList(state);
      if (!mask.length) return false;
      if (el.hasAttribute('data-private')) return mask.indexOf(el.getAttribute('data-private')) !== -1;
      var list = el.querySelectorAll('[data-private]');
      for (var i = 0; i < list.length; i++) if (mask.indexOf(list[i].getAttribute('data-private')) !== -1) return true;
      return false;
    }

    function cancelHold() {
      if (!hold) return;
      clearTimeout(hold.timer);
      if (hold.active) {
        hold.el.removeAttribute('data-peek');
        swallowClickUntil = Date.now() + 450;      // the release of a long press must not follow a link inside the card
      }
      hold = null;
    }

    doc.addEventListener('pointerdown', function (e) {
      if (e.pointerType === 'mouse' || e.isPrimary === false) return;
      var el = peekTarget(e.target);
      // a slow tap on something with nothing masked in it must behave like any tap: only masked values arm the hold
      if (!el || !(holdsMasked(el) || el.closest('[data-fp-privacy="dialog"]'))) return;
      cancelHold();
      var h = { id: e.pointerId, x: e.clientX, y: e.clientY, el: el, active: false, timer: 0 };
      h.timer = setTimeout(function () { h.active = true; el.setAttribute('data-peek', 'true'); }, HOLD_MS);
      hold = h;
    });
    doc.addEventListener('pointermove', function (e) {
      if (hold && !hold.active && e.pointerId === hold.id && Math.abs(e.clientX - hold.x) + Math.abs(e.clientY - hold.y) > MOVE_TOL) cancelHold();
    });
    ['pointerup', 'pointercancel'].forEach(function (t) {
      doc.addEventListener(t, function (e) { if (hold && e.pointerId === hold.id) cancelHold(); });
    });
    doc.addEventListener('scroll', function () { if (hold && !hold.active) cancelHold(); }, true);
    doc.addEventListener('contextmenu', function (e) { if (hold && hold.active) e.preventDefault(); });

    /* clicks: controls, sheet backdrop, and the release of a long press */
    doc.addEventListener('click', function (e) {
      if (swallowClickUntil && Date.now() < swallowClickUntil) { e.preventDefault(); e.stopPropagation(); swallowClickUntil = 0; return; }
      var t = e.target;
      if (!t || !t.closest) return;
      if (t.matches('dialog[data-fp-privacy="dialog"]')) { close(); return; }   // a click on the backdrop lands on the dialog element itself
      var c = t.closest('[data-fp-privacy]');
      if (!c) return;
      switch (c.getAttribute('data-fp-privacy')) {
        case 'toggle':
        case 'master': api.toggle(); break;
        case 'settings': open(c); break;
        case 'done': close(); break;
        case 'reset': api.reset(); break;
      }
    }, true);

    doc.addEventListener('change', function (e) {
      var t = e.target;
      if (t && t.matches && t.matches('input[data-fp-privacy-group]')) api.setGroup(t.getAttribute('data-fp-privacy-group'), t.checked);
      else if (t && t.matches && t.matches('input[data-fp-privacy-reveal]')) api.setReveal(t.checked);
    });

    /* shortcut: P, no modifiers, never while typing */
    var NON_TEXT = /^(checkbox|radio|button|submit|reset|range|color|file|image)$/i;
    doc.addEventListener('keydown', function (e) {
      if (e.defaultPrevented || e.repeat || e.isComposing || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
      if (e.key !== 'p' && e.key !== 'P') return;
      var t = e.target;
      if (t && t.nodeType === 1) {
        if (t.isContentEditable) return;
        if (t.tagName === 'INPUT' && !NON_TEXT.test(t.type)) return;
        if (t.closest('textarea, select, [contenteditable=""], [contenteditable="true"], [role="textbox"], [role="combobox"], [role="searchbox"]')) return;
      }
      e.preventDefault();
      api.toggle();
    });

    /* other tabs, back/forward cache */
    root.addEventListener('storage', function (e) { if (e.key === null || e.key === KEY) refresh(); });
    root.addEventListener('pageshow', function (e) { if (e.persisted) refresh(); });

    /* new content (HTMX swaps, island renders): CSS already masks it; this names it for assistive tech and syncs fresh controls */
    if (root.MutationObserver) {
      var queue = [];
      var scheduled = false;
      var flush = function () {
        scheduled = false;
        var batch = queue.splice(0);
        var masked = isOn(state);
        var controls = false;
        batch.forEach(function (n) {
          if (!n.isConnected) return;
          if (masked && (n.hasAttribute('data-private') || n.querySelector('[data-private]'))) syncA11y(n);
          if (!controls && (n.hasAttribute('data-fp-privacy') || n.querySelector('[data-fp-privacy], [data-fp-privacy-group]'))) controls = true;
        });
        if (controls) syncControls();
      };
      new root.MutationObserver(function (list) {
        for (var i = 0; i < list.length; i++) {
          var add = list[i].addedNodes;
          for (var j = 0; j < add.length; j++) if (add[j].nodeType === 1) queue.push(add[j]);
        }
        if (queue.length && !scheduled) {
          scheduled = true;
          (root.requestAnimationFrame || setTimeout)(flush);
        }
      }).observe(de, { childList: true, subtree: true });
    }

    /* first paint: the mask is written now, while <head> is still being parsed; the rest waits for the body */
    applyMask();
    var boot = function () {
      syncControls();
      syncA11y();
      ready = true;
    };
    if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', boot); else boot();

    return {
      render: function (message) { applyMask(); syncControls(); syncA11y(); clearPeeks(); announce(message); },
      emit: function (detail) { try { doc.dispatchEvent(new root.CustomEvent('fp:privacy', { detail: detail })); } catch (e) { /* very old engines */ } },
      open: open,
      close: close
    };
  })(root.document);
})(typeof globalThis !== 'undefined' ? globalThis : typeof window !== 'undefined' ? window : this);
