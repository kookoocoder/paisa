from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .backtest import run_symbol_backtest
from .bridge import export_stocksharp_package
from .config import AIHarnessConfig, BrokerConfig, DEFAULT_SYMBOLS, ensure_dirs, load_ai_harness_config, load_ml_config
from .data import CandleRequest, download_candles, fetch_upstox_quotes, load_candles
from .nse import fetch_equity_bhavcopy, parse_date
from .report import write_backtest_report
from .shadow import run_shadow_session
from .strategies import build_strategy


def cmd_symbols(_: argparse.Namespace) -> int:
    for symbol in DEFAULT_SYMBOLS:
        print(symbol)
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    request = CandleRequest(
        symbols=args.symbols,
        period=args.period,
        interval=args.interval,
    )
    paths = download_candles(request, force=args.force)
    for symbol, path in paths.items():
        print(f"{symbol}: {path}")
    return 0


def cmd_upstox_quote(args: argparse.Namespace) -> int:
    quotes = fetch_upstox_quotes(args.symbols, full=not args.ltp)
    print(json.dumps(quotes, indent=2, sort_keys=True, default=str))
    return 0


def cmd_nse_bhavcopy(args: argparse.Namespace) -> int:
    result = fetch_equity_bhavcopy(parse_date(args.date))
    print(f"Saved {result.date} bhavcopy to {result.path}")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    ensure_dirs()
    if args.download:
        download_candles(
            CandleRequest(
                symbols=args.symbols,
                period=args.period,
                interval=args.interval,
            ),
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
            CandleRequest(
                symbols=args.symbols,
                period=args.period,
                interval=args.interval,
            ),
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


def cmd_live(args: argparse.Namespace) -> int:
    import uvicorn

    from .live_web_app import build_live_config, create_app

    config = build_live_config(
        trade_symbols=args.trade_symbols,
        period=args.period,
        interval=args.interval,
        poll_seconds=args.poll_seconds,
        use_intelligence_filter=not args.no_intelligence,
        initial_cash=args.initial_cash,
        spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps,
        max_position_pct=args.max_position_pct,
        atr_sl_mult=args.atr_sl_mult,
        atr_target_mult=args.atr_target_mult,
        trail_atr_mult=args.trail_atr_mult,
    )
    app = create_app(config)
    print(f"Paisa live paper dashboard: http://{args.host}:{args.port}")
    print("Live Upstox LTP execution only. Cached candles used for models/indicators.")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


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


def cmd_ai_web(args: argparse.Namespace) -> int:
    import uvicorn

    from .ai_web_server import build_ai_web_config, create_app

    ai_cfg = _ai_config_from_args(args)
    symbols = args.symbols or ai_cfg.symbols or DEFAULT_SYMBOLS[:3]
    tick_seconds = args.tick_seconds
    if tick_seconds == 1.0:
        tick_seconds = ai_cfg.bar_interval_sec
    config = build_ai_web_config(
        symbols=symbols,
        period=args.period,
        interval=args.interval,
        tick_seconds=tick_seconds,
        loop=not args.no_loop,
        force_refresh=not args.no_refresh,
        initial_cash=args.initial_cash,
        spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps,
        max_position_pct=args.max_position_pct,
        ai_cfg=ai_cfg,
    )
    app = create_app(config)
    print(f"Paisa AI market intelligence harness: http://{args.host}:{args.port}")
    print("Paper-only AI replay is running. No real broker orders are placed.")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def cmd_ai_backtest(args: argparse.Namespace) -> int:
    from .ai_session import run_ai_backtest_sync
    from .intelligence import FilterConfig

    ai_cfg = _ai_config_from_args(args)
    symbols = args.symbols or ai_cfg.symbols or DEFAULT_SYMBOLS[:3]
    broker_cfg = BrokerConfig(
        initial_cash=args.initial_cash,
        spread_bps=args.spread_bps,
        slippage_bps=args.slippage_bps,
        max_position_pct=args.max_position_pct,
    )
    result = run_ai_backtest_sync(
        symbols,
        args.period,
        args.interval,
        download=args.download,
        force=args.force,
        broker_cfg=broker_cfg,
        filter_cfg=FilterConfig(),
        ai_cfg=ai_cfg,
    )
    print(f"AI session written to {result.report_dir}")
    print(f"HTML report: {result.report_dir / 'report.html'}")
    print(result.summary)
    return 0


def cmd_ai_report(args: argparse.Namespace) -> int:
    from .ai_session import latest_ai_session_dir, write_ai_session_report

    session_dir = Path(args.session_dir) if args.session_dir else latest_ai_session_dir()
    report = write_ai_session_report(session_dir)
    print(f"AI report written to {report}")
    return 0


def cmd_train_ml(args: argparse.Namespace) -> int:
    from .data import download_candles
    from .ml_models import train_models

    base_ai = load_ai_harness_config()
    symbols = args.symbols or base_ai.symbols or DEFAULT_SYMBOLS[:3]
    ml_cfg = load_ml_config()
    rows = []
    for symbol in symbols:
        candles = download_candles(
            symbol,
            interval=args.interval,
            period=args.period,
        )
        if candles.empty:
            print(f"{symbol}: no candles returned; skipped")
            continue
        result = train_models(candles, symbol, save_dir=str(ml_cfg.model_dir))
        rows.append(
            {
                "symbol": symbol,
                "xgb_accuracy": f"{result['xgb_accuracy']:.3f}",
                "lgbm_accuracy": f"{result['lgbm_accuracy']:.3f}",
                "n_train": result["n_train"],
                "n_test": result["n_test"],
            }
        )
    if not rows:
        print("No ML models were trained.")
        return 1
    headers = ["symbol", "xgb_accuracy", "lgbm_accuracy", "n_train", "n_test"]
    print("\t".join(headers))
    for row in rows:
        print("\t".join(str(row[key]) for key in headers))
    return 0


def _ai_config_from_args(args: argparse.Namespace) -> AIHarnessConfig:
    base = load_ai_harness_config()
    provider = args.model_provider or base.model_provider
    default_model = "mock" if provider == "mock" else ("auto" if provider == "lmstudio" else base.model_name)
    return AIHarnessConfig(
        model_provider=provider,
        model_name=args.model_name or default_model,
        api_key_env=args.api_key_env or base.api_key_env,
        temperature=args.temperature if args.temperature is not None else base.temperature,
        max_tokens=args.max_tokens if args.max_tokens is not None else base.max_tokens,
        local_url=args.local_url or base.local_url,
        decision_min_confidence=base.decision_min_confidence,
        position_size_pct=base.position_size_pct,
        symbols=args.symbols or base.symbols,
        bar_interval_sec=base.bar_interval_sec,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paisa", description="No-demat India trading research harness.")
    sub = parser.add_subparsers(dest="command", required=True)

    symbols = sub.add_parser("symbols", help="Print default NSE symbols.")
    symbols.set_defaults(func=cmd_symbols)

    download = sub.add_parser("download", help="Download Upstox OHLCV candles.")
    download.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    download.add_argument("--period", default="5d")
    download.add_argument("--interval", default="5minute")
    download.add_argument("--force", action="store_true")
    download.set_defaults(func=cmd_download)

    upstox_quote = sub.add_parser("upstox-quote", help="Fetch current Upstox market quotes.")
    upstox_quote.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS[:3])
    upstox_quote.add_argument("--ltp", action="store_true", help="Fetch only last-traded-price data.")
    upstox_quote.set_defaults(func=cmd_upstox_quote)

    bhav = sub.add_parser("nse-bhavcopy", help="Fetch NSE equity bhavcopy for YYYY-MM-DD.")
    bhav.add_argument("--date", required=True)
    bhav.set_defaults(func=cmd_nse_bhavcopy)

    backtest = sub.add_parser("backtest", help="Run a simulated backtest.")
    backtest.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS[:3])
    backtest.add_argument("--period", default="6mo")
    backtest.add_argument("--interval", default="1day")
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
    ss.add_argument("--interval", default="1day")
    ss.add_argument("--strategy", choices=["buy-hold", "ma-cross", "mean-reversion"], default="ma-cross")
    ss.add_argument("--download", action="store_true", help="Download missing data before exporting.")
    ss.add_argument("--force", action="store_true", help="Force data refresh when used with --download.")
    ss.add_argument("--initial-cash", type=float, default=100_000.0)
    ss.add_argument("--spread-bps", type=float, default=3.0)
    ss.add_argument("--slippage-bps", type=float, default=2.0)
    ss.add_argument("--max-position-pct", type=float, default=0.20)
    ss.set_defaults(func=cmd_stocksharp_export)

    shadow = sub.add_parser("shadow", help="Refresh Upstox candles and run a shadow paper session.")
    shadow.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS[:3])
    shadow.add_argument("--period", default="3mo")
    shadow.add_argument("--interval", default="1day")
    shadow.add_argument("--strategy", choices=["buy-hold", "ma-cross", "mean-reversion"], default="ma-cross")
    shadow.add_argument("--no-refresh", action="store_true", help="Reuse cached candles if present.")
    shadow.add_argument("--export-bridge", action="store_true", help="Also write a StockSharp bridge package.")
    shadow.add_argument("--initial-cash", type=float, default=100_000.0)
    shadow.add_argument("--spread-bps", type=float, default=3.0)
    shadow.add_argument("--slippage-bps", type=float, default=2.0)
    shadow.add_argument("--max-position-pct", type=float, default=0.20)
    shadow.set_defaults(func=cmd_shadow)

    dashboard = sub.add_parser("dashboard", help="Launch the Streamlit paper dashboard.")
    dashboard.add_argument("--port", type=int, default=8501)
    dashboard.set_defaults(func=cmd_dashboard)

    web = sub.add_parser("web", help="Launch autonomous intraday replay web dashboard.")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8080)
    web.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS[:3])
    web.add_argument("--period", default="5d")
    web.add_argument("--interval", default="5minute")
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

    live = sub.add_parser("live", help="Launch live Upstox paper trading dashboard with full NSE market view.")
    live.add_argument("--host", default="127.0.0.1")
    live.add_argument("--port", type=int, default=8080)
    live.add_argument(
        "--trade-symbols",
        nargs="+",
        default=DEFAULT_SYMBOLS,
        help="Symbols to run the paper strategy on.",
    )
    live.add_argument("--period", default="5d")
    live.add_argument("--interval", default="5minute")
    live.add_argument("--poll-seconds", type=float, default=15.0, help="Seconds between live quote refreshes.")
    live.add_argument("--atr-sl-mult", type=float, default=1.5, help="ATR multiplier for initial stop loss.")
    live.add_argument("--atr-target-mult", type=float, default=2.5, help="ATR multiplier for profit target.")
    live.add_argument("--trail-atr-mult", type=float, default=1.0, help="ATR multiplier for trailing stop.")
    live.add_argument("--no-intelligence", action="store_true", help="Disable intelligence filter on entries.")
    live.add_argument("--initial-cash", type=float, default=100_000.0)
    live.add_argument("--spread-bps", type=float, default=3.0)
    live.add_argument("--slippage-bps", type=float, default=2.0)
    live.add_argument("--max-position-pct", type=float, default=0.20)
    live.set_defaults(func=cmd_live)

    ai_web = sub.add_parser("ai-web", help="Launch AI market intelligence harness dashboard.")
    ai_web.add_argument("--host", default="127.0.0.1")
    ai_web.add_argument("--port", type=int, default=8082)
    ai_web.add_argument("--symbols", nargs="+", default=None)
    ai_web.add_argument("--period", default="5d")
    ai_web.add_argument("--interval", default="5minute")
    ai_web.add_argument("--tick-seconds", type=float, default=1.0, help="Seconds between replay bars.")
    ai_web.add_argument("--no-loop", action="store_true", help="Stop at end of historical window instead of looping.")
    ai_web.add_argument("--no-refresh", action="store_true", help="Reuse cached candles on startup.")
    ai_web.add_argument("--initial-cash", type=float, default=100_000.0)
    ai_web.add_argument("--spread-bps", type=float, default=3.0)
    ai_web.add_argument("--slippage-bps", type=float, default=2.0)
    ai_web.add_argument("--max-position-pct", type=float, default=0.20)
    ai_web.add_argument("--model-provider", choices=["mock", "claude", "openai", "local", "lmstudio"], default=None)
    ai_web.add_argument("--model-name", default=None)
    ai_web.add_argument("--api-key-env", default=None)
    ai_web.add_argument("--local-url", default=None, help="Base URL for local model providers such as LM Studio.")
    ai_web.add_argument("--temperature", type=float, default=None)
    ai_web.add_argument("--max-tokens", type=int, default=None)
    ai_web.set_defaults(func=cmd_ai_web)

    ai_backtest = sub.add_parser("ai-backtest", help="Run AI model over historical bars.")
    ai_backtest.add_argument("--symbols", nargs="+", default=None)
    ai_backtest.add_argument("--period", default="5d")
    ai_backtest.add_argument("--interval", default="5minute")
    ai_backtest.add_argument("--download", action="store_true", help="Download missing data before running.")
    ai_backtest.add_argument("--force", action="store_true", help="Force data refresh when used with --download.")
    ai_backtest.add_argument("--initial-cash", type=float, default=100_000.0)
    ai_backtest.add_argument("--spread-bps", type=float, default=3.0)
    ai_backtest.add_argument("--slippage-bps", type=float, default=2.0)
    ai_backtest.add_argument("--max-position-pct", type=float, default=0.20)
    ai_backtest.add_argument("--model-provider", choices=["mock", "claude", "openai", "local", "lmstudio"], default=None)
    ai_backtest.add_argument("--model-name", default=None)
    ai_backtest.add_argument("--api-key-env", default=None)
    ai_backtest.add_argument("--local-url", default=None, help="Base URL for local model providers such as LM Studio.")
    ai_backtest.add_argument("--temperature", type=float, default=None)
    ai_backtest.add_argument("--max-tokens", type=int, default=None)
    ai_backtest.set_defaults(func=cmd_ai_backtest)

    ai_report = sub.add_parser("ai-report", help="Generate an HTML report for an AI session.")
    ai_report.add_argument("--session-dir", default=None, help="Session directory. Defaults to latest reports/ai_sessions session.")
    ai_report.set_defaults(func=cmd_ai_report)

    train_ml = sub.add_parser("train-ml", help="Train XGBoost + LightGBM models from Upstox candles.")
    train_ml.add_argument("--symbols", nargs="+", default=None)
    train_ml.add_argument("--period", default="60d")
    train_ml.add_argument("--interval", default="5minute")
    train_ml.set_defaults(func=cmd_train_ml)

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
