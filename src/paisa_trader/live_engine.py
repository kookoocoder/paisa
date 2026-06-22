from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timezone
from typing import Any

import pandas as pd

from .broker import SimulatedBroker
from .config import BrokerConfig
from .data import (
    CandleRequest,
    download_candles,
    fetch_upstox_quotes_by_keys,
    load_candles_cached,
    nse_equity_universe,
    quote_snapshot,
)
from .intelligence import FilterConfig, ai_market_snapshot, enrich_indicators, score_next_move
from .strategies import Strategy, build_strategy
from .trade_stats import trade_pnl_stats


from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")


@dataclass
class LivePaperConfig:
    trade_symbols: list[str]
    period: str = "5d"
    interval: str = "5minute"
    strategy: str = "ma-cross"
    poll_seconds: float = 15.0
    use_intelligence_filter: bool = True
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)


class LivePaperEngine:
    def __init__(self, config: LivePaperConfig):
        if not config.trade_symbols:
            raise ValueError("At least one trade symbol is required.")
        self.config = config
        self.strategy: Strategy = build_strategy(config.strategy)
        self.broker = SimulatedBroker(config.broker)
        self._universe: list[dict[str, str]] = []
        self._key_to_symbol: dict[str, str] = {}
        self._market_rows: list[dict[str, Any]] = []
        self._candles: dict[str, pd.DataFrame] = {}
        self._signaled: dict[str, pd.DataFrame] = {}
        self._previous_targets: dict[str, float] = {}
        self._latest_prices: dict[str, float] = {}
        self._events: list[dict[str, Any]] = []
        self._equity_points: list[dict[str, Any]] = []
        self._running = False
        self._started_at: datetime | None = None
        self._last_quote_refresh: datetime | None = None
        self._quote_error: str | None = None
        self._active_trade_symbols: list[str] = list(config.trade_symbols)
        self._lock = asyncio.Lock()
        self._subscribers: set[asyncio.Queue[str]] = set()

    async def prepare(self) -> None:
        universe = await asyncio.to_thread(nse_equity_universe)
        self._universe = universe
        self._key_to_symbol = {row["instrument_key"]: row["symbol"] for row in universe}

        missing_symbols: list[str] = []
        candles_by_symbol: dict[str, pd.DataFrame] = {}
        for symbol in self.config.trade_symbols:
            try:
                candles_by_symbol[symbol] = await asyncio.to_thread(
                    load_candles_cached,
                    symbol,
                    self.config.period,
                    self.config.interval,
                )
            except FileNotFoundError:
                missing_symbols.append(symbol)

        if missing_symbols:
            try:
                await asyncio.to_thread(
                    download_candles,
                    CandleRequest(missing_symbols, self.config.period, self.config.interval),
                    False,
                )
                for symbol in missing_symbols:
                    candles_by_symbol[symbol] = await asyncio.to_thread(
                        load_candles_cached,
                        symbol,
                        self.config.period,
                        self.config.interval,
                    )
            except Exception as exc:
                self._quote_error = str(exc)

        if not candles_by_symbol:
            raise RuntimeError(
                "No cached candles available for the trade watchlist. "
                "Set UPSTOX_ANALYTICS_TOKEN and run `paisa download`, "
                "or pass --trade-symbols for symbols with cached data."
            )

        self._active_trade_symbols = list(candles_by_symbol.keys())

        async with self._lock:
            self.broker = SimulatedBroker(self.config.broker)
            self._candles = {}
            self._signaled = {}
            self._previous_targets = {}
            self._latest_prices = {}
            self._events = []
            self._equity_points = []
            for symbol, candles in candles_by_symbol.items():
                normalized = candles.sort_values("timestamp").reset_index(drop=True)
                if normalized.empty:
                    raise ValueError(f"No candles available for {symbol}")
                self._candles[symbol] = normalized
                self._signaled[symbol] = self.strategy.signals(normalized)
                self._previous_targets[symbol] = 0.0
                self._latest_prices[symbol] = float(normalized["close"].iloc[-1])

        try:
            await self._refresh_quotes()
        except Exception as exc:
            self._quote_error = str(exc)
            self._event("system", "Live quotes unavailable; using cached candles", {"error": str(exc)})
        self._event("system", "Live paper engine prepared", {"trade_symbols": self._active_trade_symbols})

    async def run_forever(self) -> None:
        if not self._candles:
            await self.prepare()
        self._running = True
        self._started_at = datetime.now(timezone.utc)
        self._event("system", "Live paper trading started", {})
        await self._publish()
        while True:
            if self._running:
                await self.step()
            await asyncio.sleep(max(1.0, self.config.poll_seconds))

    async def step(self) -> None:
        try:
            await self._refresh_quotes()
        except Exception as exc:
            self._quote_error = str(exc)
        async with self._lock:
            for symbol in self._active_trade_symbols:
                self._step_symbol(symbol)
            self._record_equity()
            state = self._state_unlocked()
        await self._publish_state(state)

    async def pause(self) -> None:
        async with self._lock:
            self._running = False
            self._event("system", "Live trading paused", {})
            state = self._state_unlocked()
        await self._publish_state(state)

    async def resume(self) -> None:
        async with self._lock:
            self._running = True
            self._event("system", "Live trading resumed", {})
            state = self._state_unlocked()
        await self._publish_state(state)

    async def reset(self) -> None:
        await self.prepare()
        async with self._lock:
            self._running = True
            self._started_at = datetime.now(timezone.utc)
            self._event("system", "Live paper session reset", {})
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

    async def _refresh_quotes(self) -> None:
        keys = [row["instrument_key"] for row in self._universe]
        if not keys:
            self._universe = await asyncio.to_thread(nse_equity_universe)
            keys = [row["instrument_key"] for row in self._universe]
            self._key_to_symbol = {row["instrument_key"]: row["symbol"] for row in self._universe}

        raw_quotes = await asyncio.to_thread(fetch_upstox_quotes_by_keys, keys, full=True)
        rows: list[dict[str, Any]] = []
        quote_by_symbol: dict[str, dict[str, Any]] = {}
        for instrument_key, raw in raw_quotes.items():
            trading_symbol = self._key_to_symbol.get(instrument_key, instrument_key)
            row = quote_snapshot(instrument_key, raw, trading_symbol)
            rows.append(row)
            quote_by_symbol[trading_symbol] = row

        async with self._lock:
            self._market_rows = sorted(rows, key=lambda item: item["symbol"])
            self._last_quote_refresh = datetime.now(timezone.utc)
            for symbol in self._active_trade_symbols:
                quote = quote_by_symbol.get(symbol)
                if quote:
                    self._apply_live_quote(symbol, quote)

    def _apply_live_quote(self, symbol: str, quote: dict[str, Any]) -> None:
        candles = self._candles.get(symbol)
        if candles is None or candles.empty:
            return
        updated = candles.copy()
        idx = len(updated) - 1
        ltp = float(quote["ltp"])
        updated.loc[idx, "close"] = ltp
        updated.loc[idx, "high"] = max(float(updated.loc[idx, "high"]), float(quote.get("high", ltp)), ltp)
        updated.loc[idx, "low"] = min(float(updated.loc[idx, "low"]), float(quote.get("low", ltp)), ltp)
        if quote.get("volume"):
            updated.loc[idx, "volume"] = int(quote["volume"])
        self._candles[symbol] = updated
        self._signaled[symbol] = self.strategy.signals(updated)
        self._latest_prices[symbol] = ltp

    def _step_symbol(self, symbol: str) -> None:
        frame = self._signaled[symbol]
        if frame.empty:
            return
        row = frame.iloc[-1]
        timestamp = pd.Timestamp(datetime.now(timezone.utc))
        price = float(row["open"] if pd.notna(row["open"]) else row["close"])
        close = float(row["close"])
        self._latest_prices[symbol] = close
        target = max(0.0, min(1.0, float(row.get("target_position", 0.0))))
        previous = self._previous_targets[symbol]

        if self.config.use_intelligence_filter and target > previous:
            enriched = enrich_indicators(frame)
            signal = score_next_move(enriched, self.config.filters)
            if not signal["paper_trade_candidate"]:
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
                    f"{self.strategy.name} live entry",
                )
            else:
                quantity = current if target == 0 else max(0, current - desired)
                fill = self.broker.submit_market_order(
                    timestamp,
                    symbol,
                    "SELL",
                    quantity,
                    price,
                    f"{self.strategy.name} live exit",
                )
            if fill is not None:
                self._event("fill", f"{fill.side} {fill.quantity} {symbol} @ {fill.price:.2f}", asdict(fill))
        self._previous_targets[symbol] = target

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
        enriched = enrich_indicators(frame)
        last = enriched.iloc[-1]
        target = max(0.0, min(1.0, float(last.get("target_position", 0.0))))
        next_move = score_next_move(enriched, self.config.filters)
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
            "cursor": len(frame) - 1,
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

    def _market_status(self) -> str:
        now_ist = datetime.now(IST)
        if now_ist.weekday() >= 5:
            return "closed"
        current = now_ist.time()
        if time(9, 15) <= current <= time(15, 30):
            return "open"
        return "closed"

    def _state_unlocked(self) -> dict[str, Any]:
        equity = self.broker.mark_to_market(self._latest_prices)
        initial = self.config.broker.initial_cash
        fills = [_jsonable(asdict(fill)) for fill in self.broker.fills]
        return {
            "mode": "live",
            "running": self._running,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "market_status": self._market_status(),
            "last_quote_refresh": self._last_quote_refresh.isoformat() if self._last_quote_refresh else None,
            "quote_error": self._quote_error,
            "config": {
                "mode": "live",
                "trade_symbols": self._active_trade_symbols,
                "period": self.config.period,
                "interval": self.config.interval,
                "strategy": self.config.strategy,
                "poll_seconds": self.config.poll_seconds,
                "use_intelligence_filter": self.config.use_intelligence_filter,
                "symbols": self._active_trade_symbols,
                "tick_seconds": self.config.poll_seconds,
                "loop": False,
            },
            "market_universe": self._market_rows,
            "universe_count": len(self._market_rows),
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
            "symbols": {symbol: self._symbol_state(symbol) for symbol in self._active_trade_symbols},
            "events": [event for event in self._events[-100:] if event.get("kind") != "filter"],
            "ai_snapshot": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "purpose": "AI model market-view input for live paper trading",
                "portfolio": {
                    "cash": round(self.broker.cash, 2),
                    "equity": round(equity, 2),
                    "return_pct": round(((equity / initial) - 1) * 100, 4) if initial else 0.0,
                },
                "symbols": {
                    symbol: self._symbol_state(symbol)["ai_snapshot"]
                    for symbol in self._active_trade_symbols
                },
            },
        }

    async def _publish(self) -> None:
        state = await self.state()
        await self._publish_state(state)

    async def _publish_state(self, state: dict[str, Any]) -> None:
        payload = json.dumps(state, default=str)
        dead: list[asyncio.Queue[str]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(queue)
        for queue in dead:
            self._subscribers.discard(queue)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return value
    return value
