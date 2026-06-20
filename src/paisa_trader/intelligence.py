from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from .indicators import (
    atr,
    bollinger_bands,
    bollinger_bandwidth,
    dmi,
    donchian_channels,
    ema,
    macd,
    obv,
    roc,
    session_phase,
    rolling_volatility,
    rsi,
    session_vwap,
    sma,
    stochastic,
)
from .snapshot import MarketSnapshot


@dataclass(frozen=True)
class FilterConfig:
    min_volume: float = 100_000
    max_spread_bps: float = 25.0
    min_signal_score: float = 55.0


REGIME_WEIGHTS = {
    "TREND_UP": {
        "trend": 0.40,
        "momentum": 0.35,
        "mean_reversion": 0.05,
        "participation": 0.15,
        "volatility": 0.05,
    },
    "TREND_DOWN": {
        "trend": 0.40,
        "momentum": 0.35,
        "mean_reversion": 0.05,
        "participation": 0.15,
        "volatility": 0.05,
    },
    "RANGE": {
        "trend": 0.10,
        "momentum": 0.10,
        "mean_reversion": 0.50,
        "participation": 0.20,
        "volatility": 0.10,
    },
    "HIGH_VOL": {
        "trend": 0.20,
        "momentum": 0.20,
        "mean_reversion": 0.10,
        "participation": 0.15,
        "volatility": 0.35,
    },
    "LOW_LIQUIDITY": {
        "trend": 0.25,
        "momentum": 0.25,
        "mean_reversion": 0.20,
        "participation": 0.25,
        "volatility": 0.05,
    },
    "UNKNOWN": {
        "trend": 0.35,
        "momentum": 0.25,
        "mean_reversion": 0.15,
        "participation": 0.15,
        "volatility": 0.10,
    },
}

for regime_name, weights in REGIME_WEIGHTS.items():
    assert abs(sum(weights.values()) - 1.0) < 1e-9, f"{regime_name} weights must sum to 1.0"


def enrich_indicators(candles: pd.DataFrame) -> pd.DataFrame:
    out = candles.copy().sort_values("timestamp").reset_index(drop=True)
    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    volume = out["volume"].astype(float)

    out["return_1"] = close.pct_change()
    out["return_5"] = close.pct_change(5)
    for lag in [1, 3, 5]:
        out[f"return_lag_{lag}"] = close.pct_change(lag).replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(-0.10, 0.10)
    out["log_return_1"] = np.log(close / close.shift())
    out["sma_10"] = sma(close, 10)
    out["sma_20"] = sma(close, 20)
    out["sma_30"] = sma(close, 30)
    out["sma_50"] = sma(close, 50)
    out["ema_12"] = ema(close, 12)
    out["ema_26"] = ema(close, 26)
    out["rsi_14"] = rsi(close, 14)
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(close)
    out["bb_low"], out["bb_mid"], out["bb_high"] = bollinger_bands(close)
    band_width = (out["bb_high"] - out["bb_low"]).replace(0, np.nan)
    out["bb_pct"] = ((close - out["bb_low"]) / band_width).clip(0, 1)
    out["bb_bandwidth"] = bollinger_bandwidth(out["bb_low"], out["bb_mid"], out["bb_high"])
    out["atr_14"] = atr(out, 14)
    out["atr_pct"] = out["atr_14"] / close.replace(0, np.nan) * 100
    out["realized_vol_20"] = rolling_volatility(close, 20)
    out["roc_10"] = roc(close, 10)
    out["stoch_k"], out["stoch_d"] = stochastic(out, 14, 3)
    out["obv"] = obv(close, volume)
    out["obv_slope_10"] = out["obv"].diff(10) / volume.rolling(10, min_periods=10).sum().replace(0, np.nan)
    out["vwap_session"] = session_vwap(out)
    out["vwap_proxy"] = out["vwap_session"]
    out["vwap_distance_pct"] = (close - out["vwap_session"]) / out["vwap_session"].replace(0, np.nan) * 100
    out["donchian_high_20"], out["donchian_low_20"] = donchian_channels(out, 20)
    out["plus_di_14"], out["minus_di_14"], out["adx_14"] = dmi(out, 14)
    out["sma_spread_pct"] = (out["sma_10"] - out["sma_30"]) / close.replace(0, np.nan) * 100
    out["ema_spread_pct"] = (out["ema_12"] - out["ema_26"]) / close.replace(0, np.nan) * 100
    out["sma_20_slope_5"] = out["sma_20"].diff(5) / out["sma_20"].shift(5).replace(0, np.nan) * 100
    out["range_pct"] = ((high - low) / close.replace(0, np.nan)) * 100
    out["volume_sma_20"] = volume.rolling(20, min_periods=1).mean()
    out["relative_volume"] = volume / out["volume_sma_20"].replace(0, np.nan)
    out["volume_score"] = ((out["relative_volume"] - 0.5) / 2).clip(0, 1)
    out["estimated_spread_bps"] = (out["range_pct"].rolling(5, min_periods=1).mean().clip(lower=0.03) * 4).clip(3, 80)
    session_features = out["timestamp"].map(session_phase).apply(pd.Series)
    out = pd.concat([out, session_features], axis=1)
    factors = out.apply(_factor_scores_from_row, axis=1, result_type="expand")
    out = pd.concat([out, factors], axis=1)
    out["regime"] = out.apply(_market_regime_from_row, axis=1)
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


