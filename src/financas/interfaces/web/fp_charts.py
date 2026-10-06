"""fp_charts — geometry for the server-rendered charts: Ritmo do mês, Fluxo de caixa, Rosca de categorias, Saldo diário.

Standard library only (fp_money is used when it can be imported, for the currency symbol and the decimal separator).
Money enters as INTEGER minor units (centavos), never float. The functions return plain dicts of numbers and ready-to-print strings;
snippets/charts-extra.html prints them (no arithmetic and no inline style in the templates).

Every formula is the canvas's own (GrafRitmo / GrafFluxo / GrafDonut / GrafSaldoDiario .dc.html): the unit tests run the canvas JavaScript in Node
and compare the numbers (golden file tests/golden/charts.json of the source package, not in this archive). Differences from the canvas are listed in PATCH_NOTES.md («Flagged deltas»).

    import fp_charts
    fp_charts.install(templates.env)                       # filters cx_odo, cx_parts, cx_compact, cx_pct, cx_cls (needs fp_money.install first)

    ctx = fp_charts.ritmo(daily=[...], counts=[...], average=[...], ceiling=619_866, days=30, year=2026, month=9)
    # → {"w": 540, "h": 264, "paths": {"step": "M8.00 …"}, "points": [...], …}      {% from "snippets/charts-extra.html" import ritmo %}{{ ritmo(ctx) }}

Public surface
    geometry   to_fixed · tangents · curve · smooth_path · step_path · hv_step_path · ticks_from_zero · ticks_window · ring · bar_path
    charts     ritmo · fluxo · donut · saldo_diario · frame (dimensions only: loading / error)
    text       compact · pct1 · odo · WEEKDAYS · MONTHS · MONTHS_FULL
    css        pct_int · pct_cls · scale_pct · shares — whole-percent carriers for the class-driven bars (.pct-N, .at-N in components.css; never an inline style)
    seal       rose · seal_roses — the two guilloche lines of the Carta seal (snippets/screens.html seal(); the SHA-256 print is computed by the caller)
"""

from __future__ import annotations

import datetime as _dt
import math
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from typing import Any

try:  # the currency layer is optional here: only compact() and the odometer parts need it
    from financas.interfaces.web import fp_money as _fm
except Exception:  # pragma: no cover - exercised only when fp_money is not on the path
    _fm = None

__all__ = [
    "MONTHS",
    "MONTHS_FULL",
    "STEPS",
    "WEEKDAYS",
    "bar_path",
    "compact",
    "curve",
    "donut",
    "fluxo",
    "frame",
    "hv_step_path",
    "install",
    "money_parts",
    "odo",
    "pct1",
    "pct_cls",
    "pct_int",
    "ring",
    "ritmo",
    "rose",
    "saldo_diario",
    "scale_pct",
    "seal_roses",
    "shares",
    "smooth_path",
    "step_path",
    "tangents",
    "ticks_from_zero",
    "ticks_window",
    "to_fixed",
]

WEEKDAYS = ("dom", "seg", "ter", "qua", "qui", "sex", "sáb")  # Sunday first, like Date.getDay()
MONTHS = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
MONTHS_FULL = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)
_FLUXO_LABEL_W = 11 * 5.7 + 10  # a typical «+R$ 3,4 mil» label: 11 characters at 5.7 px + 10
STEPS = (
    100,
    200,
    250,
    500,
    1000,
    2000,
    2500,
    5000,
    10000,
    20000,
    25000,
    50000,
    100000,
)  # round reference lines, in whole currency units


# ───────────────────────────── numbers and text ─────────────────────────────


def to_fixed(x: float, nd: int = 2) -> str:
    """Number.prototype.toFixed: the exact binary value, rounded half UP (Python's format() rounds half to even)."""
    q = Decimal(1).scaleb(-nd)
    return format(Decimal(x).quantize(q, rounding=ROUND_HALF_UP), "f")


def _pc(v: float, whole: float, nd: int = 2) -> str:
    """A position as a percentage of the plot box (the overlays are placed with these)."""
    return to_fixed(v / whole * 100, nd)


def _dec() -> str:
    if _fm is not None:
        try:
            return _fm.LOCALES[_fm.get_config()["locale"]].dec
        except Exception:
            pass
    return ","


def pct_int(value: Any, whole: Any = 100, *, lo: int = 0, hi: int = 100) -> int:
    """value / whole as a WHOLE percent, rounded half up and clamped to lo…hi — the number behind a `.pct-N` / `.at-N` / `.add-N` class.
    A missing or non-finite value, or a zero `whole`, is `lo` (an empty bar, never an exception in a template)."""
    try:
        v = float(value)
        w = float(whole)
    except (TypeError, ValueError):
        return lo
    if not math.isfinite(v) or not math.isfinite(w) or w == 0:
        return lo
    return int(max(lo, min(hi, math.floor(v / w * 100 + 0.5))))


def pct_cls(
    value: Any, prefix: str = "pct", whole: Any = 100, *, lo: int = 0, hi: int = 100
) -> str:
    """`pct-37` — the class that carries a size or a position to CSS (prefix "pct" → --p, "add" → --a, "at" → --x).  Jinja: `{{ share|cx_cls }}`, `{{ at|cx_cls('at') }}`."""
    return f"{prefix}-{pct_int(value, whole, lo=lo, hi=hi)}"


