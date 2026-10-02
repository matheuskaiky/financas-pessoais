"use strict";
// Command palette (⌘K / Ctrl+K and every [data-palette-open]). A native <dialog>: showModal() gives the
// focus trap, Esc and an inert page behind it. This file adds the shortcut, closing on the backdrop,
// returning the focus, filtering the page/action list as you type and arrow-key navigation.
// The phrase itself is read by the server (htmx -> /palette/parse): nothing is parsed here.
(function () {
  let opener = null;

  const dialog = function () { return document.getElementById("palette"); };
  const input = function () { return document.getElementById("palette-input"); };
  const fold = function (text) {
    return String(text || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  };
  const items = function () {
    return Array.from(document.querySelectorAll("#palette-list .pal-item")).filter(function (el) { return !el.hidden && !el.closest("[hidden]"); });
  };

  const setActive = function (index) {
    const list = items();
    document.querySelectorAll("#palette-list .pal-item.is-active").forEach(function (el) {
      el.classList.remove("is-active");
      el.removeAttribute("aria-selected");
    });
    const field = input();
    if (index < 0 || index >= list.length) {
      if (field) field.removeAttribute("aria-activedescendant");
      return;
    }
    const el = list[index];
    el.classList.add("is-active");
    el.setAttribute("aria-selected", "true");
    if (!el.id) el.id = "pal-item-" + index;
    if (field) field.setAttribute("aria-activedescendant", el.id);
    el.scrollIntoView({ block: "nearest" });
  };
  const activeIndex = function () {
    return items().findIndex(function (el) { return el.classList.contains("is-active"); });
  };

  const filter = function () {
    const field = input();
    const words = fold(field ? field.value : "").split(/\s+/).filter(Boolean);
    let shown = 0;
    document.querySelectorAll("#palette-list .pal-item").forEach(function (el) {
      const hay = fold(el.getAttribute("data-search") + " " + el.textContent);
      const match = words.every(function (w) { return hay.indexOf(w) !== -1; });
      const holder = el.closest(".pal-form") || el;
      holder.hidden = !match;
      if (match) shown++;
    });
    document.querySelectorAll("#palette-list [data-group]").forEach(function (group) {
      group.hidden = !group.querySelector(".pal-item:not([hidden])") && !Array.from(group.querySelectorAll(".pal-form")).some(function (f) { return !f.hidden; });
    });
    const none = document.querySelector("#palette-list [data-none]");
    if (none) none.hidden = shown > 0 || words.length === 0;
    setActive(-1);
  };

  const open = function (event) {
    const box = dialog();
    if (!box || typeof box.showModal !== "function") return;
    if (event) event.preventDefault();
    if (!box.open) {
      opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      box.showModal();
    }
    const field = input();
    if (field) { field.focus(); field.select(); }
  };
  const close = function () {
    const box = dialog();
    if (box && box.open) box.close();
  };

  document.addEventListener("keydown", function (event) {
    const key = event.key ? event.key.toLowerCase() : "";
    if ((event.metaKey || event.ctrlKey) && key === "k") {
      event.preventDefault();
      const box = dialog();
      if (box && box.open) close(); else open();
    }
  });

  document.addEventListener("click", function (event) {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target.closest("[data-palette-open]")) { open(event); return; }
    if (target.closest("[data-palette-close]")) { close(); return; }
    const box = dialog();
    if (box && target === box) close(); // a click on the backdrop (the dialog element itself)
  });

  document.addEventListener("close", function (event) {
    if (event.target !== dialog()) return;
    const field = input();
    if (field) { field.value = ""; filter(); }
    const preview = document.getElementById("palette-preview");
    if (preview && field) field.dispatchEvent(new Event("input", { bubbles: true }));
    if (opener && document.contains(opener)) opener.focus();
    opener = null;
  }, true);

  document.addEventListener("input", function (event) {
    if (event.target === input()) filter();
  });

  document.addEventListener("keydown", function (event) {
    const box = dialog();
    if (!box || !box.open || !box.contains(event.target instanceof Node ? event.target : null)) return;
    const list = items();
    if (event.key === "ArrowDown" && list.length) {
      event.preventDefault();
      setActive((activeIndex() + 1) % list.length);
    } else if (event.key === "ArrowUp" && list.length) {
      event.preventDefault();
      const at = activeIndex();
      setActive(at <= 0 ? list.length - 1 : at - 1);
    } else if (event.key === "Enter" && event.target === input()) {
      event.preventDefault();
      const at = activeIndex();
      const primary = document.querySelector("#palette-preview [data-primary]");
      if (at >= 0) list[at].click();
      else if (primary instanceof HTMLElement) primary.click();
      else if (list.length === 1) list[0].click();
    }
  });

  // after a result is shown, the page links inside the dialog close it (same-page anchors keep working)
  document.addEventListener("click", function (event) {
    const target = event.target instanceof Element ? event.target.closest("#palette a[href^='#']") : null;
    if (target) close();
  });
})();
