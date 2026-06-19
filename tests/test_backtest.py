import pandas as pd

from paisa_trader.backtest import run_symbol_backtest
from paisa_trader.config import BrokerConfig
from paisa_trader.strategies import BuyHoldStrategy, MovingAverageCrossStrategy


def sample_candles():
    dates = pd.date_range("2024-01-01", periods=50, freq="D")
    prices = [100 + i for i in range(50)]
    return pd.DataFrame(
        {
            "timestamp": dates,
            "symbol": "TEST.NS",
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": prices,
            "volume": [1000] * 50,
        }
    )


def test_buy_hold_backtest_produces_summary():
    result = run_symbol_backtest(
        sample_candles(),
        BuyHoldStrategy(),
        BrokerConfig(initial_cash=100_000, spread_bps=0, slippage_bps=0),
    )
    assert result.summary["trades"] == 2
    assert result.summary["final_equity"] > 100_000


def test_ma_cross_backtest_runs():
    result = run_symbol_backtest(
        sample_candles(),
        MovingAverageCrossStrategy(fast=3, slow=7),
        BrokerConfig(initial_cash=100_000),
    )
    assert "total_return_pct" in result.summary
    assert not result.equity_curve.empty
