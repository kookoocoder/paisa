from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from .broker import SimulatedBroker
from .config import BrokerConfig
from .strategies import Strategy


@dataclass(frozen=True)
class BacktestResult:
    summary: dict[str, float | int | str]
    equity_curve: pd.DataFrame
    fills: pd.DataFrame


def _target_quantity(equity: float, price: float, target_position: float, cfg: BrokerConfig) -> int:
    allocation = equity * cfg.max_position_pct * target_position
    return max(0, int(allocation // price))


def run_symbol_backtest(
    candles: pd.DataFrame,
    strategy: Strategy,
    broker_cfg: BrokerConfig | None = None,
) -> BacktestResult:
    cfg = broker_cfg or BrokerConfig()
    broker = SimulatedBroker(cfg)
    signaled = strategy.signals(candles)
    symbol = str(signaled["symbol"].iloc[0])

    equity_rows: list[dict[str, float | str | pd.Timestamp]] = []
    last_price = float(signaled["close"].iloc[0])
    previous_target = 0.0

    for idx, row in signaled.iterrows():
        timestamp = pd.Timestamp(row["timestamp"])
        price = float(row["open"] if pd.notna(row["open"]) else row["close"])
        close = float(row["close"])
        target_position = float(row.get("target_position", 0.0))
        target_position = max(0.0, min(1.0, target_position))
        equity = broker.mark_to_market({symbol: last_price})
        desired_qty = _target_quantity(equity, price, target_position, cfg)
        current_qty = broker.position(symbol)

        if target_position != previous_target:
            if target_position > previous_target:
                delta = max(0, desired_qty - current_qty)
                broker.submit_market_order(timestamp, symbol, "BUY", delta, price, "signal_entry")
            elif target_position < previous_target:
                qty_to_sell = current_qty if target_position == 0 else max(0, current_qty - desired_qty)
                broker.submit_market_order(timestamp, symbol, "SELL", qty_to_sell, price, "signal_exit")
        previous_target = target_position

        last_price = close
        equity_rows.append(
            {
                "timestamp": timestamp,
                "symbol": symbol,
                "cash": broker.cash,
                "position": broker.position(symbol),
                "close": close,
                "equity": broker.mark_to_market({symbol: close}),
                "target_position": target_position,
            }
        )

    if broker.position(symbol) > 0:
        last = signaled.iloc[-1]
        broker.submit_market_order(
            pd.Timestamp(last["timestamp"]),
            symbol,
            "SELL",
            broker.position(symbol),
            float(last["close"]),
            "final_flatten",
        )
        equity_rows.append(
            {
                "timestamp": pd.Timestamp(last["timestamp"]),
                "symbol": symbol,
                "cash": broker.cash,
                "position": broker.position(symbol),
                "close": float(last["close"]),
                "equity": broker.mark_to_market({symbol: float(last["close"])}),
                "target_position": 0.0,
            }
        )

    equity_curve = pd.DataFrame(equity_rows)
    fills = pd.DataFrame([asdict(fill) for fill in broker.fills])
    summary = summarize(symbol, strategy.name, cfg.initial_cash, equity_curve, fills)
    return BacktestResult(summary=summary, equity_curve=equity_curve, fills=fills)


def summarize(
    symbol: str,
    strategy_name: str,
    initial_cash: float,
    equity_curve: pd.DataFrame,
    fills: pd.DataFrame,
) -> dict[str, float | int | str]:
    final_equity = float(equity_curve["equity"].iloc[-1])
    returns = equity_curve["equity"].pct_change().fillna(0)
    total_return_pct = (final_equity / initial_cash - 1) * 100
    max_equity = equity_curve["equity"].cummax()
    drawdown = equity_curve["equity"] / max_equity - 1
    max_drawdown_pct = float(drawdown.min() * 100)
    sharpe = 0.0
    if returns.std(ddof=0) > 0:
        sharpe = float((returns.mean() / returns.std(ddof=0)) * (252 ** 0.5))
    total_costs = float(fills["costs"].sum()) if not fills.empty else 0.0
    return {
        "symbol": symbol,
        "strategy": strategy_name,
        "initial_cash": round(initial_cash, 2),
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(float(total_return_pct), 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "sharpe_like_daily": round(sharpe, 4),
        "trades": int(len(fills)),
        "total_costs": round(total_costs, 2),
    }
