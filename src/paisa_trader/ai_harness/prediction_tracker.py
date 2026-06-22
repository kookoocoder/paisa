from __future__ import annotations

from typing import Any

import pandas as pd

from ..calibration import ConfidenceCalibrator
from .decision_parser import FuturePrediction

VALID_RESULTS = {"HIT", "MISS", "NEUTRAL"}
SETTLED_RESULTS = {"HIT", "MISS"}


def prepare_future_predictions(
    predictions: list[FuturePrediction],
    bar_index: int,
    snapshot: Any,
    frame: pd.DataFrame,
) -> list[dict[str, Any]]:
    prepared = list(predictions)
    if not any(prediction.horizon_bars == 1 for prediction in prepared):
        prepared.insert(
            0,
            FuturePrediction(
                horizon_bars=1,
                horizon_label="+5m",
                direction=predicted_direction(snapshot.next_move_score),
                confidence=snapshot.confidence,
                reasoning="Fallback forecast from the rule-based next-move score.",
            ),
        )
    if not any(prediction.horizon_bars == 3 for prediction in prepared):
        prepared.append(
            FuturePrediction(
                horizon_bars=3,
                horizon_label="+15m",
                direction=predicted_direction(snapshot.next_move_score * 0.75),
                confidence=round(max(0.0, min(1.0, snapshot.confidence * 0.75)), 3),
                reasoning="Fallback +15m forecast from the damped rule-based next-move score.",
            )
        )

    return [
        {
            **prediction.to_dict(),
            "origin_bar_index": bar_index,
            "origin_timestamp": snapshot.timestamp.isoformat(),
            "origin_close": snapshot.close,
            "regime": getattr(snapshot, "market_regime", "UNKNOWN"),
            "target_bar_index": bar_index + prediction.horizon_bars,
            "target_timestamp": _target_timestamp(frame, bar_index + prediction.horizon_bars),
            "actual_close": None,
            "actual_return_pct": None,
            "actual_direction": None,
            "pnl_after_costs": 0.0,
            "result": "PENDING",
        }
        for prediction in prepared
    ]


def settle_due_predictions(
    decisions: list[dict[str, Any]],
    symbol: str,
    snapshot: Any,
    *,
    flat_return_threshold_pct: float = 0.02,
    calibrator: ConfidenceCalibrator | None = None,
) -> None:
    for record in decisions:
        if record["symbol"] != symbol:
            continue
        for prediction in record.get("future_predictions", []):
            if prediction.get("result") != "PENDING" or prediction.get("target_bar_index") != snapshot.bar_index:
                continue
            actual_return = ((snapshot.close / prediction["origin_close"]) - 1) * 100 if prediction["origin_close"] else 0.0
            actual = actual_direction(actual_return, flat_return_threshold_pct)
            prediction["actual_close"] = round(snapshot.close, 4)
            prediction["actual_return_pct"] = round(actual_return, 4)
            prediction["actual_direction"] = actual
            prediction["result"] = "NEUTRAL" if prediction["direction"] == "FLAT" else (
                "HIT" if prediction["direction"] == actual else "MISS"
            )
            prediction["pnl_after_costs"] = float(prediction.get("pnl_after_costs", 0.0) or 0.0)
            if calibrator and prediction["result"] in SETTLED_RESULTS:
                calibrator.record(float(prediction.get("confidence", 0.0) or 0.0), 1 if prediction["result"] == "HIT" else 0)
            if prediction.get("horizon_bars") == 1:
                record["actual_next_close"] = prediction["actual_close"]
                record["actual_next_return_pct"] = prediction["actual_return_pct"]
                record["actual_direction"] = prediction["actual_direction"]
                record["prediction_result"] = prediction["result"]


def prediction_context(decisions: list[dict[str, Any]], symbol: str, bar_index: int) -> dict[str, Any]:
    current_forecasts = [
        _forecast_prompt_item(record, prediction)
        for record in decisions
        if record["symbol"] == symbol
        for prediction in record.get("future_predictions", [])
        if prediction.get("target_bar_index") == bar_index and prediction.get("result") in VALID_RESULTS
    ]
    recent = [
        prediction
        for record in decisions
        if record["symbol"] == symbol
        for prediction in record.get("future_predictions", [])
        if prediction.get("result") in VALID_RESULTS
    ][-20:]
    directional = [item for item in recent if item["result"] in {"HIT", "MISS"}]
    hits = sum(1 for item in directional if item["result"] == "HIT")
    directions = [
        item["predicted_direction"]
        for item in current_forecasts
        if item.get("predicted_direction") in {"UP", "DOWN"}
    ]
    agreement = "NONE"
    if directions and len(set(directions)) == 1:
        agreement = f"PAST_FORECASTS_AGREE_{directions[0]}"
    elif directions:
        agreement = "PAST_FORECASTS_CONFLICT"
    extended = extended_prediction_context(decisions, symbol)
    return {
        "current_forecasts": current_forecasts,
        "recent_accuracy": {
            "checked": len(recent),
            "directional": len(directional),
            "hit_rate": round(hits / len(directional), 4) if directional else 0.0,
        },
        "agreement_signal": agreement,
        **extended,
    }


