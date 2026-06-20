import asyncio
import json

import pandas as pd
import pytest

from paisa_trader.ai_harness.context_builder import build_messages
from paisa_trader.ai_harness.decision_parser import FuturePrediction
from paisa_trader.ai_harness.decision_parser import TradeDecision, parse_trade_decision
from paisa_trader.ai_harness.decision_router import DecisionRouter
from paisa_trader.ai_harness.model_runner import LMStudioRunner, MockRunner, ModelRunnerConfig, runner_from_config
from paisa_trader.ai_harness.prediction_tracker import (
    extended_prediction_context,
    prediction_context,
    prediction_stats,
    prepare_future_predictions,
    settle_due_predictions,
)
from paisa_trader.broker import SimulatedBroker
from paisa_trader.calibration import ConfidenceCalibrator
from paisa_trader.config import AIHarnessConfig, BrokerConfig
from paisa_trader.intelligence import FilterConfig, build_market_snapshot, enrich_indicators


def sample_candles(rows=60):
    dates = pd.date_range("2024-01-01", periods=rows, freq="D")
    prices = [100 + i for i in range(rows)]
    return pd.DataFrame(
        {
            "timestamp": dates,
            "symbol": ["TEST.NS"] * rows,
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": prices,
            "volume": [250_000] * rows,
        }
    )


def snapshot():
    enriched = enrich_indicators(sample_candles())
    return build_market_snapshot("TEST.NS", enriched, 0.0, FilterConfig(), equity=100_000, cash=100_000)


def test_context_builder_includes_trade_decision_schema_and_snapshot():
    system, user = build_messages(snapshot())

    assert "TradeDecision schema" in system
    assert "MARKET SNAPSHOT" in user
    assert "COMPOSITE INTELLIGENCE" in user


def test_mock_runner_returns_parseable_decision():
    system, user = build_messages(snapshot())
    raw = asyncio.run(MockRunner().run(system, user))
    decision = parse_trade_decision(raw)

    assert decision.action in {"BUY", "HOLD", "CLOSE"}
    assert decision.parse_error is None


def test_lmstudio_runner_detects_first_loaded_model(monkeypatch):
    monkeypatch.setattr(
        "paisa_trader.ai_harness.model_runner._get_json",
        lambda _: {"data": [{"id": "loaded-local-model"}]},
    )
    runner = LMStudioRunner(ModelRunnerConfig(provider="lmstudio", model_name="auto", local_url="http://127.0.0.1:1234"))

    assert runner.detect_model() == "loaded-local-model"


def test_lmstudio_runner_rejects_embedding_model_for_predictions(monkeypatch):
    monkeypatch.setattr(
        "paisa_trader.ai_harness.model_runner._get_json",
        lambda _: {"data": [{"id": "text-embedding-nomic-embed-text-v1.5"}]},
    )
    runner = LMStudioRunner(ModelRunnerConfig(provider="lmstudio", model_name="auto", local_url="http://127.0.0.1:1234"))

    with pytest.raises(RuntimeError, match="chat/completion model"):
        runner.detect_model()


def test_runner_from_config_supports_lmstudio_provider():
    runner = runner_from_config(ModelRunnerConfig(provider="lmstudio", model_name="auto"))

    assert isinstance(runner, LMStudioRunner)


def test_parser_falls_back_to_hold_on_bad_json():
    decision = parse_trade_decision("not json")

    assert decision.action == "HOLD"
    assert decision.parse_error is not None


def test_parser_accepts_future_predictions():
    decision = parse_trade_decision(
        json.dumps(
            {
                "action": "HOLD",
                "confidence": 0.4,
                "reasoning": "waiting",
                "next_move_prediction": "sideways",
                "future_predictions": [
                    {"horizon_bars": 1, "horizon_label": "+5m", "direction": "UP", "confidence": 0.7},
                    {"horizon_bars": 3, "horizon_label": "+15m", "direction": "DOWN", "confidence": 0.6},
                ],
                "key_signals": ["test"],
                "risk_note": "paper only",
            }
        )
    )

    assert [item.horizon_bars for item in decision.future_predictions] == [1, 3]
    assert decision.to_dict()["future_predictions"][0]["direction"] == "UP"