def scale_pct(values: Sequence[Any], top: Any = None) -> list[int]:
    """Whole percents of a series where the tallest value (or `top`, when given) is 100 — bars use the full plot height, so the ≤ 0.5 % rounding stays as small as it can be.
    Negative values and non-numbers are 0."""
    nums = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            f = 0.0
        nums.append(f if math.isfinite(f) and f > 0 else 0.0)
    ref = float(top) if top not in (None, 0) else (max(nums) if nums else 0.0)
    return [pct_int(f, ref) for f in nums]


def shares(values: Sequence[Any], total: int = 100) -> list[int]:
    """Largest-remainder split of non-negative values into whole percents that ADD UP to `total` (a composition bar must close at 100).
    All zero (or no data) → all zero."""
    nums = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            f = 0.0
        nums.append(f if math.isfinite(f) and f > 0 else 0.0)
    s = sum(nums)
    if s <= 0:
        return [0] * len(nums)
    raw = [f / s * total for f in nums]
    out = [math.floor(r) for r in raw]
    order = sorted(range(len(nums)), key=lambda i: (-(raw[i] - out[i]), i))
    for i in order[: total - sum(out)]:
        out[i] += 1
    return out


def rose(radius: float, k: float, amp: float, phase: float = 0.0, *, steps: int = 360) -> str:
    """One guilloche line of the Carta seal: r = R·(1 + amp·cos(k·t + phase)) turned once around (60, 60) of a 120 × 120 viewBox — 361 points, two decimals, closed.
    Same arithmetic and the same toFixed as the canvas's `rose()` (golden-tested against it), so the server draws the seal the board drew."""
    d = []
    for i in range(steps + 1):
        t = (i / steps) * 2 * math.pi
        r = radius * (1 + amp * math.cos(k * t + phase))
        d.append(
            ("L" if i else "M")
            + to_fixed(60 + r * math.cos(t), 2)
            + " "
            + to_fixed(60 + r * math.sin(t), 2)
            + " "
        )
    return "".join(d) + "Z"


@lru_cache(maxsize=1)
def seal_roses() -> tuple[str, str]:
    """The two lines the seal is made of (outer ring of 9 petals, inner ring of 12): `rose(33, 9, .16, 0)` and `rose(40, 12, .11, .5)`. They do not depend on the month or the print."""
    return rose(33, 9, 0.16, 0.0), rose(40, 12, 0.11, 0.5)


def pct1(x: float, dec: str | None = None, *, sign: bool = False) -> str:
    """One decimal, no percent sign: 34.2 → "34,2" (decimal separator from fp_money's locale). Math.round(x * 10) / 10, then toFixed(1), like the canvas."""
    s = to_fixed(math.floor(x * 10 + 0.5) / 10, 1).replace(".", dec if dec is not None else _dec())
    return ("+" if sign and x > 0 else "") + s


def _units(locale: str) -> tuple[str, str, str, str] | None:
    """Short compact units per locale (thousand, million, billion, trillion), with the exact spacing Intl writes. None = not compacted here."""
    nb = "\u00a0"
    return {
        "pt-BR": (nb + "mil", nb + "mi", nb + "bi", nb + "tri"),
        "en-US": ("K", "M", "B", "T"),
    }.get(locale)


def compact(
    cents: int, *, fraction: int | None = None, sign: str = "auto", symbol: bool = True
) -> str:
    """Axis label, like Intl's compact «short» currency: 200000 → "R$ 2 mil" · 1234500 → "R$ 12,3 mil" · 150000000 → "R$ 1,5 mi" · 5000 → "R$ 50".
    `fraction` = most fraction digits (JS formatCompact's default is 2; the charts pass 1). Rounding is half away from zero on the EXACT decimal,
    and the unit is chosen after rounding (999.95 mil → "1 mi"). A locale without a table here gets the full currency text with the zeros trimmed."""
    n = int(cents)
    fraction = 2 if fraction is None else int(fraction)
    parts = money_parts(n, sign=sign)
    loc = _fm.get_config()["locale"] if _fm is not None else "pt-BR"
    units = _units(loc)
    if parts is None or _fm is None:
        return str(n)
    digits = _fm.minor_digits()
    if units is None:
        amount = parts["amount"]
        if digits and amount.endswith(_dec() + "0" * digits):
            amount = amount[: -(digits + 1)]
        if not symbol or not parts["symbol"]:
            return parts["sign"] + amount
        gap = parts["gap"]
        return parts["sign"] + (
            amount + gap + parts["symbol"]
            if parts["position"] == "after"
            else parts["symbol"] + gap + amount
        )
    d = Decimal(abs(n)).scaleb(-digits)
    q = Decimal(1).scaleb(-fraction)
    k = 0
    while d >= 1000 and k < 4:
        d = d.scaleb(-3)
        k += 1
    r = d.quantize(q, rounding=ROUND_HALF_UP)
    while r >= 1000 and k < 4:
        r = r.scaleb(-3).quantize(q, rounding=ROUND_HALF_UP)
        k += 1
    num = format(r, "f")
    if "." in num:
        num = num.rstrip("0").rstrip(".")
    num = num.replace(".", _dec()) + (units[k - 1] if k else "")
    if not symbol or not parts["symbol"]:
        return parts["sign"] + num
    gap = parts["gap"]
    return parts["sign"] + (
        num + gap + parts["symbol"] if parts["position"] == "after" else parts["symbol"] + gap + num
    )


