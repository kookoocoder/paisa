from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RoundTripTrade:
    """One closed long round trip matched from BUY and SELL fills."""

    symbol: str
    entry_time: str
    exit_time: str
    quantity: int
    entry_price: float
    exit_price: float
    gross_pnl_inr: float
    costs_inr: float
    net_pnl_inr: float
    return_pct: float
    holding_bars: int | None = None


def trade_pnl_stats(
    fills: list[dict[str, Any]],
    *,
    initial_cash: float = 0.0,
    latest_prices: dict[str, float] | None = None,
    positions: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Compute round-trip P&L statistics from broker fill records.

    Args:
        fills: Fill dicts with timestamp, symbol, side, quantity, price,
            gross_value, costs, and net_cash_delta.
        initial_cash: Starting cash for portfolio return context.
        latest_prices: Optional mark prices for open-position unrealised P&L.
        positions: Optional open share counts keyed by symbol.

    Returns:
        Summary dict with overall, per-symbol, and round-trip trade details.

    Example:
        ``trade_pnl_stats(fills, initial_cash=100_000)["overall"]["win_rate"]``
        returns the closed-trade win rate.
    """
    ordered = sorted(fills, key=lambda item: str(item.get("timestamp", "")))
    round_trips = _pair_round_trips(ordered)
    closed = round_trips
    winners = [trade for trade in closed if trade.net_pnl_inr > 0]
    losers = [trade for trade in closed if trade.net_pnl_inr < 0]
    breakeven = [trade for trade in closed if trade.net_pnl_inr == 0]

    gross_pnl = sum(trade.gross_pnl_inr for trade in closed)
    net_pnl = sum(trade.net_pnl_inr for trade in closed)
    win_sum = sum(trade.net_pnl_inr for trade in winners)
    loss_sum = sum(trade.net_pnl_inr for trade in losers)
    fill_costs = sum(float(fill.get("costs", 0.0) or 0.0) for fill in ordered)

    latest_prices = latest_prices or {}
    positions = positions or {}
    open_positions = _open_position_stats(ordered, closed, positions, latest_prices)

    overall = {
        "closed_trades": len(closed),
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "breakeven_trades": len(breakeven),
        "win_rate": round(len(winners) / len(closed), 4) if closed else 0.0,
        "gross_pnl_inr": round(gross_pnl, 2),
        "total_costs_inr": round(fill_costs, 2),
        "net_pnl_inr": round(net_pnl, 2),
        "avg_win_inr": round(win_sum / len(winners), 2) if winners else 0.0,
        "avg_loss_inr": round(loss_sum / len(losers), 2) if losers else 0.0,
        "largest_win_inr": round(max((trade.net_pnl_inr for trade in winners), default=0.0), 2),
        "largest_loss_inr": round(min((trade.net_pnl_inr for trade in losers), default=0.0), 2),
        "profit_factor": round(win_sum / abs(loss_sum), 4) if loss_sum < 0 else None,
        "expectancy_inr": round(net_pnl / len(closed), 2) if closed else 0.0,
        "total_fills": len(ordered),
        "buy_fills": sum(1 for fill in ordered if fill.get("side") == "BUY"),
        "sell_fills": sum(1 for fill in ordered if fill.get("side") == "SELL"),
    }

    by_symbol: dict[str, Any] = {}
    for symbol in sorted({trade.symbol for trade in closed} | set(positions)):
        symbol_trades = [trade for trade in closed if trade.symbol == symbol]
        symbol_winners = [trade for trade in symbol_trades if trade.net_pnl_inr > 0]
        symbol_net = sum(trade.net_pnl_inr for trade in symbol_trades)
        by_symbol[symbol] = {
            "closed_trades": len(symbol_trades),
            "win_rate": round(len(symbol_winners) / len(symbol_trades), 4) if symbol_trades else 0.0,
            "net_pnl_inr": round(symbol_net, 2),
            "gross_pnl_inr": round(sum(trade.gross_pnl_inr for trade in symbol_trades), 2),
            "costs_inr": round(sum(trade.costs_inr for trade in symbol_trades), 2),
        }
        if symbol in open_positions:
            by_symbol[symbol]["open"] = open_positions[symbol]

    portfolio = {
        "initial_cash_inr": round(float(initial_cash), 2),
        "realized_pnl_inr": round(net_pnl, 2),
        "unrealized_pnl_inr": round(open_positions.get("_total_unrealized_inr", 0.0), 2),
        "total_pnl_inr": round(net_pnl + open_positions.get("_total_unrealized_inr", 0.0), 2),
    }
    if initial_cash > 0:
        portfolio["realized_return_pct"] = round((net_pnl / initial_cash) * 100, 4)

    open_positions_clean = {
        key: value for key, value in open_positions.items() if not str(key).startswith("_")
    }
    round_trip_payload = [_round_trip_dict(trade) for trade in closed]

    return {
        "overall": overall,
        "by_symbol": by_symbol,
        "portfolio": portfolio,
        "capital": _build_capital_summary(ordered, open_positions_clean, net_pnl, open_positions.get("_total_unrealized_inr", 0.0)),
        "open_positions": open_positions_clean,
        "round_trips": round_trip_payload,
        "ledger": _build_trade_ledger(ordered, round_trip_payload),
    }


def _round_trip_dict(trade: RoundTripTrade) -> dict[str, Any]:
    payload = asdict(trade)
    payload["result"] = "WIN" if trade.net_pnl_inr > 0 else "LOSS" if trade.net_pnl_inr < 0 else "FLAT"
    return payload


def _build_capital_summary(
    fills: list[dict[str, Any]],
    open_positions: dict[str, Any],
    realized_pnl_inr: float,
    unrealized_pnl_inr: float,
) -> dict[str, float]:
    total_deployed = sum(
        abs(float(fill.get("net_cash_delta", 0.0) or 0.0))
        for fill in fills
        if str(fill.get("side", "")).upper() == "BUY"
    )
    currently_invested = sum(float(pos.get("cost_basis_inr", 0.0) or 0.0) for pos in open_positions.values())
    open_market_value = sum(float(pos.get("market_value_inr", 0.0) or 0.0) for pos in open_positions.values())
    return {
        "total_capital_deployed_inr": round(total_deployed, 2),
        "currently_invested_inr": round(currently_invested, 2),
        "open_market_value_inr": round(open_market_value, 2),
        "realized_pnl_inr": round(float(realized_pnl_inr), 2),
        "unrealized_pnl_inr": round(float(unrealized_pnl_inr), 2),
    }


def _build_trade_ledger(fills: list[dict[str, Any]], round_trips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    round_trip_index = {
        (trip["symbol"], _timestamp_key(trip["exit_time"]), int(trip["quantity"])): trip for trip in round_trips
    }
    ledger: list[dict[str, Any]] = []

    for fill in fills:
        symbol = str(fill.get("symbol", ""))
        side = str(fill.get("side", "")).upper()
        quantity = int(fill.get("quantity", 0) or 0)
        if quantity <= 0 or side not in {"BUY", "SELL"}:
            continue

        timestamp = str(fill.get("timestamp", ""))
        entry = {
            "timestamp": timestamp,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price_inr": round(float(fill.get("price", 0.0) or 0.0), 4),
            "costs_inr": round(float(fill.get("costs", 0.0) or 0.0), 2),
        }
        if side == "BUY":
            capital = round(abs(float(fill.get("net_cash_delta", 0.0) or 0.0)), 2)
            entry.update(
                {
                    "capital_inr": capital,
                    "outcome": "BUY",
                    "label": "Capital deployed",
                }
            )
        else:
            proceeds = round(float(fill.get("net_cash_delta", 0.0) or 0.0), 2)
            trip = round_trip_index.get((symbol, _timestamp_key(timestamp), quantity))
            if trip is not None:
                pnl = float(trip.get("net_pnl_inr", 0.0) or 0.0)
                entry.update(
                    {
                        "proceeds_inr": proceeds,
                        "pnl_inr": round(pnl, 2),
                        "return_pct": float(trip.get("return_pct", 0.0) or 0.0),
                        "outcome": "PROFIT" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT",
                        "label": "Profit" if pnl > 0 else "Loss" if pnl < 0 else "Flat",
                    }
                )
            else:
                entry.update(
                    {
                        "proceeds_inr": proceeds,
                        "pnl_inr": None,
                        "return_pct": None,
                        "outcome": "SELL",
                        "label": "Exit",
                    }
                )
        ledger.append(entry)

    ledger.reverse()
    return ledger


def _timestamp_key(value: Any) -> str:
    text = str(value or "")
    return text[:19]


def _pair_round_trips(fills: list[dict[str, Any]]) -> list[RoundTripTrade]:
    lots: dict[str, list[dict[str, Any]]] = {}
    round_trips: list[RoundTripTrade] = []

    for fill in fills:
        symbol = str(fill.get("symbol", ""))
        side = str(fill.get("side", "")).upper()
        quantity = int(fill.get("quantity", 0) or 0)
        if quantity <= 0 or side not in {"BUY", "SELL"}:
            continue

        if side == "BUY":
            lots.setdefault(symbol, []).append(
                {
                    "timestamp": str(fill.get("timestamp", "")),
                    "remaining_qty": quantity,
                    "quantity": quantity,
                    "price": float(fill.get("price", 0.0) or 0.0),
                    "gross_value": float(fill.get("gross_value", 0.0) or 0.0),
                    "costs": float(fill.get("costs", 0.0) or 0.0),
                    "net_cash_delta": float(fill.get("net_cash_delta", 0.0) or 0.0),
                }
            )
            continue

        remaining = quantity
        symbol_lots = lots.setdefault(symbol, [])
        while remaining > 0 and symbol_lots:
            lot = symbol_lots[0]
            matched = min(remaining, int(lot["remaining_qty"]))
            if matched <= 0:
                symbol_lots.pop(0)
                continue

            buy_fraction = matched / int(lot["quantity"])
            sell_fraction = matched / quantity
            buy_cash = float(lot["net_cash_delta"]) * buy_fraction
            sell_cash = float(fill.get("net_cash_delta", 0.0) or 0.0) * sell_fraction
            buy_gross = float(lot["gross_value"]) * buy_fraction
            sell_gross = float(fill.get("gross_value", 0.0) or 0.0) * sell_fraction
            buy_costs = float(lot["costs"]) * buy_fraction
            sell_costs = float(fill.get("costs", 0.0) or 0.0) * sell_fraction
            net_pnl = buy_cash + sell_cash
            gross_pnl = sell_gross - buy_gross
            entry_price = float(lot["price"])
            exit_price = float(fill.get("price", 0.0) or 0.0)
            entry_basis = abs(buy_cash)
            return_pct = (net_pnl / entry_basis * 100) if entry_basis else 0.0

            round_trips.append(
                RoundTripTrade(
                    symbol=symbol,
                    entry_time=str(lot["timestamp"]),
                    exit_time=str(fill.get("timestamp", "")),
                    quantity=matched,
                    entry_price=round(entry_price, 4),
                    exit_price=round(exit_price, 4),
                    gross_pnl_inr=round(gross_pnl, 2),
                    costs_inr=round(buy_costs + sell_costs, 2),
                    net_pnl_inr=round(net_pnl, 2),
                    return_pct=round(return_pct, 4),
                )
            )

            lot["remaining_qty"] = int(lot["remaining_qty"]) - matched
            remaining -= matched
            if int(lot["remaining_qty"]) <= 0:
                symbol_lots.pop(0)

    return round_trips


def _open_position_stats(
    fills: list[dict[str, Any]],
    closed: list[RoundTripTrade],
    positions: dict[str, int],
    latest_prices: dict[str, float],
) -> dict[str, Any]:
    if not positions:
        return {"_total_unrealized_inr": 0.0}

    lots: dict[str, list[dict[str, Any]]] = {}
    for fill in fills:
        symbol = str(fill.get("symbol", ""))
        side = str(fill.get("side", "")).upper()
        quantity = int(fill.get("quantity", 0) or 0)
        if quantity <= 0:
            continue
        if side == "BUY":
            lots.setdefault(symbol, []).append(
                {
                    "remaining_qty": quantity,
                    "quantity": quantity,
                    "price": float(fill.get("price", 0.0) or 0.0),
                    "net_cash_delta": float(fill.get("net_cash_delta", 0.0) or 0.0),
                    "costs": float(fill.get("costs", 0.0) or 0.0),
                }
            )
        elif side == "SELL":
            remaining = quantity
            symbol_lots = lots.setdefault(symbol, [])
            while remaining > 0 and symbol_lots:
                lot = symbol_lots[0]
                matched = min(remaining, int(lot["remaining_qty"]))
                lot["remaining_qty"] = int(lot["remaining_qty"]) - matched
                remaining -= matched
                if int(lot["remaining_qty"]) <= 0:
                    symbol_lots.pop(0)

    open_stats: dict[str, Any] = {"_total_unrealized_inr": 0.0}
    for symbol, qty in positions.items():
        if qty <= 0:
            continue
        mark = float(latest_prices.get(symbol, 0.0) or 0.0)
        remaining = int(qty)
        cost_basis = 0.0
        for lot in lots.get(symbol, []):
            if remaining <= 0:
                break
            matched = min(remaining, int(lot["remaining_qty"]))
            fraction = matched / int(lot["quantity"])
            cost_basis += abs(float(lot["net_cash_delta"]) * fraction)
            remaining -= matched
        market_value = qty * mark
        unrealized = market_value - cost_basis
        open_stats[symbol] = {
            "quantity": int(qty),
            "mark_price_inr": round(mark, 4),
            "cost_basis_inr": round(cost_basis, 2),
            "market_value_inr": round(market_value, 2),
            "unrealized_pnl_inr": round(unrealized, 2),
        }
        open_stats["_total_unrealized_inr"] += unrealized

    open_stats["_total_unrealized_inr"] = round(float(open_stats["_total_unrealized_inr"]), 2)
    return open_stats


def normalize_fill_record(record: dict[str, Any]) -> dict[str, Any]:
    """Convert a broker fill record into JSON-safe dict fields.

    Args:
        record: Raw fill mapping from broker or dataframe row.

    Returns:
        Normalized fill dict suitable for ``trade_pnl_stats``.

    Example:
        ``normalize_fill_record(asdict(fill))["side"]`` returns ``"BUY"``.
    """
    normalized = dict(record)
    timestamp = normalized.get("timestamp")
    if timestamp is not None and hasattr(timestamp, "isoformat"):
        normalized["timestamp"] = timestamp.isoformat()
    return normalized


def trade_pnl_from_backtest_results(
    results: list[Any],
    *,
    initial_cash_per_symbol: float,
) -> dict[str, Any]:
    """Build portfolio trade P&L from one or more backtest/shadow results.

    Args:
        results: Objects exposing ``summary``, ``fills``, and ``equity_curve``.
        initial_cash_per_symbol: Starting cash used for each symbol replay.

    Returns:
        Combined trade P&L stats across all provided symbol results.

    Example:
        ``trade_pnl_from_backtest_results(session.results, initial_cash_per_symbol=100_000)``
        aggregates round-trip stats for a shadow session.
    """
    fills: list[dict[str, Any]] = []
    latest_prices: dict[str, float] = {}
    positions: dict[str, int] = {}

    for result in results:
        symbol = str(result.summary["symbol"])
        if not result.fills.empty:
            for _, row in result.fills.iterrows():
                fills.append(normalize_fill_record(row.to_dict()))
        last = result.equity_curve.iloc[-1]
        latest_prices[symbol] = float(last["close"])
        position = int(last["position"])
        if position > 0:
            positions[symbol] = position

    return trade_pnl_stats(
        fills,
        initial_cash=initial_cash_per_symbol * max(len(results), 1),
        latest_prices=latest_prices,
        positions=positions,
    )
