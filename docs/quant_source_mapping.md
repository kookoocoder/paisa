# Quant Source Mapping

This note maps the local trading source material to concrete Paisa modules. It is an implementation map, not a reproduction of copyrighted book text.

## Sources

- `technical analysis - PDF Room.pdf`: use as a taxonomy for chart calculation: trend, momentum, volatility, volume, and market structure.
- `Algorithmic Trading - 2013 - Chan - Front Matter.pdf`: use as a roadmap for backtesting/execution, mean reversion, momentum, and risk management.
- `automated_intraday_india_plan.md`: use as the project-specific constraint layer for Indian markets, delayed/free data, costs, synthetic depth, and paper-only validation.
- `README.md`: use as the current product contract: no real orders, delayed data, simulated broker, StockSharp bridge, and baseline strategies.

## Concept To Module Map

| Quant concept | Project target | Implementation direction |
|---|---|---|
| Trend structure | `src/paisa_trader/indicators.py`, `src/paisa_trader/intelligence.py` | EMA/SMA separation, slope, ADX/DMI, Donchian breakout state. |
| Momentum | `src/paisa_trader/indicators.py`, `src/paisa_trader/intelligence.py` | RSI, MACD histogram, ROC, stochastic oscillator, normalized momentum factor. |
| Volatility | `src/paisa_trader/indicators.py`, `src/paisa_trader/intelligence.py` | True range, ATR, realized volatility, Bollinger bandwidth, ATR percent. |
| Volume/participation | `src/paisa_trader/indicators.py`, `src/paisa_trader/intelligence.py` | Relative volume, OBV and OBV slope, session VWAP distance. |
| Mean reversion | `src/paisa_trader/strategies.py`, `src/paisa_trader/intelligence.py` | Z-score displacement, VWAP/SMA distance, range-regime filters. |
| Momentum systems | `src/paisa_trader/strategies.py`, `src/paisa_trader/intelligence.py` | Trend and breakout factors, ADX confirmation, volume confirmation. |
| Backtesting discipline | `src/paisa_trader/backtest.py`, `src/paisa_trader/report.py` | Keep current metrics stable now; later add walk-forward and benchmark controls. |
| Risk management | `src/paisa_trader/broker.py`, `src/paisa_trader/ai_harness/decision_router.py` | Continue ATR-aware sizing now; later add kill switches and daily loss caps. |
| Execution realism | `src/paisa_trader/broker.py` | Current market-fill simulator stays unchanged in this pass; later add limit/IOC and latency models. |
| AI as overlay | `src/paisa_trader/snapshot.py`, `src/paisa_trader/ai_harness/context_builder.py` | Feed reproducible factor scores and regime data to the model; do not let the model replace the math layer. |

## First Implementation Pass

The first pass should create a reliable calculation core:

1. Centralize indicator formulas in `indicators.py`.
2. Refactor `enrich_indicators()` to call the shared indicator library.
3. Add deterministic regime/factor columns.
4. Replace fixed-point `score_next_move()` rules with normalized factor scoring.
5. Add tests for indicator invariants and feature/regime behavior.

## Guardrails

- Prefer deterministic numeric formulas over narrative chart labels.
- Keep the existing snapshot and dashboard contracts backward compatible.
- Use current delayed `yfinance` data only for research and UI demonstration.
- Treat hit rate as diagnostic, not as proof of edge; later phases should add net expectancy and walk-forward validation.
