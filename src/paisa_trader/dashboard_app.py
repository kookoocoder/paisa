from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from paisa_trader.config import BrokerConfig, DEFAULT_SYMBOLS
from paisa_trader.data import load_candles
from paisa_trader.intelligence import FilterConfig, ai_market_snapshot, enrich_indicators, estimate_depth, score_next_move
from paisa_trader.shadow import ShadowSession, run_shadow_session
from paisa_trader.strategies import build_strategy


def _inr(value: float) -> str:
    return f"₹{value:,.2f}"


def _pct(value: float) -> str:
    return f"{value:+.2f}%"


@st.cache_data(show_spinner=False)
def _load_session(
    symbols: tuple[str, ...],
    period: str,
    interval: str,
    strategy: str,
    initial_cash: float,
    spread_bps: float,
    slippage_bps: float,
    max_position_pct: float,
    export_bridge: bool,
) -> ShadowSession:
    broker_cfg = BrokerConfig(
        initial_cash=initial_cash,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
        max_position_pct=max_position_pct,
    )
    return run_shadow_session(
        list(symbols),
        period,
        interval,
        strategy,
        broker_cfg,
        force_refresh=True,
        export_bridge=export_bridge,
    )


def _latest_quote(result: ShadowSession, symbol: str) -> dict[str, float | pd.Timestamp]:
    equity = next(item for item in result.results if item.summary["symbol"] == symbol)
    last = equity.equity_curve.iloc[-1]
    prev = equity.equity_curve.iloc[-2] if len(equity.equity_curve) > 1 else last
    close = float(last["close"])
    prev_close = float(prev["close"])
    return {
        "timestamp": pd.Timestamp(last["timestamp"]),
        "close": close,
        "change_pct": ((close / prev_close) - 1) * 100 if prev_close else 0.0,
        "position": float(last["position"]),
        "target_position": float(last["target_position"]),
        "equity": float(last["equity"]),
    }


def _enriched_overlay(session: ShadowSession, symbol: str) -> pd.DataFrame | None:
    try:
        candles = load_candles(symbol, session.period, session.interval)
    except FileNotFoundError:
        return None
    signaled = build_strategy(session.strategy).signals(candles)
    enriched = enrich_indicators(signaled)
    return enriched


def _indicator_table(enriched: pd.DataFrame) -> pd.DataFrame:
    last = enriched.iloc[-1]
    keys = [
        ("Close", "close"),
        ("Volume", "volume"),
        ("1-bar return", "return_1"),
        ("5-bar return", "return_5"),
        ("SMA 10", "sma_10"),
        ("SMA 30", "sma_30"),
        ("RSI 14", "rsi_14"),
        ("MACD", "macd"),
        ("MACD signal", "macd_signal"),
        ("MACD hist", "macd_hist"),
        ("BB low", "bb_low"),
        ("BB mid", "bb_mid"),
        ("BB high", "bb_high"),
        ("VWAP proxy", "vwap_proxy"),
        ("Relative volume", "relative_volume"),
        ("Estimated spread bps", "estimated_spread_bps"),
    ]
    rows = []
    for name, key in keys:
        value = last.get(key)
        rows.append({"indicator": name, "value": None if pd.isna(value) else float(value)})
    return pd.DataFrame(rows)


def render_symbol_tab(session: ShadowSession, symbol: str, filter_cfg: FilterConfig) -> dict:
    result = next(item for item in session.results if item.summary["symbol"] == symbol)
    quote = _latest_quote(session, symbol)
    summary = result.summary
    enriched = _enriched_overlay(session, symbol)
    assert enriched is not None
    latest_target = float(enriched["target_position"].iloc[-1]) if "target_position" in enriched else 0.0
    next_move = score_next_move(enriched, filter_cfg)
    snapshot = ai_market_snapshot(symbol, enriched, latest_target, filter_cfg)

    left, right = st.columns([2, 1])
    with left:
        st.subheader(symbol)
        st.caption(f"Last bar: {quote['timestamp']}")
    with right:
        st.metric("Close", _inr(float(quote["close"])), _pct(float(quote["change_pct"])))

    metrics = st.columns(5)
    metrics[0].metric("Equity", _inr(float(summary["final_equity"])))
    metrics[1].metric("Return", _pct(float(summary["total_return_pct"])))
    metrics[2].metric("Max DD", _pct(float(summary["max_drawdown_pct"])))
    metrics[3].metric("Trades", int(summary["trades"]))
    metrics[4].metric("Next move score", f"{next_move['score']:.1f}", next_move["direction"])

    price_df = result.equity_curve.set_index("timestamp")[["close"]]
    equity_df = result.equity_curve.set_index("timestamp")[["equity"]]
    chart_left, chart_right = st.columns(2)
    with chart_left:
        st.markdown("**Price**")
        st.line_chart(price_df)
    with chart_right:
        st.markdown("**Paper equity**")
        st.line_chart(equity_df)

    chart_data = enriched.set_index("timestamp")[
        ["close", "sma_10", "sma_30", "bb_low", "bb_high"]
    ].dropna(how="all")
    st.markdown("**Indicator overlay**")
    st.line_chart(chart_data)

    signal_box = st.container(border=True)
    with signal_box:
        st.markdown("**Next move indication**")
        cols = st.columns([1, 1, 2])
        cols[0].metric("Direction", str(next_move["direction"]).upper())
        cols[1].metric("Action", str(next_move["action"]).replace("_", " "))
        cols[2].write(", ".join(next_move["reasons"][:4]) or "No strong reason yet.")
        if next_move["disqualifiers"]:
            st.warning("Disqualified: " + ", ".join(next_move["disqualifiers"]))
        elif next_move["paper_trade_candidate"]:
            st.success("Passes current filters for paper-trade consideration.")
        else:
            st.info("Does not pass current score threshold.")

    table_left, table_right = st.columns([1, 1])
    with table_left:
        st.markdown("**Indicator table for AI**")
        st.dataframe(_indicator_table(enriched), width="stretch", hide_index=True)
    with table_right:
        st.markdown("**Estimated depth**")
        st.caption("Synthetic estimate from candle/range/volume, not real exchange order book.")
        st.dataframe(estimate_depth(enriched.iloc[-1]), width="stretch", hide_index=True)

    st.markdown("**Recent fills**")
    if result.fills.empty:
        st.write("No paper fills in this window.")
    else:
        st.dataframe(result.fills.tail(10), width="stretch")

    target = float(quote["target_position"])
    position = float(quote["position"])
    if target > 0:
        st.success(f"Shadow signal: LONG target {target:.0%} · open paper position {position:.0f} shares")
    else:
        st.info("Shadow signal: FLAT · no target exposure on the latest bar")

    with st.expander("AI-readable snapshot JSON", expanded=False):
        payload = json.dumps(snapshot, indent=2)
        st.code(payload, language="json")
        st.download_button(
            "Download snapshot JSON",
            payload,
            file_name=f"{symbol.replace('.', '_')}_ai_snapshot.json",
            mime="application/json",
        )
    return snapshot


