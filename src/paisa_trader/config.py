from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
CALIBRATION_PATH = DATA_DIR / "calibration.json"
REPORTS_DIR = PROJECT_ROOT / "reports"


DEFAULT_SYMBOLS = [
    "RELIANCE",
    "TCS",
    "INFY",
    "HDFCBANK",
    "ICICIBANK",
    "SBIN",
    "LT",
    "AXISBANK",
    "ITC",
    "BHARTIARTL",
]


@dataclass(frozen=True)
class DataConfig:
    default_source: str = "upstox"
    default_interval: str = "5minute"
    default_period: str = "5d"
    token_env_var: str = "UPSTOX_ANALYTICS_TOKEN"


@dataclass(frozen=True)
class MLConfig:
    enabled: bool = True
    model_dir: Path = PROJECT_ROOT / "models"
    min_confidence: float = 0.55
    use_arima_tiebreaker: bool = True
    arima_order: tuple[int, int, int] = (2, 1, 2)


@dataclass(frozen=True)
class SentimentConfig:
    enabled: bool = True
    model: str = "ProsusAI/finbert"
    fallback_on_error: bool = True
    batch_size: int = 1


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
    calibration_enabled: bool = True
    calibration_save_path: Path = CALIBRATION_PATH
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
    calibration_save_path = Path(payload.get("calibration", {}).get("save_path", CALIBRATION_PATH))
    if not calibration_save_path.is_absolute():
        calibration_save_path = PROJECT_ROOT / calibration_save_path
    return AIHarnessConfig(
        model_provider=str(section.get("model_provider", "mock")),
        model_name=str(section.get("model_name", section.get("model_provider", "mock"))),
        api_key_env=str(section.get("api_key_env", "")),
        temperature=float(section.get("temperature", 0.1)),
        max_tokens=int(section.get("max_tokens", 900)),
        local_url=str(section.get("local_url", "http://127.0.0.1:1234")),
        decision_min_confidence=float(section.get("decision_min_confidence", 0.65)),
        position_size_pct=float(section.get("position_size_pct", 0.01)),
        calibration_enabled=bool(payload.get("calibration", {}).get("enabled", True)),
        calibration_save_path=calibration_save_path,
        symbols=list(section.get("symbols", DEFAULT_SYMBOLS[:3])),
        bar_interval_sec=float(section.get("bar_interval_sec", 5.0)),
    )


def _read_toml(path: Path | None = None) -> dict:
    config_path = path or PROJECT_ROOT / "paisa.toml"
    if not config_path.exists():
        return {}
    return tomllib.loads(config_path.read_text(encoding="utf-8"))


def load_data_config(path: Path | None = None) -> DataConfig:
    payload = _read_toml(path)
    section = payload.get("data", {})
    return DataConfig(
        default_source=str(section.get("default_source", "upstox")),
        default_interval=str(section.get("default_interval", "5minute")),
        default_period=str(section.get("default_period", "5d")),
        token_env_var=str(section.get("token_env_var", "UPSTOX_ANALYTICS_TOKEN")),
    )


def load_ml_config(path: Path | None = None) -> MLConfig:
    payload = _read_toml(path)
    section = payload.get("ml", {})
    model_dir = Path(section.get("model_dir", "models/"))
    if not model_dir.is_absolute():
        model_dir = PROJECT_ROOT / model_dir
    order = tuple(int(item) for item in section.get("arima_order", [2, 1, 2]))
    return MLConfig(
        enabled=bool(section.get("enabled", True)),
        model_dir=model_dir,
        min_confidence=float(section.get("min_confidence", 0.55)),
        use_arima_tiebreaker=bool(section.get("use_arima_tiebreaker", True)),
        arima_order=order if len(order) == 3 else (2, 1, 2),
    )


def load_sentiment_config(path: Path | None = None) -> SentimentConfig:
    payload = _read_toml(path)
    section = payload.get("sentiment", {})
    return SentimentConfig(
        enabled=bool(section.get("enabled", True)),
        model=str(section.get("model", "ProsusAI/finbert")),
        fallback_on_error=bool(section.get("fallback_on_error", True)),
        batch_size=int(section.get("batch_size", 1)),
    )


def ensure_dirs() -> None:
    for path in (RAW_DIR, PROCESSED_DIR, REPORTS_DIR, CALIBRATION_PATH.parent):
        path.mkdir(parents=True, exist_ok=True)
