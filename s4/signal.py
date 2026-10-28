"""Strategy #4 entry signal on the last closed 1-minute GC footprint bar.

1. Big delta: 1m delta >= 2x the average |delta| of the 30 bars before, bar closes in that direction,
   rolling 5m and 15m delta on the same side >= 1.5x their averages.
2. Not absorbed: the bar's move per unit of delta >= 0.30x its normal (median of 60 bars).
3. Risk r: from the close to $0.30 beyond the furthest stacked imbalance of the bar (or its low/high),
   at least $1.50, no trade above $7.50. Stop = 2r.
4. 1H trend: close above the forming 1H EMA50 for buys, below for sells.
5. Signal bar volume in the top 15% of the 300 bars before.
6. At a level: key level, POC pair or stacked-imbalance zone (single volume-cluster rows don't count).
7. Close outside the previous CME day's value area (no breakouts inside yesterday's balance).
"""

from dataclasses import dataclass

from . import levels as L
from .config import Config


@dataclass
class Signal:
    t: int              # signal bar open time (UTC); entry at the next bar's open
    side: int           # +1 buy, -1 sell
    risk: float         # r in $; stop at 2r
    close: float
    why: str


def delta_sums(bars: list, minutes: int) -> list:
    out = {}
    for b in bars:
        k = b["t"] // (60 * minutes)
        out[k] = out.get(k, 0) + b["delta"]
    return [out[k] for k in sorted(out)]


def absorption(hist: list) -> float:
    norm = sorted(abs(x["c"] - x["o"]) / abs(x["delta"]) for x in hist[-61:-1] if abs(x["delta"]) >= 20)
    b = hist[-1]
    if not norm or not b["delta"] or not norm[len(norm) // 2]:
        return 1.0
    return abs(b["c"] - b["o"]) / abs(b["delta"]) / norm[len(norm) // 2]


def trend_side(bars: list, period: int = 50) -> int:
    """+1 / -1: last close above / below the 1H EMA (previous hours' closes plus the forming hour)."""
    hc = {}
    for b in bars:
        hc[b["t"] // 3600] = b["c"]
    hours = sorted(hc)
    if len(hours) < 2:
        return 0
    k, e = 2 / (period + 1), None
    for h in hours[:-1]:
        e = hc[h] if e is None else e + k * (hc[h] - e)
    c = bars[-1]["c"]
    ema = e + k * (c - e)
    return 1 if c > ema else -1 if c < ema else 0


def evaluate(bars: list, cfg: Config) -> tuple[Signal | None, str]:
    """bars: closed 1m footprint bars, oldest first, several days if available. Returns (signal, reason)."""
    hist = bars[-1500:]
    if len(hist) < 15 * 11 + 1:
        return None, "warming up"
    b = hist[-1]
    avg1 = sum(abs(x["delta"]) for x in hist[-31:-1]) / 30 or 1
    for d in (1, -1):
        if d * b["delta"] < cfg.delta_mult_1m * avg1 or d * (b["c"] - b["o"]) <= 0:
            continue
        why = [f"1m delta {b['delta']:+.0f} ({b['delta'] / avg1:+.1f}x)"]
        for m in (5, 15):
            past = delta_sums(hist[:-m], m)[-31:-1]
            avg = sum(abs(x) for x in past) / len(past) or 1
            roll = sum(x["delta"] for x in hist[-m:])
            if d * roll < cfg.delta_mult_htf * avg:
                return None, f"{m}m delta too small"
            why.append(f"{m}m delta {roll:+.0f} ({roll / avg:+.1f}x)")
        ab = absorption(hist)
        why.append(f"move/delta {ab:.2f}x")
        lv = [p for s, p in b["stacked"] if s == ("buy" if d > 0 else "sell")]
        risk = d * (b["c"] - (min(lv) if d > 0 else max(lv))) + cfg.buffer if lv else 0
        if risk < cfg.min_risk:
            risk = d * (b["c"] - (b["l"] if d > 0 else b["h"])) + cfg.buffer
        if risk > cfg.max_risk:
            return None, f"risk {risk:.2f} too large"
        if ab < cfg.absorption:
            return None, f"absorbed (move/delta {ab:.2f}x)"
        if trend_side(bars, cfg.trend_ema) != d:
            return None, "against the 1H trend"
        if not L.volume_ok(bars, cfg.volume_pct):
            return None, "signal bar volume not in the top 15%"
        lvl = L.at_level(bars[-7200:], d, cfg.row, cfg.drop_single_clusters)
        if not lvl:
            return None, "not at a level"
        why.append(f"{lvl[2]} level {lvl[0]:.2f}-{lvl[1]:.2f}")
        if cfg.skip_inside_value:
            va = L.prior_value(bars[-7200:], cfg.row)
            if va and va[0] <= b["c"] <= va[2]:
                return None, f"inside yesterday's value {va[0]:.1f}-{va[2]:.1f}"
        return Signal(b["t"], d, round(max(risk, cfg.min_risk), 2), b["c"], " + ".join(why)), "signal"
    return None, "no big delta"
