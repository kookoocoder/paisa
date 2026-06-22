from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .broker import SimulatedBroker
from .config import BrokerConfig
from .data import CandleRequest, download_candles, load_candles
from .intelligence import FilterConfig, ai_market_snapshot, enrich_indicators, estimate_depth, score_next_move
from .strategies import Strategy, build_strategy
from .trade_stats import trade_pnl_stats


@dataclass
class ReplayConfig:
    symbols: list[str]
    period: str = "5d"
    interval: str = "5minute"
    strategy: str = "ma-cross"
    tick_seconds: float = 1.0
    loop: bool = True
    force_refresh: bool = True
    use_intelligence_filter: bool = True
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)


class ReplayEngine:
    def __init__(self, config: ReplayConfig, candles_by_symbol: dict[str, pd.DataFrame] | None = None):
        if not config.symbols:
            raise ValueError("At least one symbol is required.")
        self.config = config
        self.strategy: Strategy = build_strategy(config.strategy)
        self.broker = SimulatedBroker(config.broker)
        self._provided_candles = candles_by_symbol
        self._candles: dict[str, pd.DataFrame] = {}
        self._signaled: dict[str, pd.DataFrame] = {}
        self._indexes: dict[str, int] = {}
        self._previous_targets: dict[str, float] = {}
        self._latest_prices: dict[str, float] = {}
        self._events: list[dict[str, Any]] = []
        self._equity_points: list[dict[str, Any]] = []
        self._running = False
        self._started_at: datetime | None = None
        self._lock = asyncio.Lock()
        self._subscribers: set[asyncio.Queue[str]] = set()

    async def prepare(self) -> None:
        if self._provided_candles is None:
            download_candles(
                CandleRequest(self.config.symbols, self.config.period, self.config.interval),
                force=self.config.force_refresh,
            )
            candles_by_symbol = {
                symbol: load_candles(symbol, self.config.period, self.config.interval)
                for symbol in self.config.symbols
            }
        else:
            candles_by_symbol = self._provided_candles

        async with self._lock:
            self._candles = {}
            self._signaled = {}
            self._indexes = {}
            self._previous_targets = {}
            self._latest_prices = {}
            self._events = []
            self._equity_points = []
            self.broker = SimulatedBroker(self.config.broker)
            for symbol, candles in candles_by_symbol.items():
                normalized = candles.sort_values("timestamp").reset_index(drop=True)
                if normalized.empty:
                    raise ValueError(f"No candles available for {symbol}")
                signaled = self.strategy.signals(normalized)
                self._candles[symbol] = normalized
                self._signaled[symbol] = signaled
                self._indexes[symbol] = 0
                self._previous_targets[symbol] = 0.0
                self._latest_prices[symbol] = float(normalized["close"].iloc[0])

    async def run_forever(self) -> None:
        if not self._candles:
            await self.prepare()
        self._running = True
        self._started_at = datetime.now(timezone.utc)
        self._event("system", "Replay engine started", {})
        await self._publish()
        while True:
            if self._running:
                await self.step()
            await asyncio.sleep(max(0.05, self.config.tick_seconds))

    async def step(self) -> None:
        async with self._lock:
            for symbol in self.config.symbols:
                self._step_symbol(symbol)
            self._record_equity()
            state = self._state_unlocked()
        await self._publish_state(state)

    async def pause(self) -> None:
        async with self._lock:
            self._running = False
            self._event("system", "Replay paused", {})
            state = self._state_unlocked()
        await self._publish_state(state)

    async def resume(self) -> None:
        async with self._lock:
            self._running = True
            self._event("system", "Replay resumed", {})
            state = self._state_unlocked()
        await self._publish_state(state)

    async def reset(self) -> None:
        await self.prepare()
        async with self._lock:
            self._running = True
            self._started_at = datetime.now(timezone.utc)
            self._event("system", "Replay reset", {})
            state = self._state_unlocked()
        await self._publish_state(state)

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=5)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.discard(queue)

    async def state(self) -> dict[str, Any]:
        async with self._lock:
            return self._state_unlocked()

    def _step_symbol(self, symbol: str) -> None:
        frame = self._signaled[symbol]
        idx = self._indexes[symbol]
        if idx >= len(frame):
            if self.config.loop:
                self._indexes[symbol] = 0
                self._previous_targets[symbol] = 0.0
                self._event("system", f"{symbol} replay loop restarted", {"symbol": symbol})
                idx = 0
            else:
                return

        row = frame.iloc[idx]
        timestamp = pd.Timestamp(row["timestamp"])
        price = float(row["open"] if pd.notna(row["open"]) else row["close"])
        close = float(row["close"])
        self._latest_prices[symbol] = close
        target = max(0.0, min(1.0, float(row.get("target_position", 0.0))))
        previous = self._previous_targets[symbol]

        if self.config.use_intelligence_filter and target > previous:
            visible = frame.iloc[: idx + 1]
            enriched = enrich_indicators(visible)
            signal = score_next_move(enriched, self.config.filters, symbol=symbol)
            if not signal["paper_trade_candidate"]:
                if target > 0:
                    self._event(
                        "filter",
                        f"{symbol} entry blocked by intelligence filter (score={signal['score']})",
                        {"symbol": symbol, "signal": signal},
                    )
                target = previous

        if target != previous:
            equity = self.broker.mark_to_market(self._latest_prices)
            desired = int((equity * self.config.broker.max_position_pct * target) // max(price, 0.01))
            current = self.broker.position(symbol)
            fill = None
            if target > previous:
                fill = self.broker.submit_market_order(
                    timestamp,
                    symbol,
                    "BUY",
                    max(0, desired - current),
                    price,
                    f"{self.strategy.name} replay entry",
                )
            else:
                quantity = current if target == 0 else max(0, current - desired)
                fill = self.broker.submit_market_order(
                    timestamp,
                    symbol,
                    "SELL",
                    quantity,
                    price,
                    f"{self.strategy.name} replay exit",
                )
            if fill is not None:
                self._event("fill", f"{fill.side} {fill.quantity} {symbol} @ {fill.price:.2f}", asdict(fill))
        self._previous_targets[symbol] = target
        self._indexes[symbol] = idx + 1

    def _record_equity(self) -> None:
        equity = self.broker.mark_to_market(self._latest_prices)
        self._equity_points.append(
            {
                "time": datetime.now(timezone.utc).isoformat(),
                "equity": round(equity, 2),
                "cash": round(self.broker.cash, 2),
            }
        )
        self._equity_points = self._equity_points[-600:]

    def _event(self, kind: str, message: str, payload: dict[str, Any]) -> None:
        self._events.append(
            {
                "time": datetime.now(timezone.utc).isoformat(),
                "kind": kind,
                "message": message,
                "payload": _jsonable(payload),
            }
        )
        self._events = self._events[-200:]

    def _symbol_state(self, symbol: str) -> dict[str, Any]:
        frame = self._signaled[symbol]
        idx = max(0, min(self._indexes[symbol] - 1, len(frame) - 1))
        visible = frame.iloc[: idx + 1].copy()
        enriched = enrich_indicators(visible)
        last = enriched.iloc[-1]
        target = max(0.0, min(1.0, float(last.get("target_position", 0.0))))
        next_move = score_next_move(enriched, self.config.filters, symbol=symbol)
        equity = self.broker.mark_to_market(self._latest_prices)
        snapshot = ai_market_snapshot(
            symbol,
            enriched,
            target,
            self.config.filters,
            active_strategy=self.strategy.name,
            equity=equity,
            cash=self.broker.cash,
            open_positions=[
                {"symbol": position_symbol, "quantity": quantity}
                for position_symbol, quantity in self.broker.positions.items()
                if quantity
            ],
            recent_fills=[_jsonable(asdict(fill)) for fill in self.broker.fills[-10:]],
            total_trades=len(self.broker.fills),
        )
        recent_candles = enriched.tail(120)
        return {
            "symbol": symbol,
            "cursor": idx,
            "total_bars": len(frame),
            "last_bar_time": pd.Timestamp(last["timestamp"]).isoformat(),
            "close": round(float(last["close"]), 4),
            "volume": round(float(last["volume"]), 2),
            "position": self.broker.position(symbol),
            "target_position": target,
            "next_move": next_move,
            "indicators": snapshot["indicators"],
            "depth": snapshot["depth_levels"],
            "candles": [
                {
                    "time": pd.Timestamp(row["timestamp"]).isoformat(),
                    "open": round(float(row["open"]), 4),
                    "high": round(float(row["high"]), 4),
                    "low": round(float(row["low"]), 4),
                    "close": round(float(row["close"]), 4),
                    "sma_10": None if pd.isna(row.get("sma_10")) else round(float(row["sma_10"]), 4),
                    "sma_30": None if pd.isna(row.get("sma_30")) else round(float(row["sma_30"]), 4),
                }
                for _, row in recent_candles.iterrows()
            ],
            "ai_snapshot": snapshot,
        }

    def _state_unlocked(self) -> dict[str, Any]:
        equity = self.broker.mark_to_market(self._latest_prices)
        initial = self.config.broker.initial_cash
        fills = [_jsonable(asdict(fill)) for fill in self.broker.fills]
        return {
            "running": self._running,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "config": {
                "symbols": self.config.symbols,
                "period": self.config.period,
                "interval": self.config.interval,
                "strategy": self.config.strategy,
                "tick_seconds": self.config.tick_seconds,
                "loop": self.config.loop,
                "use_intelligence_filter": self.config.use_intelligence_filter,
            },
            "portfolio": {
                "cash": round(self.broker.cash, 2),
                "equity": round(equity, 2),
                "initial_cash": round(initial, 2),
                "return_pct": round(((equity / initial) - 1) * 100, 4) if initial else 0.0,
                "positions": dict(self.broker.positions),
                "fills": fills[-50:],
                "equity_curve": self._equity_points[-300:],
            },
            "trade_pnl_stats": trade_pnl_stats(
                fills,
                initial_cash=initial,
                latest_prices=self._latest_prices,
                positions={symbol: qty for symbol, qty in self.broker.positions.items() if qty},
            ),
            "symbols": {symbol: self._symbol_state(symbol) for symbol in self.config.symbols},
            "events": [event for event in self._events[-100:] if event.get("kind") != "filter"],
            "ai_snapshot": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "purpose": "AI model market-view input for autonomous paper replay",
                "portfolio": {
                    "cash": round(self.broker.cash, 2),
                    "equity": round(equity, 2),
                    "positions": dict(self.broker.positions),
                },
                "symbols": {
                    symbol: self._symbol_state(symbol)["ai_snapshot"]
                    for symbol in self.config.symbols
                },
            },
        }

    async def _publish(self) -> None:
        async with self._lock:
            state = self._state_unlocked()
        await self._publish_state(state)

    async def _publish_state(self, state: dict[str, Any]) -> None:
        text = json.dumps(state)
        dead = []
        for queue in self._subscribers:
            try:
                if queue.full():
                    _ = queue.get_nowait()
                queue.put_nowait(text)
            except Exception:
                dead.append(queue)
        for queue in dead:
            self.unsubscribe(queue)


def _jsonable(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value
