from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .intelligence import FilterConfig, enrich_indicators, score_next_move


@dataclass(frozen=True)
class WaveTrailConfig:
    timeframes: tuple[str, ...] = ("5m", "15m", "30m")
    max_position_pct: float = 0.20
    lot_size: int = 1


def build_wavetrail(
    symbol: str,
    candles: pd.DataFrame,
    cash: float,
    filter_cfg: FilterConfig | None = None,
    config: WaveTrailConfig | None = None,
) -> dict[str, Any]:
    """Build an intraday WaveTrail plan from visible replay candles."""
    cfg = config or WaveTrailConfig()
    filter_cfg = filter_cfg or FilterConfig()
    plans = [
        _build_timeframe_plan(symbol, timeframe, candles, cash, filter_cfg, cfg)
        for timeframe in cfg.timeframes
    ]
    actionable = [plan["action"] for plan in plans if plan["action"] != "WAIT"]
    buys = actionable.count("BUY")
    sells = actionable.count("SELL")
    if buys >= 2:
        overall_action = "BUY"
    elif sells >= 2:
        overall_action = "SELL"
    elif actionable:
        overall_action = "HOLD"
    else:
        overall_action = "WAIT"

    return {
        "symbol": symbol,
        "stock_name": _stock_name(symbol),
        "market": "NSE_INTRADAY",
        "overall_action": overall_action,
        "alignment": f"{buys} BUY / {sells} SELL / {actionable.count('HOLD')} HOLD",
        "lot_size": cfg.lot_size,
        "lot_note": "NSE cash equity lot size is assumed as 1 share; F&O lot sizes need instrument metadata.",
        "plans": plans,
    }


def _build_timeframe_plan(
    symbol: str,
    timeframe: str,
    candles: pd.DataFrame,
    cash: float,
    filter_cfg: FilterConfig,
    cfg: WaveTrailConfig,
) -> dict[str, Any]:
    frame = _to_timeframe(candles, timeframe)
    if len(frame) < 5:
        return {
            "timeframe": timeframe,
            "action": "WAIT",
            "reason": "Not enough bars yet for this interval.",
            "stock_name": _stock_name(symbol),
            "lot_size": cfg.lot_size,
            "suggested_quantity": 0,
            "suggested_lots": 0,
            "stop_loss": None,
            "trailing_stop": None,
            "trail_distance": None,
        }

    enriched = enrich_indicators(frame)
    last = enriched.iloc[-1]
    signal = score_next_move(enriched, filter_cfg)
    close = float(last["close"])
    atr = _optional_float(last.get("atr_14"))
    vwap = _optional_float(last.get("vwap_proxy"))
    sma_20 = _optional_float(last.get("sma_20"))
    macd_hist = _optional_float(last.get("macd_hist"))
    score = float(signal["score"])
    risk = _risk_distance(close, atr)
    trail_distance = _trail_distance(close, atr)
    action = _action(score, close, vwap, sma_20, macd_hist, signal["passes_filters"])
    quantity = _suggested_quantity(cash, close, cfg.max_position_pct, cfg.lot_size)
    stop_loss = close - risk if action in {"BUY", "HOLD"} else close + risk
    trailing_stop = close - trail_distance if action in {"BUY", "HOLD"} else close + trail_distance

    return {
        "timeframe": timeframe,
        "action": action,
        "stock_name": _stock_name(symbol),
        "lot_size": cfg.lot_size,
        "suggested_quantity": quantity,
        "suggested_lots": quantity // cfg.lot_size if cfg.lot_size else 0,
        "entry_reference": round(close, 2),
        "stop_loss": round(stop_loss, 2),
        "trailing_stop": round(trailing_stop, 2),
        "trail_distance": round(trail_distance, 2),
        "risk_per_share": round(abs(close - stop_loss), 2),
        "risk_pct": round(abs(close - stop_loss) / close * 100, 2) if close else 0.0,
        "score": round(score, 2),
        "confidence": signal["confidence"],
        "trend": signal["direction"].upper(),
        "reason": _reason(signal, close, vwap, sma_20),
    }


def _to_timeframe(candles: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    frame = candles.copy().sort_values("timestamp")
    if timeframe == "5m":
        return frame.reset_index(drop=True)

    rule = {"15m": "15min", "30m": "30min"}[timeframe]
    indexed = frame.assign(timestamp=pd.to_datetime(frame["timestamp"])).set_index("timestamp")
    resampled = indexed.resample(rule).agg(
        {
            "symbol": "last",
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    return resampled.dropna(subset=["open", "high", "low", "close"]).reset_index()


def _action(
    score: float,
    close: float,
    vwap: float | None,
    sma_20: float | None,
    macd_hist: float | None,
    passes_filters: bool,
) -> str:
    above_vwap = vwap is None or close >= vwap
    above_sma = sma_20 is None or close >= sma_20
    macd_ok = macd_hist is None or macd_hist >= 0
    if passes_filters and score >= 60 and above_vwap and above_sma and macd_ok:
        return "BUY"
    if score <= 42 or (sma_20 is not None and close < sma_20 and not macd_ok):
        return "SELL"
    return "HOLD"


def _reason(signal: dict[str, Any], close: float, vwap: float | None, sma_20: float | None) -> str:
    reasons = list(signal.get("reasons") or [])[:2]
    if vwap is not None:
        reasons.append("price above VWAP" if close >= vwap else "price below VWAP")
    if sma_20 is not None:
        reasons.append("price above SMA20" if close >= sma_20 else "price below SMA20")
    if signal.get("disqualifiers"):
        reasons.extend(signal["disqualifiers"])
    return "; ".join(reasons) or "Waiting for stronger intraday confirmation."


def _suggested_quantity(cash: float, close: float, max_position_pct: float, lot_size: int) -> int:
    if close <= 0 or cash <= 0:
        return 0
    raw_quantity = int((cash * max_position_pct) // close)
    return max(0, (raw_quantity // lot_size) * lot_size)


def _risk_distance(close: float, atr: float | None) -> float:
    return max((atr or 0.0) * 1.2, close * 0.006, 0.05)


def _trail_distance(close: float, atr: float | None) -> float:
    return max((atr or 0.0) * 0.9, close * 0.004, 0.05)


def _optional_float(value: Any) -> float | None:
    if pd.isna(value):
        return None
    return float(value)


def _stock_name(symbol: str) -> str:
    return symbol.removesuffix(".NS")
