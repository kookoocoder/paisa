import pandas as pd

from paisa_trader.indicators import (
    atr,
    bollinger_bands,
    donchian_channels,
    lag_returns,
    obv,
    rsi,
    session_phase,
    session_vwap,
    stochastic,
    true_range,
)


def sample_candles(rows=40):
    dates = pd.date_range("2024-01-01 09:15", periods=rows, freq="5min")
    prices = [100 + i for i in range(rows)]
    return pd.DataFrame(
        {
            "timestamp": dates,
            "symbol": ["TEST.NS"] * rows,
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": prices,
            "volume": [1000 + i * 10 for i in range(rows)],
        }
    )


def test_true_range_and_atr_use_gap_aware_range():
    candles = sample_candles(20)
    candles.loc[10, "high"] = 120

    tr = true_range(candles)
    result = atr(candles, 14)

    assert tr.iloc[10] == 11
    assert result.iloc[:13].isna().all()
    assert result.iloc[-1] > 0


def test_rsi_bounds_and_directional_extremes():
    up = pd.Series([100 + i for i in range(30)])
    down = pd.Series([130 - i for i in range(30)])

    assert rsi(up, 14).iloc[-1] == 100
    assert rsi(down, 14).iloc[-1] == 0


def test_session_vwap_resets_by_trading_date():
    candles = sample_candles(4)
    candles.loc[2:, "timestamp"] = pd.date_range("2024-01-02 09:15", periods=2, freq="5min")

    vwap = session_vwap(candles)

    first_day_typical = (candles.loc[0, "high"] + candles.loc[0, "low"] + candles.loc[0, "close"]) / 3
    second_day_typical = (candles.loc[2, "high"] + candles.loc[2, "low"] + candles.loc[2, "close"]) / 3
    assert vwap.iloc[0] == first_day_typical
    assert vwap.iloc[2] == second_day_typical


def test_volume_and_range_indicators_are_bounded_or_monotonic():
    candles = sample_candles(40)
    lower, mid, upper = bollinger_bands(candles["close"], 20)
    stoch_k, stoch_d = stochastic(candles, 14, 3)
    dc_high, dc_low = donchian_channels(candles, 20)
    obv_values = obv(candles["close"], candles["volume"])

    assert (upper.dropna() >= mid.dropna()).all()
    assert (mid.dropna() >= lower.dropna()).all()
    assert stoch_k.dropna().between(0, 100).all()
    assert stoch_d.dropna().between(0, 100).all()
    assert (dc_high.dropna() >= dc_low.dropna()).all()
    assert obv_values.iloc[-1] > obv_values.iloc[1]


def test_session_phase_open_period_from_ist_timestamp():
    phase = session_phase(pd.Timestamp("2024-01-01 09:30", tz="Asia/Kolkata"))

    assert phase["session_phase_open"] is True
    assert phase["session_phase_close"] is False
    assert phase["minutes_since_open"] == 15
    assert 0.0 <= phase["session_progress"] <= 1.0


def test_session_phase_mid_period_from_utc_timestamp():
    phase = session_phase(pd.Timestamp("2024-01-01 06:00", tz="UTC"))

    assert phase["session_phase_open"] is False
    assert phase["session_phase_close"] is False
    assert phase["minutes_since_open"] == 135
    assert 0.3 < phase["session_progress"] < 0.4


def test_session_phase_close_period():
    phase = session_phase(pd.Timestamp("2024-01-01 14:30", tz="Asia/Kolkata"))

    assert phase["session_phase_open"] is False
    assert phase["session_phase_close"] is True
    assert phase["minutes_since_open"] == 315


def test_session_phase_outside_session_uses_neutral_progress():
    phase = session_phase(pd.Timestamp("2024-01-01 16:00", tz="Asia/Kolkata"))

    assert phase["session_phase_open"] is False
    assert phase["session_phase_close"] is False
    assert phase["session_progress"] == 0.5


def test_lag_returns_are_nan_safe_and_clipped():
    values = lag_returns(pd.Series([100.0, 90.0, 300.0]), [1, 3])

    assert values["return_lag_1"] == 0.10
    assert values["return_lag_3"] == 0.0
