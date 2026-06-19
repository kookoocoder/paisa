import pandas as pd

from paisa_trader.bridge import stocksharp_candles, stocksharp_signals
from paisa_trader.strategies import BuyHoldStrategy


def sample_candles():
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=3, freq="D"),
            "symbol": ["TEST.NS"] * 3,
            "open": [100, 101, 102],
            "high": [101, 102, 103],
            "low": [99, 100, 101],
            "close": [100, 101, 102],
            "volume": [1000, 1100, 1200],
        }
    )


def test_stocksharp_candle_columns():
    out = stocksharp_candles(sample_candles())
    assert list(out.columns) == ["Symbol", "Time", "Open", "High", "Low", "Close", "Volume"]
    assert out.iloc[0]["Symbol"] == "TEST.NS"


def test_stocksharp_signals_include_actions():
    out = stocksharp_signals(sample_candles(), BuyHoldStrategy())
    assert list(out.columns) == ["Symbol", "Time", "Close", "TargetPosition", "Action", "Reason", "Strategy"]
    assert out.iloc[0]["Action"] == "BUY_OR_INCREASE"
    assert out.iloc[1]["Action"] == "HOLD"
