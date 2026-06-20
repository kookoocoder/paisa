import json

import pandas as pd

from paisa_trader.intelligence import FilterConfig, build_market_snapshot, enrich_indicators
from paisa_trader.wavetrail import build_wavetrail


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


def test_market_snapshot_serializes_to_json_safe_dict():
    enriched = enrich_indicators(sample_candles())
    snapshot = build_market_snapshot("TEST.NS", enriched, 1.0, FilterConfig(), equity=100_000, cash=80_000)

    payload = snapshot.to_dict()
    json.dumps(payload)

    assert payload["symbol"] == "TEST.NS"
    assert payload["timestamp"] == pd.Timestamp(enriched.iloc[-1]["timestamp"]).isoformat()
    assert payload["next_move_label"] in {"STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL"}
    assert set(payload["synthetic_depth"]) == {"bid_qty", "ask_qty", "imbalance"}
    assert payload["depth_levels"]


def test_market_snapshot_ai_prompt_contains_key_sections():
    enriched = enrich_indicators(sample_candles())
    prompt = build_market_snapshot("TEST.NS", enriched, 0.0, FilterConfig()).to_ai_prompt()

    assert "PRICE ACTION" in prompt
    assert "COMPOSITE INTELLIGENCE" in prompt
    assert "PORTFOLIO STATE" in prompt


def test_wavetrail_builds_intraday_trade_plan_for_core_timeframes():
    plan = build_wavetrail("TEST.NS", sample_candles(rows=120), cash=100_000, filter_cfg=FilterConfig())

    assert plan["stock_name"] == "TEST"
    assert plan["market"] == "NSE_INTRADAY"
    assert plan["lot_size"] == 1
    assert {item["timeframe"] for item in plan["plans"]} == {"5m", "15m", "30m"}
    assert all(item["action"] in {"BUY", "HOLD", "SELL", "WAIT"} for item in plan["plans"])
    assert all("stop_loss" in item and "trailing_stop" in item for item in plan["plans"])
