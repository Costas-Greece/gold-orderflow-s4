"""Runs strategy #4 live on Interactive Brokers: GC tick-by-tick data in, MGC orders out.

    python -m s4.live --config config.toml

Needs TWS or IB Gateway running with the API enabled, and a real-time CME/COMEX market data
subscription (tick-by-tick data for GC). With trade = false (the default) it only logs signals.

Flow: every GC trade is classified buy/sell from the last bid/ask, 1-minute footprint bars are built
and appended to the recording file, and on every closed bar the signal is evaluated. A signal opens a
market order on MGC sized so that the stop costs about risk_usd, then places a stop (2r), TP1 (3r, 80%)
and TP2 (4r, the rest). The stop moves to entry once price reaches 2r, and after TP1.
"""

import argparse
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ib_async import IB, Future, LimitOrder, MarketOrder, StopOrder

from .config import Config, load
from .footprint import BarBuilder, Classifier
from .replay import load_bars, session_of
from .signal import evaluate

log = logging.getLogger("s4")
ACTIVE_MONTHS = {2, 4, 6, 8, 10, 12}          # the liquid gold contract months
WARM_BARS = 600                               # footprint bars needed before trading (~10 h)


def pick_month(ib: IB, cfg: Config) -> str:
    if cfg.contract_month:
        return cfg.contract_month
    soon = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y%m%d")
    months = sorted({d.contract.lastTradeDateOrContractMonth for d in ib.reqContractDetails(Future("GC", exchange="COMEX"))})
    for m in months:
        if int(m[4:6]) in ACTIVE_MONTHS and m > soon:
            return m[:6]
    raise SystemExit("no active GC contract month found")


def contract(ib: IB, symbol: str, month: str) -> Future:
    c = Future(symbol, lastTradeDateOrContractMonth=month, exchange="COMEX", currency="USD")
    ib.qualifyContracts(c)
    return c


