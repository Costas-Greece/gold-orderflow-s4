"""Builds 1-minute footprint bars from IBKR tick-by-tick trades.

A bar is a dict:
  t       bar open time, epoch seconds UTC
  o h l c prices
  buy     aggressive buy volume (traded at the ask)
  sell    aggressive sell volume (traded at the bid)
  delta   buy - sell;  cvd: running delta
  levels  [[row price, buy, sell], ...] in rows of `row` dollars
  stacked [["buy"|"sell", row price], ...] rows that belong to a stacked imbalance
"""

import math


def rnd(p: float, row: float) -> float:
    return round(round(p / row) * row, 2)


class Classifier:
    """Aggressor side of a trade from the last bid/ask (quote rule), tick rule when inside the spread."""

    def __init__(self):
        self.bid = self.ask = math.nan
        self.last_price, self.last_side = math.nan, 1

    def quote(self, bid: float, ask: float):
        self.bid, self.ask = bid, ask

    def side(self, price: float) -> int:
        if price >= self.ask:
            s = 1
        elif price <= self.bid:
            s = -1
        elif price != self.last_price and not math.isnan(self.last_price):
            s = 1 if price > self.last_price else -1
        else:
            s = self.last_side
        self.last_price, self.last_side = price, s
        return s


def stacked(levels: list, row: float, ratio: float, n: int) -> list:
    """Diagonal imbalances (buy: ask at p vs bid at p - row; sell: bid at p vs ask at p + row),
    returned for rows in runs of at least `n` consecutive rows on the same side."""
    lv = {rnd(p, row): (b, s) for p, b, s in levels}
    out = []
    for side in ("buy", "sell"):
        run = []
        for p in sorted(lv):
            b, s = lv[p]
            if side == "buy":
                imb = b >= ratio * max(lv.get(rnd(p - row, row), (0, 0))[1], 1)
            else:
                imb = s >= ratio * max(lv.get(rnd(p + row, row), (0, 0))[0], 1)
            if imb and run and abs(p - run[-1] - row) < 1e-6:
                run.append(p)
                continue
            if len(run) >= n:
                out += [[side, x] for x in run]
            run = [p] if imb else []
        if len(run) >= n:
            out += [[side, x] for x in run]
    return out


class BarBuilder:
    def __init__(self, row: float, ratio: float, stack_rows: int, cvd: float = 0.0):
        self.row, self.ratio, self.n = row, ratio, stack_rows
        self.cvd, self.cur = cvd, None

    def add(self, ts: float, price: float, size: float, side: int) -> dict | None:
        """Add one trade; returns the finished bar when a new minute starts."""
        t = int(ts // 60 * 60)
        done = None
        if self.cur and t != self.cur["t"]:
            done = self.close()
        if not self.cur:
            self.cur = {"t": t, "o": price, "h": price, "l": price, "c": price, "buy": 0, "sell": 0, "rows": {}}
        b = self.cur
        b["h"], b["l"], b["c"] = max(b["h"], price), min(b["l"], price), price
        r = b["rows"].setdefault(rnd(price, self.row), [0, 0])
        if side > 0:
            b["buy"] += size; r[0] += size
        else:
            b["sell"] += size; r[1] += size
        return done

    def close(self) -> dict | None:
        b, self.cur = self.cur, None
        if not b:
            return None
        levels = [[p, v[0], v[1]] for p, v in sorted(b.pop("rows").items())]
        b["delta"] = b["buy"] - b["sell"]
        self.cvd += b["delta"]
        b["cvd"], b["levels"] = self.cvd, levels
        b["stacked"] = stacked(levels, self.row, self.ratio, self.n)
        return b
