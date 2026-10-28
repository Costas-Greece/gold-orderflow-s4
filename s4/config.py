"""All strategy and connection settings in one place. Override any field from config.toml."""

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass
class Config:
    # --- IBKR connection -------------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 7497                    # TWS paper 7497, TWS live 7496, Gateway paper 4002, Gateway live 4001
    client_id: int = 41
    contract_month: str = ""            # e.g. "202612"; empty = most active gold month (front with highest OI)
    trade: bool = False                 # False = signals only (logs what it would do); True = send orders

    # --- footprint -------------------------------------------------------------------------
    row: float = 0.30                   # footprint row size in $ (3 GC ticks)
    imbalance_ratio: float = 3.0        # diagonal imbalance ratio (300%)
    stack_rows: int = 3                 # consecutive imbalanced rows = a stacked imbalance
    record_path: str = "data/GC_1m.jsonl"

    # --- signal (big 1m delta confirmed by 5m and 15m delta) -------------------------------
    delta_mult_1m: float = 2.0          # 1m delta >= 2x avg |delta| of the 30 bars before
    delta_mult_htf: float = 1.5         # rolling 5m / 15m delta >= 1.5x avg of the 30 periods before
    absorption: float = 0.30            # skip when the bar moved < 0.30x its normal move per delta
    min_risk: float = 1.50              # risk r in $: SL distance is 2r
    max_risk: float = 7.50
    buffer: float = 0.30
    trend_ema: int = 50                 # 1H EMA on GC: buys above, sells below
    volume_pct: float = 0.85            # signal bar volume >= 85th percentile of the 300 bars before

    # --- strategy #4 filters -----------------------------------------------------------------
    drop_single_clusters: bool = True   # a level must be a key level, a POC pair or a stacked-imbalance zone
    skip_inside_value: bool = True      # no trades while the close is inside the previous CME day's 70% value area

    # --- sessions and risk ----------------------------------------------------------------
    sessions: tuple = ((0, 7), (13, 19))   # UTC hours with new entries: Asia and New York
    losses_per_session: int = 2         # no new entries after this many losing trades in the session
    risk_usd: float = 90.0              # $ lost at the stop (incl. costs)
    cost_per_contract: float = 2.50     # round-trip commission + fees + 1 tick slippage per MGC, $
    max_contracts: int = 20

    # --- exits (multiples of r) --------------------------------------------------------------
    sl_r: float = 2.0
    tp1_r: float = 3.0
    tp1_close: float = 0.80             # share of the position closed at TP1
    be_r: float = 2.0                   # stop to entry once price reaches this
    tp2_r: float = 4.0


def load(path: str | None) -> Config:
    cfg = Config()
    if path and Path(path).exists():
        names = {f.name for f in fields(Config)}
        for k, v in tomllib.loads(Path(path).read_text()).items():
            if k not in names:
                raise SystemExit(f"unknown setting in {path}: {k}")
            setattr(cfg, k, tuple(tuple(x) for x in v) if k == "sessions" else v)
    return cfg
