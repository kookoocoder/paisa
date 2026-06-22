from __future__ import annotations

from paisa_trader.data import quote_snapshot


def test_quote_snapshot_computes_change_pct():
    row = quote_snapshot(
        "NSE_EQ|INE002A01018",
        {
            "last_price": 110.0,
            "ohlc": {"open": 100.0, "high": 112.0, "low": 99.0, "close": 100.0},
            "volume": 12345,
            "name": "Reliance Industries Ltd",
        },
        "RELIANCE",
    )
    assert row["symbol"] == "RELIANCE"
    assert row["ltp"] == 110.0
    assert row["change_pct"] == 10.0
    assert row["volume"] == 12345


def test_nse_equity_universe_filters_nse_eq(monkeypatch):
    from paisa_trader import data

    monkeypatch.setattr(
        data,
        "load_upstox_instruments",
        lambda force_refresh=False: [
            {
                "segment": "NSE_EQ",
                "instrument_type": "EQ",
                "trading_symbol": "TCS",
                "instrument_key": "NSE_EQ|TCS",
                "name": "Tata Consultancy Services Ltd",
            },
            {
                "segment": "NSE_FO",
                "instrument_type": "FUT",
                "trading_symbol": "NIFTY",
                "instrument_key": "NSE_FO|NIFTY",
            },
        ],
    )
    rows = data.nse_equity_universe()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "TCS"
