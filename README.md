# Gold order-flow strategy #4 for Interactive Brokers

An intraday gold futures strategy that trades **big aggressive order flow at strong levels, outside yesterday's value area**.
It reads the **GC** (COMEX gold) footprint tick by tick from IBKR and trades **MGC** (micro gold, $1 per tick) so the risk
per trade can be sized precisely.

> ⚠️ Education and testing only. Futures trading can lose more than you deposit. The test results below cover
> **one week and 9 trades**. That is a clue, not proof. Run it with `trade = false` and on a paper account first.

## The rules

On every closed 1-minute GC bar:

1. **Big delta**: 1m delta ≥ 2× the average |delta| of the 30 bars before, and the bar closes in the same direction.
   Rolling 5m and 15m delta on the same side ≥ 1.5× their averages.
2. **Not absorbed**: the bar's price move per unit of delta ≥ 0.30× its normal (median of the last 60 bars).
3. **Risk r**: from the close to $0.30 beyond the furthest stacked imbalance of the bar (or its low/high).
   At least $1.50, and no trade above $7.50. The stop sits at 2r.
4. **Trend**: buys only above the 1H EMA50, sells only below it.
5. **Volume**: the signal bar's volume is in the top 15% of the last 300 bars.
6. **Level** (filter #2): the bar tested a key level or a strong volume zone:
   - key levels: the previous day's high, low and close; the day before's high and low; today's high and low; and the
     overnight high and low during New York hours
   - strong volume zones: POC pairs, or stacked-imbalance zones on 30m footprint bars

   Single "volume cluster" rows don't count; in a range they appear every few cents.
7. **Outside value** (filter #4): no trade while the close is inside the previous CME day's 70% value area.
   Breakouts inside yesterday's balance tend to fail.

**Trade management**: market entry on the next bar, stop at 2r, TP1 at 3r closes 80%, the stop moves to entry at 2r,
TP2 at 4r takes the rest. One position at a time.

**Limits**:
- new entries only in Asia (00–07 UTC) and New York (13–19 UTC)
- after **2 losing trades in a session**, no more entries in that session

## Test results so far

The test replays the strategy on a recorded GC footprint, 21–28 Oct 2026, all sessions, $30 risk per trade:

| Version | Trades | Win % | Profit factor | Net |
|---|---|---|---|---|
| Base rules (1–5, any level) | 20 | 65% | 3.75 | +$431 |
| **#4 (this repo)** | **9** | **78%** | **5.37** | **+$270** |

Break-even trades count as losses in Win %. #4 takes fewer, cleaner trades: higher PF, lower total profit.

## Setup

1. Install **TWS** or **IB Gateway**, log in (paper account first), and enable the API: *Configure → API → Settings →
   Enable ActiveX and Socket Clients*. Note the port.
2. Market data: you need real-time **CME / COMEX** data on the account (for example the *CME Real-Time* non-professional
   bundle). Tick-by-tick data doesn't work with delayed data.
3. Python 3.11+:
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   cp config.example.toml config.toml      # set the port, risk_usd, trade = false
   python -m s4.live --config config.toml
   ```

**Warm-up**: at start the strategy loads 5 days of plain 1m bars from IBKR (for the trend and the key levels). Every
footprint bar it builds is saved to `data/GC_1m.jsonl`. It needs **~10 hours of its own footprint** before the first
signal, and **about 2 days** before the volume zones and the value-area filter are complete. Leave it running, and
restarting keeps the recording. Everything is logged to `data/GC_1m.log`.

When you are happy with the signals, set `trade = true` (on paper first). Size is
`risk_usd ÷ (2r × $10 + costs per contract)` in MGC, rounded down. If a single MGC would risk more than `risk_usd`,
the trade is skipped.

## Replay your own recording

```bash
python -m s4.replay data/GC_1m.jsonl                 # every trade + summary
python -m s4.replay data/GC_1m.jsonl --from 2026-11-02
```

## Files

| File | What it does |
|---|---|
| `s4/footprint.py` | buy/sell classification from bid/ask, 1m footprint bars, diagonal stacked imbalances |
| `s4/levels.py` | key levels, 30m volume zones, value area |
| `s4/signal.py` | the entry rules above |
| `s4/replay.py` | no-look-ahead replay with the exit rules, per-session loss stop |
| `s4/live.py` | IBKR connection, live bars, MGC orders and management |
| `s4/config.py` | every setting with its default |

## Known limits

- **Aggressor side**: IBKR has no exchange aggressor flag, so each trade is classified from the last bid/ask (tick
  rule inside the spread). Footprints differ a little from platforms that use the exchange flag.
- **Restarts**: open orders are GTC and stay at IBKR. If the script restarts while a position is open, it refuses to
  start until the position is closed or finished. It doesn't adopt it.
- **No news filter**: it trades through CPI, NFP and FOMC.
- **Roll**: the active month is chosen at start, and the next active month once the current one is within 30 days of
  expiry. Restart it after a roll.
