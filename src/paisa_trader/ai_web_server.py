from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .ai_harness.context_builder import build_messages
from .ai_harness.decision_parser import TradeDecision, parse_trade_decision
from .ai_harness.decision_router import DecisionRouter, DecisionRouterConfig
from .ai_harness.model_runner import ModelRunner, runner_from_config
from .ai_harness.prediction_tracker import (
    prediction_context,
    prediction_stats,
    predicted_direction,
    prepare_future_predictions,
    settle_due_predictions,
)
from .broker import SimulatedBroker
from .config import AIHarnessConfig, BrokerConfig, DEFAULT_SYMBOLS
from .data import CandleRequest, download_candles, load_candles
from .intelligence import FilterConfig, build_market_snapshot, enrich_indicators
from .wavetrail import WaveTrailConfig, build_wavetrail


@dataclass
class AIWebConfig:
    symbols: list[str]
    period: str = "5d"
    interval: str = "5m"
    tick_seconds: float = 1.0
    loop: bool = True
    force_refresh: bool = True
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    ai: AIHarnessConfig = field(default_factory=AIHarnessConfig)


class AIReplayEngine:
    def __init__(self, config: AIWebConfig, candles_by_symbol: dict[str, pd.DataFrame] | None = None):
        self.config = config
        self.broker = SimulatedBroker(config.broker)
        self.runner: ModelRunner = runner_from_config(config.ai)
        self.router = DecisionRouter(
            DecisionRouterConfig(config.ai.decision_min_confidence, config.ai.position_size_pct)
        )
        self._provided_candles = candles_by_symbol
        self._candles: dict[str, pd.DataFrame] = {}
        self._indexes: dict[str, int] = {}
        self._latest_prices: dict[str, float] = {}
        self._latest_snapshots: dict[str, dict[str, Any]] = {}
        self._data_windows: dict[str, dict[str, Any]] = {}
        self._decisions: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []
        self._equity_points: list[dict[str, Any]] = []
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._running = False
        self._started_at: datetime | None = None
        self._lock = asyncio.Lock()

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
            self.broker = SimulatedBroker(self.config.broker)
            self._candles = {
                symbol: candles.sort_values("timestamp").reset_index(drop=True)
                for symbol, candles in candles_by_symbol.items()
            }
            self._indexes = {symbol: 0 for symbol in self._candles}
            self._latest_prices = {
                symbol: float(candles["close"].iloc[0]) for symbol, candles in self._candles.items()
            }
            self._latest_snapshots = {}
            self._data_windows = {
                symbol: {
                    "source": "yfinance delayed historical candles replayed as live paper data",
                    "start": pd.Timestamp(candles["timestamp"].iloc[0]).isoformat(),
                    "end": pd.Timestamp(candles["timestamp"].iloc[-1]).isoformat(),
                    "bars": len(candles),
                    "period": self.config.period,
                    "interval": self.config.interval,
                }
                for symbol, candles in self._candles.items()
            }
            self._decisions = []
            self._events = []
            self._equity_points = []

    async def run_forever(self) -> None:
        if not self._candles:
            await self.prepare()
        self._running = True
        self._started_at = datetime.now(timezone.utc)
        self._event("system", "AI replay engine started", {})
        await self._publish()
        while True:
            if self._running:
                await self.step()
            await asyncio.sleep(max(0.05, self.config.tick_seconds))

    async def step(self) -> None:
        async with self._lock:
            symbols = list(self.config.symbols)
        for symbol in symbols:
            await self._step_symbol(symbol)
        async with self._lock:
            self._record_equity()
            state = self._state_unlocked()
        await self._publish_state(state)

    async def pause(self) -> None:
        async with self._lock:
            self._running = False
            self._event("system", "AI replay paused", {})
            state = self._state_unlocked()
        await self._publish_state(state)

    async def resume(self) -> None:
        async with self._lock:
            self._running = True
            self._event("system", "AI replay resumed", {})
            state = self._state_unlocked()
        await self._publish_state(state)

    async def reset(self) -> None:
        await self.prepare()
        async with self._lock:
            self._running = True
            self._started_at = datetime.now(timezone.utc)
            self._event("system", "AI replay reset", {})
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

    async def _step_symbol(self, symbol: str) -> None:
        async with self._lock:
            frame = self._candles[symbol]
            idx = self._indexes[symbol]
            if idx >= len(frame):
                if not self.config.loop:
                    return
                self._indexes[symbol] = 0
                idx = 0
                self._event("system", f"{symbol} replay loop restarted", {"symbol": symbol})
            visible = enrich_indicators(frame.iloc[: idx + 1])
            last = visible.iloc[-1]
            self._latest_prices[symbol] = float(last["close"])
            snapshot = build_market_snapshot(
                symbol,
                visible,
                0.0,
                self.config.filters,
                active_strategy="ai-harness",
                equity=self.broker.mark_to_market(self._latest_prices),
                cash=self.broker.cash,
                open_positions=self._open_positions_unlocked(),
                recent_fills=[_jsonable(asdict(fill)) for fill in self.broker.fills[-10:]],
                total_trades=len(self.broker.fills),
            )
            settle_due_predictions(self._decisions, symbol, snapshot)
            context = prediction_context(self._decisions, symbol, idx)

        system, user = build_messages(snapshot, context)
        try:
            raw = await self.runner.run(system, user)
            decision = parse_trade_decision(raw)
        except Exception as exc:
            decision = TradeDecision.hold(f"AI model call failed: {exc}")

        async with self._lock:
            route = self.router.route(decision, snapshot, self.broker)
            future_predictions = prepare_future_predictions(decision.future_predictions, idx, snapshot, self._candles[symbol])
            record = {
                "bar_index": idx,
                "timestamp": snapshot.timestamp.isoformat(),
                "symbol": symbol,
                "close": snapshot.close,
                "algorithm_score": snapshot.next_move_score,
                "algorithm_label": snapshot.next_move_label,
                "predicted_direction": predicted_direction(snapshot.next_move_score),
                **decision.to_dict(),
                "future_predictions": future_predictions,
                "route_accepted": route.accepted,
                "route_reason": route.reason,
                "fill": route.fill,
                "actual_next_close": None,
                "actual_next_return_pct": None,
                "actual_direction": None,
                "prediction_result": "PENDING",
            }
            self._latest_snapshots[symbol] = snapshot.to_dict()
            self._decisions.append(record)
            self._decisions = self._decisions[-300:]
            self._event("ai_decision", f"{symbol} {decision.action} ({decision.confidence:.0%})", record)
            if route.fill:
                self._event("fill", f"{route.fill['side']} {route.fill['quantity']} {symbol}", route.fill)
            self._indexes[symbol] = idx + 1

    def _record_equity(self) -> None:
        self._equity_points.append(
            {
                "time": datetime.now(timezone.utc).isoformat(),
                "equity": round(self.broker.mark_to_market(self._latest_prices), 2),
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
        self._events = self._events[-300:]

    def _symbol_state_unlocked(self, symbol: str) -> dict[str, Any]:
        frame = self._candles[symbol]
        idx = max(0, min(self._indexes[symbol] - 1, len(frame) - 1))
        visible = enrich_indicators(frame.iloc[: idx + 1])
        snapshot = self._latest_snapshots.get(symbol)
        if snapshot is None:
            built = build_market_snapshot(symbol, visible, 0.0, self.config.filters)
            snapshot = built.to_dict()
        recent_candles = visible.tail(90)
        return {
            "symbol": symbol,
            "cursor": idx,
            "total_bars": len(frame),
            "position": self.broker.position(symbol),
            "snapshot": snapshot,
            "wavetrail": build_wavetrail(
                symbol,
                visible,
                self.broker.cash,
                self.config.filters,
                WaveTrailConfig(max_position_pct=self.config.broker.max_position_pct),
            ),
            "decisions": [item for item in self._decisions if item["symbol"] == symbol][-30:],
            "data_window": self._data_windows.get(symbol, {}),
            "candles": [
                {
                    "time": pd.Timestamp(row["timestamp"]).isoformat(),
                    "open": round(float(row["open"]), 4),
                    "high": round(float(row["high"]), 4),
                    "low": round(float(row["low"]), 4),
                    "close": round(float(row["close"]), 4),
                    "volume": round(float(row.get("volume", 0)), 4),
                    "bb_upper": None if pd.isna(row.get("bb_high")) else round(float(row["bb_high"]), 4),
                    "bb_mid": None if pd.isna(row.get("bb_mid")) else round(float(row["bb_mid"]), 4),
                    "bb_lower": None if pd.isna(row.get("bb_low")) else round(float(row["bb_low"]), 4),
                }
                for _, row in recent_candles.iterrows()
            ],
        }

    def _state_unlocked(self) -> dict[str, Any]:
        equity = self.broker.mark_to_market(self._latest_prices)
        initial = self.config.broker.initial_cash
        return {
            "running": self._running,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "config": {
                "symbols": self.config.symbols,
                "period": self.config.period,
                "interval": self.config.interval,
                "tick_seconds": self.config.tick_seconds,
                "model_provider": self.config.ai.model_provider,
                "model_name": self.config.ai.model_name,
                "local_url": self.config.ai.local_url,
                "data_source": "yfinance delayed historical candles replay",
                "model_explanation": _model_explanation(self.config.ai.model_provider, self.config.ai.local_url),
            },
            "portfolio": {
                "cash": round(self.broker.cash, 2),
                "equity": round(equity, 2),
                "initial_cash": round(initial, 2),
                "return_pct": round(((equity / initial) - 1) * 100, 4) if initial else 0.0,
                "positions": dict(self.broker.positions),
                "fills": [_jsonable(asdict(fill)) for fill in self.broker.fills[-50:]],
                "equity_curve": self._equity_points[-300:],
            },
            "symbols": {symbol: self._symbol_state_unlocked(symbol) for symbol in self.config.symbols},
            "ai_decisions": self._decisions[-100:],
            "prediction_stats": prediction_stats(self._decisions),
            "events": self._events[-100:],
        }

    def _open_positions_unlocked(self) -> list[dict[str, Any]]:
        return [
            {
                "symbol": symbol,
                "quantity": quantity,
                "last_price": self._latest_prices.get(symbol, 0.0),
                "market_value": round(quantity * self._latest_prices.get(symbol, 0.0), 2),
            }
            for symbol, quantity in self.broker.positions.items()
            if quantity
        ]

    async def _publish(self) -> None:
        async with self._lock:
            state = self._state_unlocked()
        await self._publish_state(state)

    async def _publish_state(self, state: dict[str, Any]) -> None:
        text = json.dumps(_jsonable(state))
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


def create_app(config: AIWebConfig | None = None) -> FastAPI:
    cfg = config or AIWebConfig(symbols=DEFAULT_SYMBOLS[:3])
    engine = AIReplayEngine(cfg)
    static_dir = Path(__file__).resolve().parent / "static" / "ai_dashboard"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await engine.prepare()
        task = asyncio.create_task(engine.run_forever())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Paisa AI Market Intelligence Harness", lifespan=lifespan)
    app.state.engine = engine
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    async def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/api/state")
    async def state():
        return JSONResponse(await engine.state())

    @app.get("/api/ai-snapshot")
    async def ai_snapshot():
        current = await engine.state()
        return JSONResponse({symbol: item["snapshot"] for symbol, item in current["symbols"].items()})

    @app.post("/api/control/{action}")
    async def control(action: str):
        if action == "pause":
            await engine.pause()
        elif action == "resume":
            await engine.resume()
        elif action == "reset":
            await engine.reset()
        else:
            return JSONResponse({"error": f"unknown action {action}"}, status_code=400)
        return JSONResponse(await engine.state())

    @app.websocket("/ws")
    async def websocket(websocket: WebSocket):
        await websocket.accept()
        queue = engine.subscribe()
        try:
            await websocket.send_json(await engine.state())
            while True:
                text = await queue.get()
                await websocket.send_text(text)
        except WebSocketDisconnect:
            return
        finally:
            engine.unsubscribe(queue)

    return app


def build_ai_web_config(
    symbols: list[str] | None = None,
    period: str = "5d",
    interval: str = "5m",
    tick_seconds: float = 1.0,
    loop: bool = True,
    force_refresh: bool = True,
    initial_cash: float = 100_000.0,
    spread_bps: float = 3.0,
    slippage_bps: float = 2.0,
    max_position_pct: float = 0.20,
    ai_cfg: AIHarnessConfig | None = None,
) -> AIWebConfig:
    ai = ai_cfg or AIHarnessConfig(symbols=symbols or DEFAULT_SYMBOLS[:3])
    return AIWebConfig(
        symbols=symbols or ai.symbols or DEFAULT_SYMBOLS[:3],
        period=period,
        interval=interval,
        tick_seconds=tick_seconds,
        loop=loop,
        force_refresh=force_refresh,
        broker=BrokerConfig(
            initial_cash=initial_cash,
            spread_bps=spread_bps,
            slippage_bps=slippage_bps,
            max_position_pct=max_position_pct,
        ),
        ai=ai,
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _model_explanation(provider: str, local_url: str = "") -> str:
    if provider == "mock":
        return "MockRunner: deterministic rule-based AI placeholder using the snapshot next-move score. It is not Claude/OpenAI."
    if provider == "claude":
        return "ClaudeRunner: sends the snapshot prompt to Anthropic Claude and routes the returned JSON decision."
    if provider == "openai":
        return "OpenAIRunner: sends the snapshot prompt to OpenAI and routes the returned JSON decision."
    if provider == "local":
        return "LocalRunner: sends the snapshot prompt to a local Ollama-compatible endpoint."
    if provider == "lmstudio":
        return f"LMStudioRunner: detects the loaded model from {local_url}/v1/models and sends OpenAI-compatible chat completions."
    return f"{provider}: configured model runner."
