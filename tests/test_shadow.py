from unittest.mock import patch

import pandas as pd

from paisa_trader.config import BrokerConfig
from paisa_trader.shadow import run_shadow_session
from paisa_trader.strategies import BuyHoldStrategy


def sample_candles(symbol: str = "TEST.NS"):
    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    prices = [100 + i for i in range(30)]
    return pd.DataFrame(
        {
            "timestamp": dates,
            "symbol": symbol,
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": prices,
            "volume": [1000] * 30,
        }
    )


def test_run_shadow_session_writes_report_and_metadata(tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports"
    monkeypatch.setattr("paisa_trader.report.REPORTS_DIR", reports_dir)
    monkeypatch.setattr("paisa_trader.config.REPORTS_DIR", reports_dir)
    candles = sample_candles()

    with patch("paisa_trader.shadow.download_candles", return_value={"TEST.NS": tmp_path / "x.parquet"}):
        with patch("paisa_trader.shadow.load_candles", return_value=candles):
            session = run_shadow_session(
                ["TEST.NS"],
                "1mo",
                "1d",
                BuyHoldStrategy(),
                BrokerConfig(initial_cash=100_000, spread_bps=0, slippage_bps=0),
                force_refresh=True,
                export_bridge=False,
            )

    assert session.report_dir.exists()
    assert (session.report_dir / "shadow.json").exists()
    assert session.results[0].summary["symbol"] == "TEST.NS"
    assert session.bridge_dir is None
