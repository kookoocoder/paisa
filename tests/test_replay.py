import asyncio

import pandas as pd

from paisa_trader.config import BrokerConfig
from paisa_trader.replay import ReplayConfig, ReplayEngine


def sample_intraday(symbol: str = "TEST.NS", rows: int = 40) -> pd.DataFrame:
    dates = pd.date_range("2024-06-01 09:15", periods=rows, freq="5min")
    prices = [100 + i * 0.2 for i in range(rows)]
    return pd.DataFrame(
        {
            "timestamp": dates,
            "symbol": symbol,
            "open": prices,
            "high": [p + 0.5 for p in prices],
            "low": [p - 0.5 for p in prices],
            "close": prices,
            "volume": [250_000] * rows,
        }
    )


def test_replay_engine_steps_and_publishes_state():
    candles = {"TEST.NS": sample_intraday()}
    config = ReplayConfig(
        symbols=["TEST.NS"],
        period="5d",
        interval="5minute",
        strategy="ma-cross",
        tick_seconds=0.01,
        force_refresh=False,
        use_intelligence_filter=False,
        broker=BrokerConfig(initial_cash=100_000, spread_bps=0, slippage_bps=0),
    )
    engine = ReplayEngine(config, candles_by_symbol=candles)

    async def run():
        await engine.prepare()
        await engine.step()
        return await engine.state()

    state = asyncio.run(run())
    assert state["running"] is False
    assert state["symbols"]["TEST.NS"]["cursor"] >= 0
    assert state["portfolio"]["equity"] > 0
    assert "events" in state


def test_replay_state_includes_intelligence_flag():
    candles = {"TEST.NS": sample_intraday()}
    config = ReplayConfig(
        symbols=["TEST.NS"],
        force_refresh=False,
        use_intelligence_filter=True,
    )
    engine = ReplayEngine(config, candles_by_symbol=candles)

    async def run():
        await engine.prepare()
        return await engine.state()

    state = asyncio.run(run())
    assert state["config"]["use_intelligence_filter"] is True
