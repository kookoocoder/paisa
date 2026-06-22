import numpy as np
import pandas as pd
import pytest

from paisa_trader.ml_models import FEATURE_COLUMNS, engineer_features, predict, train_models


def sample_ml_candles(rows: int = 180) -> pd.DataFrame:
    index = pd.date_range("2026-01-01 09:15", periods=rows, freq="5min", tz="Asia/Kolkata")
    trend = np.linspace(100, 130, rows)
    cycle = np.sin(np.arange(rows) / 3.0) * 1.5
    close = trend + cycle
    open_ = close - 0.2
    high = close + 0.8
    low = close - 0.8
    volume = np.linspace(120_000, 180_000, rows)
    return pd.DataFrame(
        {
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        },
        index=index,
    )


def test_engineer_features_outputs_expected_columns():
    features = engineer_features(sample_ml_candles())

    for column in FEATURE_COLUMNS + ["label"]:
        assert column in features.columns
    assert features[FEATURE_COLUMNS + ["label"]].isna().sum().sum() == 0


def test_train_models_returns_accuracy(tmp_path):
    _require_ml_deps()

    result = train_models(sample_ml_candles(), "RELIANCE", save_dir=str(tmp_path))

    assert result["xgb_accuracy"] > 0.4
    assert result["lgbm_accuracy"] > 0.4
    assert result["n_train"] > result["n_test"] > 0


def test_predict_returns_direction_and_confidence(tmp_path):
    _require_ml_deps()

    candles = sample_ml_candles()
    train_models(candles, "RELIANCE", save_dir=str(tmp_path))
    result = predict(candles, "RELIANCE", model_dir=str(tmp_path))

    assert result["direction"] in ["UP", "DOWN"]
    assert 0.0 <= result["confidence"] <= 1.0
    assert 0.0 <= result["xgb_prob"] <= 1.0
    assert 0.0 <= result["lgbm_prob"] <= 1.0


def _require_ml_deps() -> None:
    try:
        import lightgbm  # noqa: F401
        import xgboost  # noqa: F401
    except Exception as exc:
        pytest.skip(f"ML dependencies unavailable: {exc}")