def test_prediction_tracker_settles_horizon_forecasts():
    candles = sample_candles(rows=65)
    enriched = enrich_indicators(candles.iloc[:60])
    first = build_market_snapshot("TEST.NS", enriched, 0.0, FilterConfig())
    predictions = prepare_future_predictions(
        [FuturePrediction(1, "+5m", "UP", 0.7), FuturePrediction(3, "+15m", "UP", 0.6)],
        first.bar_index,
        first,
        candles,
    )
    record = {
        "symbol": "TEST.NS",
        "timestamp": first.timestamp.isoformat(),
        "close": first.close,
        "future_predictions": predictions,
        "prediction_result": "PENDING",
    }
    decisions = [record]

    for rows in (61, 63):
        current = build_market_snapshot("TEST.NS", enrich_indicators(candles.iloc[:rows]), 0.0, FilterConfig())
        settle_due_predictions(decisions, "TEST.NS", current)

    assert {item["horizon_bars"]: item["result"] for item in record["future_predictions"]} == {1: "HIT", 3: "HIT"}
    assert prediction_stats(decisions)["hits"] == 2
    context = prediction_context(decisions, "TEST.NS", first.bar_index + 1)
    assert context["agreement_signal"] == "PAST_FORECASTS_AGREE_UP"
    assert context["by_horizon"]["+5m"]["hit"] == 1
    assert context["rolling"]["last_20"]["n"] == 2


def test_prediction_tracker_records_calibration_on_settlement():
    candles = sample_candles(rows=62)
    first = build_market_snapshot("TEST.NS", enrich_indicators(candles.iloc[:60]), 0.0, FilterConfig())
    predictions = prepare_future_predictions([FuturePrediction(1, "+5m", "UP", 0.7)], first.bar_index, first, candles)
    record = {"symbol": "TEST.NS", "timestamp": first.timestamp.isoformat(), "future_predictions": predictions}
    current = build_market_snapshot("TEST.NS", enrich_indicators(candles.iloc[:61]), 0.0, FilterConfig())
    calibrator = ConfidenceCalibrator()

    settle_due_predictions([record], "TEST.NS", current, calibrator=calibrator)

    assert calibrator.calibration_stats()[0]["n"] == 1


def test_extended_prediction_context_groups_by_regime_and_window():
    decisions = [
        {
            "symbol": "TEST.NS",
            "future_predictions": [
                {"result": "HIT", "horizon_label": "+5m", "regime": "RANGE", "pnl_after_costs": 10.0},
                {"result": "MISS", "horizon_label": "+15m", "regime": "TREND_UP", "pnl_after_costs": -5.0},
                {"result": "NEUTRAL", "horizon_label": "+5m", "regime": "RANGE", "pnl_after_costs": 0.0},
            ],
        }
    ]

    context = extended_prediction_context(decisions, "TEST.NS", [2])

    assert context["overall"] == {"hit": 1, "miss": 1, "rate": 0.5}
    assert context["by_regime"]["RANGE"] == {"rate": 1.0, "n": 1}
    assert context["rolling"]["last_2"] == {"rate": 0.5, "n": 2}
    assert context["net_pnl_per_trade"] == 2.5


def test_decision_router_blocks_low_confidence_buy():
    broker = SimulatedBroker(BrokerConfig(initial_cash=100_000, spread_bps=0, slippage_bps=0))
    decision = TradeDecision(
        action="BUY",
        confidence=0.1,
        reasoning="weak",
        next_move_prediction="weak",
        key_signals=[],
        risk_note="weak",
    )

    result = DecisionRouter().route(decision, snapshot(), broker)

    assert result.accepted is False
    assert "confidence" in result.reason


def test_web_engine_settles_multi_horizon_predictions():
    pytest.importorskip("yfinance")
    from paisa_trader.ai_web_server import AIReplayEngine, AIWebConfig

    class HorizonRunner:
        def __init__(self):
            self.prompts = []

        async def run(self, system, user):
            self.prompts.append(user)
            return json.dumps(
                {
                    "action": "HOLD",
                    "confidence": 0.7,
                    "reasoning": "paper forecast only",
                    "next_move_prediction": "up",
                    "future_predictions": [
                        {"horizon_bars": 1, "horizon_label": "+5m", "direction": "UP", "confidence": 0.7},
                        {"horizon_bars": 3, "horizon_label": "+15m", "direction": "UP", "confidence": 0.6},
                    ],
                    "key_signals": ["test"],
                    "risk_note": "paper only",
                }
            )

    runner = HorizonRunner()
    config = AIWebConfig(
        symbols=["TEST.NS"],
        loop=False,
        force_refresh=False,
        ai=AIHarnessConfig(symbols=["TEST.NS"]),
    )
    engine = AIReplayEngine(config, {"TEST.NS": sample_candles(rows=65)})
    engine.runner = runner

    async def run_steps():
        await engine.prepare()
        for _ in range(4):
            await engine.step()
        return await engine.state()

    state = asyncio.run(run_steps())
    first_decision = state["symbols"]["TEST.NS"]["decisions"][0]
    results = {item["horizon_bars"]: item["result"] for item in first_decision["future_predictions"]}

    assert results[1] == "HIT"
    assert results[3] == "HIT"
    assert state["prediction_stats"]["settled"] >= 2
    assert any("PAST FORECAST CHECKS" in prompt for prompt in runner.prompts[1:])
