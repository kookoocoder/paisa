from __future__ import annotations

from ..calibration import ConfidenceCalibrator
from ..snapshot import MarketSnapshot


SYSTEM_PROMPT = """You are an intraday trading analysis AI for Indian NSE stocks.
You will receive a structured market snapshot every bar.
You must respond with a JSON TradeDecision object only.
You do NOT place real trades. This is a paper research harness.
Keep the response compact and decision-focused.

TradeDecision schema:
{
  "action": "BUY" | "SELL" | "HOLD" | "CLOSE",
  "confidence": 0.0,
  "reasoning": "<3 sentences explaining your decision>",
  "next_move_prediction": "<plain English: expected direction and magnitude>",
  "future_predictions": [
    {
      "horizon_bars": 1,
      "horizon_label": "+5m",
      "direction": "UP" | "DOWN" | "FLAT",
      "confidence": 0.0,
      "price_target": 0.0,
      "reasoning": "<why this future bar should move that way>"
    }
  ],
  "key_signals": ["signal1", "signal2"],
  "risk_note": "<any concern about this trade>"
}

Rules:
- Only BUY if confidence > 0.65 and intelligence_gate is true.
- Only one open position per symbol at a time.
- future_predictions is mandatory. Include exactly two entries: horizon_bars 1 labeled "+5m" and horizon_bars 3 labeled "+15m".
- direction must be UP, DOWN, or FLAT. Use FLAT when expected move is too small to matter.
- Use PAST FORECAST CHECKS as factual evidence, but do not ignore the current snapshot.
- Keep reasoning to 1-2 sentences and reference the strongest specific indicator values only.
- Return JSON only, with no markdown fences or commentary.
"""


def build_user_prompt(
    snapshot: MarketSnapshot,
    prediction_context: dict | None = None,
    calibrator: ConfidenceCalibrator | None = None,
) -> str:
    if calibrator is not None:
        prediction_context = dict(prediction_context or {})
        prediction_context["calibration"] = calibrator.calibration_stats()
    context_text = _prediction_context_text(prediction_context)
    return f"""{snapshot.to_ai_prompt()}
{context_text}

Analyse this snapshot and respond with a TradeDecision JSON object."""


def build_messages(
    snapshot: MarketSnapshot,
    prediction_context: dict | None = None,
    calibrator: ConfidenceCalibrator | None = None,
) -> tuple[str, str]:
    return SYSTEM_PROMPT, build_user_prompt(snapshot, prediction_context, calibrator)


def _prediction_context_text(prediction_context: dict | None) -> str:
    if not prediction_context:
        return """
PAST FORECAST CHECKS
  No settled past forecasts are available yet for this bar.
"""
    return f"""
PAST FORECAST CHECKS
  Current-bar forecasts from earlier model calls: {prediction_context.get("current_forecasts", [])}
  Recent settled accuracy: {prediction_context.get("recent_accuracy", {})}
  Rolling accuracy: {prediction_context.get("rolling", {})}
  Accuracy by horizon: {prediction_context.get("by_horizon", {})}
  Accuracy by regime: {prediction_context.get("by_regime", {})}
  Net P&L per trade after costs: {prediction_context.get("net_pnl_per_trade", 0.0)}
  Calibration: {prediction_context.get("calibration", [])}
  Agreement signal: {prediction_context.get("agreement_signal", "NONE")}
"""
