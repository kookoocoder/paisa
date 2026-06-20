from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


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


@dataclass(frozen=True)
class AIHarnessConfig:
    model_provider: str = "mock"
    model_name: str = "mock"
    api_key_env: str = ""
    temperature: float = 0.1
    max_tokens: int = 900
    local_url: str = "http://127.0.0.1:1234"
    decision_min_confidence: float = 0.65
    position_size_pct: float = 0.01
    symbols: list[str] | None = None
    bar_interval_sec: float = 5.0

    @property
    def provider(self) -> str:
        return self.model_provider


def load_ai_harness_config(path: Path | None = None) -> AIHarnessConfig:
    config_path = path or PROJECT_ROOT / "paisa.toml"
    if not config_path.exists():
        return AIHarnessConfig(symbols=DEFAULT_SYMBOLS[:3])
    payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    section = payload.get("ai_harness", {})
    return AIHarnessConfig(
        model_provider=str(section.get("model_provider", "mock")),
        model_name=str(section.get("model_name", section.get("model_provider", "mock"))),
        api_key_env=str(section.get("api_key_env", "")),
        temperature=float(section.get("temperature", 0.1)),
        max_tokens=int(section.get("max_tokens", 900)),
        local_url=str(section.get("local_url", "http://127.0.0.1:1234")),
        decision_min_confidence=float(section.get("decision_min_confidence", 0.65)),
        position_size_pct=float(section.get("position_size_pct", 0.01)),
        symbols=list(section.get("symbols", DEFAULT_SYMBOLS[:3])),
        bar_interval_sec=float(section.get("bar_interval_sec", 5.0)),
    )


def ensure_dirs() -> None:
    for path in (RAW_DIR, PROCESSED_DIR, REPORTS_DIR):
        path.mkdir(parents=True, exist_ok=True)
