# Paisa Trader — Master Reference

**Document date:** 22 June 2026  
**Package version:** 0.2.0 (`pyproject.toml`; `__init__.py` still says 0.1.0 — see [Known inconsistencies](#known-inconsistencies))  
**Audience:** Developers, operators, and AI agents working on this repo  
**Purpose:** Single source of truth for what Paisa is, how it works, why it works, why it might not, what it looks like, how to configure and run it, and what guardrails apply.

---

## Table of contents

1. [What Paisa is](#1-what-paisa-is)
2. [Architecture and data flow](#2-architecture-and-data-flow)
3. [Module reference](#3-module-reference)
4. [How it works — end-to-end paths](#4-how-it-works--end-to-end-paths)
5. [Signal layer deep dive](#5-signal-layer-deep-dive)
6. [Why it works — design rationale](#6-why-it-works--design-rationale)
7. [Why it does not work — limitations and failure modes](#7-why-it-does-not-work--limitations-and-failure-modes)
8. [UI and looks](#8-ui-and-looks)
9. [Hooks, rules, and automation](#9-hooks-rules-and-automation)
10. [Configuration](#10-configuration)
11. [CLI reference](#11-cli-reference)
12. [Testing and CI](#12-testing-and-ci)
13. [StockSharp bridge](#13-stocksharp-bridge)
14. [Safety invariants](#14-safety-invariants)
15. [Roadmap](#15-roadmap)
16. [Known inconsistencies](#16-known-inconsistencies)
17. [Related documents](#17-related-documents)

---

## 1. What Paisa is

### Mission

**Paisa Trader** is a **no-demat, paper-only research harness** for Indian NSE equity intraday and swing experiments. It ingests broker-backed market data (Upstox), combines rule-based signals with gradient-boosted ML and optional LLM decisions, runs everything through a simulated broker with India-style transaction costs, and exposes results through CLI tools, four web surfaces, Streamlit, and HTML reports.

The goal is to **prove or disprove trading edges in a controlled environment** before connecting a demat account, broker order APIs, or production risk controls.

### Current stage

| Phase | Status | Notes |
|-------|--------|-------|
| Data ingestion | Done | Upstox candles/quotes, NSE bhavcopy fallback |
| Simulated execution | Done | Long-only, India cost stack, spread/slippage |
| Rule baselines | Done | `buy-hold`, `ma-cross`, `mean-reversion` |
| Feature / signal layer | Done | Regime-aware scoring, `MarketSnapshot`, synthetic depth |
| ML direction models | Wired | XGBoost + LightGBM per symbol; ARIMA tiebreaker |
| Sentiment (FinBERT) | Wired | Lazy CPU load + lexical fallback |
| AI harness (LLM) | Working | LM Studio / mock / Claude / OpenAI; prediction tracking |
| Web replay dashboards | Working | Rule replay (8080), AI replay (8082), live paper |
| StockSharp bridge | Working | CSV + manifest export for .NET paper harness |
| Live broker / orders | **Not started** | No demat, no order placement, no WebSocket feed |
| Production controls | **Not started** | Kill switches, RMS, static IP, audit compliance |

### What Paisa is

- A Python 3.11+ package (`paisa-trader`) with CLI entry point `paisa`
- A layered signal stack: indicators → intelligence → optional ML/sentiment/LLM → broker
- A paper broker (`SimulatedBroker`) with realistic India cost modeling
- Four interactive UIs plus offline HTML/Markdown reports
- A cross-language bridge to StockSharp for deterministic .NET replay
- A test suite (17 pytest modules, ~77 test functions) enforcing invariants

### What Paisa is not

- **Not a live trading system** — it does not place real orders
- **Not investment advice** — research harness only
- **Not real-time tick data** — Upstox REST is delayed; depth is synthetic
- **Not a shorting platform** — long-only; bearish signals block or exit longs
- **Not production-ready** — no RMS, margin modeling, kill switches, or SEBI algo compliance
- **Not a substitute for broker behavior** — no partial fills, rejections, or auction logic

---

## 2. Architecture and data flow

### High-level diagram

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                           DATA SOURCES                                   │
│  Upstox REST (candles, quotes)  │  NSE bhavcopy  │  Cached parquet/CSV  │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────────┐
│                      FEATURE & SIGNAL LAYER                              │
│  indicators.py  →  intelligence.py  →  MarketSnapshot                    │
│  ml_models.py (XGB/LGBM)  │  arima_model.py  │  sentiment.py (FinBERT)  │
│  wavetrail.py  │  calibration.py                                         │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
         ▼                   ▼                   ▼
┌────────────────┐  ┌─────────────────┐  ┌──────────────────┐
│ Rule strategies│  │  AI harness     │  │  Shadow / replay │
│ strategies.py  │  │  ai_harness/    │  │  replay.py       │
│ backtest.py    │  │  ai_session.py  │  │  shadow.py       │
└───────┬────────┘  └────────┬────────┘  └────────┬─────────┘
        │                    │                     │
        └────────────────────┼─────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    SimulatedBroker (broker.py)                           │
│  Long-only │ India costs │ spread/slippage │ equity curve │ fill logs   │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
   web_app.py          ai_web_server.py     report.py / bridge.py
   live_web_app.py     dashboard_app.py     HTML + StockSharp export
   (ports 8080/8501)   (port 8082)
```

### Data ingestion

1. **Upstox historical candles** (`data.py`)
   - Requires `UPSTOX_ANALYTICS_TOKEN` (or name from `[data].token_env_var` in `paisa.toml`)
   - Resolves symbols via `COMMON_INSTRUMENT_KEYS` or Upstox BOD instrument master
   - Writes parquet to `data/processed/{symbol}__{period}__{interval}.parquet`

2. **Upstox live quotes** (`fetch_upstox_quotes`)
   - Full or LTP-only snapshots for `paisa upstox-quote` and `LivePaperEngine`

3. **NSE bhavcopy** (`nse.py`)
   - Dual URL fallback: legacy `cm{DD}{MON}{YYYY}bhav.csv.zip` and newer UDiFF archives
   - Saves to `data/raw/nse_equity_bhavcopy_{date}.zip`
   - Fails loudly with attempted URLs if NSE changes layout

### Output artifacts

| Output | Path pattern | Producer |
|--------|--------------|----------|
| Cached candles | `data/processed/*.parquet` | `paisa download` |
| Calibration state | `data/calibration.json` | `calibration.py` |
| ML models | `models/{SYMBOL}_xgb.pkl`, `*_lgbm.pkl` | `paisa train-ml` |
| Backtest reports | `reports/backtest_{strategy}_{timestamp}/` | `report.py` |
| Shadow sessions | same + `shadow.json` | `shadow.py` |
| AI sessions | `reports/ai_sessions/` + `report.html` | `ai_session.py` |
| StockSharp bridge | `reports/stocksharp_bridge/{strategy}_{timestamp}/` | `bridge.py` |

All of `data/processed/`, `reports/`, and `models/` are gitignored and regenerated by commands.

---

## 3. Module reference

### Core package (`src/paisa_trader/`)

| Module | Responsibility |
|--------|----------------|
| `cli.py` | Entry point `paisa`; 14 subcommands via argparse |
| `config.py` | TOML loaders, `BrokerConfig`, `CostConfig`, path constants |
| `data.py` | Candle download, Upstox instrument resolution, quote fetch, cache |
| `nse.py` | NSE equity bhavcopy with dual URL fallback |
| `indicators.py` | Pure technical library: SMA, EMA, RSI, MACD, BB, ATR, DMI, VWAP, Donchian, OBV, session phase |
| `intelligence.py` | Regime-weighted scoring, filters, synthetic depth, ML/sentiment integration |
| `snapshot.py` | Typed `MarketSnapshot` for AI prompts and dashboards |
| `ml_models.py` | 14-feature engineering; train/load XGBoost + LightGBM per symbol |
| `arima_model.py` | ARIMA tiebreaker when ML models disagree |
| `sentiment.py` | FinBERT headline scoring with lexical fallback |
| `calibration.py` | Rolling confidence bins, ECE, adjusted thresholds |
| `strategies.py` | Baselines: `buy-hold`, `ma-cross`, `mean-reversion` |
| `backtest.py` | Batch historical backtest per symbol |
| `broker.py` | Long-only paper fills, spread/slippage, India cost stack |
| `trade_stats.py` | Round-trip P&L, trade ledger, capital invested |
| `replay.py` | Autonomous intraday replay engine (`paisa web`) |
| `shadow.py` | Refresh Upstox + backtest + report (+ optional bridge) |
| `live_engine.py` | Live Upstox quote polling + paper trading loop |
| `live_trade.py` | `LiveTradeCall` schema, ATR stop/target/trailing |
| `web_app.py` | FastAPI rule replay server (port 8080) |
| `live_web_app.py` | FastAPI live paper server (shares `web_static/`) |
| `ai_web_server.py` | FastAPI AI replay server (port 8082) |
| `dashboard_app.py` | Streamlit research dashboard (port 8501) |
| `ai_session.py` | Batch AI backtest, JSONL session, HTML report |
| `report.py` | Backtest report writer (CSV + Markdown) |
| `bridge.py` | StockSharp package export (manifest + CSVs) |
| `wavetrail.py` | Multi-timeframe intraday action plan overlay for AI dashboard |

### AI harness subpackage (`src/paisa_trader/ai_harness/`)

| Module | Responsibility |
|--------|----------------|
| `context_builder.py` | System/user prompts from `MarketSnapshot`; JSON-only `TradeDecision` schema |
| `model_runner.py` | Providers: `mock`, `lmstudio`, `local` (Ollama), `claude`, `openai` |
| `decision_parser.py` | Parse LLM JSON → `TradeDecision`; fail-safe → HOLD |
| `decision_router.py` | Intelligence gate + calibrated confidence + ATR sizing → `SimulatedBroker` |
| `prediction_tracker.py` | Directional hit rate, +5m/+15m horizon settlement, ECE inputs |

### Static assets

| Path | Used by |
|------|---------|
| `web_static/index.html`, `app.js`, `styles.css` | `paisa web` and `paisa live` |
| `static/ai_dashboard/index.html`, `app.js`, `style.css` | `paisa ai-web` |

### StockSharp (vendored .NET)

- **Paisa-specific sample only:** `StockSharp/Samples/11_PaisaPaperHarness/`
- The rest of `StockSharp/` is upstream framework code (thousands of C# files) — ignore unless extending the bridge

---

## 4. How it works — end-to-end paths

### Path A: Rule backtest (`paisa backtest`)

```text
download/load candles → strategy.signals(candles) → run_symbol_backtest()
  → on target_position change: BUY/SELL at bar open
  → mark-to-market equity → final flatten at last bar close
  → write_backtest_report() → reports/backtest_*/
```

- Strategies output `target_position` in `[0, 1]` (long-only)
- Position size: `equity × max_position_pct × target_position // price`
- Three baselines: `buy-hold`, `ma-cross`, `mean-reversion`

### Path B: Shadow session (`paisa shadow`)

```text
force-refresh Upstox candles → same backtest loop per symbol
  → report dir + shadow.json metadata
  → optional stocksharp-export (--export-bridge)
```

Designed for cron/EOD automation (not yet wired in repo).

### Path C: Rule replay (`paisa web`, port 8080)

```text
download/cache intraday candles → ReplayEngine streams bars (tick-seconds)
  → strategy precomputed; intelligence filter blocks entries when paper_trade_candidate is false
  → broker fills at bar open → WebSocket push (equity, fills, signals, activity log)
  → auto-loop when session window ends
```

Useful flags: `--tick-seconds 0.5`, `--no-intelligence`, `--no-loop`, `--strategy ma-cross`

### Path D: Live paper (`paisa live`, default port 8080)

```text
poll Upstox quotes (poll-seconds) → full NSE universe screener view
  → LivePaperEngine trades subset with ATR stop/target/trailing (live_trade.py)
  → fills at live LTP (not bar-open model)
  → shared web_static/ UI with live-only panels (universe table, trade calls)
```

**Port collision:** `paisa web` and `paisa live` both default to 8080 — only one can run without `--port` override.

### Path E: AI batch (`paisa ai-backtest` / `paisa ai-report`)

```text
bar-by-bar build_market_snapshot (causal: iloc[:idx+1])
  → LLM call via model_runner → parse_trade_decision
  → DecisionRouter (gate + confidence + sizing) → SimulatedBroker
  → JSONL under reports/ai_sessions/ → HTML report with accuracy/P&L cards
```

### Path F: AI replay (`paisa ai-web`, port 8082)

Same bar loop as batch but async real-time. Adds prediction tracker settlement, WaveTrail overlay on price canvas, calibration-aware confidence gates. Bar cadence tied to LLM latency (~3–4 decisions/min on CPU).

### Path G: Streamlit research (`paisa dashboard`, port 8501)

Manual **Refresh Upstox data** triggers a shadow session. Shows price/equity charts, indicator tables, synthetic depth, AI snapshot JSON, fills, trade P&L. Optional StockSharp bridge export checkbox.

### Path H: StockSharp replay

```text
paisa stocksharp-export → manifest.json + CSVs
  → dotnet run StockSharp/Samples/11_PaisaPaperHarness/ -- <manifest.json>
```

Deterministic cross-language validation without live broker credentials.

---

## 5. Signal layer deep dive

### Layer 1: Indicators (`indicators.py`)

Pure numeric functions — no regimes or weights here. Includes SMA, EMA, RSI, MACD, Bollinger, ATR, DMI, Donchian, OBV, session VWAP, stochastic, session phase (NSE 09:15–15:30 IST), lag returns.

### Layer 2: Intelligence (`intelligence.py`)

**Regime classification** (`_market_regime_from_row`):

| Condition | Regime |
|-----------|--------|
| `relative_volume < 0.35` | `LOW_LIQUIDITY` |
| `atr_pct > 2.5` | `HIGH_VOL` |
| `adx_14 >= 20` and positive EMA spread | `TREND_UP` |
| `adx_14 >= 20` and negative EMA spread | `TREND_DOWN` |
| Else | `RANGE` |

**Regime weights** (`REGIME_WEIGHTS`) — five factors with regime-specific blending:

| Regime | trend | momentum | mean_reversion | participation | volatility |
|--------|-------|----------|----------------|---------------|------------|
| TREND_UP/DOWN | 0.40 | 0.35 | 0.05 | 0.15 | 0.05 |
| RANGE | 0.10 | 0.10 | 0.50 | 0.20 | 0.10 |
| HIGH_VOL | 0.20 | 0.20 | 0.10 | 0.15 | 0.35 |
| LOW_LIQUIDITY | 0.25 | 0.25 | 0.20 | 0.25 | 0.05 |

**Scoring** (`score_next_move`):

```text
weighted = Σ(active_weights[factor] × factor_score[factor])
score = clip(50 + weighted × 50, 0, 100)
```

Direction thresholds (hardcoded in code — see inconsistencies):
- `score >= 62` → bullish / `paper_long_candidate`
- `score <= 38` → bearish / `avoid_long_or_exit`
- Else → neutral / `no_trade`

`paper_trade_candidate` requires: non-`no_trade`, volume/spread filters pass, regime ≠ `LOW_LIQUIDITY`, `score >= min_signal_score` (default 55).

**Tradability filters** (`FilterConfig` defaults in `intelligence.py`):

| Gate | Default |
|------|---------|
| `min_volume` | 100,000 |
| `max_spread_bps` | 25.0 |
| `min_signal_score` | 55.0 |

Streamlit dashboard exposes sliders for these; replay/live respect the same gates when intelligence filter is enabled.

**Synthetic depth** (`estimate_depth`): 5-level bid/ask ladder from `estimated_spread_bps` and volume — **not exchange DOM**. Always labeled as estimated in UI and snapshots.

### Layer 3: ML (`ml_models.py`)

- 14 engineered features; label = next-bar direction (up/down)
- Per-symbol XGBoost + LightGBM → `models/{SYMBOL}_xgb.pkl`, `*_lgbm.pkl`
- Gated by `[ml].min_confidence` (default 0.55)
- ARIMA(2,1,2) tiebreaker when models disagree (`use_arima_tiebreaker = true`)
- **Caution:** `predict()` auto-trains if pickles missing — risk of in-sample overfit on first run

### Layer 4: Sentiment (`sentiment.py`)

- Model: `ProsusAI/finbert` via HuggingFace `transformers` (optional dep, not in core `pyproject.toml`)
- Install separately: `pip install transformers torch accelerate`
- Lexical keyword fallback on timeout/error
- Often scores on generic/default headlines — not a live news stream

### Layer 5: MarketSnapshot (`snapshot.py`)

Typed bar + portfolio state for AI and dashboards. Exposes:
- OHLCV, indicators, synthetic microstructure
- `next_move_score`, `next_move_label`, `confidence`
- **Observability:** `market_regime`, `factor_scores`, `signal_components` (includes `active_weights`)
- `intelligence_gate`, ML/sentiment/ARIMA fields
- Methods: `to_ai_prompt()`, `to_dict()`

### Layer 6: AI harness

- LLM proposes `TradeDecision` (BUY/SELL/HOLD + confidence + future_predictions)
- `DecisionRouter` enforces: intelligence gate, no duplicate position, calibrated confidence threshold, ATR-based sizing
- `prediction_tracker` settles +5m/+15m directional forecasts; feeds `ConfidenceCalibrator`

### India cost stack (`broker.py` / `CostConfig`)

| Component | Buy | Sell |
|-----------|-----|------|
| Brokerage | 0 bps | 0 bps |
| Exchange txn | 0.307 bps | 0.307 bps |
| SEBI | 0.01 bps | 0.01 bps |
| Stamp duty | 0.3 bps | — |
| STT | — | 2.5 bps |
| GST | 18% on (brokerage + exchange + SEBI) | same |

Plus configurable `spread_bps` (default 3) and `slippage_bps` (default 2). Sell costs exceed buy costs.

---

## 6. Why it works — design rationale

1. **Baselines first** — Simple strategies (`buy-hold`, `ma-cross`, `mean-reversion`) verify data, costs, fills, and reporting before trusting ML or LLM layers. If baselines fail after costs, the stack likely fails too.

2. **India-first costs** — STT on sells, stamp on buys, GST on charges, spread, and slippage are baked into every fill. Ignoring them inflates backtest returns on intraday strategies.

3. **Regime-adaptive weighting** — The same five factors (trend, momentum, mean reversion, participation, volatility) get different weights in trend vs range vs high-vol vs low-liquidity regimes. Trend regimes emphasize trend+momentum; range emphasizes mean reversion.

4. **Layered separation** — `indicators.py` (primitives) → `intelligence.py` (ensemble) → `MarketSnapshot` (typed I/O) → strategies/AI → broker. ML training stays out of `intelligence.py`.

5. **LLM as overlay, not core signal** — The math layer gates entries; the LLM consumes structured `MarketSnapshot` text, not chart images. Parse failures default to HOLD.

6. **Long-only India intraday** — Matches cash equity constraints; bearish signals exit or block longs. Simplifies compliance and avoids shorting infrastructure.

7. **Synthetic depth honesty** — Depth is estimated and labeled. Avoids false precision from fake order books.

8. **Calibration feedback loop** — Settled predictions update confidence bins and ECE; thresholds adjust when calibration drifts.

9. **Deterministic bridge** — StockSharp CSV replay enables cross-language validation without broker credentials.

10. **Causal replay for intelligence** — `replay.py` and `ai_session.py` use `iloc[:idx+1]` so indicators at bar *t* only see history through *t*.

---

## 7. Why it does not work — limitations and failure modes

### Documented limitations

| Limitation | Impact |
|------------|--------|
| Paper broker only | No real fills, partial fills, margin, RMS, auction/circuit behavior |
| Read-only Upstox REST | No WebSocket; delayed snapshots; no order APIs |
| Synthetic depth/spread | `estimated_spread_bps` from range proxy — not real book |
| Long-only | Bearish alpha unexploitable; down markets = exit or sit out |
| Delayed data | Free/delayed sources; not licensed tick data |
| AI latency | Local LLM ~3–4 decisions/min on CPU |
| Research stage | Hit rate ≠ net expectancy after costs |
| FinBERT on dummy headlines | `default_headlines()` is generic; not live news |
| No Python CI | Contributor workflow is manual `pytest` only |

### Implementation failure modes

1. **Fill-timing inconsistency** — Design intent is next-bar open. Actual behavior varies by path (see [§16](#16-known-inconsistencies)). Backtests may be optimistic vs stated invariant.

2. **Hardcoded score thresholds** — 62/38 direction cutoffs in `score_next_move()` are not in `paisa.toml` (unlike `min_signal_score` and AI `decision_min_confidence`).

3. **ML auto-train on predict** — Missing pickles trigger training on available data — in-sample overfit risk.

4. **Simple 80/20 chronological split** — No walk-forward validation in core pipeline.

5. **ARIMA on short windows** — Last 100 closes, order (2,1,2); fragile on noisy 5m bars.

6. **Regime classifier is heuristic** — Fixed ADX/ATR/volume thresholds; not validated against factor performance.

7. **Hand-tuned factor scaling** — `_scaled()` divisors not calibrated from data.

8. **Position sizing mismatch** — Backtest uses `max_position_pct=0.20`; AI router uses `position_size_pct=0.01` with ATR sizing — very different risk profiles.

9. **Sharpe in `summarize()`** — Uses bar returns × √252; misleading for 5-minute intraday bars.

10. **Calibration cold start** — `adjusted_threshold()` only helps after enough settled predictions.

### Why the approach might not work in practice

- **Edge decay** — 5-factor rule score + gradient boosting on 14 features is a crowded approach; India intraday STT (2.5 bps on sell) erodes small edges.
- **Data quality** — Upstox historical candles may have gaps; free data is not tick-level.
- **Overfitting chain** — Hand-tuned factors → per-symbol ML → LLM interpreting snapshots — multiple layers can fit noise.
- **Regime misclassification** — Low liquidity and high vol can flip weights at the wrong time.
- **No order-book alpha** — Synthetic depth cannot support microstructure strategies.
- **LLM non-stationarity** — AI decisions are non-deterministic; may not generalize across symbols/regimes.
- **Transaction costs not in intelligence score** — High-frequency signals may not clear STT+GST after costs.

---

## 8. UI and looks

Paisa has **four interactive surfaces** plus offline reports. No React, Gradio, Flask, or bundler — FastAPI dashboards use **vanilla HTML/CSS/JS** with **HTML5 Canvas 2D** hand-drawn charts.

### Dashboard 1 — Streamlit research (`paisa dashboard`, port 8501)

| Item | Detail |
|------|--------|
| File | `src/paisa_trader/dashboard_app.py` |
| Theme | Default Streamlit wide layout; title "Paisa Trader" / icon "PT" |
| Charts | `st.line_chart` for price, equity, indicator overlays (not canvas) |
| Panels | Metrics, next-move score, indicator table, synthetic depth table, per-symbol + combined AI snapshot JSON, fills, trade P&L |
| Interaction | Manual **Refresh Upstox data**; filter sliders (`min_signal_score`, volume, spread); optional StockSharp bridge export |

### Dashboard 2 — Rule replay (`paisa web`, port 8080)

| Item | Detail |
|------|--------|
| Files | `web_static/index.html`, `app.js`, `styles.css` + `web_app.py` |
| Theme | **Light** (`color-scheme: light`); sidebar dark `#111820`; Inter/system font |
| Charts | Canvas: `priceChart` (candles + overlays), `equityChart` |
| Live | WebSocket `/ws`; REST `/api/state`, `/api/control/*`, `/api/ai-snapshot` |
| Panels | Pause/resume/reset, activity log, fills table, positions, signals |

### Dashboard 3 — AI replay (`paisa ai-web`, port 8082)

| Item | Detail |
|------|--------|
| Files | `static/ai_dashboard/index.html`, `app.js`, `style.css` + `ai_web_server.py` |
| Theme | **Dark** (`--bg: #070b10`) |
| Charts | Canvas candlesticks + **WaveTrail** overlay |
| Panels | Decision cards, calibration stats, forecast log, capital/trade ledger, P&L tables |
| Note | Slower bar cadence tied to LLM latency |

### Dashboard 4 — Live paper (`paisa live`, default port 8080)

| Item | Detail |
|------|--------|
| Files | Reuses `web_static/*` + `live_web_app.py` |
| Theme | Same as rule replay; `modeLabel` shows "live" |
| Extra panels | Full NSE universe table (`/api/universe`), market status, open trade calls with ATR SL/target/trailing |
| Fills | At live LTP — different from backtest bar-open model |

### Offline reports

| Type | Command | Output |
|------|---------|--------|
| Backtest | `paisa backtest` | `reports/backtest_*/report.md`, CSVs |
| AI session | `paisa ai-report` | `reports/ai_sessions/.../report.html` |
| Shadow | `paisa shadow` | Same as backtest + `shadow.json` |

### Cross-UI contracts

- INR formatting via `Intl.NumberFormat("en-IN")` in JS dashboards
- Depth always labeled synthetic in Streamlit and snapshot disclaimers
- FastAPI dashboards share: `/api/state`, `/api/ai-snapshot`, `/ws`, `/api/control/{pause|resume|reset}`

### Planned but not implemented

- Design note in repo discusses **React + TradingView Lightweight Charts** — not in codebase
- `automated_intraday_india_plan.md` describes future dashboard architecture — not executable

---

## 9. Hooks, rules, and automation

### Cursor rules (in repo)

**File:** `.cursor/rules/paisa-trader-invariants.mdc` (`alwaysApply: true`)

**Never do:**
- Introduce lookahead bias (fills at next-bar open, never signal-bar close)
- Skip India costs (STT, GST, stamp duty, brokerage, spread, slippage)
- Claim real-time market data (depth is synthetic unless documented)
- Add shorting logic (bearish = block or exit longs only)
- Hardcode confidence thresholds (read from config or calibration)

**Always do:**
- After editing `indicators.py` or `intelligence.py`: `pytest tests/test_indicators.py tests/test_intelligence.py -q`
- After editing `ai_harness/`: `pytest tests/test_ai_harness.py -q`
- New public functions: type hints + docstring (Args, Returns, usage example)
- Signal outputs expose active regime and active weights
- Enriched features exposed through `MarketSnapshot` and AI prompt output
- Use `pathlib.Path` for paths; monetary values in INR

**Code style:** Python 3.11+, pytest, dataclasses; keep ML training out of `intelligence.py`.

**Stale note:** The rule mentions `yfinance` as delayed data — the codebase uses Upstox SDK, not yfinance.

### Cursor hooks

**None in this repo.**

| Checked | Result |
|---------|--------|
| `.cursor/hooks/` | Does not exist |
| `hooks.json` | Not present |
| `.pre-commit-config.yaml` | Not present |

User-level Cursor skills live under `~/.cursor/skills-cursor/` — external to this repo.

### Planned automation (not implemented)

- **Cron + `paisa shadow`** — EOD refresh, backtest, report, optional `--export-bridge` (mentioned in README and PROJECT_STATUS)
- **StockSharp post-export** — Run .NET harness after shadow cron
- **`.env.example`** — Not yet in repo; secrets must stay in environment variables

### Agent workflow for contributors

When an AI agent edits signal code, it should run the targeted pytest subsets above before claiming success. Full suite: `pytest -q` from repo root.

---

## 10. Configuration

### `paisa.toml` (project root)

```toml
[data]
default_source = "upstox"
default_interval = "5minute"
default_period = "5d"
token_env_var = "UPSTOX_ANALYTICS_TOKEN"

[ai_harness]
model_provider = "lmstudio"      # mock | lmstudio | local | claude | openai
model_name = "auto"
api_key_env = ""                 # env var name for cloud providers
temperature = 0.1
max_tokens = 512                 # code default is 900 if omitted
local_url = "http://127.0.0.1:1234"
decision_min_confidence = 0.55
position_size_pct = 0.01
symbols = ["RELIANCE", "TCS", "INFY"]
bar_interval_sec = 5

# Optional — read by load_ai_harness_config() even if absent from committed file:
# [calibration]
# enabled = true
# save_path = "data/calibration.json"

[ml]
enabled = true
model_dir = "models/"
min_confidence = 0.55
use_arima_tiebreaker = true
arima_order = [2, 1, 2]

[sentiment]
enabled = true
model = "ProsusAI/finbert"
fallback_on_error = true
batch_size = 1
```

**Calibration** (`data/calibration.json`) is read/written by `calibration.py`. The loader supports optional `[calibration]` keys `enabled` and `save_path` in `paisa.toml` (defaults: `true`, `data/calibration.json`).

### Code defaults not in TOML

| Setting | Default | Where |
|---------|---------|-------|
| `initial_cash` | 100,000 INR | `BrokerConfig` |
| `spread_bps` | 3 | `BrokerConfig` |
| `slippage_bps` | 2 | `BrokerConfig` |
| `max_position_pct` | 0.20 | `BrokerConfig` |
| `decision_min_confidence` (code fallback) | 0.65 | `AIHarnessConfig` / `DecisionRouterConfig` |

When `paisa.toml` is loaded, TOML values override code defaults. The 0.55 in TOML wins over 0.65 in dataclass defaults.

### Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `UPSTOX_ANALYTICS_TOKEN` | Yes (for data) | Upstox read-only market data |
| `ANTHROPIC_API_KEY` | Optional | Claude provider |
| `OPENAI_API_KEY` | Optional | OpenAI provider |
| `PAISA_FINBERT_TIMEOUT_SECONDS` | Optional | FinBERT load timeout (default 8s) |

`.env` and `.env.*` are gitignored. **Never commit API tokens.**

### Path constants (`config.py`)

```text
PROJECT_ROOT/
├── data/raw/           # Upstox instrument master, NSE zips
├── data/processed/     # Parquet candle cache
├── data/calibration.json
├── models/             # XGB/LGBM pickles
├── reports/            # Backtest, shadow, AI, bridge outputs
└── paisa.toml
```

Default symbols (`DEFAULT_SYMBOLS` in `config.py`): `RELIANCE`, `TCS`, `INFY`, `HDFCBANK`, `ICICIBANK`, `SBIN`, `LT`, `AXISBANK`, `ITC`, `BHARTIARTL` (10 names; most CLI commands default to first 3; `paisa live` defaults to all 10).

---

## 11. CLI reference

Install:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"           # core + pytest
pip install -e ".[dev,dashboard]" # + streamlit
```

| Command | Purpose | Notable flags |
|---------|---------|---------------|
| `paisa symbols` | Print default NSE symbol list | — |
| `paisa download` | Upstox OHLCV → parquet | `--symbols`, `--period`, `--interval`, `--force` |
| `paisa upstox-quote` | Current market quotes | `--symbols`, `--ltp` |
| `paisa nse-bhavcopy` | NSE EOD bhavcopy | `--date YYYY-MM-DD` |
| `paisa train-ml` | Train XGB + LGBM per symbol | `--symbols`, `--period`, `--interval` |
| `paisa backtest` | Rule-based backtest + report | `--strategy`, `--initial-cash`, `--download` |
| `paisa shadow` | Refresh + backtest + report | `--export-bridge` |
| `paisa stocksharp-export` | Bridge package for .NET | same as backtest |
| `paisa dashboard` | Streamlit research UI | `--port` (8501) |
| `paisa web` | Rule intraday replay | `--port` (8080), `--tick-seconds`, `--no-loop`, `--no-intelligence` |
| `paisa live` | Live Upstox paper dashboard | `--trade-symbols`, `--poll-seconds`, `--port` (8080) |
| `paisa ai-web` | AI-driven replay | `--port` (8082), model provider overrides |
| `paisa ai-backtest` | Batch LLM over historical bars | `--download`, model overrides |
| `paisa ai-report` | HTML report from AI session | `--session-dir` |

Broker flags (shared): `--initial-cash`, `--spread-bps`, `--slippage-bps`, `--max-position-pct`

Strategies: `buy-hold`, `ma-cross`, `mean-reversion`

---

## 12. Testing and CI

### Python tests

**Location:** `tests/` (17 modules, ~77 test functions)

```bash
pytest -q   # pythonpath=src in pyproject.toml
```

| Module | Focus |
|--------|-------|
| `test_indicators.py` | ATR, RSI, VWAP, session phase, lag returns |
| `test_intelligence.py` | Scoring, filters, regime |
| `test_ai_harness.py` | LLM runners, parser, prediction tracker, web engine |
| `test_backtest.py` | Backtest engine |
| `test_broker.py` | India costs, simulated broker |
| `test_bridge.py` | StockSharp export |
| `test_calibration.py` | Confidence calibration |
| `test_data.py` | Candle loading, Upstox |
| `test_live_engine.py` | Live quotes, NSE universe |
| `test_live_trade.py` | Entry/exit calls, trailing stop |
| `test_ml_models.py` | XGB/LGBM feature engineering |
| `test_nse.py` | NSE bhavcopy URLs/parsing |
| `test_replay.py` | Replay engine |
| `test_sentiment.py` | FinBERT headline scoring |
| `test_shadow.py` | Shadow session + reports |
| `test_snapshot.py` | `MarketSnapshot` |
| `test_trade_stats.py` | P&L, round trips, ledger |

**Mandatory after edits (per Cursor rules):**
- `indicators.py` / `intelligence.py` → `pytest tests/test_indicators.py tests/test_intelligence.py -q`
- `ai_harness/` → `pytest tests/test_ai_harness.py -q`

**Not present:** `conftest.py`, `test_cli.py`, web/UI integration tests, Playwright e2e.

### CI status

| Scope | Status |
|-------|--------|
| Python `paisa_trader` | **No CI** — no root `.github/workflows/` |
| StockSharp (vendored) | `StockSharp/.github/workflows/dotnet.yml` — dotnet build/test on ubuntu/windows/macos |

---

## 13. StockSharp bridge

### Export

```bash
paisa stocksharp-export --symbols RELIANCE TCS --period 60d --interval 1day --strategy ma-cross
```

**Output:** `reports/stocksharp_bridge/<strategy>_<timestamp>/`

```text
manifest.json
RELIANCE_candles.csv
RELIANCE_signals.csv
RELIANCE_fills.csv
RELIANCE_equity.csv
summary.csv
```

### Consumer

```bash
dotnet run --project StockSharp/Samples/11_PaisaPaperHarness/11_PaisaPaperHarness.csproj \
  -- reports/stocksharp_bridge/ma-cross_YYYYMMDD_HHMMSS/manifest.json
```

Verified with .NET SDK 10.0.301.

### Purpose

- **Python** = research, data, features, signal generation
- **StockSharp** = event-driven paper execution harness
- **File-based** = deterministic replay without live broker credentials
- Live broker adapters deferred to long-term roadmap

---

## 14. Safety invariants

### Trading invariants

- **Long-only** — `target_position ∈ [0, 1]`; no short positions
- **Bearish = exit or block** — never open shorts
- **Intelligence gate** — entries blocked when `paper_trade_candidate` is false (in filtered modes)
- **India costs on every fill** — STT, GST, stamp, exchange, SEBI, spread, slippage

### Data honesty

- Upstox REST is **delayed** — do not claim real-time market data
- Depth is **synthetic** — estimated from spread/volume proxies
- Hit rate and directional accuracy are **diagnostic** — net expectancy after costs is the real metric

### Development invariants

- No lookahead fills at signal-bar close (design intent — see path-specific deviations)
- Confidence thresholds from config/calibration, not hardcoded (partial compliance — see §16)
- All signal outputs expose `active_regime` and `active_weights`
- Monetary values in INR; paths via `pathlib.Path`

### Compliance framing

- Research harness, not investment advice
- Market data licensing must be verified before commercial use
- Rotate API secrets if exposed in chat or logs
- SEBI/NSE algo compliance (static IP, order tagging, rate limits) not implemented

---

## 15. Roadmap

### Near term

1. Complete AI backtest runs; review calibration + trade gate thresholds
2. Beat `ma-cross` baseline on out-of-sample windows before adding complexity
3. Wire FinBERT to live news headlines (currently on-demand, not streamed)
4. Add `.env.example`; resolve version mismatch in `__init__.py`

### Medium term

1. Upstox WebSocket for live LTP/feed in replay dashboards
2. Broker adapter interface: `SimulatedBrokerAdapter` vs future `UpstoxBrokerAdapter`
3. Scheduled shadow runs (`cron paisa shadow`) for EOD reports
4. Walk-forward validation and net-expectancy metrics in reports
5. Unify fill-timing across backtest, replay, AI, and live paths

### Long term

See `automated_intraday_india_plan.md`:

- Licensed tick/depth data; point-in-time instrument metadata
- SEBI/NSE retail algo compliance
- Production kill switches, RMS, audit logging, multi-broker
- Limit/IOC fills, latency models, partial-fill simulation
- StockSharp for event-driven live execution; Python for research/ML

---

## 16. Known inconsistencies

| Issue | Detail |
|-------|--------|
| **Version mismatch** | `pyproject.toml` = 0.2.0; `src/paisa_trader/__init__.py` = 0.1.0 |
| **Fill timing variance** | Design rule: next-bar open. **Actual:** backtest/replay fill at same-bar open when signal uses same-bar close; AI router fills at bar close (`snapshot.close`); live fills at live LTP; backtest final flatten uses last bar close |
| **Port collision** | `paisa web` and `paisa live` both default to port 8080 |
| **Confidence defaults** | Code default 0.65 vs `paisa.toml` 0.55 — TOML wins when loaded |
| **Hardcoded 62/38 thresholds** | In `score_next_move()` — not in config (violates Cursor rule intent) |
| **Test count drift** | PROJECT_STATUS says 70 tests; repo has ~77 |
| **README module list stale** | Lists ~12 modules; package has 31+ |
| **yfinance in Cursor rule** | Rule mentions yfinance; code uses Upstox SDK |
| **Optional FinBERT deps** | `transformers`/`torch`/`accelerate` not in core `pyproject.toml` |
| **Position sizing mismatch** | Backtest 20% max vs AI router 1% with ATR sizing |

---

## 17. Related documents

| Document | Purpose |
|----------|---------|
| `README.md` | User-facing quick start |
| `PROJECT_STATUS.md` | Architecture snapshot (June 2026) |
| `paisa.toml` | Runtime config |
| `docs/quant_source_mapping.md` | Maps quant concepts (Chan, TA) to modules |
| `automated_intraday_india_plan.md` | Long-term India intraday roadmap |
| `.cursor/rules/paisa-trader-invariants.mdc` | Agent/developer invariants |
| `StockSharp/Samples/11_PaisaPaperHarness/README.md` | Bridge consumer docs |

### Quant source mapping (summary)

From `docs/quant_source_mapping.md`:

| Concept | Modules |
|---------|---------|
| Trend structure | `indicators.py`, `intelligence.py` — EMA/SMA, ADX/DMI, Donchian |
| Momentum | RSI, MACD, ROC, stochastic |
| Volatility | ATR, BB bandwidth, realized vol |
| Volume/participation | Relative volume, OBV, session VWAP |
| Mean reversion | `strategies.py`, intelligence mean-reversion factor |
| Backtesting discipline | `backtest.py`, `report.py` |
| Risk management | `broker.py`, `decision_router.py` — ATR sizing |
| AI as overlay | `snapshot.py`, `ai_harness/context_builder.py` — math layer gates, LLM interprets |

---

## Quick start (minimal)

```bash
export UPSTOX_ANALYTICS_TOKEN="your-token"
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,dashboard]"

paisa download --symbols RELIANCE TCS INFY --period 5d --interval 5minute
paisa train-ml --symbols RELIANCE TCS INFY --period 60d --interval 5minute
paisa backtest --strategy ma-cross
paisa web                    # rule replay → http://localhost:8080
paisa ai-web                 # AI replay   → http://localhost:8082
paisa dashboard              # Streamlit   → http://localhost:8501
pytest -q
```

---

*This document was assembled from README, PROJECT_STATUS, source code, Cursor rules, and multi-agent exploration/review on 22 June 2026. Update when major features land or architecture changes.*