def main() -> None:
    st.set_page_config(page_title="Paisa Trader", page_icon="PT", layout="wide")
    st.title("Paisa Trader")
    st.caption("Live-delayed paper dashboard: indicators, filters, AI snapshots, simulated fills, no real orders.")

    with st.sidebar:
        st.header("Market view")
        symbols = st.multiselect("Symbols", DEFAULT_SYMBOLS, default=DEFAULT_SYMBOLS[:3])
        strategy = st.selectbox("Strategy", ["ma-cross", "buy-hold", "mean-reversion"])
        period = st.selectbox("Period", ["1mo", "3mo", "6mo", "1y", "2y", "60d", "90d"], index=5)
        interval = st.selectbox("Interval", ["1d", "1h", "30m", "15m", "5m"], index=0)
        st.header("Filters")
        min_volume = st.number_input("Min volume", min_value=0.0, value=100_000.0, step=50_000.0)
        max_spread_bps = st.number_input("Max estimated spread bps", min_value=1.0, value=25.0, step=1.0)
        min_signal_score = st.slider("Min signal score", min_value=0.0, max_value=100.0, value=55.0, step=1.0)
        st.header("Paper broker")
        initial_cash = st.number_input("Initial cash (INR)", min_value=10_000.0, value=100_000.0, step=10_000.0)
        spread_bps = st.number_input("Spread (bps)", min_value=0.0, value=3.0, step=0.5)
        slippage_bps = st.number_input("Slippage (bps)", min_value=0.0, value=2.0, step=0.5)
        max_position_pct = st.slider("Max position %", min_value=0.05, max_value=1.0, value=0.20, step=0.05)
        export_bridge = st.checkbox("Export StockSharp bridge on refresh", value=False)
        refresh = st.button("Refresh delayed data", type="primary", width="stretch")

    if not symbols:
        st.warning("Select at least one symbol in the sidebar.")
        return
    filter_cfg = FilterConfig(min_volume=min_volume, max_spread_bps=max_spread_bps, min_signal_score=min_signal_score)

    if refresh:
        _load_session.clear()

    if refresh or "session" not in st.session_state:
        with st.spinner("Downloading delayed candles and replaying shadow strategy..."):
            session = _load_session(
                tuple(symbols),
                period,
                interval,
                strategy,
                initial_cash,
                spread_bps,
                slippage_bps,
                max_position_pct,
                export_bridge,
            )
        st.session_state.session = session

    session: ShadowSession = st.session_state.session
    st.success(
        f"Shadow session refreshed at {session.refreshed_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')} "
        f"· report `{session.report_dir.name}`"
    )
    if session.bridge_dir is not None:
        st.info(f"StockSharp bridge exported to `{session.bridge_dir}`")

    summary_df = pd.DataFrame([result.summary for result in session.results])
    total_equity = float(summary_df["final_equity"].sum())
    total_return = ((total_equity / (initial_cash * len(session.symbols))) - 1) * 100
    headline = st.columns(4)
    headline[0].metric("Symbols", len(session.symbols))
    headline[1].metric("Combined paper equity", _inr(total_equity))
    headline[2].metric("Avg return", _pct(total_return))
    headline[3].metric("Total trades", int(summary_df["trades"].sum()))

    st.markdown("**Portfolio summary**")
    st.dataframe(summary_df, width="stretch")

    st.markdown("**AI harness contract**")
    st.caption("Each symbol tab exposes a JSON snapshot with indicators, filters, estimated depth, signal score, and current paper target.")

    snapshots = {}
    tabs = st.tabs(list(session.symbols))
    for tab, symbol in zip(tabs, session.symbols):
        with tab:
            snapshots[symbol] = render_symbol_tab(session, symbol, filter_cfg)

    with st.expander("Combined AI snapshot for all selected symbols", expanded=False):
        combined = json.dumps(
            {
                "session": {
                    "refreshed_at": session.refreshed_at.isoformat(),
                    "strategy": session.strategy,
                    "period": session.period,
                    "interval": session.interval,
                    "data_source": "yfinance delayed candles; synthetic depth estimates",
                },
                "symbols": snapshots,
            },
            indent=2,
        )
        st.code(combined, language="json")
        st.download_button("Download combined AI snapshot", combined, "paisa_ai_snapshot.json", "application/json")


if __name__ == "__main__":
    main()
