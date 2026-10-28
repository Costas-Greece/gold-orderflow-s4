from s4.config import Config
from s4.footprint import BarBuilder, Classifier, stacked
from s4.levels import touched, value_area
from s4.replay import trade


def test_classifier_quote_and_tick_rule():
    c = Classifier()
    c.quote(100.0, 100.1)
    assert c.side(100.1) == 1          # at the ask: aggressive buy
    assert c.side(100.0) == -1         # at the bid: aggressive sell
    c.quote(100.0, 100.2)
    assert c.side(100.1) == 1          # inside the spread, uptick from 100.0


def test_stacked_needs_three_consecutive_diagonal_rows():
    rows = [[99.9, 0, 10], [100.2, 40, 2], [100.5, 40, 2], [100.8, 40, 2], [101.1, 1, 1]]   # rows on the $0.30 grid
    assert [p for s, p in stacked(rows, 0.3, 3.0, 3) if s == "buy"] == [100.2, 100.5, 100.8]
    assert stacked(rows[:3], 0.3, 3.0, 3) == []


def test_bar_builder_rolls_minutes():
    bb = BarBuilder(0.3, 3.0, 3)
    assert bb.add(60.0, 100.0, 2, 1) is None
    assert bb.add(90.0, 100.3, 1, -1) is None
    bar = bb.add(121.0, 100.6, 1, 1)
    assert bar["t"] == 60 and bar["o"] == 100.0 and bar["h"] == 100.3 and bar["c"] == 100.3
    assert (bar["buy"], bar["sell"], bar["delta"], bar["cvd"]) == (2, 1, 1, 1)


def test_value_area_contains_poc():
    bars = [{"levels": [[round(99.9 + 0.3 * k, 2), 10 + (50 if k == 10 else 0), 10] for k in range(25)]}]
    val, poc, vah = value_area(bars)
    assert val <= poc <= vah and abs(poc - 102.9) < 1e-9


def test_touched_buy_and_sell():
    b = {"c": 100.5, "l": 99.8, "h": 100.6}
    assert touched(b, 1, 100.0, 100.0)
    assert not touched(b, -1, 100.0, 100.0)


def test_trade_tp1_then_be():
    cfg = Config()
    bars = [{"t": 0, "o": 100, "h": 100, "l": 100, "c": 100},
            {"t": 60, "o": 100, "h": 103.1, "l": 99.9, "c": 103},   # r = 1: TP1 at 103 hit, stop to 100
            {"t": 120, "o": 103, "h": 103, "l": 99.5, "c": 99.6}]   # back to entry: rest closed at BE
    k, usd, how = trade(bars, 1, 1, 1.0, cfg)
    assert how == "TP1+BE" and k == 2
    assert abs(usd - (0.8 * 3 - 0.15) / 2 * cfg.risk_usd) < 1e-9
