from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd

from .ai_harness.context_builder import build_messages
from .ai_harness.decision_parser import TradeDecision, parse_trade_decision
from .ai_harness.decision_router import DecisionRouter, DecisionRouterConfig, RouteResult
from .ai_harness.model_runner import ModelRunner, runner_from_config
from .ai_harness.prediction_tracker import (
    prediction_context,
    prediction_stats,
    predicted_direction,
    prepare_future_predictions,
    settle_due_predictions,
)
from .broker import SimulatedBroker
from .config import AIHarnessConfig, BrokerConfig, REPORTS_DIR, ensure_dirs
from .data import CandleRequest, download_candles, load_candles
from .intelligence import FilterConfig, build_market_snapshot, enrich_indicators


@dataclass(frozen=True)
class AIBacktestResult:
    report_dir: Path
    summary: dict[str, Any]
    decisions: list[dict[str, Any]]
    equity_curve: list[dict[str, Any]]


async def run_ai_backtest(
    symbols: list[str],
    period: str,
    interval: str,
    *,
    download: bool = False,
    force: bool = False,
    broker_cfg: BrokerConfig | None = None,
    filter_cfg: FilterConfig | None = None,
    ai_cfg: AIHarnessConfig | None = None,
    runner: ModelRunner | None = None,
) -> AIBacktestResult:
    if not symbols:
        raise ValueError("At least one symbol is required.")

    ensure_dirs()
    broker_cfg = broker_cfg or BrokerConfig()
    filter_cfg = filter_cfg or FilterConfig()
    ai_cfg = ai_cfg or AIHarnessConfig(symbols=symbols)
    runner = runner or runner_from_config(ai_cfg)
    router = DecisionRouter(
        DecisionRouterConfig(
            decision_min_confidence=ai_cfg.decision_min_confidence,
            position_size_pct=ai_cfg.position_size_pct,
        )
    )
    session_dir = _new_session_dir()

    if download:
        download_candles(CandleRequest(symbols=symbols, period=period, interval=interval), force=force)

    candles_by_symbol = {symbol: load_candles(symbol, period, interval) for symbol in symbols}
    broker = SimulatedBroker(broker_cfg)
    latest_prices = {symbol: float(candles["close"].iloc[0]) for symbol, candles in candles_by_symbol.items()}
    decisions: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []

    max_rows = max(len(candles) for candles in candles_by_symbol.values())
    for idx in range(max_rows):
        for symbol, candles in candles_by_symbol.items():
            if idx >= len(candles):
                continue
            visible = enrich_indicators(candles.iloc[: idx + 1])
            last = visible.iloc[-1]
            latest_prices[symbol] = float(last["close"])
            snapshot = build_market_snapshot(
                symbol,
                visible,
                0.0,
                filter_cfg,
                active_strategy="ai-harness",
                equity=broker.mark_to_market(latest_prices),
                cash=broker.cash,
                open_positions=_open_positions(broker, latest_prices),
                recent_fills=[_jsonable(asdict(fill)) for fill in broker.fills[-10:]],
                total_trades=len(broker.fills),
            )
            settle_due_predictions(decisions, symbol, snapshot)
            context = prediction_context(decisions, symbol, snapshot.bar_index)
            system, user = build_messages(snapshot, context)
            decision = await _call_model(runner, system, user)
            route = router.route(decision, snapshot, broker)
            record = _decision_record(idx, snapshot, decision, route, candles)
            decisions.append(record)
            _append_jsonl(session_dir / "decisions.jsonl", record)
            _append_jsonl(session_dir / "snapshots.jsonl", snapshot.to_dict())
            if route.fill:
                _append_jsonl(session_dir / "fills.jsonl", route.fill)

        equity_curve.append(
            {
                "bar_index": idx,
                "time": datetime.now(timezone.utc).isoformat(),
                "equity": round(broker.mark_to_market(latest_prices), 2),
                "cash": round(broker.cash, 2),
            }
        )

    summary = _summary(symbols, broker_cfg.initial_cash, broker, latest_prices, decisions)
    (session_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (session_dir / "equity_curve.json").write_text(json.dumps(equity_curve, indent=2), encoding="utf-8")
    write_ai_session_report(session_dir)
    return AIBacktestResult(session_dir, summary, decisions, equity_curve)


def run_ai_backtest_sync(*args: Any, **kwargs: Any) -> AIBacktestResult:
    return asyncio.run(run_ai_backtest(*args, **kwargs))


def write_ai_session_report(session_dir: Path | None = None) -> Path:
    directory = session_dir or latest_ai_session_dir()
    summary = _read_json(directory / "summary.json", {})
    decisions = _read_jsonl(directory / "decisions.jsonl")
    rows = "\n".join(
        "<tr>"
        f"<td>{escape(str(item.get('timestamp', '')))}</td>"
        f"<td>{escape(str(item.get('symbol', '')))}</td>"
        f"<td>{escape(str(item.get('action', '')))}</td>"
        f"<td>{float(item.get('confidence', 0)):.0%}</td>"
        f"<td>{escape(str(item.get('route_reason', '')))}</td>"
        f"<td>{escape(str(item.get('reasoning', '')))}</td>"
        "</tr>"
        for item in decisions[-300:]
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Paisa AI Session Report</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; color: #18202a; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #dfe4ea; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f4f6f8; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 16px 0; }}
    .card {{ border: 1px solid #dfe4ea; border-radius: 8px; padding: 12px; }}
    .card strong {{ display: block; font-size: 22px; margin-top: 6px; }}
  </style>
</head>
<body>
  <h1>Paisa AI Session Report</h1>
  <p>Paper-only AI research harness. No real broker execution.</p>
  <div class="cards">
    <div class="card">Final equity<strong>{summary.get('final_equity', 0):,.2f}</strong></div>
    <div class="card">Return %<strong>{summary.get('total_return_pct', 0):.2f}%</strong></div>
    <div class="card">AI decisions<strong>{summary.get('decisions', 0)}</strong></div>
    <div class="card">Broker fills<strong>{summary.get('fills', 0)}</strong></div>
  </div>
  <h2>Recent Decisions</h2>
  <table>
    <thead><tr><th>Time</th><th>Symbol</th><th>Action</th><th>Confidence</th><th>Route</th><th>Reasoning</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>
"""
    output = directory / "report.html"
    output.write_text(html, encoding="utf-8")
    return output


def latest_ai_session_dir() -> Path:
    root = REPORTS_DIR / "ai_sessions"
    sessions = sorted([path for path in root.glob("session_*") if path.is_dir()])
    if not sessions:
        raise FileNotFoundError("No AI sessions found. Run paisa ai-backtest first.")
    return sessions[-1]


async def _call_model(runner: ModelRunner, system: str, user: str) -> TradeDecision:
    try:
        raw = await runner.run(system, user)
    except Exception as exc:
        return TradeDecision.hold(f"AI model call failed: {exc}")
    return parse_trade_decision(raw)


def _new_session_dir() -> Path:
    root = REPORTS_DIR / "ai_sessions"
    root.mkdir(parents=True, exist_ok=True)
    directory = root / f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def _decision_record(
    bar_index: int,
    snapshot: Any,
    decision: TradeDecision,
    route: RouteResult,
    frame: pd.DataFrame,
) -> dict[str, Any]:
    return {
        "bar_index": bar_index,
        "timestamp": snapshot.timestamp.isoformat(),
        "symbol": snapshot.symbol,
        "close": snapshot.close,
        "algorithm_score": snapshot.next_move_score,
        "algorithm_label": snapshot.next_move_label,
        "predicted_direction": predicted_direction(snapshot.next_move_score),
        "action": decision.action,
        "confidence": decision.confidence,
        "reasoning": decision.reasoning,
        "next_move_prediction": decision.next_move_prediction,
        "future_predictions": prepare_future_predictions(decision.future_predictions, bar_index, snapshot, frame),
        "key_signals": decision.key_signals,
        "risk_note": decision.risk_note,
        "parse_error": decision.parse_error,
        "route_accepted": route.accepted,
        "route_reason": route.reason,
        "fill": route.fill,
        "actual_next_close": None,
        "actual_next_return_pct": None,
        "actual_direction": None,
        "prediction_result": "PENDING",
    }


def _summary(
    symbols: list[str],
    initial_cash: float,
    broker: SimulatedBroker,
    latest_prices: dict[str, float],
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    final_equity = broker.mark_to_market(latest_prices)
    accepted = sum(1 for item in decisions if item["route_accepted"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "initial_cash": round(initial_cash, 2),
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(((final_equity / initial_cash) - 1) * 100, 4) if initial_cash else 0.0,
        "decisions": len(decisions),
        "accepted_decisions": accepted,
        "fills": len(broker.fills),
        "cash": round(broker.cash, 2),
        "positions": dict(broker.positions),
        "prediction_stats": prediction_stats(decisions),
    }


def _open_positions(broker: SimulatedBroker, prices: dict[str, float]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": symbol,
            "quantity": quantity,
            "last_price": prices.get(symbol, 0.0),
            "market_value": round(quantity * prices.get(symbol, 0.0), 2),
        }
        for symbol, quantity in broker.positions.items()
        if quantity
    ]


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_jsonable(payload)) + "\n")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


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
