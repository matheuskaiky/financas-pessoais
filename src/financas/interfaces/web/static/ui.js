"use strict";
// Shared UI behavior (design v3). No inline handlers and no dependencies: the CSP only allows scripts
// from this origin. Everything here is progressive: the server already prints the final state, and the
// CSS (tokens.css / app.css) animates it; this file only updates components in place.
//
// Public API (window.FinUI):
//   FinUI.odometer.set(el, cents)   roll an existing odometer ([data-odometer], see _ui.html) to a new value
//   FinUI.odometer.armOnView(el)    hold an odometer that is below the fold (.od-wait) and let it roll when it scrolls into view
//   FinUI.gauge.set(el, percent, add?)  move an existing stroke gauge (.sg) and re-pick its tone
//   FinUI.formatCents(cents)        "1.234,56" (no symbol; negative values keep a leading "−"); formatted by fp-money.js when the page has it
//   FinUI.reducedMotion()           true when the user asked for less motion
// Hooks (data attributes) other scripts bind to: [data-palette-open] (palette.js, work package 4).
(function () {
  const reducedMotion = function () {
    return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  };

  // The one currency layer is fp-money.js (an ES module that publishes window.FP.money). It is looked up at call time, never at load
  // (module and classic scripts both run deferred, in document order, but a page may omit the module); the pt-BR formatting below is
  // only the fallback for a page without it (same output: "1.234,56", "−0,05").
  const money = function () {
    const m = window.FP && window.FP.money;
    return m && typeof m.formatAmount === "function" && typeof m.minorDigits === "function" ? m : null;
  };

  const formatCents = function (cents) {
    const value = Math.trunc(Number(cents) || 0);
    const m = money();
    if (m) return m.formatAmount(value);
    const whole = Math.floor(Math.abs(value) / 100);
    const frac = String(Math.abs(value) % 100).padStart(2, "0");
    return (value < 0 ? "−" : "") + String(whole).replace(/\B(?=(\d{3})+(?!\d))/g, ".") + "," + frac;
  };

  const DIGITS = "<i>0</i><i>1</i><i>2</i><i>3</i><i>4</i><i>5</i><i>6</i><i>7</i><i>8</i><i>9</i>";

  const digitCell = function (ch, tail, index) {
    const cell = document.createElement("span");
    cell.setAttribute("aria-hidden", "true");
    if (tail) cell.className = "od-tail";
    if (ch >= "0" && ch <= "9") {
      const box = document.createElement("span");
      box.className = "od-d";
      const ghost = document.createElement("span");
      ghost.style.visibility = "hidden";
      ghost.textContent = ch;
      const strip = document.createElement("span");
      strip.className = "od-s";
      strip.style.transform = "translateY(-" + ch + "em)";
      strip.style.animationDelay = index * 55 + "ms";
      strip.innerHTML = DIGITS; // static markup, no user data
      box.append(ghost, strip);
      cell.append(box);
    } else {
      cell.textContent = ch;
    }
    return cell;
  };

  const odometerSet = function (el, cents) {
    if (!(el instanceof HTMLElement) || !el.hasAttribute("data-odometer")) return;
    const value = Math.trunc(Number(cents) || 0);
    const text = formatCents(Math.abs(value));
    const withPrefix = el.dataset.odPrefix !== "0";
    const negative = value < 0;
    el.dataset.odometer = String(value);
    const m = money();
    const symbol = m ? m.currencySymbol() : "R$";
    el.setAttribute("aria-label", (negative ? "−" : "") + (withPrefix ? symbol + " " : "") + text);

    // the sign appears or disappears
    let sign = el.querySelector(":scope > .od-sign");
    if (negative && !sign) {
      sign = document.createElement("span");
      sign.className = "od-sign";
      sign.setAttribute("aria-hidden", "true");
      sign.textContent = "−";
      el.prepend(sign);
    } else if (!negative && sign) {
      sign.remove();
    }

    const boxes = Array.from(el.querySelectorAll(":scope > span > .od-d"));
    const digits = text.replace(/[^0-9]/g, "");
    if (boxes.length === digits.length) {
      // same number of digits: only the strips move, and the CSS transition rolls them
      boxes.forEach(function (box, i) {
        const ghost = box.firstElementChild;
        const strip = box.querySelector(".od-s");
        if (ghost) ghost.textContent = digits[i];
        if (strip) {
          strip.style.animation = "none";
          strip.style.transform = "translateY(-" + digits[i] + "em)";
        }
      });
      return;
    }
    // a different length (1.000,00 -> 999,00): rebuild the cells
    Array.from(el.children).forEach(function (child) {
      if (!child.classList.contains("od-sign") && !child.classList.contains("od-cur")) child.remove();
    });
    let n = 0;
    let tail = false;
    // the cents start at the decimal separator, which sits `places` digits from the end (pt-BR "," today; whatever the locale says)
    const places = m ? m.minorDigits() : 2;
    const tailAt = places > 0 ? text.length - places - 1 : -1;
    Array.from(text).forEach(function (ch, at) {
      if (at === tailAt) tail = true;
      el.append(digitCell(ch, tail, n));
      if (ch >= "0" && ch <= "9") n++;
    });
  };

  const gaugeSet = function (el, percent, add) {
    if (!(el instanceof HTMLElement) || !el.classList.contains("sg")) return;
    const main = Math.min(Math.max(Number(percent) || 0, 0), 100);
    const extra = Math.max(0, Math.min(Number(add) || 0, 100 - main));
    const total = (Number(percent) || 0) + (Number(add) || 0);
    const warn = parseFloat(el.style.getPropertyValue("--sg-mark")) || 80;
    el.style.setProperty("--sg-w", main.toFixed(2) + "%");
    el.style.setProperty("--sg-add", extra.toFixed(2) + "%");
    el.classList.remove("sg-forest", "sg-gold", "sg-negative");
    el.classList.add(total > 100 ? "sg-negative" : total >= warn ? "sg-gold" : "sg-forest");
  };

  // A roll that plays below the fold is a roll nobody sees: hold it (.od-wait stops the CSS animation, the final digits are
  // already printed) until the figure enters the viewport, then let it play once. Without IntersectionObserver it just plays.
  const rollObserver =
    typeof window.IntersectionObserver === "function"
      ? new window.IntersectionObserver(
          function (entries) {
            entries.forEach(function (entry) {
              if (!entry.isIntersecting) return;
              entry.target.classList.remove("od-wait");
              rollObserver.unobserve(entry.target);
            });
          },
          { threshold: 0.1 },
        )
      : null;

  const armable = function (el) {
    return !!rollObserver && el instanceof HTMLElement && !el.classList.contains("od-flat");
  };

  const hold = function (el, box) {
    if (box.bottom > 0 && box.top < window.innerHeight) return; // already on screen: it plays now
    el.classList.add("od-wait");
    rollObserver.observe(el);
  };

  const armOnView = function (el) {
    if (armable(el)) hold(el, el.getBoundingClientRect());
  };

  // Every box is measured first and the classes are written afterwards: one layout for the whole page, not one per odometer.
  const armAll = function () {
    const list = Array.from(document.querySelectorAll(".od")).filter(armable);
    const boxes = list.map(function (el) { return el.getBoundingClientRect(); });
    list.forEach(function (el, i) { hold(el, boxes[i]); });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", armAll);
  else armAll();

  window.FinUI = {
    odometer: { set: odometerSet, armOnView: armOnView },
    gauge: { set: gaugeSet },
    formatCents: formatCents,
    reducedMotion: reducedMotion,
  };
})();
