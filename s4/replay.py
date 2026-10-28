"""Replays strategy #4 on a recorded 1m footprint file (JSON lines, one bar per line, format in footprint.py).

No look-ahead: each signal sees only the bars up to it. Entry at the next bar's open, stop 2r, TP1 3r
(close 80%), stop to entry at 2r, TP2 4r, one position at a time, the per-session loss stop, and a
cost of $0.15/oz. Results in $ at cfg.risk_usd per stop.

    python -m s4.replay data/GC_1m.jsonl [--config config.toml] [--from 2026-10-21]
"""

import argparse
import json
import time
from datetime import datetime, timezone

from .config import Config, load
from .signal import evaluate

COST_PER_OZ = 0.15


def session_of(t: int, cfg: Config):
    h = time.gmtime(t + 60).tm_hour
    return next((n for n, (a, b) in enumerate(cfg.sessions) if a <= h < b), None)


def trade(bars: list, j: int, d: int, r: float, cfg: Config) -> tuple[int, float, str]:
    """Exit bar index, result in $ (at cfg.risk_usd per stop) and outcome of a trade entered at bars[j] open."""
    n, e = len(bars), bars[j]["o"]
    tp1, tp2, be = e + d * cfg.tp1_r * r, e + d * cfg.tp2_r * r, e + d * cfg.be_r * r
    left, pnl, hit1, stop, k, how = 1.0, 0.0, False, e - d * cfg.sl_r * r, j, "open"
    while k < n:
        x = bars[k]
        if k > j and x["t"] - bars[k - 1]["t"] > 3 * 3600:           # data gap / weekend: out at the open
            pnl += left * d * (x["o"] - e); left, how = 0, "gap"; break
        adv, fav = (x["l"], x["h"]) if d > 0 else (x["h"], x["l"])
        if d * (adv - stop) <= 0:
            pnl += left * d * (stop - e); left = 0
            how = ("TP1+BE" if hit1 else "BE") if stop == e else ("TP1+SL" if hit1 else "SL"); break
        if not hit1 and d * (fav - tp1) >= 0:
            pnl += cfg.tp1_close * d * (tp1 - e); left -= cfg.tp1_close; hit1 = True
            if d * (stop - e) < 0: stop = e
        if d * (fav - be) >= 0 and d * (stop - e) < 0:
            stop = e
        if d * (fav - tp2) >= 0:
            pnl += left * d * (tp2 - e); left, how = 0, "TP2"; break
        k += 1
    if left > 0:
        pnl += left * d * (bars[-1]["c"] - e)
    return k, (pnl - COST_PER_OZ) / (cfg.sl_r * r) * cfg.risk_usd, how


def run(bars: list, cfg: Config, start: int = 0) -> list:
    res, busy, losses = [], -1, {}
    for i in range(200, len(bars) - 1):
        if bars[i]["t"] < start or i + 1 <= busy:
            continue
        s = session_of(bars[i]["t"], cfg)
        if s is None:
            continue
        key = ((bars[i]["t"] + 60) // 86400, s)
        if losses.get(key, 0) >= cfg.losses_per_session:
            continue
        sig, _ = evaluate(bars[max(0, i - 7200):i + 1], cfg)
        if not sig:
            continue
        busy, usd, how = trade(bars, i + 1, sig.side, sig.risk, cfg)
        if how != "open" and usd < 0:
            losses[key] = losses.get(key, 0) + 1
        res.append({"t": bars[i + 1]["t"], "side": "BUY" if sig.side > 0 else "SELL", "usd": usd, "how": how, "why": sig.why})
    return res


def summary(tr: list) -> str:
    if not tr:
        return "0 trades"
    w = sum(t["usd"] > 0.5 for t in tr)
    gp = sum(t["usd"] for t in tr if t["usd"] > 0)
    gl = -sum(t["usd"] for t in tr if t["usd"] < 0)
    eq = pk = dd = 0.0
    for t in tr:
        eq += t["usd"]; pk = max(pk, eq); dd = min(dd, eq - pk)
    pf = f"{gp / gl:.2f}" if gl else "inf"
    return f"{len(tr)} trades, win {100 * w / len(tr):.0f}% (BE counts as a loss), PF {pf}, net ${gp - gl:+.0f}, max DD ${dd:.0f}"


def load_bars(path: str) -> list:
    seen, out = set(), []
    with open(path) as f:
        for line in f:
            if line.strip():
                b = json.loads(line)
                if b["t"] not in seen:
                    seen.add(b["t"]); out.append(b)
    return sorted(out, key=lambda b: b["t"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--config")
    ap.add_argument("--from", dest="start", help="first signal date, YYYY-MM-DD (UTC)")
    a = ap.parse_args()
    cfg = load(a.config)
    start = int(datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc).timestamp()) if a.start else 0
    tr = run(load_bars(a.file), cfg, start)
    for t in tr:
        print(f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(t['t']))} UTC {t['side']:4} {t['usd']:+8.2f} {t['how']:7} {t['why']}")
    print(summary(tr))


if __name__ == "__main__":
    main()
