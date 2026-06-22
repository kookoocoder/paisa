from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

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
from .intelligence import FilterConfig, ai_market_snapshot, default_headlines, enrich_indicators
from .live_trade import (
    LiveTradeCall,
    LiveTradeRiskConfig,
    build_entry_call,
    build_exit_call,
    evaluate_exit,
    signal_call_preview,
    update_trailing_stop,
)
from .trade_stats import trade_pnl_stats


IST = ZoneInfo("Asia/Kolkata")


@dataclass
class LivePaperConfig:
    trade_symbols: list[str]
    period: str = "5d"
    interval: str = "5minute"
    poll_seconds: float = 15.0
    use_intelligence_filter: bool = True
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    risk: LiveTradeRiskConfig = field(default_factory=LiveTradeRiskConfig)


class LivePaperEngine:
    def __init__(self, config: LivePaperConfig):
        if not config.trade_symbols:
            raise ValueError("At least one trade symbol is required.")
        self.config = config
        self.broker = SimulatedBroker(config.broker)
        self._universe: list[dict[str, str]] = []
        self._symbol_meta: dict[str, dict[str, str]] = {}
        self._market_rows: list[dict[str, Any]] = []
        self._live_quotes: dict[str, dict[str, Any]] = {}
        self._feature_candles: dict[str, pd.DataFrame] = {}
        self._open_calls: dict[str, LiveTradeCall] = {}
        self._trade_calls: list[dict[str, Any]] = []
        self._latest_prices: dict[str, float] = {}
        self._events: list[dict[str, Any]] = []
        self._equity_points: list[dict[str, Any]] = []
        self._running = False
        self._started_at: datetime | None = None
        self._last_quote_refresh: datetime | None = None
        self._quote_error: str | None = None
        self._active_trade_symbols: list[str] = list(config.trade_symbols)
        self._latest_ai_snapshots: dict[str, dict[str, Any]] = {}
        self._latest_next_moves: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._poll_count = 0
        self._subscribers: set[asyncio.Queue[str]] = set()

    async def prepare(self) -> None:
        universe = await asyncio.to_thread(nse_equity_universe)
        self._universe = universe
        self._symbol_meta = {
            row["symbol"]: {
                "symbol": row["symbol"],
                "segment": "NSE_EQ",
                "stock_name": row.get("name", row["symbol"]),
                "instrument_key": row["instrument_key"],
            }
            for row in universe
        }

        missing_symbols: list[str] = []
        feature_candles: dict[str, pd.DataFrame] = {}
        for symbol in self.config.trade_symbols:
            try:
                feature_candles[symbol] = await asyncio.to_thread(
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
                    feature_candles[symbol] = await asyncio.to_thread(
                        load_candles_cached,
                        symbol,
                        self.config.period,
                        self.config.interval,
                    )
            except Exception as exc:
                self._quote_error = str(exc)

        if not feature_candles:
            raise RuntimeError(
                "No feature candles available for the trade watchlist. "
                "Set UPSTOX_ANALYTICS_TOKEN and run `paisa download`."
            )

        self._active_trade_symbols = list(feature_candles.keys())

        async with self._lock:
            self.broker = SimulatedBroker(self.config.broker)
            self._feature_candles = {
                symbol: candles.sort_values("timestamp").reset_index(drop=True)
                for symbol, candles in feature_candles.items()
            }
            self._open_calls = {}
            self._trade_calls = []
            self._latest_prices = {}
            self._latest_ai_snapshots = {}
            self._latest_next_moves = {}
            self._events = []
            self._equity_points = []

        try:
            await self._refresh_quotes(watchlist_only=True)
        except Exception as exc:
            self._quote_error = str(exc)
            self._event("system", "Live quotes unavailable; trading paused until quotes return", {"error": str(exc)})
        self._event(
            "system",
            "Live paper engine prepared (feature cache for models, live LTP for execution)",
            {"trade_symbols": self._active_trade_symbols},
        )

    async def run_forever(self) -> None:
        if not self._feature_candles:
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
        self._poll_count += 1
        try:
            await self._refresh_quotes(watchlist_only=self._poll_count % 4 != 0)
        except Exception as exc:
            self._quote_error = str(exc)
        snapshot_jobs: list[dict[str, Any]] = []
        async with self._lock:
            equity = self.broker.mark_to_market(self._latest_prices)
            for symbol in self._active_trade_symbols:
                feature_frame = self._feature_candles.get(symbol)
                if feature_frame is None or feature_frame.empty:
                    continue
                snapshot_jobs.append(
                    {
                        "symbol": symbol,
                        "feature_frame": feature_frame,
                        "open_call": self._open_calls.get(symbol),
                        "equity": equity,
                        "cash": self.broker.cash,
                        "positions": dict(self.broker.positions),
                        "fills": list(self.broker.fills[-10:]),
                        "total_trades": len(self.broker.fills),
                    }
                )
        refreshed: dict[str, dict[str, Any]] = {}
        for job in snapshot_jobs:
            refreshed[job["symbol"]] = await asyncio.to_thread(self._build_ai_snapshot_job, job)
        async with self._lock:
            self._latest_ai_snapshots.update(refreshed)
            self._latest_next_moves.update(
                {
                    symbol: snapshot["next_move"]
                    for symbol, snapshot in refreshed.items()
                    if "next_move" in snapshot
                }
            )
            for symbol in self._active_trade_symbols:
                self._step_symbol(symbol, self._latest_next_moves.get(symbol))
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

    async def _refresh_quotes(self, watchlist_only: bool = False) -> None:
        if watchlist_only:
            keys: list[str] = []
            for symbol in self._active_trade_symbols:
                meta = self._symbol_metadata(symbol)
                key = meta.get("instrument_key", "")
                if key:
                    keys.append(key)
            if not keys:
                watchlist_only = False

        if not watchlist_only:
            keys = [row["instrument_key"] for row in self._universe]
            if not keys:
                self._universe = await asyncio.to_thread(nse_equity_universe)
                self._symbol_meta = {
                    row["symbol"]: {
                        "symbol": row["symbol"],
                        "segment": "NSE_EQ",
                        "stock_name": row.get("name", row["symbol"]),
                        "instrument_key": row["instrument_key"],
                    }
                    for row in self._universe
                }
                keys = [row["instrument_key"] for row in self._universe]

        raw_quotes = await asyncio.to_thread(fetch_upstox_quotes_by_keys, keys, full=True)
        rows: list[dict[str, Any]] = []
        live_quotes: dict[str, dict[str, Any]] = {}
        for instrument_key, raw in raw_quotes.items():
            trading_symbol = next(
                (row["symbol"] for row in self._universe if row["instrument_key"] == instrument_key),
                instrument_key,
            )
            row = quote_snapshot(instrument_key, raw, trading_symbol)
            rows.append(row)
            live_quotes[trading_symbol] = row

        async with self._lock:
            if not watchlist_only:
                self._market_rows = sorted(rows, key=lambda item: item["symbol"])
            else:
                merged = {row["symbol"]: row for row in self._market_rows}
                for row in rows:
                    merged[row["symbol"]] = row
                self._market_rows = sorted(merged.values(), key=lambda item: item["symbol"])
            self._live_quotes.update(live_quotes)
            self._last_quote_refresh = datetime.now(timezone.utc)
            for symbol in self._active_trade_symbols:
                quote = self._live_quote(symbol)
                if quote:
                    self._latest_prices[symbol] = float(quote["ltp"])

    def _canonical_symbol(self, symbol: str) -> str:
        return symbol.upper().removesuffix(".NS").removesuffix(".BO")

    def _live_quote(self, symbol: str) -> dict[str, Any] | None:
        base = self._canonical_symbol(symbol)
        for key in (base, symbol.upper(), f"{base}.NS"):
            quote = self._live_quotes.get(key)
            if quote and float(quote.get("ltp", 0) or 0) > 0:
                return quote
        return None

    def _symbol_metadata(self, symbol: str) -> dict[str, str]:
        base = self._canonical_symbol(symbol)
        return self._symbol_meta.get(
            base,
            {"symbol": base, "segment": "NSE_EQ", "stock_name": base, "instrument_key": ""},
        )

    def _feature_atr(self, symbol: str) -> float:
        frame = self._feature_candles.get(symbol)
        if frame is None or frame.empty:
            return 0.0
        enriched = enrich_indicators(frame)
        value = enriched.iloc[-1].get("atr_14")
        if value is not None and not pd.isna(value):
            return float(value)
        return float(enriched.iloc[-1]["close"]) * 0.01

    def _append_trade_call(self, call: LiveTradeCall) -> None:
        payload = call.to_dict()
        self._trade_calls.append(payload)
        self._trade_calls = self._trade_calls[-200:]

    def _step_symbol(self, symbol: str, signal: dict[str, Any] | None = None) -> None:
        quote = self._live_quote(symbol)
        if quote is None:
            return

        live_ltp = float(quote["ltp"])
        if live_ltp <= 0:
            return

        self._latest_prices[symbol] = live_ltp
        feature_frame = self._feature_candles.get(symbol)
        if feature_frame is None or feature_frame.empty:
            return

        enriched = enrich_indicators(feature_frame)
        signal = signal or self._neutral_next_move(enriched)
        atr_value = self._feature_atr(symbol)
        meta = self._symbol_metadata(symbol)
        timestamp = pd.Timestamp(datetime.now(timezone.utc))

        open_call = self._open_calls.get(symbol)
        if open_call is not None:
            open_call = update_trailing_stop(open_call, live_ltp, atr_value, self.config.risk)
            exit_status, exit_reason = evaluate_exit(open_call, live_ltp)
            if exit_status:
                exit_call = build_exit_call(open_call, live_ltp, exit_status, exit_reason)
                fill = self.broker.submit_market_order(
                    timestamp,
                    symbol,
                    "SELL",
                    self.broker.position(symbol),
                    live_ltp,
                    f"live {exit_status.lower()}",
                )
                if fill is not None:
                    self._append_trade_call(exit_call)
                    self._open_calls.pop(symbol, None)
                    self._event("trade_call", self._format_trade_call_message(exit_call), exit_call.to_dict())
                    self._event("fill", f"{fill.side} {fill.quantity} {symbol} @ {fill.price:.2f}", asdict(fill))
                return
            self._open_calls[symbol] = open_call
            return

        if self.broker.position(symbol) > 0:
            return

        if not signal.get("paper_trade_candidate"):
            return
        if self.config.use_intelligence_filter and not signal.get("paper_trade_candidate"):
            return

        preview = signal_call_preview(
            meta["symbol"],
            meta["segment"],
            meta["stock_name"],
            live_ltp,
            atr_value,
            list(signal.get("reasons", [])),
            self.config.risk,
        )
        self._event("trade_call", self._format_trade_call_message(preview), preview.to_dict())

        equity = self.broker.mark_to_market(self._latest_prices)
        quantity = int((equity * self.config.broker.max_position_pct) // max(live_ltp, 0.01))
        if quantity <= 0:
            return

        entry_call = build_entry_call(
            meta["symbol"],
            meta["segment"],
            meta["stock_name"],
            live_ltp,
            atr_value,
            quantity,
            list(signal.get("reasons", [])),
            self.config.risk,
        )
        fill = self.broker.submit_market_order(
            timestamp,
            symbol,
            "BUY",
            quantity,
            live_ltp,
            "live ensemble entry",
        )
        if fill is None:
            return
        self._open_calls[symbol] = entry_call
        self._append_trade_call(entry_call)
        self._event("trade_call", self._format_trade_call_message(entry_call), entry_call.to_dict())
        self._event("fill", f"{fill.side} {fill.quantity} {symbol} @ {fill.price:.2f}", asdict(fill))

    def _format_trade_call_message(self, call: LiveTradeCall) -> str:
        exit_text = f"{call.exit_inr:.2f}" if call.exit_inr is not None else "—"
        return (
            f"{call.stock_name} ({call.symbol}) [{call.segment}] "
            f"{call.call} @ {call.entry_inr:.2f} | SL {call.stop_loss_inr:.2f} | "
            f"Target {call.target_inr:.2f} | Exit {exit_text} | {call.status}"
        )

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

    @staticmethod
    def _neutral_next_move(enriched: pd.DataFrame | None = None) -> dict[str, Any]:
        regime = "UNKNOWN"
        if enriched is not None and not enriched.empty:
            last_regime = enriched.iloc[-1].get("regime")
            if last_regime:
                regime = str(last_regime)
        return {
            "direction": "neutral",
            "action": "no_trade",
            "score": 50.0,
            "confidence": 0.0,
            "reasons": ["Model snapshot warming up."],
            "disqualifiers": [],
            "factor_scores": {},
            "regime": regime,
            "active_regime": regime,
            "active_weights": {},
            "passes_filters": False,
            "paper_trade_candidate": False,
            "model_signals": {},
        }

    @staticmethod
    def _warming_snapshot(symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "warming_up": True,
            "ml_direction": "NEUTRAL",
            "ml_confidence": 0.0,
            "sentiment_label": "neutral",
            "sentiment_composite": 0.0,
            "arima_direction": "NEUTRAL",
            "indicators": {},
            "depth_levels": [],
        }

    def _build_ai_snapshot_job(self, job: dict[str, Any]) -> dict[str, Any]:
        symbol = str(job["symbol"])
        enriched = enrich_indicators(job["feature_frame"])
        open_call = job.get("open_call")
        positions = job.get("positions", {})
        fills = job.get("fills", [])
        return ai_market_snapshot(
            symbol,
            enriched,
            1.0 if open_call else 0.0,
            self.config.filters,
            active_strategy="live-ensemble",
            equity=float(job.get("equity", 0.0)),
            cash=float(job.get("cash", 0.0)),
            open_positions=[
                {"symbol": position_symbol, "quantity": quantity}
                for position_symbol, quantity in positions.items()
                if quantity
            ],
            recent_fills=[_jsonable(asdict(fill)) for fill in fills],
            total_trades=int(job.get("total_trades", 0)),
            headlines=default_headlines(symbol),
        )

    def _symbol_state(self, symbol: str) -> dict[str, Any]:
        feature_frame = self._feature_candles[symbol]
        enriched = enrich_indicators(feature_frame)
        quote = self._live_quote(symbol) or {}
        live_ltp = float(quote.get("ltp", self._latest_prices.get(symbol, 0.0)) or 0.0)
        next_move = self._latest_next_moves.get(symbol) or self._neutral_next_move(enriched)
        open_call = self._open_calls.get(symbol)
        snapshot = self._latest_ai_snapshots.get(symbol) or self._warming_snapshot(symbol)
        recent_candles = enriched.tail(120)
        return {
            "symbol": symbol,
            "live_ltp": round(live_ltp, 4),
            "feature_bars": len(feature_frame),
            "last_feature_bar_time": pd.Timestamp(feature_frame.iloc[-1]["timestamp"]).isoformat(),
            "quote_timestamp": quote.get("timestamp", ""),
            "execution_source": "live",
            "position": self.broker.position(symbol),
            "open_trade_call": open_call.to_dict() if open_call else None,
            "next_move": next_move,
            "indicators": snapshot.get("indicators", {}),
            "depth": snapshot.get("depth_levels", []),
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
            "execution_policy": "live_ltp_only",
            "feature_data_policy": "cached_candles_for_models_only",
            "config": {
                "mode": "live",
                "trade_symbols": self._active_trade_symbols,
                "period": self.config.period,
                "interval": self.config.interval,
                "strategy": "live-ensemble",
                "poll_seconds": self.config.poll_seconds,
                "use_intelligence_filter": self.config.use_intelligence_filter,
                "symbols": self._active_trade_symbols,
                "tick_seconds": self.config.poll_seconds,
                "loop": False,
                "risk": asdict(self.config.risk),
            },
            "market_universe": self._market_rows,
            "universe_count": len(self._market_rows),
            "trade_calls": list(self._trade_calls[-50:]),
            "open_trade_calls": {symbol: call.to_dict() for symbol, call in self._open_calls.items()},
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
            "symbols": (
                symbol_states := {
                    symbol: self._symbol_state(symbol) for symbol in self._active_trade_symbols
                }
            ),
            "events": [
                event
                for event in self._events[-100:]
                if event.get("kind") in {"fill", "system", "trade_call"}
            ],
            "ai_snapshot": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "purpose": "AI model market-view input for live paper trading",
                "portfolio": {
                    "cash": round(self.broker.cash, 2),
                    "equity": round(equity, 2),
                    "return_pct": round(((equity / initial) - 1) * 100, 4) if initial else 0.0,
                },
                "symbols": {symbol: state["ai_snapshot"] for symbol, state in symbol_states.items()},
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