class Strategy:
    def __init__(self, ib: IB, cfg: Config):
        self.ib, self.cfg = ib, cfg
        month = pick_month(ib, cfg)
        self.gc, self.mgc = contract(ib, "GC", month), contract(ib, "MGC", month)
        log.info("data GC %s, orders MGC %s, trade=%s", self.gc.localSymbol, self.mgc.localSymbol, cfg.trade)
        self.record = Path(cfg.record_path)
        self.record.parent.mkdir(parents=True, exist_ok=True)
        self.bars = self.load_history()
        self.cls = Classifier()
        self.builder = BarBuilder(cfg.row, cfg.imbalance_ratio, cfg.stack_rows, self.bars[-1].get("cvd", 0) if self.bars else 0)
        self.pos, self.losses = None, {}

    # --- data ------------------------------------------------------------------------------
    def load_history(self) -> list:
        """Recorded footprint bars, plus 5 days of plain 1m bars from IBKR before them (for levels and the trend)."""
        rec = load_bars(str(self.record)) if self.record.exists() else []
        hist = self.ib.reqHistoricalData(self.gc, "", "5 D", "1 min", "TRADES", useRTH=False, formatDate=2)
        first = rec[0]["t"] if rec else math.inf
        plain = [{"t": int(h.date.timestamp()), "o": h.open, "h": h.high, "l": h.low, "c": h.close,
                  "buy": 0, "sell": 0, "delta": 0, "cvd": 0, "levels": [], "stacked": [], "fp": False}
                 for h in hist if h.date.timestamp() < first]
        log.info("history: %d plain 1m bars + %d recorded footprint bars", len(plain), len(rec))
        return plain + rec

    def start(self):
        self.ib.reqTickByTickData(self.gc, "BidAsk", 0, False).updateEvent += self.on_quotes
        self.ib.reqTickByTickData(self.gc, "AllLast", 0, False).updateEvent += self.on_trades

    def on_quotes(self, ticker):
        for q in ticker.tickByTicks:
            self.cls.quote(q.bidPrice, q.askPrice)

    def on_trades(self, ticker):
        for tr in ticker.tickByTicks:
            bar = self.builder.add(tr.time.timestamp(), tr.price, tr.size, self.cls.side(tr.price))
            if bar:
                self.on_bar(bar)
            if self.pos and self.pos.get("entry") is not None:
                self.manage(tr.price)

    def on_bar(self, bar: dict):
        bar["fp"] = True
        self.bars.append(bar)
        self.bars = self.bars[-12000:]
        with self.record.open("a") as f:
            f.write(json.dumps(bar) + "\n")
        fp = [b for b in self.bars[-WARM_BARS:] if b.get("fp", True)]
        if len(fp) < WARM_BARS:
            log.info("warming up: %d/%d footprint bars", len(fp), WARM_BARS)
            return
        s = session_of(bar["t"], self.cfg)
        if s is None:
            return
        sig, why = evaluate(self.bars[-7200:], self.cfg)
        if not sig:
            if why not in ("no big delta", "warming up"):
                log.info("%s skipped: %s", time.strftime("%H:%M", time.gmtime(bar["t"])), why)
            return
        key = ((bar["t"] + 60) // 86400, s)
        side = "BUY" if sig.side > 0 else "SELL"
        log.info("SIGNAL %s r=%.2f (%s)", side, sig.risk, sig.why)
        if self.losses.get(key, 0) >= self.cfg.losses_per_session:
            log.info("not taken: %d losing trades this session", self.losses[key])
        elif self.pos:
            log.info("not taken: a position is already open")
        else:
            self.enter(sig, key)

    # --- orders ----------------------------------------------------------------------------
    def enter(self, sig, key):
        cfg, d = self.cfg, sig.side
        per = cfg.sl_r * sig.risk * 10 + cfg.cost_per_contract       # MGC: $10 per $1 move
        qty = min(int(cfg.risk_usd // per), cfg.max_contracts)
        if qty < 1:
            log.info("not taken: one MGC would risk $%.0f > $%.0f", per, cfg.risk_usd)
            return
        q1 = min(qty, max(1, round(qty * cfg.tp1_close)))
        if not cfg.trade:
            log.info("[signals only] would %s %d MGC, stop %.1f below/above entry, TP1 %d @ %.1fr, TP2 %d @ %.1fr",
                     "BUY" if d > 0 else "SELL", qty, cfg.sl_r * sig.risk, q1, cfg.tp1_r, qty - q1, cfg.tp2_r)
            return
        ref = f"s4-{sig.t}"
        self.pos = {"d": d, "r": sig.risk, "qty": qty, "q1": q1, "key": key, "ref": ref, "entry": None, "be": False}
        t = self.ib.placeOrder(self.mgc, MarketOrder("BUY" if d > 0 else "SELL", qty, orderRef=ref, outsideRth=True))
        t.filledEvent += self.on_entry
        log.info("ENTRY %s %d MGC sent", "BUY" if d > 0 else "SELL", qty)

    def exit_order(self, cls, qty, price, **kw):
        p = self.pos
        o = cls("SELL" if p["d"] > 0 else "BUY", qty, round(price, 1), orderRef=p["ref"], tif="GTC", outsideRth=True, **kw)
        return self.ib.placeOrder(self.mgc, o)

    def on_entry(self, trade):
        p = self.pos
        e, d, r = trade.orderStatus.avgFillPrice, p["d"], p["r"]
        p["entry"] = e
        p["stop"] = self.exit_order(StopOrder, p["qty"], e - d * self.cfg.sl_r * r)
        p["tp1"] = self.exit_order(LimitOrder, p["q1"], e + d * self.cfg.tp1_r * r)
        p["tp2"] = self.exit_order(LimitOrder, p["qty"] - p["q1"], e + d * self.cfg.tp2_r * r) if p["qty"] > p["q1"] else None
        p["stop"].filledEvent += self.on_done
        p["tp1"].filledEvent += self.on_tp1
        if p["tp2"]:
            p["tp2"].filledEvent += self.on_done
        log.info("FILLED at %.1f: stop %.1f, TP1 %d @ %.1f, TP2 %d @ %.1f", e, e - d * self.cfg.sl_r * r,
                 p["q1"], e + d * self.cfg.tp1_r * r, p["qty"] - p["q1"], e + d * self.cfg.tp2_r * r)

    def move_stop(self, qty=None):
        p = self.pos
        o = p["stop"].order
        o.auxPrice = round(p["entry"], 1)
        if qty is not None:
            o.totalQuantity = qty
        self.ib.placeOrder(self.mgc, o)
        p["be"] = True

    def manage(self, price: float):
        p = self.pos
        if not p["be"] and p["d"] * (price - (p["entry"] + p["d"] * self.cfg.be_r * p["r"])) >= 0:
            self.move_stop()
            log.info("stop moved to entry %.1f", p["entry"])

    def on_tp1(self, trade):
        p = self.pos
        if p["tp2"]:
            self.move_stop(p["qty"] - p["q1"])
            log.info("TP1 filled, stop on the rest moved to entry")
        else:
            self.ib.cancelOrder(p["stop"].order)
            self.finish()

    def on_done(self, trade):
        p = self.pos
        for k in ("stop", "tp1", "tp2"):
            t = p.get(k)
            if t and t is not trade and not t.isDone():
                self.ib.cancelOrder(t.order)
        self.finish()

    def finish(self):
        p, self.pos = self.pos, None
        net = 0.0
        for f in self.ib.fills():
            if f.execution.orderRef == p["ref"]:
                sgn = 1 if f.execution.side == "SLD" else -1
                net += sgn * f.execution.price * f.execution.shares * 10
                net -= f.commissionReport.commission or 0
        if net < 0:
            self.losses[p["key"]] = self.losses.get(p["key"], 0) + 1
        log.info("TRADE CLOSED net $%.2f, losing trades this session: %d", net, self.losses.get(p["key"], 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    a = ap.parse_args()
    cfg = load(a.config)
    Path(cfg.record_path).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(Path(cfg.record_path).with_suffix(".log"))])
    ib = IB()
    ib.connect(cfg.host, cfg.port, clientId=cfg.client_id)
    if any(p.contract.symbol == "MGC" and p.position for p in ib.positions()):
        raise SystemExit("an MGC position is already open on this account: close it or let it finish first")
    s = Strategy(ib, cfg)
    s.start()
    ib.disconnectedEvent += lambda: log.error("disconnected from TWS / IB Gateway, restart the strategy")
    ib.run()


if __name__ == "__main__":
    main()