def money_parts(cents: Any, **kw: Any) -> dict[str, Any] | None:
    """fp_money.format_money_parts, or None when fp_money is not importable / the value is not a number."""
    if _fm is None:
        return None
    return _fm.format_money_parts(cents, **kw)


def odo(cents: Any, *, sign: str = "auto", symbol: bool = True) -> dict[str, Any]:
    """The rolling number (Odômetro) as data: {label, sign, symbol, gap, position, cells, digits, amount, tail}. `cells` = [{ch, digit, tail}] for the amount;
    the tail starts at the decimal separator (index `tail`, -1 = none; the canvas draws it smaller). `label` is the full text for aria-label.
    A missing value gives "—" and no cells."""
    p = money_parts(cents, sign=sign, symbol=symbol)
    if p is None:
        return {
            "label": "—",
            "sign": "",
            "symbol": "",
            "gap": "",
            "position": "before",
            "cells": [],
            "digits": 0,
            "amount": "",
            "tail": -1,
        }
    amount = p["amount"]
    cut = amount.rfind(_dec()) if _fm is not None and _fm.minor_digits() > 0 else -1
    cells = [
        {"ch": ch, "digit": "0" <= ch <= "9", "tail": cut >= 0 and i >= cut}
        for i, ch in enumerate(amount)
    ]
    return {
        "label": p["text"],
        "sign": p["sign"],
        "symbol": p["symbol"] if symbol else "",
        "gap": p["gap"],
        "position": p["position"],
        "cells": cells,
        "digits": sum(1 for c in cells if c["digit"]),
        "amount": amount,
        "tail": cut,
    }


# ───────────────────────────── paths ─────────────────────────────


def tangents(y: Sequence[float]) -> list[float]:
    """Fritsch–Carlson tangents for uniformly spaced points (one per day), as GrafRitmo.tangents: never overshoots a local extremum."""
    n = len(y)
    if n < 2:
        return [0.0] * n
    d = [y[i + 1] - y[i] for i in range(n - 1)]
    m = [0.0] * n
    m[0], m[n - 1] = d[0], d[n - 2]
    for i in range(1, n - 1):
        m[i] = 0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / d[i], m[i + 1] / d[i]
        s = a * a + b * b
        if s > 9:
            k = 3 / math.sqrt(s)
            m[i], m[i + 1] = k * a * d[i], k * b * d[i]
    return m


def curve(xs: Sequence[float], ys: Sequence[float], my: Sequence[float], nd: int = 2) -> str:
    """Cubic Bézier path through the points with the given tangents (GrafRitmo.curve)."""
    f = lambda v: to_fixed(v, nd)  # noqa: E731
    out = [f"M{f(xs[0])} {f(ys[0])}"]
    for i in range(len(xs) - 1):
        dx = (xs[i + 1] - xs[i]) / 3
        out.append(
            f"C{f(xs[i] + dx)} {f(ys[i] + my[i] / 3)} {f(xs[i + 1] - dx)} {f(ys[i + 1] - my[i + 1] / 3)} {f(xs[i + 1])} {f(ys[i + 1])}"
        )
    return "".join(out)


def smooth_path(xs: Sequence[float], ys: Sequence[float], nd: int = 2) -> str:
    """The monotone curve of the «média» line. Uniform x spacing (one point per day)."""
    return curve(xs, ys, tangents(ys), nd)


def step_path(xs: Sequence[float], ys: Sequence[float], y0: float, nd: int = 2) -> str:
    """Step-after path that rises from the baseline y0: each day's spending is a vertical jump on that day, flat until the next (GrafRitmo.step)."""
    f = lambda v: to_fixed(v, nd)  # noqa: E731
    d = f"M{f(xs[0])} {f(y0)}V{f(ys[0])}"
    return d + "".join(f"H{f(xs[i])}V{f(ys[i])}" for i in range(1, len(xs)))


def hv_step_path(xs: Sequence[float], ys: Sequence[float], a: int, b: int, nd: int = 1) -> str:
    """Balance steps from index a to b: horizontal to the next day, vertical on the day of the entry (GrafSaldoDiario.step)."""
    f = lambda v: to_fixed(v, nd)  # noqa: E731
    d = f"M{f(xs[a])} {f(ys[a])}"
    return d + "".join(f"H{f(xs[i])}V{f(ys[i])}" for i in range(a + 1, b + 1))


