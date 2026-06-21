import pandas as pd

from paisa_trader.data import _normalize_upstox_candles, candle_path, upstox_instrument_key


def test_upstox_instrument_key_resolves_nse_symbol():
    instruments = [
        {
            "segment": "NSE_EQ",
            "instrument_type": "EQ",
            "trading_symbol": "RELIANCE",
            "instrument_key": "NSE_EQ|INE002A01018",
        }
    ]

    assert upstox_instrument_key("RELIANCE.NS", instruments) == "NSE_EQ|INE002A01018"


def test_upstox_instrument_key_accepts_raw_key():
    assert upstox_instrument_key("NSE_EQ|INE002A01018", []) == "NSE_EQ|INE002A01018"


def test_normalize_upstox_candles_accepts_list_schema():
    payload = {
        "status": "success",
        "data": {
            "candles": [
                ["2026-06-19T09:15:00+05:30", 100.0, 101.0, 99.5, 100.5, 12345, 0],
            ]
        },
    }

    candles = _normalize_upstox_candles(payload, "RELIANCE.NS")

    assert list(candles.columns) == ["timestamp", "symbol", "open", "high", "low", "close", "volume"]
    assert candles.iloc[0]["symbol"] == "RELIANCE.NS"
    assert candles.iloc[0]["close"] == 100.5
    assert pd.notna(candles.iloc[0]["timestamp"])


def test_normalize_upstox_candles_accepts_dict_schema():
    payload = {
        "data": [
            {
                "timestamp": "2026-06-19T09:15:00+05:30",
                "ohlcv": [100.0, 101.0, 99.5, 100.5, 12345],
            }
        ],
    }

    candles = _normalize_upstox_candles(payload, "RELIANCE.NS")

    assert candles.iloc[0]["open"] == 100.0
    assert candles.iloc[0]["volume"] == 12345


def test_candle_path_keeps_yfinance_names_compatible():
    assert candle_path("RELIANCE.NS", "5d", "5m").name == "RELIANCE.NS__5d__5m.parquet"
    assert candle_path("RELIANCE.NS", "5d", "5m", "upstox").name == "upstox__RELIANCE.NS__5d__5m.parquet"
