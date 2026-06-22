import os
from pathlib import Path

import pandas as pd
import pytest

from paisa_trader.data import _normalize_upstox_candles, candle_path, download_candles, resolve_instrument_key, upstox_instrument_key


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


def test_resolve_instrument_key_nifty():
    assert resolve_instrument_key("NIFTY50") == "NSE_INDEX|Nifty 50"
    assert resolve_instrument_key("^NSEI") == "NSE_INDEX|Nifty 50"


def test_resolve_instrument_key_equity():
    assert resolve_instrument_key("RELIANCE") == "NSE_EQ|INE002A01018"
    assert resolve_instrument_key("RELIANCE.NS") == "NSE_EQ|INE002A01018"


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


def test_candle_path_uses_upstox_interval_names():
    assert candle_path("RELIANCE", "5d", "5m").name == "RELIANCE__5d__5minute.parquet"
    assert candle_path("RELIANCE", "5d", "5minute").name == "RELIANCE__5d__5minute.parquet"


def test_download_candles_returns_correct_columns():
    if not os.getenv("UPSTOX_ANALYTICS_TOKEN"):
        pytest.skip("UPSTOX_ANALYTICS_TOKEN not set")
    df = download_candles("RELIANCE", period="5d", interval="5minute")
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert df.index.tz is not None


def test_interval_string_format():
    offenders = []
    for path in (Path(__file__).resolve().parents[1] / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "interval=\"5m\"" in text or "interval='5m'" in text or "interval=\"1h\"" in text or "interval='1h'" in text:
            offenders.append(str(path))
    assert offenders == []