def _factor_scores_from_row(row: pd.Series) -> dict[str, float]:
    trend = _clip(
        0.45 * _scaled(row.get("ema_spread_pct"), 0.5)
        + 0.25 * _scaled(row.get("sma_spread_pct"), 0.6)
        + 0.20 * _scaled(row.get("sma_20_slope_5"), 0.4)
        + 0.10 * _dmi_bias(row),
    )
    momentum = _clip(
        0.35 * _scaled(row.get("roc_10"), 0.02)
        + 0.30 * _scaled(row.get("macd_hist"), max(abs(_num(row.get("close"))) * 0.002, 0.01))
        + 0.20 * _scaled((_num(row.get("rsi_14")) - 50) if pd.notna(row.get("rsi_14")) else np.nan, 25)
        + 0.15 * _scaled((_num(row.get("stoch_k")) - 50) if pd.notna(row.get("stoch_k")) else np.nan, 35),
    )
    mean_reversion = _clip(
        0.50 * _mean_reversion_bias(row)
        - 0.30 * _scaled(row.get("vwap_distance_pct"), 1.0)
        - 0.20 * _scaled(row.get("sma_spread_pct"), 1.0),
    )
    participation = _clip(
        0.55 * _scaled((_num(row.get("relative_volume")) - 1.0) if pd.notna(row.get("relative_volume")) else np.nan, 1.0)
        + 0.45 * _scaled(row.get("obv_slope_10"), 0.4),
    )
    volatility = _volatility_quality(row)
    return {
        "trend_score": trend,
        "momentum_score": momentum,
        "mean_reversion_score": mean_reversion,
        "participation_score": participation,
        "volatility_score": volatility,
    }


def _market_regime_from_row(row: pd.Series) -> str:
    rel_volume = _num(row.get("relative_volume"), 1.0)
    atr_pct = _num(row.get("atr_pct"), 0.0)
    adx = _num(row.get("adx_14"), 0.0)
    ema_spread = _num(row.get("ema_spread_pct"), 0.0)
    if rel_volume < 0.35:
        return "LOW_LIQUIDITY"
    if atr_pct > 2.5:
        return "HIGH_VOL"
    if adx >= 20 and ema_spread > 0:
        return "TREND_UP"
    if adx >= 20 and ema_spread < 0:
        return "TREND_DOWN"
    return "RANGE"


