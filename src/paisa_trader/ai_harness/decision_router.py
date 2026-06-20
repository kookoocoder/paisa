from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from ..broker import Fill, SimulatedBroker
from ..snapshot import MarketSnapshot
from .decision_parser import TradeDecision


@dataclass(frozen=True)
class DecisionRouterConfig:
    decision_min_confidence: float = 0.65
    position_size_pct: float = 0.01


@dataclass(frozen=True)
class RouteResult:
    accepted: bool
    reason: str
    fill: dict[str, Any] | None = None


class DecisionRouter:
    def __init__(self, config: DecisionRouterConfig | None = None):
        self.config = config or DecisionRouterConfig()

    def route(self, decision: TradeDecision, snapshot: MarketSnapshot, broker: SimulatedBroker) -> RouteResult:
        if decision.action == "BUY":
            blocked = self._buy_block_reason(decision, snapshot, broker)
            if blocked:
                return RouteResult(False, blocked)
            quantity = self._size_position(snapshot, broker)
            fill = broker.submit_market_order(
                pd.Timestamp(snapshot.timestamp),
                snapshot.symbol,
                "BUY",
                quantity,
                snapshot.close,
                "AI paper entry",
            )
            return _fill_result(fill, "AI BUY routed to simulated broker")

        if decision.action in {"SELL", "CLOSE"}:
            quantity = broker.position(snapshot.symbol)
            if quantity <= 0:
                return RouteResult(False, "BLOCKED: no open position to close")
            fill = broker.submit_market_order(
                pd.Timestamp(snapshot.timestamp),
                snapshot.symbol,
                "SELL",
                quantity,
                snapshot.close,
                "AI paper exit",
            )
            return _fill_result(fill, "AI exit routed to simulated broker")

        return RouteResult(False, "HOLD: no broker action")

    def _buy_block_reason(self, decision: TradeDecision, snapshot: MarketSnapshot, broker: SimulatedBroker) -> str | None:
        if not snapshot.intelligence_gate:
            return "BLOCKED: intelligence gate is false"
        if broker.position(snapshot.symbol) > 0:
            return "BLOCKED: already in position"
        if decision.confidence < self.config.decision_min_confidence:
            return f"BLOCKED: confidence {decision.confidence:.0%} below threshold"
        return None

    def _size_position(self, snapshot: MarketSnapshot, broker: SimulatedBroker) -> int:
        equity = broker.mark_to_market({snapshot.symbol: snapshot.close})
        risk_budget = max(0.0, equity * self.config.position_size_pct)
        risk_per_share = max(snapshot.atr_14 or snapshot.close * 0.01, snapshot.close * 0.002, 0.01)
        quantity = int(risk_budget // risk_per_share)
        max_affordable = int(broker.cash // max(snapshot.ask or snapshot.close, 0.01))
        return max(0, min(quantity, max_affordable))


def _fill_result(fill: Fill | None, reason: str) -> RouteResult:
    if fill is None:
        return RouteResult(False, "BLOCKED: broker did not produce a fill")
    return RouteResult(True, reason, asdict(fill))
