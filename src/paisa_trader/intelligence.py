from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FilterConfig:
    min_volume: float = 100_000
    max_spread_bps: float = 25.0
    min_signal_score: float = 55.0


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window, min_periods=window).mean()
    loss = (-delta.clip(upper=0)).rolling(window, min_periods=window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    fast = ema(series, 12)
    slow = ema(series, 26)
    line = fast - slow
    signal = ema(line, 9)
    hist = line - signal
    return line, signal, hist


def bollinger(series: pd.Series, window: int = 20, width: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std(ddof=0)
    return mid - width * std, mid, mid + width * std


def enrich_indicators(candles: pd.DataFrame) -> pd.DataFrame:
    out = candles.copy().sort_values("timestamp").reset_index(drop=True)
    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    volume = out["volume"].astype(float)

    out["return_1"] = close.pct_change()
    out["return_5"] = close.pct_change(5)
    out["sma_10"] = close.rolling(10, min_periods=10).mean()
    out["sma_30"] = close.rolling(30, min_periods=30).mean()
    out["ema_12"] = ema(close, 12)
    out["ema_26"] = ema(close, 26)
    out["rsi_14"] = rsi(close, 14)
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(close)
    out["bb_low"], out["bb_mid"], out["bb_high"] = bollinger(close)
    typical = (high + low + close) / 3
    out["vwap_proxy"] = (typical * volume).cumsum() / volume.replace(0, np.nan).cumsum()
    out["range_pct"] = ((high - low) / close.replace(0, np.nan)) * 100
    out["volume_sma_20"] = volume.rolling(20, min_periods=1).mean()
    out["relative_volume"] = volume / out["volume_sma_20"].replace(0, np.nan)
    out["estimated_spread_bps"] = (out["range_pct"].rolling(5, min_periods=1).mean().clip(lower=0.03) * 4).clip(3, 80)
    return out


def estimate_depth(last_row: pd.Series, levels: int = 5) -> pd.DataFrame:
    close = float(last_row["close"])
    spread_bps = float(last_row.get("estimated_spread_bps", 10.0))
    volume = float(last_row.get("volume", 0) or 0)
    base_qty = max(1, int(volume * 0.002))
    half_spread = close * spread_bps / 10_000 / 2
    tick = max(0.05, close * 0.0001)
    rows = []
    for level in range(1, levels + 1):
        step = half_spread + tick * (level - 1)
        qty = int(base_qty / (level ** 0.8))
        rows.append({"level": level, "side": "bid", "price": round(close - step, 2), "quantity": qty})
        rows.append({"level": level, "side": "ask", "price": round(close + step, 2), "quantity": qty})
    return pd.DataFrame(rows)


def score_next_move(enriched: pd.DataFrame, filter_cfg: FilterConfig | None = None) -> dict[str, Any]:
    filter_cfg = filter_cfg or FilterConfig()
    last = enriched.iloc[-1]
    score = 50.0
    reasons: list[str] = []

    if pd.notna(last.get("sma_10")) and pd.notna(last.get("sma_30")):
        if last["sma_10"] > last["sma_30"]:
            score += 12
            reasons.append("short SMA is above long SMA")
        else:
            score -= 12
            reasons.append("short SMA is below long SMA")

    rsi_value = last.get("rsi_14")
    if pd.notna(rsi_value):
        if rsi_value < 35:
            score += 8
            reasons.append("RSI is near oversold")
        elif rsi_value > 70:
            score -= 10
            reasons.append("RSI is overbought")
        elif 45 <= rsi_value <= 60:
            score += 3
            reasons.append("RSI is neutral-positive")

    hist = last.get("macd_hist")
    if pd.notna(hist):
        if hist > 0:
            score += 8
            reasons.append("MACD histogram is positive")
        else:
            score -= 8
            reasons.append("MACD histogram is negative")

    ret5 = last.get("return_5")
    if pd.notna(ret5):
        if ret5 > 0:
            score += 5
            reasons.append("5-bar return is positive")
        else:
            score -= 5
            reasons.append("5-bar return is negative")

    rel_vol = last.get("relative_volume")
    if pd.notna(rel_vol) and rel_vol > 1.2:
        score += 4
        reasons.append("relative volume is elevated")

    volume_ok = float(last.get("volume", 0) or 0) >= filter_cfg.min_volume
    spread_ok = float(last.get("estimated_spread_bps", 999) or 999) <= filter_cfg.max_spread_bps
    score = max(0.0, min(100.0, score))

    if score >= 62 and volume_ok and spread_ok:
        direction = "bullish"
        action = "paper_long_candidate"
    elif score <= 38 and volume_ok and spread_ok:
        direction = "bearish"
        action = "avoid_long_or_exit"
    else:
        direction = "neutral"
        action = "no_trade"

    disqualifiers = []
    if not volume_ok:
        disqualifiers.append("volume below filter")
    if not spread_ok:
        disqualifiers.append("estimated spread too wide")

    return {
        "direction": direction,
        "action": action,
        "score": round(score, 2),
        "confidence": round(abs(score - 50) / 50, 3),
        "reasons": reasons,
        "disqualifiers": disqualifiers,
        "passes_filters": volume_ok and spread_ok,
        "paper_trade_candidate": action != "no_trade" and volume_ok and spread_ok and score >= filter_cfg.min_signal_score,
    }


def ai_market_snapshot(
    symbol: str,
    enriched: pd.DataFrame,
    strategy_target: float,
    filter_cfg: FilterConfig | None = None,
) -> dict[str, Any]:
    filter_cfg = filter_cfg or FilterConfig()
    last = enriched.iloc[-1]
    depth = estimate_depth(last)
    signal = score_next_move(enriched, filter_cfg)

    indicator_keys = [
        "close",
        "volume",
        "return_1",
        "return_5",
        "sma_10",
        "sma_30",
        "rsi_14",
        "macd",
        "macd_signal",
        "macd_hist",
        "bb_low",
        "bb_mid",
        "bb_high",
        "vwap_proxy",
        "relative_volume",
        "estimated_spread_bps",
    ]
    indicators = {
        key: (None if pd.isna(last.get(key)) else round(float(last.get(key)), 6))
        for key in indicator_keys
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_source": "yfinance delayed candles; depth is synthetic estimate, not exchange DOM",
        "symbol": symbol,
        "last_bar_time": pd.Timestamp(last["timestamp"]).isoformat(),
        "strategy_target_position": strategy_target,
        "next_move": signal,
        "filters": {
            "min_volume": filter_cfg.min_volume,
            "max_spread_bps": filter_cfg.max_spread_bps,
            "min_signal_score": filter_cfg.min_signal_score,
        },
        "indicators": indicators,
        "synthetic_depth": depth.to_dict(orient="records"),
    }
