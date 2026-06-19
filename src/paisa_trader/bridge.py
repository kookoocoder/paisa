from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from .backtest import BacktestResult, run_symbol_backtest
from .config import BrokerConfig, REPORTS_DIR, ensure_dirs
from .data import load_candles
from .strategies import Strategy


def _signal_action(prev: float, current: float) -> str:
    if current > prev:
        return "BUY_OR_INCREASE"
    if current < prev:
        return "SELL_OR_REDUCE"
    return "HOLD"


def stocksharp_candles(candles: pd.DataFrame) -> pd.DataFrame:
    out = candles.copy()
    out["Time"] = pd.to_datetime(out["timestamp"]).dt.strftime("%Y-%m-%dT%H:%M:%S")
    out = out.rename(
        columns={
            "symbol": "Symbol",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    return out[["Symbol", "Time", "Open", "High", "Low", "Close", "Volume"]]


def stocksharp_signals(candles: pd.DataFrame, strategy: Strategy) -> pd.DataFrame:
    signaled = strategy.signals(candles)
    target = signaled["target_position"].fillna(0).clip(lower=0, upper=1)
    prev = target.shift(fill_value=0)
    out = pd.DataFrame(
        {
            "Symbol": signaled["symbol"],
            "Time": pd.to_datetime(signaled["timestamp"]).dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "Close": signaled["close"],
            "TargetPosition": target,
            "Action": [_signal_action(float(p), float(c)) for p, c in zip(prev, target)],
            "Strategy": strategy.name,
        }
    )
    out["Reason"] = out.apply(
        lambda row: f"{row['Strategy']} target_position={row['TargetPosition']:.4f}",
        axis=1,
    )
    return out[["Symbol", "Time", "Close", "TargetPosition", "Action", "Reason", "Strategy"]]


def export_stocksharp_package(
    symbols: list[str],
    period: str,
    interval: str,
    strategy: Strategy,
    broker_cfg: BrokerConfig | None = None,
    output_root: Path | None = None,
) -> Path:
    ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = (output_root or REPORTS_DIR / "stocksharp_bridge") / f"{strategy.name}_{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    broker_cfg = broker_cfg or BrokerConfig()
    summaries: list[dict[str, float | int | str]] = []
    result_files: dict[str, dict[str, str]] = {}

    for symbol in symbols:
        candles = load_candles(symbol, period, interval)
        result: BacktestResult = run_symbol_backtest(candles, strategy, broker_cfg)
        safe = symbol.replace(".", "_")

        candles_path = output_dir / f"{safe}_candles.csv"
        signals_path = output_dir / f"{safe}_signals.csv"
        equity_path = output_dir / f"{safe}_equity.csv"
        fills_path = output_dir / f"{safe}_fills.csv"

        stocksharp_candles(candles).to_csv(candles_path, index=False)
        stocksharp_signals(candles, strategy).to_csv(signals_path, index=False)
        result.equity_curve.to_csv(equity_path, index=False)
        result.fills.to_csv(fills_path, index=False)

        summaries.append(result.summary)
        result_files[symbol] = {
            "candles": str(candles_path),
            "signals": str(signals_path),
            "equity": str(equity_path),
            "fills": str(fills_path),
        }

    summary_path = output_dir / "summary.csv"
    pd.DataFrame(summaries).to_csv(summary_path, index=False)

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "purpose": "StockSharp paper-trading bridge package generated from yfinance/NSE-free workflow.",
        "symbols": symbols,
        "period": period,
        "interval": interval,
        "strategy": strategy.name,
        "broker_config": asdict(broker_cfg),
        "files": result_files,
        "summary": str(summary_path),
        "columns": {
            "candles": ["Symbol", "Time", "Open", "High", "Low", "Close", "Volume"],
            "signals": ["Symbol", "Time", "Close", "TargetPosition", "Action", "Reason", "Strategy"],
        },
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return output_dir
