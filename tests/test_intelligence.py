import pandas as pd

from paisa_trader.intelligence import FilterConfig, build_market_snapshot, enrich_indicators, score_next_move


def trend_candles(rows=90, slope=0.3, volume=250_000):
    dates = pd.date_range("2024-01-01 09:15", periods=rows, freq="5min")
    prices = [100 + i * slope for i in range(rows)]
    return pd.DataFrame(
        {
            "timestamp": dates,
            "symbol": ["TEST.NS"] * rows,
            "open": prices,
            "high": [p + 0.8 for p in prices],
            "low": [p - 0.8 for p in prices],
            "close": prices,
            "volume": [volume] * rows,
        }
    )


def test_enrich_indicators_adds_quant_feature_contract():
    enriched = enrich_indicators(trend_candles())
    last = enriched.iloc[-1]

    expected = {
        "adx_14",
        "atr_pct",
        "bb_bandwidth",
        "donchian_high_20",
        "donchian_low_20",
        "ema_spread_pct",
        "momentum_score",
        "obv_slope_10",
        "participation_score",
        "return_lag_1",
        "return_lag_3",
        "return_lag_5",
        "realized_vol_20",
        "regime",
        "session_phase_close",
        "session_phase_open",
        "session_progress",
        "stoch_k",
        "trend_score",
        "vwap_distance_pct",
    }
    assert expected.issubset(enriched.columns)
    assert last["regime"] in {"TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL", "LOW_LIQUIDITY"}
    assert -1 <= last["trend_score"] <= 1
    assert -1 <= last["momentum_score"] <= 1


def test_score_next_move_returns_factor_breakdown_and_regime():
    enriched = enrich_indicators(trend_candles())
    signal = score_next_move(enriched, FilterConfig())

    assert signal["direction"] in {"bullish", "bearish", "neutral"}
    assert signal["action"] in {"paper_long_candidate", "avoid_long_or_exit", "no_trade"}
    assert set(signal["factor_scores"]) == {
        "trend_score",
        "momentum_score",
        "mean_reversion_score",
        "participation_score",
        "volatility_score",
    }
    assert signal["regime"] == enriched.iloc[-1]["regime"]
    assert signal["active_regime"] == enriched.iloc[-1]["regime"]
    assert set(signal["active_weights"]) == {"trend", "momentum", "mean_reversion", "participation", "volatility"}
    assert all(-1 <= value <= 1 for value in signal["factor_scores"].values())


def test_build_market_snapshot_exposes_regime_and_factor_scores():
    enriched = enrich_indicators(trend_candles())
    snapshot = build_market_snapshot("TEST.NS", enriched, 0.0, FilterConfig())
    payload = snapshot.to_dict()

    assert payload["market_regime"] == enriched.iloc[-1]["regime"]
    assert "trend_score" in payload["factor_scores"]
    assert "factor_scores" in payload["signal_components"]
    assert "Session progress:" in snapshot.to_ai_prompt()
    assert "Regime:" in snapshot.to_ai_prompt()


def test_low_volume_disqualifies_candidate_even_with_directional_score():
    enriched = enrich_indicators(trend_candles(volume=10_000))
    signal = score_next_move(enriched, FilterConfig(min_volume=100_000))

    assert signal["passes_filters"] is False
    assert signal["paper_trade_candidate"] is False
    assert "volume below filter" in signal["disqualifiers"]


def test_range_regime_uses_mean_reversion_heavy_weights():
    enriched = enrich_indicators(trend_candles())
    enriched.loc[enriched.index[-1], "regime"] = "RANGE"

    signal = score_next_move(enriched, FilterConfig())

    assert signal["active_weights"]["mean_reversion"] >= 0.40


def test_trend_up_regime_uses_trend_and_momentum_heavy_weights():
    enriched = enrich_indicators(trend_candles())
    enriched.loc[enriched.index[-1], "regime"] = "TREND_UP"

    signal = score_next_move(enriched, FilterConfig())

    assert signal["active_weights"]["trend"] + signal["active_weights"]["momentum"] >= 0.70
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
