from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import BrokerConfig, DEFAULT_SYMBOLS
from .intelligence import FilterConfig
from .live_engine import LivePaperConfig, LivePaperEngine
from .live_trade import LiveTradeRiskConfig


def create_app(config: LivePaperConfig | None = None) -> FastAPI:
    cfg = config or LivePaperConfig(trade_symbols=DEFAULT_SYMBOLS)
    engine = LivePaperEngine(cfg)
    static_dir = Path(__file__).resolve().parent / "web_static"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def _boot() -> None:
            await engine.prepare()
            await engine.run_forever()

        task = asyncio.create_task(_boot())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Paisa Live Paper Trading", lifespan=lifespan)
    app.state.engine = engine
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    async def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/api/state")
    async def state():
        return JSONResponse(await engine.state())

    @app.get("/api/universe")
    async def universe():
        current = await engine.state()
        return JSONResponse(
            {
                "count": current.get("universe_count", 0),
                "last_quote_refresh": current.get("last_quote_refresh"),
                "market_status": current.get("market_status"),
                "rows": current.get("market_universe", []),
            }
        )

    @app.get("/api/ai-snapshot")
    async def ai_snapshot():
        current = await engine.state()
        return JSONResponse(current["ai_snapshot"])

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


def build_live_config(
    trade_symbols: list[str] | None = None,
    period: str = "5d",
    interval: str = "5minute",
    poll_seconds: float = 15.0,
    use_intelligence_filter: bool = True,
    initial_cash: float = 100_000.0,
    spread_bps: float = 3.0,
    slippage_bps: float = 2.0,
    max_position_pct: float = 0.20,
    min_volume: float = 100_000.0,
    max_spread_bps: float = 25.0,
    min_signal_score: float = 55.0,
    atr_sl_mult: float = 1.5,
    atr_target_mult: float = 2.5,
    trail_atr_mult: float = 1.0,
) -> LivePaperConfig:
    return LivePaperConfig(
        trade_symbols=trade_symbols or DEFAULT_SYMBOLS,
        period=period,
        interval=interval,
        poll_seconds=poll_seconds,
        use_intelligence_filter=use_intelligence_filter,
        broker=BrokerConfig(
            initial_cash=initial_cash,
            spread_bps=spread_bps,
            slippage_bps=slippage_bps,
            max_position_pct=max_position_pct,
        ),
        filters=FilterConfig(
            min_volume=min_volume,
            max_spread_bps=max_spread_bps,
            min_signal_score=min_signal_score,
        ),
        risk=LiveTradeRiskConfig(
            atr_sl_mult=atr_sl_mult,
            atr_target_mult=atr_target_mult,
            trail_atr_mult=trail_atr_mult,
        ),
    )