def extended_prediction_context(
    decisions: list[dict[str, Any]],
    symbol: str | None = None,
    window_sizes: list[int] | None = None,
) -> dict[str, Any]:
    """
    Build rolling accuracy context for settled, directional AI predictions.

    Args:
        decisions: Decision records containing ``future_predictions``.
        symbol: Optional symbol filter. Use ``None`` to aggregate all symbols.
        window_sizes: Rolling windows to compute. Defaults to [20, 50, 100].

    Returns:
        Dictionary with overall, horizon, rolling, regime, PnL, and agreement
        summaries.

    Example:
        ``extended_prediction_context(decisions, "RELIANCE")`` returns
        recent hit rates split by horizon and market regime.
    """
    window_sizes = [20, 50, 100] if window_sizes is None else window_sizes
    settled = _settled_directional_predictions(decisions, symbol)
    hits = sum(1 for item in settled if item["result"] == "HIT")
    misses = len(settled) - hits
    rolling = {f"last_{size}": _rate_summary(settled[-size:]) for size in window_sizes}
    by_horizon = {
        label: _hit_miss_summary([item for item in settled if item.get("horizon_label") == label])
        for label in ["+5m", "+15m"]
    }
    regimes = sorted({str(item.get("regime", "UNKNOWN")) for item in settled})
    by_regime = {
        regime: {
            "rate": _rate_summary([item for item in settled if str(item.get("regime", "UNKNOWN")) == regime])["rate"],
            "n": _rate_summary([item for item in settled if str(item.get("regime", "UNKNOWN")) == regime])["n"],
        }
        for regime in regimes
    }
    last_20 = rolling.get("last_20", {"rate": 0.0})["rate"]
    last_50 = rolling.get("last_50", {"rate": 0.0})["rate"]
    pnl_items = [float(item.get("pnl_after_costs", 0.0) or 0.0) for item in settled]

    return {
        "overall": {"hit": hits, "miss": misses, "rate": round(hits / len(settled), 4) if settled else 0.0},
        "by_horizon": by_horizon,
        "rolling": rolling,
        "by_regime": by_regime,
        "net_pnl_per_trade": round(sum(pnl_items) / len(pnl_items), 4) if pnl_items else 0.0,
        "rolling_agreement_signal": _rolling_agreement_signal(last_20, last_50),
    }


def prediction_stats(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [
        prediction
        for item in decisions
        for prediction in item.get("future_predictions", [])
        if prediction.get("result") in VALID_RESULTS
    ]
    if not settled:
        settled = [item for item in decisions if item.get("prediction_result") in VALID_RESULTS]
    directional = [item for item in settled if _prediction_result(item) in {"HIT", "MISS"}]
    hits = sum(1 for item in directional if _prediction_result(item) == "HIT")
    return {
        "settled": len(settled),
        "directional": len(directional),
        "hits": hits,
        "misses": len(directional) - hits,
        "hit_rate": round(hits / len(directional), 4) if directional else 0.0,
    }


def predicted_direction(score: float, threshold: float = 0.02) -> str:
    if score >= threshold:
        return "UP"
    if score <= -threshold:
        return "DOWN"
    return "FLAT"


def actual_direction(return_pct: float, flat_threshold_pct: float = 0.02) -> str:
    if return_pct > flat_threshold_pct:
        return "UP"
    if return_pct < -flat_threshold_pct:
        return "DOWN"
    return "FLAT"


def _target_timestamp(frame: pd.DataFrame, target_idx: int) -> str | None:
    if target_idx >= len(frame):
        return None
    return pd.Timestamp(frame.iloc[target_idx]["timestamp"]).isoformat()


def _forecast_prompt_item(record: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    return {
        "origin_time": record["timestamp"],
        "horizon": prediction.get("horizon_label"),
        "predicted_direction": prediction.get("direction"),
        "prediction_confidence": prediction.get("confidence"),
        "actual_direction": prediction.get("actual_direction"),
        "actual_return_pct": prediction.get("actual_return_pct"),
        "result": prediction.get("result"),
    }


def _prediction_result(item: dict[str, Any]) -> str:
    return str(item.get("result", item.get("prediction_result", "")))


def _settled_directional_predictions(decisions: list[dict[str, Any]], symbol: str | None) -> list[dict[str, Any]]:
    return [
        prediction
        for record in decisions
        if symbol is None or record["symbol"] == symbol
        for prediction in record.get("future_predictions", [])
        if prediction.get("result") in SETTLED_RESULTS
    ]


def _hit_miss_summary(items: list[dict[str, Any]]) -> dict[str, float | int]:
    hits = sum(1 for item in items if item.get("result") == "HIT")
    misses = sum(1 for item in items if item.get("result") == "MISS")
    total = hits + misses
    return {"hit": hits, "miss": misses, "rate": round(hits / total, 4) if total else 0.0}


def _rate_summary(items: list[dict[str, Any]]) -> dict[str, float | int]:
    hits = sum(1 for item in items if item.get("result") == "HIT")
    return {"rate": round(hits / len(items), 4) if items else 0.0, "n": len(items)}


def _rolling_agreement_signal(last_20: float, last_50: float) -> str:
    if last_20 > last_50 + 0.05:
        return "improving"
    if last_20 < last_50 - 0.05:
        return "degrading"
    return "consistent"
