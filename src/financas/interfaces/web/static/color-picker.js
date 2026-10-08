// Color picker (macro color_picker in _macros.html): a swatch that opens the system dialog, a HEX field
// (the one that is posted), bank presets and the browser's EyeDropper where it exists. Everything is
// delegated from the document, so a fragment swapped in by HTMX works without any re-binding.
(function () {
  "use strict";
  var FULL = /^#[0-9A-F]{6}$/;
  var MESSAGE = "Use o formato #RRGGBB (por exemplo, #FF7A00).";

  // keep only hex digits, one leading "#", upper case, at most six digits
  function clean(text) {
    var digits = String(text || "").toUpperCase().replace(/[^0-9A-F]/g, "").slice(0, 6);
    return digits ? "#" + digits : "";
  }
  function parts(picker) {
    return {
      hex: picker.querySelector("[data-cp-hex]"),
      native: picker.querySelector("[data-cp-native]"),
      preview: picker.querySelector("[data-cp-preview]"),
      feedback: picker.querySelector("[data-cp-feedback]"),
    };
  }
  function say(picker, text) {
    var p = parts(picker);
    if (p.feedback) p.feedback.textContent = text || "";
    if (p.hex) p.hex.setAttribute("aria-invalid", text ? "true" : "false");
    picker.classList.toggle("is-invalid", Boolean(text));
  }
  function paint(picker, color) {  // swatch, system picker, optional live previews, event for other widgets
    var p = parts(picker), shown = color || picker.getAttribute("data-fallback") || "#0F5C45";
    if (p.preview) { p.preview.style.background = shown; p.preview.classList.toggle("is-empty", !color); }
    if (p.native && FULL.test(shown)) p.native.value = shown.toLowerCase();
    var target = picker.getAttribute("data-preview-target");
    if (target) document.querySelectorAll(target).forEach(function (node) { node.style.setProperty("--live-color", shown); });
    picker.dispatchEvent(new CustomEvent("colorpicker:change", { bubbles: true, detail: { value: color } }));
  }
  function set(picker, color) {  // a color chosen by a control: normalize, show everywhere
    var p = parts(picker), value = clean(color);
    if (p.hex) p.hex.value = value;
    say(picker, value && !FULL.test(value) ? MESSAGE : "");
    paint(picker, FULL.test(value) ? value : "");
  }
  function pickerOf(target) {
    return target instanceof Element ? target.closest("[data-color-picker]") : null;
  }

  // typing in the HEX field: sanitize as it goes; the swatch follows once it is a full color
  document.addEventListener("input", function (event) {
    var picker = pickerOf(event.target);
    if (!picker) return;
    if (event.target.matches("[data-cp-hex]")) {
      var value = clean(event.target.value);
      event.target.value = value;
      var full = FULL.test(value);
      say(picker, "");  // a half-typed color is only reported on submit
      if (full || !value) paint(picker, full ? value : "");
    } else if (event.target.matches("[data-cp-native]")) {
      set(picker, event.target.value);  // the system dialog fills the text field
    }
  });
  document.addEventListener("click", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    var picker = pickerOf(target);
    if (!picker || !target) return;
    var preset = target.closest("[data-cp-preset]");
    if (preset) { set(picker, preset.getAttribute("data-cp-preset")); return; }
    if (target.closest("[data-cp-clear]")) { set(picker, ""); return; }
    var drop = target.closest("[data-cp-eyedropper]");
    if (drop && "EyeDropper" in window) {
      drop.disabled = true;
      new window.EyeDropper().open().then(function (result) {
        set(picker, result.sRGBHex);
      }).catch(function () {
        // the user pressed Esc, or the page lost focus: nothing to do
      }).then(function () { drop.disabled = false; });
    }
  });
  // before a submit: a missing "#" is added; a half-typed color stops the form with a message
  document.addEventListener("submit", function (event) {
    var form = event.target instanceof Element ? event.target : null;
    if (!form) return;
    var broken = null;
    form.querySelectorAll("[data-color-picker]").forEach(function (picker) {
      var hex = parts(picker).hex, value = clean(hex.value);
      if (value && !FULL.test(value)) { say(picker, MESSAGE); broken = broken || hex; }
      else hex.value = value;
    });
    if (broken) {
      event.preventDefault();
      event.stopImmediatePropagation();  // HTMX forms must not send it either
      broken.focus();
    }
  }, true);

  // the EyeDropper button exists only where the browser has the API (no error, no dead button)
  function reveal(root) {
    if (!("EyeDropper" in window)) return;
    (root || document).querySelectorAll("[data-cp-eyedropper]").forEach(function (button) { button.hidden = false; });
  }
  document.addEventListener("htmx:afterSettle", function (event) { reveal(event.target); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { reveal(document); });
  else reveal(document);
})();