def _factor_reasons(factors: dict[str, float], regime: str) -> list[str]:
    labels = {
        "trend_score": "trend",
        "momentum_score": "momentum",
        "mean_reversion_score": "mean reversion",
        "participation_score": "volume participation",
        "volatility_score": "volatility quality",
    }
    ranked = sorted(factors.items(), key=lambda item: abs(item[1]), reverse=True)
    reasons = [f"{labels[key]} factor {value:+.2f}" for key, value in ranked if abs(value) >= 0.15][:4]
    reasons.append(f"market regime is {regime}")
    return reasons


def _mean_reversion_bias(row: pd.Series) -> float:
    bb_pct = row.get("bb_pct")
    rsi_value = row.get("rsi_14")
    if pd.isna(bb_pct) or pd.isna(rsi_value):
        return 0.0
    if float(bb_pct) <= 0.2 and float(rsi_value) <= 45:
        return 1.0
    if float(bb_pct) >= 0.8 and float(rsi_value) >= 55:
        return -1.0
    return _clip((0.5 - float(bb_pct)) * 1.2)


def _dmi_bias(row: pd.Series) -> float:
    plus_di = row.get("plus_di_14")
    minus_di = row.get("minus_di_14")
    if pd.isna(plus_di) or pd.isna(minus_di):
        return 0.0
    return _clip((float(plus_di) - float(minus_di)) / 35.0)


def _volatility_quality(row: pd.Series) -> float:
    atr_pct = row.get("atr_pct")
    bandwidth = row.get("bb_bandwidth")
    if pd.isna(atr_pct):
        return 0.0
    atr_value = float(atr_pct)
    if atr_value <= 0.05:
        atr_score = -0.4
    elif atr_value <= 1.5:
        atr_score = 0.4
    elif atr_value <= 2.5:
        atr_score = 0.0
    else:
        atr_score = -0.5
    bandwidth_score = 0.0 if pd.isna(bandwidth) else _clip((0.12 - float(bandwidth)) / 0.12)
    return _clip(0.7 * atr_score + 0.3 * bandwidth_score)


def _scaled(value: Any, scale: float) -> float:
    if pd.isna(value) or scale == 0:
        return 0.0
    return _clip(float(value) / scale)


def _clip(value: float, lower: float = -1.0, upper: float = 1.0) -> float:
    return float(max(lower, min(upper, value)))


def _num(value: Any, default: float = 0.0) -> float:
    if pd.isna(value):
        return default
    return float(value)


def score_next_move(enriched: pd.DataFrame, filter_cfg: FilterConfig | None = None) -> dict[str, Any]:
    filter_cfg = filter_cfg or FilterConfig()
    last = enriched.iloc[-1]
    factor_scores = _factor_scores_from_row(last)
    regime = str(last.get("regime") or _market_regime_from_row(last))
    active_weights = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["UNKNOWN"])
    weighted = (
        active_weights["trend"] * factor_scores["trend_score"]
        + active_weights["momentum"] * factor_scores["momentum_score"]
        + active_weights["mean_reversion"] * factor_scores["mean_reversion_score"]
        + active_weights["participation"] * factor_scores["participation_score"]
        + active_weights["volatility"] * factor_scores["volatility_score"]
    )
    score = max(0.0, min(100.0, 50.0 + weighted * 50.0))
    volume_ok = float(last.get("volume", 0) or 0) >= filter_cfg.min_volume
    spread_ok = float(last.get("estimated_spread_bps", 999) or 999) <= filter_cfg.max_spread_bps

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
    if regime == "LOW_LIQUIDITY":
        disqualifiers.append("low-liquidity regime")
    reasons = _factor_reasons(factor_scores, regime)
    confidence = round(abs(score - 50) / 50, 3)

    return {
        "direction": direction,
        "action": action,
        "score": round(score, 2),
        "confidence": confidence,
        "reasons": reasons,
        "disqualifiers": disqualifiers,
        "factor_scores": {key: round(float(value), 4) for key, value in factor_scores.items()},
        "regime": regime,
        "active_regime": regime,
        "active_weights": active_weights.copy(),
        "passes_filters": volume_ok and spread_ok,
        "paper_trade_candidate": (
            action != "no_trade"
            and volume_ok
            and spread_ok
            and regime != "LOW_LIQUIDITY"
            and score >= filter_cfg.min_signal_score
        ),
    }


