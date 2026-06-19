from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .backtest import run_symbol_backtest
from .bridge import export_stocksharp_package
from .config import BrokerConfig, DEFAULT_SYMBOLS, ensure_dirs
from .data import CandleRequest, download_candles, load_candles
from .nse import fetch_equity_bhavcopy, parse_date
from .report import write_backtest_report
from .shadow import run_shadow_session
from .strategies import build_strategy


def cmd_symbols(_: argparse.Namespace) -> int:
    for symbol in DEFAULT_SYMBOLS:
        print(symbol)
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    request = CandleRequest(symbols=args.symbols, period=args.period, interval=args.interval)
    paths = download_candles(request, force=args.force)
    for symbol, path in paths.items():
        print(f"{symbol}: {path}")
    return 0


def cmd_nse_bhavcopy(args: argparse.Namespace) -> int:
    result = fetch_equity_bhavcopy(parse_date(args.date))
    print(f"Saved {result.date} bhavcopy to {result.path}")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    ensure_dirs()
    if args.download:
        download_candles(
            CandleRequest(symbols=args.symbols, period=args.period, interval=args.interval),
            force=args.force,
        )
    strategy = build_strategy(args.strategy)
    broker_cfg = BrokerConfig(
        initial_cash=args.initial_cash,
        spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps,
        max_position_pct=args.max_position_pct,
    )
    results = []
    for symbol in args.symbols:
        candles = load_candles(symbol, args.period, args.interval)
        results.append(run_symbol_backtest(candles, strategy, broker_cfg))
    report_dir = write_backtest_report(results, args.strategy)
    print(f"Report written to {report_dir}")
    for result in results:
        print(result.summary)
    return 0


def cmd_stocksharp_export(args: argparse.Namespace) -> int:
    ensure_dirs()
    if args.download:
        download_candles(
            CandleRequest(symbols=args.symbols, period=args.period, interval=args.interval),
            force=args.force,
        )
    strategy = build_strategy(args.strategy)
    broker_cfg = BrokerConfig(
        initial_cash=args.initial_cash,
        spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps,
        max_position_pct=args.max_position_pct,
    )
    output_dir = export_stocksharp_package(
        args.symbols,
        args.period,
        args.interval,
        strategy,
        broker_cfg,
    )
    print(f"StockSharp bridge package written to {output_dir}")
    print(f"Manifest: {output_dir / 'manifest.json'}")
    return 0


def cmd_shadow(args: argparse.Namespace) -> int:
    broker_cfg = BrokerConfig(
        initial_cash=args.initial_cash,
        spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps,
        max_position_pct=args.max_position_pct,
    )
    session = run_shadow_session(
        args.symbols,
        args.period,
        args.interval,
        args.strategy,
        broker_cfg,
        force_refresh=not args.no_refresh,
        export_bridge=args.export_bridge,
    )
    print(f"Shadow report written to {session.report_dir}")
    if session.bridge_dir is not None:
        print(f"StockSharp bridge written to {session.bridge_dir}")
    for result in session.results:
        print(result.summary)
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    try:
        import streamlit  # noqa: F401
    except ImportError:
        print(
            "error: streamlit is required for the dashboard. "
            'Install with: pip install -e ".[dashboard]"',
            file=sys.stderr,
        )
        return 1

    app = Path(__file__).resolve().parent / "dashboard_app.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--server.port",
        str(args.port),
    ]
    return subprocess.call(cmd)


