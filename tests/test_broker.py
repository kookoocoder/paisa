import pandas as pd

from paisa_trader.broker import SimulatedBroker, estimate_costs
from paisa_trader.config import BrokerConfig, CostConfig


def test_estimate_costs_buy_and_sell_are_positive():
    cfg = CostConfig()
    assert estimate_costs(100_000, "BUY", cfg) > 0
    assert estimate_costs(100_000, "SELL", cfg) > estimate_costs(100_000, "BUY", cfg)


def test_simulated_broker_round_trip():
    broker = SimulatedBroker(BrokerConfig(initial_cash=100_000, spread_bps=0, slippage_bps=0))
    ts = pd.Timestamp("2024-01-01")
    buy = broker.submit_market_order(ts, "RELIANCE.NS", "BUY", 10, 100.0, "test")
    sell = broker.submit_market_order(ts, "RELIANCE.NS", "SELL", 10, 110.0, "test")
    assert buy is not None
    assert sell is not None
    assert broker.position("RELIANCE.NS") == 0
    assert broker.cash > 100_000
