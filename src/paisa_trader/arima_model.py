from __future__ import annotations

import logging
from typing import Any

import pandas as pd


LOGGER = logging.getLogger(__name__)


def fit_arima(close_series: pd.Series, order: tuple = (2, 1, 2)) -> Any | None:
    """Fit an ARIMA model to recent close prices.

    Args:
        close_series: Close price series, oldest first.
        order: Statsmodels ARIMA order tuple.

    Returns:
        Fitted statsmodels result, or ``None`` when fitting fails.

    Example:
        ``fit_arima(candles["Close"])`` returns a fitted model for forecasting.
    """
    try:
        from statsmodels.tsa.arima.model import ARIMA

        close = pd.Series(close_series).astype(float).dropna().tail(100)
        if len(close) < 30:
            return None
        return ARIMA(close, order=order).fit()
    except Exception as exc:
        LOGGER.debug("ARIMA fit failed: %s", exc)
        return None


def arima_next_direction(close_series: pd.Series) -> dict[str, float | str | None]:
    """Forecast one step and convert it into a directional tiebreaker.

    Args:
        close_series: Close price series, oldest first.

    Returns:
        Dict with direction, forecast, and confidence.

    Example:
        ``arima_next_direction(candles["Close"])["direction"]`` returns ``"UP"`` or ``"DOWN"``.
    """
    close = pd.Series(close_series).astype(float).dropna().tail(100)
    model = fit_arima(close)
    if model is None or close.empty:
        return {"direction": "NEUTRAL", "forecast": None, "confidence": 0.0}
    try:
        forecast = float(model.forecast(steps=1).iloc[0])
    except Exception as exc:
        LOGGER.debug("ARIMA forecast failed: %s", exc)
        return {"direction": "NEUTRAL", "forecast": None, "confidence": 0.0}
    last_close = float(close.iloc[-1])
    if last_close <= 0:
        return {"direction": "NEUTRAL", "forecast": forecast, "confidence": 0.0}
    if forecast > last_close:
        direction = "UP"
    elif forecast < last_close:
        direction = "DOWN"
    else:
        direction = "NEUTRAL"
    confidence = min(abs(forecast - last_close) / last_close * 100, 1.0)
    return {"direction": direction, "forecast": forecast, "confidence": float(confidence)}
