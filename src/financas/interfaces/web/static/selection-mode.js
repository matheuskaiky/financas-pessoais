// Selection Mode ("Modo de Seleção") on /entries and /cards: a "Selecionar" button turns on a
// checkbox on every row, a floating dock offers "Apagar (N)" and "Mesclar lançamentos".
//
// The server decides what each row may do and renders it as data-* hooks (see
// application/queries/selection.py): data-sel-id ("entry:<id>" / "plan:<id>"), data-sel-key (what
// may be merged together), data-sel-lock (+ data-sel-reason: the row cannot be checked) and
// data-sel-merge="no" (+ data-sel-merge-reason: it can be deleted, never merged). This script never
// computes the month window or any other rule. State lives in the DOM (data-selecting on the scope,
// native checkboxes, aria-pressed) plus the ordered list of checked ids, so a swap that replaces
// rows keeps the selection. Every sentence comes from the #sel-config block (messages/selection.py).
(function () {
  "use strict";

  var cfg = {};
  try { cfg = JSON.parse(document.getElementById("sel-config").textContent || "{}"); } catch (e) { cfg = {}; }
  var HOLD_MS = (cfg.seconds || 5) * 1000;
  var CAP = cfg.max || 200;
  var states = new WeakMap();
  var lastScope = null;

  function stateOf(scope) {
    var s = states.get(scope);
    if (!s) { s = { order: [], notice: "", tone: "warn", hold: 0, liveTimer: 0, lastSaid: "", lastSaidAt: 0, last: -1 }; states.set(scope, s); }
    return s;
  }
  function q(scope, selector) { return scope.querySelector(selector); }
  function rows(scope) { return Array.prototype.slice.call(scope.querySelectorAll("[data-sel-row]")); }
  function boxOf(row) { return row.querySelector(".sel-check"); }
  function isOn(scope) { return scope.getAttribute("data-selecting") === "true"; }
  function rowById(scope, id) {
    return rows(scope).filter(function (r) { return r.getAttribute("data-sel-id") === id; })[0] || null;
  }
  function selectedRows(scope) {
    return stateOf(scope).order.map(function (id) { return rowById(scope, id); }).filter(Boolean);
  }
  function words(n) { return n === 1 ? cfg.selected_one : cfg.selected_many; }

  // One polite region. A burst of changes announces only the last one (debounce), and the same
  // sentence is not repeated within the message window.
  function say(scope, text, immediate) {
    var live = q(scope, ".sel-live");
    var s = stateOf(scope);
    if (!live) return;
    var now = Date.now();
    if (text === s.lastSaid && now - s.lastSaidAt < HOLD_MS) return;
    clearTimeout(s.liveTimer);
    s.liveTimer = setTimeout(function () {
      live.textContent = "";
      setTimeout(function () { live.textContent = text; }, 30);
      s.lastSaid = text; s.lastSaidAt = Date.now();
    }, immediate ? 0 : 150);
  }

  function render(scope) {
    var s = stateOf(scope);
    var checked = selectedRows(scope);
    s.order = checked.map(function (r) { return r.getAttribute("data-sel-id"); });
    var noRow = checked.filter(function (r) { return r.getAttribute("data-sel-merge") === "no"; })[0] || null;
    var anchor = checked.filter(function (r) { return r.getAttribute("data-sel-merge") !== "no"; })[0] || null;
    var anchorKey = anchor ? anchor.getAttribute("data-sel-key") : null;
    var mismatch = false;
    rows(scope).forEach(function (r) {
      var on = s.order.indexOf(r.getAttribute("data-sel-id")) > -1;
      boxOf(r).checked = on;
      if (!on) { r.removeAttribute("data-sel-state"); return; }
      var bad = r.getAttribute("data-sel-merge") === "no" || r.getAttribute("data-sel-key") !== anchorKey;
      if (!bad) r.setAttribute("data-sel-state", "selected");
      else {
        r.setAttribute("data-sel-state", "conflict");
        if (r.getAttribute("data-sel-merge") !== "no") mismatch = true;
      }
    });
    var n = checked.length;
    var canMerge = n >= 2 && !noRow && !mismatch;
    var text = s.notice;
    var tone = s.tone;
    if (!text) {
      tone = "warn";
      if (noRow) text = noRow.getAttribute("data-sel-merge-reason") || "";
      else if (mismatch) text = cfg.mismatch;
      else if (n === 1) { text = cfg.pick_more; tone = "info"; }
    }
    var nEl = q(scope, ".sel-count__n");
    if (nEl.textContent !== String(n)) {
      nEl.removeAttribute("data-tick"); void nEl.offsetWidth; nEl.setAttribute("data-tick", "");
      nEl.textContent = String(n);
    }
    q(scope, ".sel-count__l").textContent = words(n);
    q(scope, ".sel-delete__n").textContent = String(n);
    q(scope, ".sel-delete").setAttribute("aria-disabled", n === 0 ? "true" : "false");
    q(scope, ".sel-merge").setAttribute("aria-disabled", canMerge ? "false" : "true");
    var msg = q(scope, ".sel-msg");
    msg.classList.toggle("warn", tone === "warn");
    msg.classList.toggle("info", tone !== "warn");
    // always filled while "Mesclar" is disabled: it is its aria-describedby
    q(scope, ".sel-msg__t").textContent = text || (canMerge ? "" : cfg.need_merge);
    msg.setAttribute("data-show", text ? "true" : "false");
    return { n: n, text: text, canMerge: canMerge };
  }

  function holdNotice(scope) {
    var s = stateOf(scope);
    clearTimeout(s.hold);
    s.hold = setTimeout(function () { s.notice = ""; render(scope); }, HOLD_MS);
  }
  // A transient message in the bubble (and the live region): paused while the pointer is over it.
  function flash(scope, text, tone) {
    var s = stateOf(scope);
    s.notice = text; s.tone = tone || "warn";
    render(scope);
    say(scope, text, true);
    holdNotice(scope);
  }

  function setMode(scope, on, refocus) {
    if (on === isOn(scope)) return;
    var s = stateOf(scope);
    scope.setAttribute("data-selecting", on ? "true" : "false");
    q(scope, ".sel-toggle").setAttribute("aria-pressed", on ? "true" : "false");
    q(scope, ".sel-dock").setAttribute("data-open", on ? "true" : "false");
    if (!on) { s.order = []; s.notice = ""; clearTimeout(s.hold); }
    render(scope);
    say(scope, on ? cfg.mode_on : cfg.mode_off, true);
    if (!on && refocus) q(scope, ".sel-toggle").focus();
  }

  // Rows the server refused (the month rolled over, or a rule changed): lock them, uncheck them.
  function lockRows(scope, ids) {
    var s = stateOf(scope);
    (ids || []).forEach(function (id) {
      var row = rowById(scope, id.indexOf(":") > -1 ? id : "entry:" + id);
      if (!row) return;
      var box = boxOf(row);
      row.setAttribute("data-sel-lock", "month");
      row.setAttribute("data-sel-reason", cfg.merge_outside);
      box.setAttribute("aria-disabled", "true");
      s.order = s.order.filter(function (x) { return x !== row.getAttribute("data-sel-id"); });
    });
    render(scope);
  }

  function idQuery(ids) {
    return ids.map(function (id) { return "ids=" + encodeURIComponent(id); }).join("&");
  }

  // --- merge -------------------------------------------------------------------------------
  function openMerge(scope) {
    var dialog = document.getElementById("merge-dialog");
    var chosen = selectedRows(scope);
    if (!dialog || chosen.length < 2 || typeof dialog.showModal !== "function") return;
    var ids = dialog.querySelector("[data-merge-ids]");
    ids.replaceChildren();
    chosen.forEach(function (row) {
      var input = document.createElement("input");
      input.type = "hidden"; input.name = "ids"; input.value = row.getAttribute("data-sel-id").replace(/^entry:/, "");
      ids.appendChild(input);
    });
    var form = dialog.querySelector("form");
    form.elements["description"].value = chosen[0].getAttribute("data-merge-desc") || "";
    form.elements["date"].value = chosen.map(function (r) { return r.getAttribute("data-merge-date") || ""; }).sort().pop();
    form.elements["ack"].checked = false;
    dialog.querySelector("#merge-error").replaceChildren();
    dialog.showModal();
  }

  // --- batch delete ------------------------------------------------------------------------
  function dialogSlot() { return document.getElementById("sel-dialog-slot"); }

  async function problem(response) {
    var body = null;
    try { body = await response.json(); } catch (e) { body = null; }
    return body;
  }

  async function openDelete(scope) {
    var ids = selectedRows(scope).map(function (r) { return r.getAttribute("data-sel-id"); });
    if (!ids.length) { flash(scope, cfg.need_delete, "info"); return; }
    var slot = dialogSlot();
    if (!slot) return;
    var response;
    try {
      response = await fetch("/entries/batch-delete/confirm?" + idQuery(ids), { headers: { "Accept": "text/html" } });
    } catch (e) { flash(scope, cfg.delete_error); return; }
    if (!response.ok) {
      var body = await problem(response);
      flash(scope, (body && body.message) || cfg.delete_error);
      if (body && body.ids && body.ids.length) lockRows(scope, body.ids);
      return;
    }
    slot.innerHTML = await response.text();
    var dialog = slot.querySelector("dialog");
    if (!dialog || typeof dialog.showModal !== "function") return;
    dialog.addEventListener("close", function () { slot.replaceChildren(); });
    dialog.addEventListener("cancel", function (event) {
      if (dialog.querySelector("#del-yes").getAttribute("aria-busy") === "true") event.preventDefault();
    });
    dialog.showModal();
    var no = dialog.querySelector("#del-no");
    if (no) no.focus();
    dialog._scope = scope; dialog._ids = ids;
  }

  async function confirmDelete(dialog) {
    var scope = dialog._scope, ids = dialog._ids;
    var yes = dialog.querySelector("#del-yes"), no = dialog.querySelector("#del-no"), box = dialog.querySelector("#del-err");
    if (yes.getAttribute("aria-busy") === "true") return;
    yes.setAttribute("aria-busy", "true"); yes.setAttribute("aria-disabled", "true"); no.setAttribute("aria-disabled", "true");
    box.hidden = true;
    var done = false, failure = cfg.delete_error, offenders = [];
    try {
      var response = await fetch("/entries/batch-delete", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: idQuery(ids)
      });
      if (response.ok) done = true;
      else {
        var body = await problem(response);
        if (body && body.message) failure = body.message;
        if (body && body.ids) offenders = body.ids;
      }
    } catch (e) { done = false; }
    yes.removeAttribute("aria-busy"); yes.removeAttribute("aria-disabled"); no.removeAttribute("aria-disabled");
    if (!done) {
      box.textContent = failure; box.hidden = false;
      if (offenders.length) lockRows(scope, offenders);
      yes.focus();
      return;
    }
    dialog.close();
    var slot = dialogSlot();
    if (slot) slot.replaceChildren();  // do not wait for the close event: the ids must be free at once
    finishDelete(scope, ids.length);
  }

  // The totals, balances and statements are computed by the server: ask it for the page again.
  function finishDelete(scope, count) {
    setMode(scope, false, false);
    var text = count === 1 ? cfg.deleted_one : String(cfg.deleted_many).replace("{n}", String(count));
    var url = window.location.pathname + window.location.search;
    var reload = function () { window.location.reload(); };
    if (!window.htmx) { reload(); return; }
    window.htmx.ajax("GET", url, { target: "main", select: "main", swap: "outerHTML" }).then(function () {
      var fresh = document.querySelector("[data-sel-scope]");
      if (fresh) {
        say(fresh, text, true);
        var trigger = fresh.querySelector(".sel-toggle") || document.querySelector("main h1");
        if (trigger) { if (!trigger.hasAttribute("tabindex") && trigger.tagName !== "BUTTON") trigger.setAttribute("tabindex", "-1"); trigger.focus(); }
      } else {
        var heading = document.querySelector("main h1");
        if (heading) { heading.setAttribute("tabindex", "-1"); heading.focus(); }
      }
    }, reload);
  }

  // --- events ------------------------------------------------------------------------------
  function scopeOf(target) { return target.closest ? target.closest("[data-sel-scope]") : null; }

  document.addEventListener("click", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    var scope = scopeOf(target);
    if (scope) lastScope = scope;
    if (target.closest("[data-merge-cancel]")) {
      var md = document.getElementById("merge-dialog");
      if (md) md.close();
      return;
    }
    var yes = target.closest("#del-yes");
    if (yes) { confirmDelete(yes.closest("dialog")); return; }
    var no = target.closest("#del-no");
    if (no) { if (no.getAttribute("aria-disabled") !== "true") no.closest("dialog").close(); return; }
    if (!scope) return;
    if (target.closest(".sel-toggle")) { setMode(scope, !isOn(scope), false); return; }
    if (target.closest(".sel-cancel")) { setMode(scope, false, true); return; }
    var del = target.closest(".sel-delete");
    if (del) {
      if (del.getAttribute("aria-disabled") === "true") flash(scope, cfg.need_delete, "info");
      else openDelete(scope);
      return;
    }
    var merge = target.closest(".sel-merge");
    if (merge) {
      if (merge.getAttribute("aria-disabled") === "true") {
        var shown = q(scope, ".sel-msg__t").textContent || cfg.need_merge;
        flash(scope, shown, q(scope, ".sel-msg").classList.contains("info") ? "info" : "warn");
      } else openMerge(scope);
      return;
    }
    if (!isOn(scope)) return;
    // the whole row is the target; inner controls keep their own behaviour
    var row = target.closest("[data-sel-row]");
    if (!row || target.closest(".sel-check") || target.closest("button, a, input, select, textarea, summary, [data-sel-keep]")) return;
    if (row.hasAttribute("data-sel-lock")) { flash(scope, row.getAttribute("data-sel-reason") || ""); return; }
    boxOf(row).click();
  });

  // a locked row's checkbox stays focusable: activating it explains why instead of checking
  document.addEventListener("click", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    if (!target || !target.classList.contains("sel-check") || target.getAttribute("aria-disabled") !== "true") return;
    event.preventDefault();
    var scope = scopeOf(target), row = target.closest("[data-sel-row]");
    if (scope && row) flash(scope, row.getAttribute("data-sel-reason") || "");
  }, true);

  document.addEventListener("change", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    if (!target || !target.classList.contains("sel-check")) return;
    var scope = scopeOf(target), row = target.closest("[data-sel-row]");
    if (!scope || !row) return;
    var s = stateOf(scope), id = row.getAttribute("data-sel-id"), at = s.order.indexOf(id);
    if (target.checked && at < 0) {
      if (s.order.length >= CAP) { target.checked = false; flash(scope, cfg.cap); return; }
      s.order.push(id);
    } else if (!target.checked && at > -1) s.order.splice(at, 1);
    s.notice = ""; clearTimeout(s.hold);
    var result = render(scope);
    say(scope, result.n + " " + words(result.n) + (result.text ? ". " + result.text : ""));
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape" || document.querySelector("dialog[open]")) return;
    var target = event.target instanceof Element ? event.target : null;
    var scope = target ? scopeOf(target) : null;
    if (!scope) {
      var active = document.activeElement;
      if (active && active !== document.body && active !== document.documentElement) return;
      scope = lastScope;
    }
    if (scope && isOn(scope)) { event.preventDefault(); setMode(scope, false, true); }
  });

  // the bubble holds still while the pointer is over it
  document.addEventListener("mouseover", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    var msg = target && target.closest(".sel-msg");
    var scope = msg && scopeOf(msg);
    if (scope) clearTimeout(stateOf(scope).hold);
  });
  document.addEventListener("mouseout", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    var msg = target && target.closest(".sel-msg");
    var scope = msg && scopeOf(msg);
    if (scope && stateOf(scope).notice) holdNotice(scope);
  });

  // the server refused a merge (HTMX swaps no 4xx): say why and lock what it named
  document.addEventListener("htmx:responseError", function (event) {
    var source = event.detail && event.detail.elt instanceof Element ? event.detail.elt : null;
    var xhr = event.detail && event.detail.xhr;
    if (!source || !xhr || !source.closest("#merge-dialog")) return;
    var body = null;
    try { body = JSON.parse(xhr.responseText); } catch (e) { body = null; }
    var scope = lastScope || document.querySelector("[data-sel-scope]");
    var dialog = document.getElementById("merge-dialog");
    if (dialog && dialog.open) dialog.close();
    if (!scope) return;
    if (body && body.ids && body.ids.length) lockRows(scope, body.ids);
    flash(scope, (body && body.message) || cfg.merge_outside);
  });

  // a swap may replace rows: put the checks and states back from the remembered ids
  function renderAll() {
    document.querySelectorAll("[data-sel-scope]").forEach(function (scope) {
      if (isOn(scope) || stateOf(scope).order.length) render(scope);
    });
  }
  document.addEventListener("htmx:afterSettle", renderAll);
})();