def ai_market_snapshot(
    symbol: str,
    enriched: pd.DataFrame,
    strategy_target: float,
    filter_cfg: FilterConfig | None = None,
    *,
    active_strategy: str = "unknown",
    equity: float = 0.0,
    cash: float = 0.0,
    open_positions: list[dict[str, Any]] | None = None,
    recent_fills: list[dict[str, Any]] | None = None,
    unrealised_pnl: float = 0.0,
    total_trades: int = 0,
    win_rate: float = 0.0,
) -> dict[str, Any]:
    filter_cfg = filter_cfg or FilterConfig()
    last = enriched.iloc[-1]
    depth = estimate_depth(last)
    signal = score_next_move(enriched, filter_cfg)
    snapshot = build_market_snapshot(
        symbol=symbol,
        enriched=enriched,
        strategy_target=strategy_target,
        filter_cfg=filter_cfg,
        active_strategy=active_strategy,
        equity=equity,
        cash=cash,
        open_positions=open_positions,
        recent_fills=recent_fills,
        unrealised_pnl=unrealised_pnl,
        total_trades=total_trades,
        win_rate=win_rate,
        depth=depth,
        signal=signal,
    )

    indicator_keys = [
        "close",
        "volume",
        "return_1",
        "return_5",
        "sma_10",
        "sma_20",
        "sma_30",
        "sma_50",
        "rsi_14",
        "macd",
        "macd_signal",
        "macd_hist",
        "bb_low",
        "bb_mid",
        "bb_high",
        "bb_pct",
        "atr_14",
        "atr_pct",
        "realized_vol_20",
        "roc_10",
        "stoch_k",
        "stoch_d",
        "adx_14",
        "plus_di_14",
        "minus_di_14",
        "bb_bandwidth",
        "vwap_proxy",
        "vwap_distance_pct",
        "obv_slope_10",
        "relative_volume",
        "volume_score",
        "estimated_spread_bps",
        "session_phase_open",
        "session_phase_close",
        "session_progress",
        "minutes_since_open",
        "return_lag_1",
        "return_lag_3",
        "return_lag_5",
    ]
    indicators = {
        key: (
            None
            if pd.isna(last.get(key))
            else bool(last.get(key))
            if key in {"session_phase_open", "session_phase_close"}
            else round(float(last.get(key)), 6)
        )
        for key in indicator_keys
    }
    payload = snapshot.to_dict()
    payload.update(
        {
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
            "depth_levels": depth.to_dict(orient="records"),
        }
    )
    return payload


