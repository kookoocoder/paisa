import json
from pathlib import Path

from paisa_trader.trade_stats import trade_pnl_from_backtest_results, trade_pnl_stats


def _sample_fills():
    return [
        {
            "timestamp": "2026-06-16T06:30:00",
            "symbol": "INFY",
            "side": "BUY",
            "quantity": 100,
            "price": 100.0,
            "gross_value": 10_000.0,
            "costs": 10.0,
            "net_cash_delta": -10_010.0,
        },
        {
            "timestamp": "2026-06-16T08:30:00",
            "symbol": "INFY",
            "side": "SELL",
            "quantity": 100,
            "price": 105.0,
            "gross_value": 10_500.0,
            "costs": 15.0,
            "net_cash_delta": 10_485.0,
        },
        {
            "timestamp": "2026-06-16T09:00:00",
            "symbol": "RELIANCE",
            "side": "BUY",
            "quantity": 50,
            "price": 200.0,
            "gross_value": 10_000.0,
            "costs": 10.0,
            "net_cash_delta": -10_010.0,
        },
        {
            "timestamp": "2026-06-16T12:20:00",
            "symbol": "RELIANCE",
            "side": "SELL",
            "quantity": 50,
            "price": 190.0,
            "gross_value": 9_500.0,
            "costs": 12.0,
            "net_cash_delta": 9_488.0,
        },
        {
            "timestamp": "2026-06-16T20:15:00",
            "symbol": "TCS",
            "side": "BUY",
            "quantity": 20,
            "price": 300.0,
            "gross_value": 6_000.0,
            "costs": 6.0,
            "net_cash_delta": -6_006.0,
        },
    ]


def test_trade_pnl_stats_pairs_round_trips():
    stats = trade_pnl_stats(_sample_fills(), initial_cash=100_000)

    assert stats["overall"]["closed_trades"] == 2
    assert stats["overall"]["winning_trades"] == 1
    assert stats["overall"]["losing_trades"] == 1
    assert stats["overall"]["win_rate"] == 0.5
    assert stats["overall"]["net_pnl_inr"] == -47.0
    assert stats["overall"]["total_fills"] == 5
    assert len(stats["round_trips"]) == 2
    assert stats["round_trips"][0]["result"] in {"WIN", "LOSS"}


def test_trade_pnl_stats_tracks_open_positions():
    stats = trade_pnl_stats(
        _sample_fills(),
        initial_cash=100_000,
        positions={"TCS": 20},
        latest_prices={"TCS": 310.0},
    )

    assert "TCS" in stats["open_positions"]
    assert stats["open_positions"]["TCS"]["quantity"] == 20
    assert stats["open_positions"]["TCS"]["unrealized_pnl_inr"] > 0
    assert stats["portfolio"]["realized_pnl_inr"] == -47.0
    assert stats["portfolio"]["unrealized_pnl_inr"] > 0


def test_trade_pnl_stats_builds_ledger_and_capital():
    stats = trade_pnl_stats(_sample_fills(), initial_cash=100_000)

    assert stats["capital"]["total_capital_deployed_inr"] > 0
    assert len(stats["ledger"]) == 5
    buy = next(item for item in stats["ledger"] if item["side"] == "BUY")
    sell = next(item for item in stats["ledger"] if item["side"] == "SELL" and item.get("pnl_inr") is not None)
    assert buy["capital_inr"] > 0
    assert sell["outcome"] in {"PROFIT", "LOSS", "FLAT"}


def test_trade_pnl_from_backtest_results():
    import pandas as pd
    from paisa_trader.backtest import run_symbol_backtest
    from paisa_trader.config import BrokerConfig
    from paisa_trader.strategies import MovingAverageCrossStrategy

    dates = pd.date_range("2024-01-01", periods=50, freq="D")
    prices = [100 + i for i in range(50)]
    candles = pd.DataFrame(
        {
            "timestamp": dates,
            "symbol": ["RELIANCE"] * 50,
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": prices,
            "volume": [1000] * 50,
        }
    )
    result = run_symbol_backtest(
        candles,
        MovingAverageCrossStrategy(fast=3, slow=7),
        BrokerConfig(initial_cash=100_000, spread_bps=0, slippage_bps=0),
    )
    stats = trade_pnl_from_backtest_results([result], initial_cash_per_symbol=100_000)

    assert stats["overall"]["total_fills"] == int(result.summary["trades"])
    assert "portfolio" in stats
    assert "round_trips" in stats


def test_trade_pnl_stats_on_saved_ai_session():
    session = Path("reports/ai_sessions/session_20260621_172129")
    fills_path = session / "fills.jsonl"
    summary_path = session / "summary.json"
    if not fills_path.exists() or not summary_path.exists():
        return

    fills = [json.loads(line) for line in fills_path.read_text().splitlines() if line.strip()]
    summary = json.loads(summary_path.read_text())
    stats = trade_pnl_stats(
        fills,
        initial_cash=float(summary.get("initial_cash", 0.0)),
        latest_prices={},
        positions=summary.get("positions", {}),
    )

    assert stats["overall"]["closed_trades"] >= 1
    assert stats["overall"]["total_fills"] == len(fills)
