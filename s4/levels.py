"""Where the strategy is allowed to trade.

Key levels (Valtos): previous CME day high/low/close, the day before's high/low, today's high/low
(older than 15 min) and, in New York hours, the overnight high/low. CME day starts 22:00 UTC.
Volume zones (Trader Dale) from 30-minute footprint bars: POC pairs (two bars in a row with POCs
within one row), stacked-imbalance zones (3+ diagonal 300% imbalances of >= 10 lots), and volume
clusters (one row with >= 2.5x the bar's average row). A zone counts once a full 30m bar closed
completely above or below it. Strategy #4 ignores single volume-cluster rows.
"""

import time

from .footprint import rnd

sday = lambda t: (t + 2 * 3600) // 86400          # CME day starts 22:00 UTC


def volume_ok(bars: list, pct: float = 0.85) -> bool:
    """The last bar's volume >= the `pct` percentile of the 300 bars before it."""
    past = sorted(x["buy"] + x["sell"] for x in bars[-301:-1])
    if len(past) <= 50:
        return False
    b = bars[-1]
    return b["buy"] + b["sell"] >= past[int(pct * len(past))]


def key_levels(bars: list) -> list:
    i = len(bars) - 1
    t = bars[i]["t"]
    k = sday(t)
    days = {}
    for j, b in enumerate(bars):
        days.setdefault(sday(b["t"]), []).append(j)
    pk = sorted(x for x in days if x < k)
    out = []
    for n, d in enumerate(reversed(pk[-2:])):
        v = days[d]
        out += [max(bars[j]["h"] for j in v), min(bars[j]["l"] for j in v)]
        if n == 0:
            out.append(bars[v[-1]]["c"])
    today = [j for j in days.get(k, []) if j < i - 15]
    if today:
        out += [max(bars[j]["h"] for j in today), min(bars[j]["l"] for j in today)]
        hr = lambda j: time.gmtime(bars[j]["t"]).tm_hour
        on = [j for j in today if hr(j) < 13 or hr(j) >= 22]
        if on and 13 <= time.gmtime(t).tm_hour < 22:
            out += [max(bars[j]["h"] for j in on), min(bars[j]["l"] for j in on)]
    return [(x, x, "key") for x in out]


def volume_zones(bars: list, row: float = 0.30) -> list:
    """(lo, hi, kind) zones from 30m footprint bars, kind = poc / stacked / cluster."""
    i_last = len(bars) - 1
    m30 = {}
    for i, b in enumerate(bars):
        z = m30.setdefault(b["t"] // 1800, {"h": b["h"], "l": b["l"], "lv": {}, "last": i})
        z["h"], z["l"], z["last"] = max(z["h"], b["h"]), min(z["l"], b["l"]), i
        for p, bu, se in b.get("levels", []):
            p = rnd(p, row)
            a, s = z["lv"].get(p, (0, 0))
            z["lv"][p] = (a + bu, s + se)
    M = [m30[k] for k in sorted(m30)]
    for z in M:
        z["poc"] = max(z["lv"], key=lambda p: sum(z["lv"][p])) if z["lv"] else (z["h"] + z["l"]) / 2
    zones = []
    for n, z in enumerate(M):
        if n >= 1 and abs(z["poc"] - M[n - 1]["poc"]) <= row + 1e-9:
            zones.append((min(z["poc"], M[n - 1]["poc"]) - row / 2, max(z["poc"], M[n - 1]["poc"]) + row / 2, n, "poc"))
        ps = sorted(z["lv"])
        for side in ("buy", "sell"):
            run = []
            for p in ps + [None]:
                imb = False
                if p is not None:
                    a, s = z["lv"][p]
                    _, s_below = z["lv"].get(rnd(p - row, row), (0, 0))
                    a_above, _ = z["lv"].get(rnd(p + row, row), (0, 0))
                    imb = (a >= 3 * max(s_below, 1) and a >= 10) if side == "buy" else (s >= 3 * max(a_above, 1) and s >= 10)
                if imb:
                    run.append(p)
                else:
                    if len(run) >= 3:
                        zones.append((run[0] - row / 2, run[-1] + row / 2, n, "stacked"))
                    run = []
        tot = [sum(v) for v in z["lv"].values()]
        avg = sum(tot) / len(tot) if tot else 0
        zones += [(p - row / 2, p + row / 2, n, "cluster") for p, v in z["lv"].items() if sum(v) >= 2.5 * avg]
    ready = []
    for lo, hi, n, kind in zones:      # tradable once a full 30m bar closed completely above or below it
        for m in range(n + 1, len(M)):
            if M[m]["last"] + 1 > i_last:
                break
            if M[m]["l"] > hi or M[m]["h"] < lo:
                ready.append((lo, hi, kind))
                break
    return ready


def touched(b: dict, d: int, lo: float, hi: float) -> bool:
    """A buy needs a level at or below the close that the bar's low tested ($1 under to $0.30 over); sell = mirror."""
    if d > 0:
        return hi <= b["c"] + 0.3 and lo - 1.0 <= b["l"] <= hi + 0.3
    return lo >= b["c"] - 0.3 and lo - 0.3 <= b["h"] <= hi + 1.0


def at_level(bars: list, d: int, row: float = 0.30, drop_single_clusters: bool = True):
    """The (lo, hi, kind) level the last bar tested in direction d, else None."""
    b = bars[-1]
    for lo, hi, kind in key_levels(bars) + volume_zones(bars, row):
        if drop_single_clusters and kind == "cluster":
            continue
        if touched(b, d, lo, hi):
            return lo, hi, kind
    return None


def value_area(bars: list, row: float = 0.30, share: float = 0.70):
    """(VAL, POC, VAH) of a list of footprint bars; None when there is no footprint data."""
    pf = {}
    for b in bars:
        for p, bu, se in b.get("levels", []):
            pf[rnd(p, row)] = pf.get(rnd(p, row), 0) + bu + se
    if len(pf) <= 20:
        return None
    ps = sorted(pf)
    poc = max(ps, key=pf.get)
    i = j = ps.index(poc)
    tot, acc = sum(pf.values()), pf[poc]
    while acc < share * tot and (i > 0 or j < len(ps) - 1):
        up = pf[ps[j + 1]] if j < len(ps) - 1 else -1
        dn = pf[ps[i - 1]] if i > 0 else -1
        if up >= dn:
            j += 1; acc += up
        else:
            i -= 1; acc += dn
    return ps[i], poc, ps[j]


def prior_value(bars: list, row: float = 0.30):
    """Value area of the most recent previous CME day with footprint data (Monday uses Friday)."""
    k = sday(bars[-1]["t"])
    days = {}
    for b in bars:
        if sday(b["t"]) < k:
            days.setdefault(sday(b["t"]), []).append(b)
    for d in sorted(days, reverse=True)[:3]:
        va = value_area(days[d], row)
        if va:
            return va
    return None
