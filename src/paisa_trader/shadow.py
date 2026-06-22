from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .backtest import BacktestResult, run_symbol_backtest
from .bridge import export_stocksharp_package
from .config import BrokerConfig, ensure_dirs
from .data import CandleRequest, download_candles, load_candles
from .report import write_backtest_report
from .strategies import Strategy, build_strategy


@dataclass(frozen=True)
class ShadowSession:
    refreshed_at: datetime
    strategy: str
    period: str
    interval: str
    symbols: list[str]
    results: list[BacktestResult]
    report_dir: Path
    bridge_dir: Path | None


def _write_shadow_metadata(session: ShadowSession) -> None:
    payload = {
        "refreshed_at": session.refreshed_at.isoformat(),
        "data_source": "Upstox historical candles",
        "purpose": "shadow paper-trading session — no live broker execution",
        "strategy": session.strategy,
        "period": session.period,
        "interval": session.interval,
        "symbols": session.symbols,
        "report_dir": str(session.report_dir),
        "bridge_dir": str(session.bridge_dir) if session.bridge_dir else None,
        "summaries": [result.summary for result in session.results],
    }
    (session.report_dir / "shadow.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def run_shadow_session(
    symbols: list[str],
    period: str,
    interval: str,
    strategy: Strategy | str,
    broker_cfg: BrokerConfig | None = None,
    *,
    force_refresh: bool = True,
    export_bridge: bool = False,
) -> ShadowSession:
    if not symbols:
        raise ValueError("At least one symbol is required.")

    ensure_dirs()
    broker_cfg = broker_cfg or BrokerConfig()
    strategy_obj = build_strategy(strategy) if isinstance(strategy, str) else strategy

    download_candles(
        CandleRequest(symbols=symbols, period=period, interval=interval),
        force=force_refresh,
    )

    results: list[BacktestResult] = []
    for symbol in symbols:
        candles = load_candles(symbol, period, interval)
        results.append(run_symbol_backtest(candles, strategy_obj, broker_cfg))

    report_dir = write_backtest_report(results, strategy_obj.name)
    bridge_dir = None
    if export_bridge:
        bridge_dir = export_stocksharp_package(
            symbols,
            period,
            interval,
            strategy_obj,
            broker_cfg,
        )

    session = ShadowSession(
        refreshed_at=datetime.now(timezone.utc),
        strategy=strategy_obj.name,
        period=period,
        interval=interval,
        symbols=symbols,
        results=results,
        report_dir=report_dir,
        bridge_dir=bridge_dir,
    )
    _write_shadow_metadata(session)
    return session
