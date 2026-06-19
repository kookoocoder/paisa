from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
REPORTS_DIR = PROJECT_ROOT / "reports"


DEFAULT_SYMBOLS = [
    "RELIANCE.NS",
    "TCS.NS",
    "INFY.NS",
    "HDFCBANK.NS",
    "ICICIBANK.NS",
    "SBIN.NS",
    "LT.NS",
    "AXISBANK.NS",
    "ITC.NS",
    "BHARTIARTL.NS",
]


@dataclass(frozen=True)
class CostConfig:
    brokerage_bps: float = 0.0
    exchange_txn_bps: float = 0.307
    sebi_bps: float = 0.01
    stamp_buy_bps: float = 0.3
    stt_sell_bps: float = 2.5
    gst_rate: float = 0.18


@dataclass(frozen=True)
class BrokerConfig:
    initial_cash: float = 100_000.0
    spread_bps: float = 3.0
    slippage_bps: float = 2.0
    max_position_pct: float = 0.20
    costs: CostConfig = CostConfig()


def ensure_dirs() -> None:
    for path in (RAW_DIR, PROCESSED_DIR, REPORTS_DIR):
        path.mkdir(parents=True, exist_ok=True)
