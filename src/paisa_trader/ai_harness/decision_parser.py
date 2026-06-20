from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


VALID_ACTIONS = {"BUY", "SELL", "HOLD", "CLOSE"}
VALID_DIRECTIONS = {"UP", "DOWN", "FLAT"}


@dataclass(frozen=True)
class FuturePrediction:
    horizon_bars: int
    horizon_label: str
    direction: str
    confidence: float
    reasoning: str = ""
    price_target: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon_bars": self.horizon_bars,
            "horizon_label": self.horizon_label,
            "direction": self.direction,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "price_target": self.price_target,
        }


@dataclass(frozen=True)
class TradeDecision:
    action: str
    confidence: float
    reasoning: str
    next_move_prediction: str
    key_signals: list[str]
    risk_note: str
    future_predictions: list[FuturePrediction] = field(default_factory=list)
    raw_response: str = ""
    parse_error: str | None = None

    @classmethod
    def hold(cls, reason: str, raw_response: str = "") -> "TradeDecision":
        return cls(
            action="HOLD",
            confidence=0.0,
            reasoning=reason,
            next_move_prediction="No actionable prediction because the model response was not usable.",
            future_predictions=[],
            key_signals=["FAIL_SAFE_HOLD"],
            risk_note=reason,
            raw_response=raw_response,
            parse_error=reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "next_move_prediction": self.next_move_prediction,
            "future_predictions": [prediction.to_dict() for prediction in self.future_predictions],
            "key_signals": self.key_signals,
            "risk_note": self.risk_note,
            "raw_response": self.raw_response,
            "parse_error": self.parse_error,
        }


def parse_trade_decision(raw_response: str) -> TradeDecision:
    try:
        payload = json.loads(_extract_json(raw_response))
        action = str(payload.get("action", "HOLD")).upper()
        if action not in VALID_ACTIONS:
            raise ValueError(f"Unsupported action: {action}")

        confidence = float(payload.get("confidence", 0.0))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"Confidence out of range: {confidence}")

        key_signals = payload.get("key_signals", [])
        if not isinstance(key_signals, list):
            raise ValueError("key_signals must be a list")

        return TradeDecision(
            action=action,
            confidence=confidence,
            reasoning=str(payload.get("reasoning", "")).strip()[:800],
            next_move_prediction=str(payload.get("next_move_prediction", "")).strip()[:500],
            future_predictions=_parse_future_predictions(payload.get("future_predictions", [])),
            key_signals=[str(signal)[:80] for signal in key_signals[:3]],
            risk_note=str(payload.get("risk_note", "")).strip()[:500],
            raw_response=raw_response,
        )
    except Exception as exc:
        return TradeDecision.hold(f"AI response parse failed: {exc}", raw_response)


def _extract_json(raw_response: str) -> str:
    text = raw_response.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        return match.group(0)
    raise ValueError("No JSON object found")


def _parse_future_predictions(value: Any) -> list[FuturePrediction]:
    if not isinstance(value, list):
        return []

    predictions: list[FuturePrediction] = []
    for item in value[:4]:
        if not isinstance(item, dict):
            continue
        try:
            horizon_bars = max(1, int(item.get("horizon_bars", 1)))
            direction = str(item.get("direction", "FLAT")).upper()
            if direction not in VALID_DIRECTIONS:
                direction = "FLAT"
            confidence = float(item.get("confidence", 0.0))
            confidence = min(1.0, max(0.0, confidence))
            price_target = item.get("price_target")
            predictions.append(
                FuturePrediction(
                    horizon_bars=horizon_bars,
                    horizon_label=str(item.get("horizon_label", f"+{horizon_bars} bars")).strip()[:40],
                    direction=direction,
                    confidence=confidence,
                    reasoning=str(item.get("reasoning", "")).strip()[:240],
                    price_target=None if price_target is None else float(price_target),
                )
            )
        except (TypeError, ValueError):
            continue
    return predictions
