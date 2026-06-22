from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .indicators import atr, bollinger_bands, ema, rsi


FEATURE_COLUMNS = [
    "rsi_14",
    "rsi_7",
    "ema_9",
    "ema_21",
    "ema_diff",
    "bb_width",
    "bb_position",
    "atr_14",
    "volume_ratio",
    "candle_body",
    "high_low_range",
    "return_1",
    "return_3",
    "return_5",
]


def engineer_features(candles: pd.DataFrame) -> pd.DataFrame:
    """Build next-bar direction features from canonical OHLCV candles.

    Args:
        candles: DataFrame with ``Open``, ``High``, ``Low``, ``Close``, and ``Volume`` columns.

    Returns:
        A DataFrame containing engineered feature columns and the ``label`` target.

    Example:
        ``engineer_features(candles)["label"].tail(1)`` returns the latest training label.
    """
    required = {"Open", "High", "Low", "Close", "Volume"}
    missing = required - set(candles.columns)
    if missing:
        raise ValueError(f"Candle data missing required columns: {sorted(missing)}")

    out = candles.copy().sort_index()
    close = out["Close"].astype(float)
    open_ = out["Open"].astype(float)
    high = out["High"].astype(float)
    low = out["Low"].astype(float)
    volume = out["Volume"].astype(float)

    out["rsi_14"] = rsi(close, 14)
    out["rsi_7"] = rsi(close, 7)
    out["ema_9"] = ema(close, 9)
    out["ema_21"] = ema(close, 21)
    out["ema_diff"] = out["ema_9"] - out["ema_21"]
    bb_low, bb_mid, bb_high = bollinger_bands(close, 20, 2.0)
    bb_span = (bb_high - bb_low).replace(0, np.nan)
    out["bb_width"] = bb_span / bb_mid.replace(0, np.nan)
    out["bb_position"] = ((close - bb_low) / bb_span).clip(0, 1)
    lower_frame = pd.DataFrame({"high": high, "low": low, "close": close})
    out["atr_14"] = atr(lower_frame, 14)
    out["volume_ratio"] = volume / volume.rolling(20, min_periods=20).mean().replace(0, np.nan)
    out["candle_body"] = (close - open_).abs() / open_.replace(0, np.nan)
    out["high_low_range"] = high - low
    out["return_1"] = close.pct_change(1)
    out["return_3"] = close.pct_change(3)
    out["return_5"] = close.pct_change(5)
    out["label"] = (close.shift(-1) > close).astype(int)
    return out.replace([np.inf, -np.inf], np.nan).dropna(subset=FEATURE_COLUMNS + ["label"])


def train_models(candles: pd.DataFrame, symbol: str, save_dir: str = "models/") -> dict[str, Any]:
    """Train and cache XGBoost and LightGBM classifiers.

    Args:
        candles: Canonical OHLCV candle DataFrame.
        symbol: Human-readable symbol used for model filenames.
        save_dir: Directory where model pickle files are saved.

    Returns:
        Dict with accuracy metrics and train/test row counts.

    Example:
        ``train_models(candles, "RELIANCE")["xgb_accuracy"]`` returns holdout accuracy.
    """
    from joblib import dump
    from lightgbm import LGBMClassifier
    from sklearn.metrics import accuracy_score
    from xgboost import XGBClassifier

    features = engineer_features(candles)
    if len(features) < 40:
        raise ValueError("Need at least 40 feature rows to train ML models.")

    split = int(len(features) * 0.8)
    train = features.iloc[:split]
    test = features.iloc[split:]
    x_train = train[FEATURE_COLUMNS]
    y_train = train["label"].astype(int)
    x_test = test[FEATURE_COLUMNS]
    y_test = test["label"].astype(int)

    xgb = XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric="logloss",
        tree_method="hist",
        n_jobs=4,
    )
    lgbm = LGBMClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        num_leaves=31,
        device="cpu",
        verbose=-1,
    )
    xgb.fit(x_train, y_train)
    lgbm.fit(x_train, y_train)

    model_root = Path(save_dir)
    model_root.mkdir(parents=True, exist_ok=True)
    safe = _safe_model_symbol(symbol)
    dump(xgb, model_root / f"{safe}_xgb.pkl")
    dump(lgbm, model_root / f"{safe}_lgbm.pkl")
    return {
        "xgb_accuracy": float(accuracy_score(y_test, xgb.predict(x_test))),
        "lgbm_accuracy": float(accuracy_score(y_test, lgbm.predict(x_test))),
        "n_train": int(len(train)),
        "n_test": int(len(test)),
    }


def predict(candles: pd.DataFrame, symbol: str, model_dir: str = "models/") -> dict[str, Any]:
    """Predict the next-bar direction using cached or auto-trained models.

    Args:
        candles: Canonical OHLCV candle DataFrame.
        symbol: Human-readable symbol used for model filenames.
        model_dir: Directory containing cached models.

    Returns:
        Dict containing direction, ensemble confidence, and model probabilities.

    Example:
        ``predict(candles, "RELIANCE")["direction"]`` returns ``"UP"`` or ``"DOWN"``.
    """
    from joblib import load

    model_root = Path(model_dir)
    safe = _safe_model_symbol(symbol)
    xgb_path = model_root / f"{safe}_xgb.pkl"
    lgbm_path = model_root / f"{safe}_lgbm.pkl"
    if not xgb_path.exists() or not lgbm_path.exists():
        train_models(candles, symbol, str(model_root))

    xgb = load(xgb_path)
    lgbm = load(lgbm_path)
    features = engineer_features(candles)
    if features.empty:
        raise ValueError("No feature rows available for prediction.")
    x_last = features[FEATURE_COLUMNS].tail(1)
    xgb_prob = float(xgb.predict_proba(x_last)[0][1])
    lgbm_prob = float(lgbm.predict_proba(x_last)[0][1])
    ensemble = (xgb_prob + lgbm_prob) / 2
    direction = "UP" if ensemble >= 0.5 else "DOWN"
    confidence = ensemble if direction == "UP" else 1 - ensemble
    return {
        "direction": direction,
        "confidence": float(max(0.0, min(1.0, confidence))),
        "xgb_prob": xgb_prob,
        "lgbm_prob": lgbm_prob,
    }


def _safe_model_symbol(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", symbol.upper())
