import pandas as pd

from paisa_trader.intelligence import FilterConfig, ai_market_snapshot, enrich_indicators, estimate_depth, score_next_move


def sample_candles(rows=60):
    dates = pd.date_range("2024-01-01", periods=rows, freq="D")
    prices = [100 + i * 0.5 for i in range(rows)]
    return pd.DataFrame(
        {
            "timestamp": dates,
            "symbol": ["TEST.NS"] * rows,
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": prices,
            "volume": [200_000] * rows,
        }
    )


def test_enrich_indicators_adds_ai_fields():
    enriched = enrich_indicators(sample_candles())
    for column in ["rsi_14", "macd_hist", "bb_low", "vwap_proxy", "estimated_spread_bps"]:
        assert column in enriched.columns
    assert enriched["estimated_spread_bps"].iloc[-1] > 0


def test_depth_and_snapshot_are_serializable():
    enriched = enrich_indicators(sample_candles())
    depth = estimate_depth(enriched.iloc[-1])
    assert set(depth.columns) == {"level", "side", "price", "quantity"}
    assert len(depth) == 10

    snapshot = ai_market_snapshot("TEST.NS", enriched, 1.0, FilterConfig())
    assert snapshot["symbol"] == "TEST.NS"
    assert "next_move" in snapshot
    assert "synthetic_depth" in snapshot
    assert snapshot["synthetic_depth"]


def test_score_next_move_returns_action():
    enriched = enrich_indicators(sample_candles())
    score = score_next_move(enriched, FilterConfig())
    assert score["action"] in {"paper_long_candidate", "avoid_long_or_exit", "no_trade"}
    assert 0 <= score["score"] <= 100
