from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import BrokerConfig, CostConfig


@dataclass(frozen=True)
class Order:
    timestamp: pd.Timestamp
    symbol: str
    side: str
    quantity: int
    reason: str


@dataclass(frozen=True)
class Fill:
    timestamp: pd.Timestamp
    symbol: str
    side: str
    quantity: int
    price: float
    gross_value: float
    costs: float
    net_cash_delta: float
    reason: str


def estimate_costs(gross_value: float, side: str, cfg: CostConfig) -> float:
    brokerage = gross_value * cfg.brokerage_bps / 10_000
    exchange = gross_value * cfg.exchange_txn_bps / 10_000
    sebi = gross_value * cfg.sebi_bps / 10_000
    stamp = gross_value * cfg.stamp_buy_bps / 10_000 if side == "BUY" else 0.0
    stt = gross_value * cfg.stt_sell_bps / 10_000 if side == "SELL" else 0.0
    gst = (brokerage + exchange + sebi) * cfg.gst_rate
    return brokerage + exchange + sebi + stamp + stt + gst


class SimulatedBroker:
    def __init__(self, cfg: BrokerConfig | None = None):
        self.cfg = cfg or BrokerConfig()
        self.cash = float(self.cfg.initial_cash)
        self.positions: dict[str, int] = {}
        self.fills: list[Fill] = []

    def position(self, symbol: str) -> int:
        return self.positions.get(symbol, 0)

    def _fill_price(self, side: str, price: float) -> float:
        half_spread = self.cfg.spread_bps / 2 / 10_000
        slip = self.cfg.slippage_bps / 10_000
        if side == "BUY":
            return price * (1 + half_spread + slip)
        return price * (1 - half_spread - slip)

    def submit_market_order(
        self,
        timestamp: pd.Timestamp,
        symbol: str,
        side: str,
        quantity: int,
        reference_price: float,
        reason: str,
    ) -> Fill | None:
        if quantity <= 0:
            return None
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"Unsupported side: {side}")
        if side == "SELL":
            quantity = min(quantity, self.position(symbol))
            if quantity <= 0:
                return None

        price = self._fill_price(side, reference_price)
        gross = price * quantity
        costs = estimate_costs(gross, side, self.cfg.costs)

        if side == "BUY":
            affordable_qty = int(self.cash // (price * (1 + 0.01)))
            quantity = min(quantity, affordable_qty)
            if quantity <= 0:
                return None
            gross = price * quantity
            costs = estimate_costs(gross, side, self.cfg.costs)
            cash_delta = -(gross + costs)
            self.positions[symbol] = self.position(symbol) + quantity
        else:
            cash_delta = gross - costs
            self.positions[symbol] = self.position(symbol) - quantity

        self.cash += cash_delta
        fill = Fill(
            timestamp=timestamp,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=float(price),
            gross_value=float(gross),
            costs=float(costs),
            net_cash_delta=float(cash_delta),
            reason=reason,
        )
        self.fills.append(fill)
        return fill

    def mark_to_market(self, prices: dict[str, float]) -> float:
        value = self.cash
        for symbol, qty in self.positions.items():
            value += qty * prices.get(symbol, 0.0)
        return float(value)
