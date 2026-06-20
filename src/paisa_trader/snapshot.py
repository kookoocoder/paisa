from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    timestamp: datetime
    bar_index: int
    open: float
    high: float
    low: float
    close: float
    volume: int
    rsi_14: float | None
    macd_line: float | None
    macd_signal: float | None
    macd_hist: float | None
    bb_upper: float | None
    bb_mid: float | None
    bb_lower: float | None
    bb_pct: float | None
    sma_20: float | None
    sma_50: float | None
    atr_14: float | None
    volume_ratio: float | None
    volume_score: float
    bid: float
    ask: float
    spread_pct: float
    synthetic_depth: dict[str, float | int]
    next_move_score: float
    next_move_label: str
    confidence: float
    signal_components: dict[str, Any]
    market_regime: str
    factor_scores: dict[str, float]
    active_strategy: str
    strategy_signal: str
    intelligence_gate: bool
    equity: float
    cash: float
    open_positions: list[dict[str, Any]] = field(default_factory=list)
    recent_fills: list[dict[str, Any]] = field(default_factory=list)
    unrealised_pnl: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    depth_levels: list[dict[str, Any]] = field(default_factory=list)

    def to_ai_prompt(self) -> str:
        return "\n".join(
            [
                f"=== MARKET SNAPSHOT - {self.symbol} @ {self.timestamp.isoformat()} ===",
                "",
                "PRICE ACTION",
                f"  Close: INR {self.close:.2f} | Open: INR {self.open:.2f} | High: INR {self.high:.2f} | Low: INR {self.low:.2f}",
                f"  ATR(14): {_fmt(self.atr_14)}",
                "",
                "MOMENTUM INDICATORS",
                f"  RSI(14): {_fmt(self.rsi_14)}",
                f"  MACD line: {_fmt(self.macd_line)} | Signal: {_fmt(self.macd_signal)} | Hist: {_fmt(self.macd_hist)}",
                "",
                "BOLLINGER BANDS",
                f"  Upper: {_fmt(self.bb_upper)} | Mid: {_fmt(self.bb_mid)} | Lower: {_fmt(self.bb_lower)}",
                f"  Price position in band: {_fmt_pct(self.bb_pct)}",
                "",
                "MOVING AVERAGES",
                f"  SMA(20): {_fmt(self.sma_20)} | SMA(50): {_fmt(self.sma_50)}",
                "",
                "VOLUME",
                f"  Current: {self.volume:,} | Ratio vs 20-bar avg: {_fmt(self.volume_ratio)}x",
                f"  Volume score: {self.volume_score:.2f}/1.0",
                "",
                "DEPTH (SYNTHETIC)",
                f"  Bid: {self.bid:.2f} | Ask: {self.ask:.2f} | Spread: {self.spread_pct:.3f}%",
                f"  Bid qty: {self.synthetic_depth.get('bid_qty', 0)} | Ask qty: {self.synthetic_depth.get('ask_qty', 0)} | Imbalance: {self.synthetic_depth.get('imbalance', 0):+.2f}",
                "",
                "COMPOSITE INTELLIGENCE",
                f"  Next-move score: {self.next_move_score:+.3f} ({self.next_move_label})",
                f"  Confidence: {_fmt_pct(self.confidence)}",
                f"  Regime: {self.market_regime}",
                f"  Factor scores: {self.factor_scores}",
                f"  Intelligence gate: {self.intelligence_gate}",
                f"  Signal breakdown: {self.signal_components}",
                "",
                "ACTIVE STRATEGY",
                f"  Strategy: {self.active_strategy} | Signal: {self.strategy_signal}",
                "",
                "PORTFOLIO STATE",
                f"  Equity: INR {self.equity:,.2f} | Cash: INR {self.cash:,.2f}",
                f"  Open positions: {self.open_positions}",
                f"  Unrealised P&L: INR {self.unrealised_pnl:+,.2f}",
                f"  Win rate: {_fmt_pct(self.win_rate)} ({self.total_trades} trades)",
                "",
                "=== END SNAPSHOT ===",
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0%}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value
