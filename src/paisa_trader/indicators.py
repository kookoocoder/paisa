from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def zscore(series: pd.Series, window: int) -> pd.Series:
    mean = series.rolling(window=window, min_periods=window).mean()
    std = series.rolling(window=window, min_periods=window).std(ddof=0)
    return (series - mean) / std.replace(0, np.nan)


def true_range(df: pd.DataFrame) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift()
    ranges = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    return true_range(df).rolling(window=window, min_periods=window).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.astype(float).diff()
    gain = delta.clip(lower=0).rolling(window, min_periods=window).mean()
    loss = (-delta.clip(upper=0)).rolling(window, min_periods=window).mean()
    rs = gain / loss.replace(0, np.nan)
    value = 100 - (100 / (1 + rs))
    value = value.where(~((loss == 0) & (gain > 0)), 100)
    value = value.where(~((gain == 0) & (loss > 0)), 0)
    value = value.where(~((gain == 0) & (loss == 0)), 50)
    return value


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    fast_ema = ema(series.astype(float), fast)
    slow_ema = ema(series.astype(float), slow)
    line = fast_ema - slow_ema
    signal_line = ema(line, signal)
    hist = line - signal_line
    return line, signal_line, hist


def bollinger_bands(
    series: pd.Series,
    window: int = 20,
    width: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = sma(series.astype(float), window)
    std = series.astype(float).rolling(window, min_periods=window).std(ddof=0)
    return mid - width * std, mid, mid + width * std


def bollinger_bandwidth(
    lower: pd.Series,
    mid: pd.Series,
    upper: pd.Series,
) -> pd.Series:
    return (upper - lower) / mid.replace(0, np.nan)


def rolling_volatility(series: pd.Series, window: int = 20) -> pd.Series:
    returns = series.astype(float).pct_change()
    return returns.rolling(window, min_periods=window).std(ddof=0)


def roc(series: pd.Series, window: int = 10) -> pd.Series:
    return series.astype(float).pct_change(window)


def stochastic(
    df: pd.DataFrame,
    window: int = 14,
    smooth: int = 3,
) -> tuple[pd.Series, pd.Series]:
    low_min = df["low"].astype(float).rolling(window, min_periods=window).min()
    high_max = df["high"].astype(float).rolling(window, min_periods=window).max()
    k = 100 * (df["close"].astype(float) - low_min) / (high_max - low_min).replace(0, np.nan)
    d = k.rolling(smooth, min_periods=smooth).mean()
    return k, d


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.astype(float).diff()).fillna(0)
    return (direction * volume.astype(float)).cumsum()


def session_vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)) / 3
    volume = df["volume"].astype(float)
    if "timestamp" not in df:
        return (typical * volume).cumsum() / volume.replace(0, np.nan).cumsum()
    sessions = pd.to_datetime(df["timestamp"]).dt.date
    pv = (typical * volume).groupby(sessions).cumsum()
    vv = volume.groupby(sessions).cumsum().replace(0, np.nan)
    return pv / vv


def donchian_channels(df: pd.DataFrame, window: int = 20) -> tuple[pd.Series, pd.Series]:
    high = df["high"].astype(float).rolling(window, min_periods=window).max()
    low = df["low"].astype(float).rolling(window, min_periods=window).min()
    return high, low


def dmi(df: pd.DataFrame, window: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    tr = true_range(df)
    atr_sum = tr.rolling(window, min_periods=window).sum().replace(0, np.nan)
    plus_di = 100 * plus_dm.rolling(window, min_periods=window).sum() / atr_sum
    minus_di = 100 * minus_dm.rolling(window, min_periods=window).sum() / atr_sum
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.rolling(window, min_periods=window).mean()
    return plus_di, minus_di, adx