def cmd_web(args: argparse.Namespace) -> int:
    import uvicorn

    from .web_app import build_replay_config, create_app

    config = build_replay_config(
        symbols=args.symbols,
        period=args.period,
        interval=args.interval,
        strategy=args.strategy,
        tick_seconds=args.tick_seconds,
        loop=not args.no_loop,
        force_refresh=not args.no_refresh,
        use_intelligence_filter=not args.no_intelligence,
        initial_cash=args.initial_cash,
        spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps,
        max_position_pct=args.max_position_pct,
    )
    app = create_app(config)
    print(f"Paisa intraday replay dashboard: http://{args.host}:{args.port}")
    print("Autonomous paper trading is running — open the URL to watch activity.")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paisa", description="No-demat India trading research harness.")
    sub = parser.add_subparsers(dest="command", required=True)

    symbols = sub.add_parser("symbols", help="Print default NSE symbols.")
    symbols.set_defaults(func=cmd_symbols)

    download = sub.add_parser("download", help="Download yfinance candles.")
    download.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    download.add_argument("--period", default="6mo")
    download.add_argument("--interval", default="1d")
    download.add_argument("--force", action="store_true")
    download.set_defaults(func=cmd_download)

    bhav = sub.add_parser("nse-bhavcopy", help="Fetch NSE equity bhavcopy for YYYY-MM-DD.")
    bhav.add_argument("--date", required=True)
    bhav.set_defaults(func=cmd_nse_bhavcopy)

    backtest = sub.add_parser("backtest", help="Run a simulated backtest.")
    backtest.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS[:3])
    backtest.add_argument("--period", default="6mo")
    backtest.add_argument("--interval", default="1d")
    backtest.add_argument("--strategy", choices=["buy-hold", "ma-cross", "mean-reversion"], default="ma-cross")
    backtest.add_argument("--download", action="store_true", help="Download missing data before backtesting.")
    backtest.add_argument("--force", action="store_true", help="Force data refresh when used with --download.")
    backtest.add_argument("--initial-cash", type=float, default=100_000.0)
    backtest.add_argument("--spread-bps", type=float, default=3.0)
    backtest.add_argument("--slippage-bps", type=float, default=2.0)
    backtest.add_argument("--max-position-pct", type=float, default=0.20)
    backtest.set_defaults(func=cmd_backtest)

    ss = sub.add_parser("stocksharp-export", help="Export candles/signals/fills for the StockSharp paper harness.")
    ss.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS[:3])
    ss.add_argument("--period", default="6mo")
    ss.add_argument("--interval", default="1d")
    ss.add_argument("--strategy", choices=["buy-hold", "ma-cross", "mean-reversion"], default="ma-cross")
    ss.add_argument("--download", action="store_true", help="Download missing data before exporting.")
    ss.add_argument("--force", action="store_true", help="Force data refresh when used with --download.")
    ss.add_argument("--initial-cash", type=float, default=100_000.0)
    ss.add_argument("--spread-bps", type=float, default=3.0)
    ss.add_argument("--slippage-bps", type=float, default=2.0)
    ss.add_argument("--max-position-pct", type=float, default=0.20)
    ss.set_defaults(func=cmd_stocksharp_export)

    shadow = sub.add_parser("shadow", help="Refresh delayed candles and run a shadow paper session.")
    shadow.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS[:3])
    shadow.add_argument("--period", default="3mo")
    shadow.add_argument("--interval", default="1d")
    shadow.add_argument("--strategy", choices=["buy-hold", "ma-cross", "mean-reversion"], default="ma-cross")
    shadow.add_argument("--no-refresh", action="store_true", help="Reuse cached candles if present.")
    shadow.add_argument("--export-bridge", action="store_true", help="Also write a StockSharp bridge package.")
    shadow.add_argument("--initial-cash", type=float, default=100_000.0)
    shadow.add_argument("--spread-bps", type=float, default=3.0)
    shadow.add_argument("--slippage-bps", type=float, default=2.0)
    shadow.add_argument("--max-position-pct", type=float, default=0.20)
    shadow.set_defaults(func=cmd_shadow)

    dashboard = sub.add_parser("dashboard", help="Launch the live-delayed Streamlit dashboard.")
    dashboard.add_argument("--port", type=int, default=8501)
    dashboard.set_defaults(func=cmd_dashboard)

    web = sub.add_parser("web", help="Launch autonomous intraday replay web dashboard.")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8080)
    web.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS[:3])
    web.add_argument("--period", default="5d")
    web.add_argument("--interval", default="5m")
    web.add_argument("--strategy", choices=["buy-hold", "ma-cross", "mean-reversion"], default="ma-cross")
    web.add_argument("--tick-seconds", type=float, default=1.0, help="Seconds between replay bars.")
    web.add_argument("--no-loop", action="store_true", help="Stop at end of historical window instead of looping.")
    web.add_argument("--no-refresh", action="store_true", help="Reuse cached candles on startup.")
    web.add_argument("--no-intelligence", action="store_true", help="Disable intelligence filter on entries.")
    web.add_argument("--initial-cash", type=float, default=100_000.0)
    web.add_argument("--spread-bps", type=float, default=3.0)
    web.add_argument("--slippage-bps", type=float, default=2.0)
    web.add_argument("--max-position-pct", type=float, default=0.20)
    web.set_defaults(func=cmd_web)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
