from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from .backtest import BacktestResult
from .config import REPORTS_DIR, ensure_dirs


def write_backtest_report(results: list[BacktestResult], strategy: str) -> Path:
    ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = REPORTS_DIR / f"backtest_{strategy}_{stamp}"
    report_dir.mkdir(parents=True, exist_ok=True)

    summary = pd.DataFrame([result.summary for result in results])
    summary.to_csv(report_dir / "summary.csv", index=False)

    with (report_dir / "report.md").open("w", encoding="utf-8") as handle:
        handle.write(f"# Backtest Report: {strategy}\n\n")
        handle.write("This is a simulated paper-trading report. It is not live execution evidence.\n\n")
        handle.write("## Summary\n\n")
        handle.write(summary.to_markdown(index=False))
        handle.write("\n\n")

    for result in results:
        symbol = str(result.summary["symbol"])
        safe = symbol.replace(".", "_")
        result.equity_curve.to_csv(report_dir / f"{safe}_equity.csv", index=False)
        result.fills.to_csv(report_dir / f"{safe}_fills.csv", index=False)

    return report_dir