def build_market_snapshot(
    symbol: str,
    enriched: pd.DataFrame,
    strategy_target: float,
    filter_cfg: FilterConfig | None = None,
    *,
    active_strategy: str = "unknown",
    equity: float = 0.0,
    cash: float = 0.0,
    open_positions: list[dict[str, Any]] | None = None,
    recent_fills: list[dict[str, Any]] | None = None,
    unrealised_pnl: float = 0.0,
    total_trades: int = 0,
    win_rate: float = 0.0,
    depth: pd.DataFrame | None = None,
    signal: dict[str, Any] | None = None,
) -> MarketSnapshot:
    filter_cfg = filter_cfg or FilterConfig()
    last = enriched.iloc[-1]
    depth = estimate_depth(last) if depth is None else depth
    signal = score_next_move(enriched, filter_cfg) if signal is None else signal
    bid_rows = depth[depth["side"] == "bid"]
    ask_rows = depth[depth["side"] == "ask"]
    best_bid = float(bid_rows["price"].max()) if not bid_rows.empty else float(last["close"])
    best_ask = float(ask_rows["price"].min()) if not ask_rows.empty else float(last["close"])
    bid_qty = int(bid_rows["quantity"].sum()) if not bid_rows.empty else 0
    ask_qty = int(ask_rows["quantity"].sum()) if not ask_rows.empty else 0
    total_depth = bid_qty + ask_qty
    imbalance = ((bid_qty - ask_qty) / total_depth) if total_depth else 0.0
    score = (float(signal["score"]) - 50.0) / 50.0
    strategy_signal = "BUY" if strategy_target > 0 else "HOLD"

    return MarketSnapshot(
        symbol=symbol,
        timestamp=pd.Timestamp(last["timestamp"]).to_pydatetime(),
        bar_index=int(enriched.index[-1]),
        open=_float(last.get("open"), 0.0),
        high=_float(last.get("high"), 0.0),
        low=_float(last.get("low"), 0.0),
        close=_float(last.get("close"), 0.0),
        volume=int(_float(last.get("volume"), 0.0)),
        rsi_14=_optional_float(last.get("rsi_14")),
        macd_line=_optional_float(last.get("macd")),
        macd_signal=_optional_float(last.get("macd_signal")),
        macd_hist=_optional_float(last.get("macd_hist")),
        bb_upper=_optional_float(last.get("bb_high")),
        bb_mid=_optional_float(last.get("bb_mid")),
        bb_lower=_optional_float(last.get("bb_low")),
        bb_pct=_optional_float(last.get("bb_pct")),
        sma_20=_optional_float(last.get("sma_20")),
        sma_50=_optional_float(last.get("sma_50")),
        atr_14=_optional_float(last.get("atr_14")),
        volume_ratio=_optional_float(last.get("relative_volume")),
        volume_score=_float(last.get("volume_score"), 0.0),
        session_phase_open=bool(last.get("session_phase_open", False)),
        session_phase_close=bool(last.get("session_phase_close", False)),
        session_progress=_float(last.get("session_progress"), 0.5),
        minutes_since_open=int(_float(last.get("minutes_since_open"), 0.0)),
        return_lag_1=_float(last.get("return_lag_1"), 0.0),
        return_lag_3=_float(last.get("return_lag_3"), 0.0),
        return_lag_5=_float(last.get("return_lag_5"), 0.0),
        bid=best_bid,
        ask=best_ask,
        spread_pct=((best_ask - best_bid) / max(_float(last.get("close"), 0.0), 0.01)) * 100,
        synthetic_depth={"bid_qty": bid_qty, "ask_qty": ask_qty, "imbalance": round(imbalance, 4)},
        next_move_score=round(score, 3),
        next_move_label=_next_move_label(score),
        confidence=float(signal["confidence"]),
        signal_components={
            "direction": signal["direction"],
            "action": signal["action"],
            "raw_score": signal["score"],
            "reasons": signal["reasons"],
            "disqualifiers": signal["disqualifiers"],
            "factor_scores": signal.get("factor_scores", {}),
            "regime": signal.get("regime", str(last.get("regime", "UNKNOWN"))),
            "active_weights": signal.get("active_weights", {}),
        },
        market_regime=str(signal.get("regime", last.get("regime", "UNKNOWN"))),
        factor_scores={key: float(value) for key, value in signal.get("factor_scores", {}).items()},
        active_strategy=active_strategy,
        strategy_signal=strategy_signal,
        intelligence_gate=bool(signal["paper_trade_candidate"]),
        equity=float(equity),
        cash=float(cash),
        open_positions=open_positions or [],
        recent_fills=recent_fills or [],
        unrealised_pnl=float(unrealised_pnl),
        total_trades=int(total_trades),
        win_rate=float(win_rate),
        depth_levels=depth.to_dict(orient="records"),
    )


def _optional_float(value: Any) -> float | None:
    if pd.isna(value):
        return None
    return float(value)


def _float(value: Any, default: float) -> float:
    if pd.isna(value):
        return default
    return float(value)


def _next_move_label(score: float) -> str:
    if score >= 0.6:
        return "STRONG_BUY"
    if score >= 0.2:
        return "BUY"
    if score <= -0.6:
        return "STRONG_SELL"
    if score <= -0.2:
        return "SELL"
    return "NEUTRAL"