def ticks_from_zero(ymax: float, max_ticks: int = 3) -> list[int]:
    """Reference lines of a 0…ymax axis: multiples of the smallest round step that keeps at most `max_ticks` lines (Ritmo: 2, 4, 6 mil)."""
    for st in STEPS:
        step = st * 100
        k = int(ymax // step)
        if k <= max_ticks:
            return [step * i for i in range(1, k + 1)]
    return []


def ticks_window(lo: float, hi: float, max_ticks: int = 3) -> list[int]:
    """At most `max_ticks` round reference lines strictly inside the padded domain (GrafSaldoDiario.domOf)."""
    for st in STEPS:
        step = st * 100
        a = lo + (hi - lo) * 0.06
        b = hi - (hi - lo) * 0.10
        out: list[int] = []
        v = math.ceil(a / step) * step
        while v <= b:
            out.append(int(v))
            v += step
        if len(out) <= max_ticks:
            return out
    return []


def ring(
    fractions: Sequence[float],
    *,
    size: float,
    stroke: float = 20,
    hot_stroke: float = 25,
    gap: float = 6,
    inset: float = 14,
) -> dict[str, Any]:
    """Donut segments as circles that start at 12 o'clock: stroke-dasharray «dash rest» and stroke-dashoffset.
    Round caps extend by half the stroke at both ends, so each dash is shortened by the gap plus the stroke: the VISIBLE gap stays `gap` px at any width.
    Each segment carries the geometry at rest (`stroke`) and hovered (`hot_stroke`), because the cap compensation depends on the width."""
    c = size / 2
    r = size / 2 - inset
    circ = 2 * math.pi * r
    acc = 0.0
    segs = []
    for f in fractions:
        length = f * circ

        def at(sw: float, _length: float = length, _acc: float = acc) -> dict[str, float]:
            dash = max(0.5, _length - gap - sw)
            return {
                "sw": sw,
                "dash": dash,
                "rest": circ - dash,
                "offset": -(_acc * circ + (gap + sw) / 2),
            }

        segs.append({"frac": f, "rest": at(stroke), "hot": at(hot_stroke)})
        acc += f
    return {
        "c": c,
        "r": r,
        "circ": circ,
        "stroke": stroke,
        "hot_stroke": hot_stroke,
        "gap": gap,
        "segments": segs,
    }


def bar_path(
    x: float, y: float, w: float, h: float, r_top: float, r_bottom: float, nd: int = 2
) -> str:
    """Closed rounded bar with different top and bottom radii (CSS border-radius: «9px 9px 2px 2px»). Radii shrink together when they do not fit."""
    f = lambda v: to_fixed(v, nd)  # noqa: E731
    rt, rb = float(r_top), float(r_bottom)
    for side, (a, b) in ((w, (rt, rt)), (w, (rb, rb)), (h, (rt, rb))):
        if a + b > side > 0:
            k = side / (a + b)
            rt, rb = rt * k, rb * k
    return (
        f"M{f(x + rt)} {f(y)}H{f(x + w - rt)}A{f(rt)} {f(rt)} 0 0 1 {f(x + w)} {f(y + rt)}V{f(y + h - rb)}A{f(rb)} {f(rb)} 0 0 1 {f(x + w - rb)} {f(y + h)}"
        f"H{f(x + rb)}A{f(rb)} {f(rb)} 0 0 1 {f(x)} {f(y + h - rb)}V{f(y + rt)}A{f(rt)} {f(rt)} 0 0 1 {f(x + rt)} {f(y)}Z"
    )


def _open_bar_path(
    x: float, y: float, w: float, h: float, r: float, *, up: bool, nd: int = 2
) -> str:
    """Outline of a ghost bar: the base is open (CSS: border-bottom/top: 0). `up` = it stands on the baseline and the rounded corners are at the top."""
    f = lambda v: to_fixed(v, nd)  # noqa: E731
    r = min(r, w / 2, h)
    if up:
        return f"M{f(x)} {f(y + h)}V{f(y + r)}A{f(r)} {f(r)} 0 0 1 {f(x + r)} {f(y)}H{f(x + w - r)}A{f(r)} {f(r)} 0 0 1 {f(x + w)} {f(y + r)}V{f(y + h)}"
    return f"M{f(x)} {f(y)}V{f(y + h - r)}A{f(r)} {f(r)} 0 0 0 {f(x + r)} {f(y + h)}H{f(x + w - r)}A{f(r)} {f(r)} 0 0 0 {f(x + w)} {f(y + h - r)}V{f(y)}"


def _est_half(text_len: int) -> int:
    """Half the estimated tooltip width in px, as the canvas computes it (the controller measures the real one; this is the no-JS clamp)."""
    return math.ceil((22 + 7.2 * text_len) / 2) + 2


def _day(year: int, month: int, day: int) -> dict[str, Any]:
    d = _dt.date(year, month, day)
    wd = (d.weekday() + 1) % 7
    return {
        "date": d,
        "wd": WEEKDAYS[wd],
        "mon": MONTHS[d.month - 1],
        "dd": f"{d.day:02d}",
        "mm": f"{d.month:02d}",
        "label": f"{WEEKDAYS[wd]} {d.day:02d} {MONTHS[d.month - 1]}",
    }


# ───────────────────────────── Ritmo do mês ─────────────────────────────


def ritmo(
    daily: Sequence[int],
    counts: Sequence[int],
    average: Sequence[int],
    ceiling: int,
    *,
    days: int,
    year: int,
    month: int,
    w: int = 540,
    h: int = 200,
    compact_layout: bool = False,
    uid: str = "r",
    y_round: int = 50_000,
    max_ticks: int = 3,
) -> dict[str, Any]:
    """Cumulative spending as a step line against the usual pace (average of the previous months, a monotone curve) and the monthly ceiling.
    `daily`/`counts`: spending and number of entries per day so far (1st … today; empty = nothing registered yet → `sparse`).
    `average`: the usual cumulative curve per day (padded with its last value to `days`). `month` is 1…12."""
    D = int(days)
    TOP, BOT, PADL, PADR = 34, 30, 8, 8
    PH = int(h)
    H = TOP + PH + BOT
    daily = [int(v) for v in daily]
    counts = [int(v) for v in counts] + [0] * max(0, len(daily) - len(counts))
    sparse = len(daily) == 0
    cum: list[int] = []
    run = 0
    for a in daily:
        run += a
        cum.append(run)
    avg = [int(v) for v in average][:D]
    while len(avg) < D:
        avg.append(avg[-1] if avg else 0)
    teto = int(ceiling)
    max_val = max([cum[-1] if cum else 0, avg[-1], teto])
    ymax = math.ceil(max_val * 1.04 / y_round) * y_round
    W = int(w)
    y_of = lambda v: TOP + (1 - v / ymax) * PH  # noqa: E731
    x_of = lambda i: PADL + i / (D - 1) * (W - PADL - PADR)  # noqa: E731
    xs = [x_of(i) for i in range(D)]
    avg_ys = [y_of(v) for v in avg]
    ticks = ticks_from_zero(ymax, max_ticks)
    paths = {
        "grid": "".join(f"M{PADL} {to_fixed(y_of(v), 1)}H{W - PADR}" for v in ticks),
        "teto": f"M{PADL} {to_fixed(y_of(teto), 1)}H{W - PADR}",
        "avg": smooth_path(xs, avg_ys),
        "step": "",
        "area": "",
    }
    points: list[dict[str, Any]] = []
    cross = None
    if not sparse:
        cx = [x_of(i) for i in range(len(cum))]
        cy = [y_of(v) for v in cum]
        paths["step"] = step_path(cx, cy, y_of(0))
        paths["area"] = (
            paths["step"]
            + f"L{to_fixed(cx[-1])} {to_fixed(y_of(0))}L{to_fixed(cx[0])} {to_fixed(y_of(0))}Z"
        )
        for i, v in enumerate(cum):
            dd = _day(year, month, i + 1)
            points.append(
                {
                    "i": i,
                    "day": i + 1,
                    "wd": dd["wd"],
                    "mon": dd["mon"],
                    "dd": dd["dd"],
                    "date": dd["label"],
                    "x": _pc(x_of(i), W, 3),
                    "y": _pc(cy[i], H, 3),
                    "y_avg": _pc(avg_ys[i], H, 3),
                    "amt": daily[i],
                    "n": counts[i],
                    "cum": v,
                    "avg": avg[i],
                    "d_avg": v - avg[i],
                    "d_ceiling": v - teto,
                }
            )
        k = next((i for i, v in enumerate(cum) if v >= teto), -1)
        if k >= 0:
            cross = {"i": k, "day": k + 1, "x": _pc(cx[k], W, 2), "y": _pc(cy[k], H, 2)}
    x_ticks = []
    for k, n in enumerate((0, 9, 19, D - 1)):
        x_ticks.append(
            {
                "i": n,
                "left": to_fixed(n / (D - 1) * 100, 2),
                "anchor": "start" if k == 0 else "end" if k == 3 else "middle",
                "label": "dia 1" if k == 0 else str(n + 1),
            }
        )
    return {
        "kind": "ritmo",
        "uid": uid,
        "w": W,
        "h": H,
        "plot_h": PH,
        "top": TOP,
        "bot": BOT,
        "days": D,
        "compact": bool(compact_layout),
        "sparse": sparse,
        "ymax": ymax,
        "ratio": f"{W} / {H}",
        "gid": f"cx-ritmo-{uid}",
        "paths": paths,
        "y_ticks": [{"cents": v, "top": _pc(y_of(v), H)} for v in ticks],
        "x_ticks": x_ticks,
        "teto": {"cents": teto, "top": _pc(y_of(teto), H)},
        "guide": {"top": _pc(TOP, H), "bot": _pc(BOT, H)},
        "origin": {"x": _pc(x_of(0), W), "y": _pc(y_of(0), H)},
        "cross": cross,
        "points": points,
        "last": len(points) - 1,
        "total": cum[-1] if cum else 0,
        "entries": sum(counts),
        "average_last": avg[-1],
        "ceiling": teto,
        "year": year,
        "month": month,
        "month_name": MONTHS_FULL[month - 1],
    }


# ───────────────────────────── Fluxo de caixa ─────────────────────────────


def _k_label(cents: int, short: bool, unit: str) -> str:
    """Balance under a column in thousands: «+R$ 3,4 mil»; short (narrow columns): «+3,4», the caption says the unit."""
    sgn = "−" if cents < 0 else "+"
    if short:
        return sgn + pct1(abs(cents) / 100000)
    return (
        compact(cents, fraction=1, sign="always")
        if _fm is not None
        else sgn + pct1(abs(cents) / 100000) + " " + unit
    )


def fluxo(
    income: Sequence[int],
    spend: Sequence[int],
    months: Sequence[str],
    fulls: Sequence[str],
    *,
    first: int = 0,
    w: int = 540,
    h: int = 220,
    compact_layout: bool = False,
    full_scale: int = 1_000_000,
    unit: str = "mil",
    uid: str = "f",
    year: int | None = None,
) -> dict[str, Any]:
    """Income up, spending down, net saving under each month. `full_scale` = the income (cents) that fills the upper half (R$ 10 mil in the canvas).
    Columns before `first` are outlines only (no history yet)."""
    n = len(months)
    W, PH = int(w), int(h)
    TOP, BOT = 34, 46
    H = TOP + PH + BOT
    HUP = round(PH * 0.56)
    HDN = PH - HUP
    gap = 8
    colw = (W - (n - 1) * gap) / n
    scale = HUP / full_scale  # px per cent
    y0 = TOP + HUP
    lab_w = max(len(_k_label(income[i] - spend[i], False, unit)) for i in range(n)) * 5.7 + 10
    short = bool(compact_layout) or (W / n < lab_w)
    sel_default = n - 1
    cols = []
    for i in range(n):
        real = i >= first
        x0 = i * (colw + gap)
        bx, bw = x0 + 0.22 * colw, 0.56 * colw
        net = income[i] - spend[i]
        col: dict[str, Any] = {
            "i": i,
            "m": months[i],
            "full": fulls[i],
            "real": real,
            "ghost": not real,
            "income": income[i],
            "spend": spend[i],
            "net": net,
            "rate": (net / income[i] * 100) if income[i] else 0.0,
            "x": to_fixed(x0 + colw / 2, 2),
            "xc": _pc(x0 + colw / 2, W, 2),
            "delay": 60 + (i - first) * 55 if real else 0,
            "net_label": _k_label(net, short, unit) if real else "",
            "tone": "pos" if net >= 0 else "neg",
        }
        if real:
            hu, hd = income[i] * scale, spend[i] * scale
            col["hu"], col["hd"] = to_fixed(hu, 3), to_fixed(hd, 3)
            col["up"] = bar_path(bx, y0 - 1 - hu, bw, hu, 9, 2)
            col["dn"] = bar_path(bx, y0 + 1, bw, hd, 2, 9)
        else:
            col["up"] = _open_bar_path(bx, y0 - 1 - 0.36 * PH, bw, 0.36 * PH, 9, up=True)
            col["dn"] = _open_bar_path(bx, y0 + 1, bw, 0.26 * PH, 9, up=False)
        cols.append(col)
    return {
        "kind": "fluxo",
        "uid": uid,
        "n": n,
        "w": W,
        "h": H,
        "plot_h": PH,
        "top": TOP,
        "bot": BOT,
        "hup": HUP,
        "hdn": HDN,
        "gap": gap,
        "first": first,
        "colw": to_fixed(colw, 3),
        "step": to_fixed(colw + gap, 3),
        "band_r": 12,
        "ratio": f"{W} / {H}",
        "compact": bool(compact_layout),
        "sparse": first > 0,
        "short": short,
        "gut": 16 if short else 40,
        "y_label": "5" if short else "5 " + unit,
        "y0": _pc(y0, H, 3),
        "y5u": _pc(y0 - 500_000 * scale, H, 3),
        "y5d": _pc(y0 + 500_000 * scale, H, 3),
        "y0_u": to_fixed(y0, 2),
        "y5u_u": to_fixed(y0 - 500_000 * scale, 2),
        "y5d_u": to_fixed(y0 + 500_000 * scale, 2),
        "plot_top": _pc(TOP, H, 3),
        "plot_pct": _pc(PH, H, 3),
        "bot_pct": _pc(BOT, H, 3),
        "label_y": _pc(TOP + PH + 9, H, 3),
        "cols": cols,
        "sel": sel_default,
        "caption_unit": unit,
        "year": year,
        "fulls": list(fulls),
        "months": list(months),
    }


# ───────────────────────────── Rosca de categorias ─────────────────────────────


def donut(
    categories: Sequence[Mapping[str, Any]],
    *,
    size: int = 232,
    top_n: int = 5,
    layout: str = "row",
    other_id: str = "demais",
    uid: str = "d",
    sparse: bool = False,
) -> dict[str, Any]:
    """Top `top_n` categories plus «Demais categorias» (Pareto). Each category: {id, name, group, n (entries), cents, color (token name, e.g. «forest»)}.
    `sparse` draws two categories and no remainder."""
    cats = sorted((dict(c) for c in categories), key=lambda c: -int(c["cents"]))
    top_n = 2 if sparse else top_n
    top = cats[:top_n]
    rest = [] if sparse else cats[top_n:]
    groups = [
        {
            "id": c["id"],
            "name": c["name"],
            "group": c.get("group", ""),
            "n": int(c["n"]),
            "cents": int(c["cents"]),
            "color": c["color"],
            "is_rest": False,
        }
        for c in top
    ]
    if rest:
        groups.append(
            {
                "id": other_id,
                "name": "Demais categorias",
                "group": "",
                "n": sum(int(c["n"]) for c in rest),
                "cents": sum(int(c["cents"]) for c in rest),
                "color": "mist",
                "is_rest": True,
                "count": len(rest),
            }
        )
    total = sum(g["cents"] for g in groups)
    top_sum = sum(int(c["cents"]) for c in top)
    geo = ring([g["cents"] / total for g in groups] if total else [], size=size)

    def fmt_geo(e):
        return {
            "sw": to_fixed(e["sw"], 0),
            "dash": to_fixed(e["dash"]),
            "rest": to_fixed(e["rest"]),
            "offset": to_fixed(e["offset"]),
        }

    for k, (g, sg) in enumerate(zip(groups, geo["segments"], strict=False)):
        g.update(
            {
                "frac": sg["frac"],
                "pct": pct1(g["cents"] / total * 100),
                "geo": fmt_geo(sg["rest"]),
                "geo_hot": fmt_geo(sg["hot"]),
                "delay": 60 + k * 80,
            }
        )
    items = [
        {
            "id": c["id"],
            "name": c["name"],
            "color": c["color"],
            "cents": int(c["cents"]),
            "pct": pct1(int(c["cents"]) / total * 100),
        }
        for c in rest
    ]
    return {
        "kind": "donut",
        "uid": uid,
        "size": size,
        "layout": layout,
        "sparse": sparse,
        "c": to_fixed(geo["c"], 2),
        "r": to_fixed(geo["r"], 2),
        "circ": to_fixed(geo["circ"], 2),
        "track": int(geo["stroke"]),
        "inner": int(size - 2 * (geo["hot_stroke"] + 14)),
        "odo": round(size * 0.14),
        "sk_in": to_fixed((geo["r"] - geo["stroke"] / 2) / geo["c"] * 100, 2),
        "sk_out": to_fixed((geo["r"] + geo["stroke"] / 2) / geo["c"] * 100, 2),
        "groups": groups,
        "rest_items": items,
        "total": total,
        "top_sum": top_sum,
        "top_pct": pct1(top_sum / total * 100) if total else "0,0",
        "pair_pct": pct1((cats[0]["cents"] + cats[1]["cents"]) / total * 100)
        if len(cats) > 1 and total
        else "0,0",
        "categories": len(cats),
        "shown": len(top),
        "entries": sum(int(c["n"]) for c in cats),
    }


# ───────────────────────────── Saldo diário ─────────────────────────────


def saldo_diario(
    balances: Sequence[int],
    today: int,
    *,
    start: _dt.date,
    events: Sequence[Mapping[str, Any]] = (),
    w: int = 720,
    h: int = 236,
    mini: bool = False,
    compact_layout: bool = False,
    uid: str = "s",
    max_ticks: int = 3,
) -> dict[str, Any]:
    """Balance at the end of each day as steps (it only changes when there is an entry: no invented curve between two days); the past in ink,
    the projection of the closed invoices dashed. `balances[today]` is today; `events`: {i, c (cents), t (text), proj (bool)} of THIS account."""
    N = len(balances)
    T = int(today)
    v = [int(b) for b in balances]
    W, PH = int(w), int(h)
    TOP, BOT = (6, 6) if mini else (34, 30)
    PADL = PADR = 4 if mini else 8
    H = TOP + PH + BOT
    mn, mx = min(v), max(v)
    span = max(mx - mn, abs(mx) * 0.02, 100)
    lo, hi = mn - span * 0.10, mx + span * 0.12
    ticks = ticks_window(lo, hi, max_ticks)
    y_of = lambda c: TOP + (1 - (c - lo) / (hi - lo)) * PH  # noqa: E731
    x_of = lambda i: PADL + i / (N - 1) * (W - PADL - PADR)  # noqa: E731
    xs, ys = [x_of(i) for i in range(N)], [y_of(c) for c in v]
    hist, proj = hv_step_path(xs, ys, 0, T), hv_step_path(xs, ys, T, N - 1)
    area = hist + f"V{TOP + PH}H{to_fixed(xs[0], 1)}Z"
    zero = f"M{PADL} {to_fixed(y_of(0), 1)}H{W - PADR}" if lo < 0 < hi else ""
    ev_first: dict[int, Mapping[str, Any]] = {}
    for e in events:
        ev_first.setdefault(
            int(e["i"]), e
        )  # the canvas shows the FIRST entry of the day in the tooltip
    points = []
    for i in range(N):
        d = start + _dt.timedelta(days=i)
        wd = (d.weekday() + 1) % 7
        base = v[T] if i > T else v[0]
        points.append(
            {
                "i": i,
                "x": _pc(xs[i], W, 3),
                "y": _pc(ys[i], H, 3),
                "cents": v[i],
                "proj": i > T,
                "change": v[i] - base,
                "pct": ((v[i] - base) / abs(base) * 100) if base else 0.0,
                "dd": f"{d.day:02d}",
                "mm": f"{d.month:02d}",
                "mon": MONTHS[d.month - 1],
                "wd": WEEKDAYS[wd],
                "year": d.year,
                "event": dict(ev_first[i]) if i in ev_first else None,
            }
        )
    proj_ev = [e for e in events if e.get("proj")]
    hoje_x = x_of(T)
    chips = []
    if not (compact_layout or mini):
        for e in proj_ev:
            i = int(e["i"])
            d = start + _dt.timedelta(days=i)
            text = f"{d.day:02d}/{d.month:02d} · {str(e['t']).replace('Fatura ', '').replace('BB Ourocard', 'BB')}"
            money = _fm.format_currency(int(e["c"]), sign="always") if _fm is not None else ""
            width = (
                24 + len(f"{text} {money}") * 6.1
            )  # the canvas estimates the chip's width from its text, amount included
            cx = x_of(i)
            left = min(max(cx - width / 2, hoje_x + 10), W - PADR - width)
            chips.append(
                {
                    "i": i,
                    "left": _pc(left, W),
                    "top": _pc(ys[i] + 12, H),
                    "text": text,
                    "cents": int(e["c"]),
                    "left_u": to_fixed(left, 2),
                    "top_u": to_fixed(ys[i] + 12, 2),
                }
            )
    x_ticks = []
    for k, i in enumerate((0, 15, T, N - 1)):
        d = start + _dt.timedelta(days=i)
        x_ticks.append(
            {
                "i": i,
                "left": _pc(xs[i], W),
                "anchor": "start" if k == 0 else "end" if k == 3 else "middle",
                "today": i == T,
                "label": "hoje" if i == T else f"{d.day:02d} {MONTHS[d.month - 1]}",
            }
        )
    return {
        "kind": "saldo",
        "uid": uid,
        "mini": mini,
        "compact": bool(compact_layout),
        "w": W,
        "h": H,
        "plot_h": PH,
        "top": TOP,
        "bot": BOT,
        "n": N,
        "today": T,
        "ratio": f"{W} / {H}",
        "gid": f"cx-saldo-{uid}",
        "paths": {
            "hist": hist,
            "proj": proj,
            "area": area,
            "zero": zero,
            "grid": "".join(f"M{PADL} {to_fixed(y_of(c), 1)}H{W - PADR}" for c in ticks),
        },
        "y_ticks": [{"cents": c, "top": _pc(y_of(c), H)} for c in ticks],
        "x_ticks": x_ticks,
        "guide": {"top": _pc(TOP, H), "bot": _pc(BOT, H)},
        "today_x": _pc(hoje_x, W, 3),
        "today_xu": to_fixed(hoje_x, 2),
        "today_dot": {"x": _pc(hoje_x, W, 3), "y": _pc(ys[T], H, 3)},
        "dots": [{"x": _pc(xs[int(e["i"])], W), "y": _pc(ys[int(e["i"])], H)} for e in proj_ev],
        "chips": chips,
        "points": points,
        "proj_events": [dict(e) for e in proj_ev],
        "start": start,
        "ghost": f"M{PADL} {to_fixed(TOP + PH * 0.72, 1)}H{W - PADR}",
        "first_cents": v[0],
        "today_cents": v[T],
        "last_cents": v[-1],
    }


def frame(
    kind: str,
    *,
    w: int | None = None,
    h: int | None = None,
    compact_layout: bool = False,
    mini: bool = False,
    size: int = 232,
    layout: str = "row",
    uid: str = "x",
    n: int = 9,
    short: bool | None = None,
    month_name: str = "",
    unit: str = "mil",
) -> dict[str, Any]:
    """Dimensions only, for the loading, error and empty states (no data yet). Pass the same `w`/`h`/`compact_layout`/`size`/`layout` as the ready chart and the
    box is the same, so nothing moves when data arrives (CLS 0). Fluxo: `n` columns; `short` = narrow columns (default: the rule `fluxo()` applies to a typical
    «+R$ 3,4 mil» label); `month_name` feeds the hidden legend of Ritmo."""
    if kind == "ritmo":
        w, h = w or 540, h or 200
        top, bot = 34, 30
    elif kind == "fluxo":
        w, h = w or 540, h or 220
        top, bot = 34, 46
    elif kind == "saldo":
        w, h = w or (220 if mini else 720), h or (40 if mini else 236)
        top, bot = (6, 6) if mini else (34, 30)
    elif kind == "donut":
        return {
            "kind": "donut",
            "uid": uid,
            "size": size,
            "layout": layout,
            "sparse": False,
            "groups": [],
            "total": 0,
        }
    else:
        raise ValueError(f"unknown chart kind: {kind!r}")
    H = top + h + bot
    out = {
        "kind": kind,
        "uid": uid,
        "w": w,
        "h": H,
        "plot_h": h,
        "top": top,
        "bot": bot,
        "ratio": f"{w} / {H}",
        "compact": bool(compact_layout),
        "mini": mini,
        "gut": 16 if compact_layout else 40,
        "points": [],
        "cols": [],
        "sparse": False,
        "short": False,
        "month_name": month_name,
    }
    if kind == "fluxo":
        out["short"] = bool(compact_layout) or (
            w / max(1, n) < _FLUXO_LABEL_W if short is None else bool(short)
        )
        out["gut"] = 16 if out["short"] else 40
        out["caption_unit"] = unit
    if (
        kind == "saldo"
    ):  # the «sem saldo» state draws two reference lines and the dotted ghost of a balance
        pad = 4 if mini else 8
        out["paths"] = {
            "grid": f"M{pad} {to_fixed(top + h * 0.28, 1)}H{w - pad}M{pad} {to_fixed(top + h * 0.5, 1)}H{w - pad}"
        }
        out["ghost"] = f"M{pad} {to_fixed(top + h * 0.72, 1)}H{w - pad}"
    return out


# ───────────────────────────── Jinja ─────────────────────────────


def install(env: Any) -> None:
    """Filters for snippets/charts-extra.html and snippets/screens.html: `cx_odo` (the rolling number), `cx_parts` (money pieces), `cx_compact` (axis labels), `cx_pct` (one decimal), `cx_cls` (whole-percent class: pct-37 / at-63)."""
    env.filters["cx_odo"] = lambda v, **kw: odo(v, **kw)
    env.filters["cx_parts"] = lambda v, **kw: money_parts(v, **kw)
    env.filters["cx_compact"] = lambda v, **kw: compact(v, **kw)
    env.filters["cx_pct"] = lambda v, **kw: pct1(v, **kw)
    env.filters["cx_cls"] = lambda v, prefix="pct", whole=100, **kw: pct_cls(v, prefix, whole, **kw)
    env.globals["cx_seal_roses"] = (
        seal_roses  # snippets/screens.html seal(): the two guilloche lines (computed once per call; ~12 KB of path data)
    )
