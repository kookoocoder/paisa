# Paisa Trader — Project Stage & Technology

**Document date:** 21 June 2026  
**Package version:** 0.2.0  
**Stage:** Research & paper-trading harness (no live broker execution)

---

## Executive summary

Paisa Trader is a **no-demat, paper-only** research platform for Indian equity intraday experiments. It ingests Upstox broker-backed market data, combines rule-based signals with gradient-boosted ML and optional LLM decisions, runs everything through a simulated broker with India-style costs, and exposes results through CLI tools, web dashboards, Streamlit, and HTML reports.

The project is **functional end-to-end for research and replay** but **not production-ready for live trading**. Core pipelines work; Upstox integration is read-only (REST candles + quotes); AI/ML evaluation is ongoing.

---

## Current stage

| Phase | Status | Description |
|-------|--------|-------------|
| Data ingestion | ✅ Done | Upstox candles/quotes, NSE bhavcopy fallback |
| Simulated execution | ✅ Done | Long-only, next-bar open fills, full India cost stack |
| Rule baselines | ✅ Done | `buy-hold`, `ma-cross`, `mean-reversion` |
| Feature / signal layer | ✅ Done | Regime-aware scoring, `MarketSnapshot`, synthetic depth |
| ML direction models | ✅ Wired | XGBoost + LightGBM per symbol; ARIMA tiebreaker |
| Sentiment (FinBERT) | ✅ Wired | Supervised fine-tuned BERT; lazy CPU load + fallback |
| AI harness (LLM) | ✅ Working | LM Studio / mock / Claude / OpenAI; prediction tracking |
| Web replay dashboards | ✅ Working | Rule replay (8080), AI replay (8082) |
| StockSharp bridge | ✅ Working | CSV + manifest export for .NET paper harness |
| Live broker / orders | ❌ Not started | No demat, no order placement, no WebSocket feed |
| Production controls | ❌ Not started | Kill switches, RMS, static IP, audit compliance |

---

## Technology stack

### Core runtime

| Layer | Technology | Notes |
|-------|------------|-------|
| Language | Python 3.11+ | Primary runtime |
| Packaging | setuptools, `pyproject.toml` | CLI entry: `paisa` |
| Config | `paisa.toml` | AI, ML, sentiment, data defaults |
| Tests | pytest (70 tests, 15 modules) | `pytest -q` |

### Data & storage

| Component | Technology | Role |
|-----------|------------|------|
| Market data | Upstox Python SDK + REST | Historical candles, LTP/full quotes |
| Reference data | NSE public bhavcopy | EOD validation, archive URL fallbacks |
| Local cache | Parquet/CSV under `data/processed/` | Regenerated; gitignored |
| Reports | JSONL, HTML, CSV under `reports/` | Backtests, AI sessions, bridge exports |

### Computation & ML

| Component | Technology | Role |
|-----------|------------|------|
| Tabular ML | XGBoost, LightGBM, scikit-learn | Per-symbol direction classifiers (`paisa train-ml`) |
| Time series | statsmodels ARIMA | Optional tiebreaker when ML models disagree |
| Features | pandas, numpy | 14 engineered features (RSI, EMA, BB, ATR, returns, volume) |
| Sentiment | HuggingFace `ProsusAI/finbert` | **Supervised** fine-tuned BERT on financial text (3-class: pos/neg/neutral) |
| Optional deps | `transformers`, `torch`, `accelerate` | FinBERT only; not in core `pyproject.toml` deps |

### AI / LLM layer

| Component | Technology | Role |
|-----------|------------|------|
| Local LLM | LM Studio (`http://127.0.0.1:1234`) | Default provider in `paisa.toml` |
| Alternatives | Ollama (`local`), Claude, OpenAI, `mock` | Swappable via config |
| Harness | `ai_harness/` | Prompt builder, decision parser/router, prediction tracker |
| Calibration | Rolling confidence bins | ECE, adjusted thresholds from settled predictions |

### Web & UI

| Surface | Stack | Port | Purpose |
|---------|-------|------|---------|
| Rule replay | FastAPI + WebSocket + vanilla JS | 8080 | `paisa web` — autonomous intraday paper trading |
| AI replay | FastAPI + WebSocket + AI dashboard | 8082 | `paisa ai-web` — LLM-driven decisions live |
| Research dashboard | Streamlit | 8501 | `paisa dashboard` — indicators, depth, snapshots |
| AI reports | Jinja-style HTML | — | `paisa ai-backtest` / `paisa ai-report` |

### Execution simulation

| Component | Technology | Role |
|-----------|------------|------|
| Paper broker | Custom `SimulatedBroker` | Cash, long-only positions, order/fill logs |
| Costs | Configurable India stack | STT, GST, stamp duty, brokerage, spread, slippage |
| Fill model | Next-bar open | No lookahead bias |
| Trade analytics | `trade_stats.py` | Capital invested, buy/sell ledger, P&L per trade |

### Cross-language bridge

| Component | Technology | Role |
|-----------|------------|------|
| Export | Python `bridge.py` | Manifest + candle/signal/fill CSVs |
| Consumer | StockSharp (.NET 10+) | `StockSharp/Samples/11_PaisaPaperHarness/` |

---

## Architecture

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
│  Long-only │ next-bar open │ India costs │ equity curve │ fill logs     │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
   web_app.py          ai_web_server.py     report.py / bridge.py
   (port 8080)         (port 8082)          HTML + StockSharp export
```

### Key modules (`src/paisa_trader/`)

| Module | Responsibility |
|--------|----------------|
| `data.py` | Candle download, Upstox instrument lookup, live quotes |
| `broker.py` | Paper fills, spread, slippage, STT/GST/brokerage |
| `indicators.py` | SMA, EMA, RSI, MACD, Bollinger, ATR, DMI, VWAP, regime helpers |
| `intelligence.py` | Regime-weighted next-move scoring, filters, synthetic depth |
| `snapshot.py` | Typed `MarketSnapshot` for AI prompts and dashboards |
| `ml_models.py` | Feature engineering, train/load XGBoost + LightGBM |
| `arima_model.py` | ARIMA tiebreaker for conflicting ML votes |
| `sentiment.py` | FinBERT headline scoring with lexical fallback |
| `strategies.py` | Baseline strategies for benchmarking |
| `replay.py` | Autonomous intraday replay engine (rule-based web) |
| `web_app.py` | FastAPI + WebSocket dashboard (rule replay) |
| `ai_web_server.py` | FastAPI + WebSocket dashboard (AI replay) |
| `ai_session.py` | Batch AI backtest + HTML report generation |
| `ai_harness/` | LLM runner, context builder, decision parser/router, prediction tracker |
| `calibration.py` | Confidence calibration from historical hit rates |
| `trade_stats.py` | Trade ledger, capital invested, realized P&L |
| `bridge.py` | StockSharp package export |
| `nse.py` | NSE equity bhavcopy ingestion |
| `config.py` | TOML loader for AI, ML, sentiment settings |

---

## ML & AI details

### Gradient-boosted direction models

- **Training:** `paisa train-ml --symbols RELIANCE TCS INFY --period 60d --interval 5minute`
- **Artifacts:** `models/{SYMBOL}_xgb.pkl`, `models/{SYMBOL}_lgbm.pkl`
- **Features:** RSI (7/14), EMA (9/21), Bollinger width/position, ATR, volume ratio, candle body, returns (1/3/5 bars)
- **Label:** Next-bar direction (up/down)
- **Gate:** `min_confidence` from `paisa.toml` `[ml]` section (default 0.55)
- **Tiebreaker:** ARIMA(2,1,2) when XGB and LGBM disagree (`use_arima_tiebreaker = true`)

### FinBERT (sentiment)

- **Type:** **Supervised** — BERT pre-trained on general text, then fine-tuned on Financial PhraseBank (labeled financial headlines)
- **Model:** `ProsusAI/finbert` via HuggingFace `transformers`
- **Output:** positive / negative / neutral with confidence scores
- **Integration:** `sentiment.py` → optional input to `MarketSnapshot`; lazy CPU pipeline; neutral fallback on error
- **Config:** `[sentiment]` in `paisa.toml` (`enabled = true`)

### LLM AI harness

```toml
[ai_harness]
model_provider = "lmstudio"
model_name = "auto"
local_url = "http://127.0.0.1:1234"
decision_min_confidence = 0.55
position_size_pct = 0.01
symbols = ["RELIANCE", "TCS", "INFY"]
```

Supported providers: `mock`, `lmstudio`, `local` (Ollama), `claude`, `openai`.

The harness tracks directional hit rate, rolling accuracy by horizon (+5m, +15m), regime breakdown, and confidence calibration (ECE).

---

## What works today

| Area | Status | Notes |
|------|--------|-------|
| Upstox data download | ✅ | Needs `UPSTOX_ANALYTICS_TOKEN` |
| Upstox live quotes | ✅ | `paisa upstox-quote [--ltp]` |
| NSE bhavcopy | ✅ | Dual archive URL fallback |
| Simulated broker | ✅ | Long-only, next-bar open, India costs |
| Rule backtests | ✅ | Three baseline strategies |
| ML training & inference | ✅ | XGB + LGBM + ARIMA tiebreaker |
| FinBERT sentiment | ✅ | Lazy load, fallback on missing deps |
| Shadow paper sessions | ✅ | Refresh + report + optional bridge export |
| Streamlit dashboard | ✅ | Indicators, depth, AI snapshots |
| Rule web replay | ✅ | `paisa web` on port 8080 |
| AI web replay | ✅ | `paisa ai-web` on port 8082 |
| AI backtest + HTML report | ✅ | Accuracy cards, trade P&L stats |
| StockSharp bridge | ✅ | Verified with .NET SDK 10.0.301 |
| Test suite | ✅ | 70 tests across 15 modules |

---

## CLI quick reference

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,dashboard]"

# Data
paisa symbols
paisa download --symbols RELIANCE TCS INFY --period 5d --interval 5minute
paisa upstox-quote --symbols RELIANCE TCS INFY
paisa nse-bhavcopy --date 2024-06-03

# ML
paisa train-ml --symbols RELIANCE TCS INFY --period 60d --interval 5minute

# Backtest & shadow
paisa backtest --strategy ma-cross
paisa shadow --export-bridge
paisa stocksharp-export

# Dashboards
paisa dashboard          # Streamlit → http://localhost:8501
paisa web                # Rule replay  → http://localhost:8080
paisa ai-web             # AI replay    → http://localhost:8082

# AI batch
paisa ai-backtest
paisa ai-report [--session-dir PATH]

# Verify
pytest -q
```

### Environment variables

```bash
export UPSTOX_ANALYTICS_TOKEN="your-upstox-analytics-token"   # required for Upstox data
# Optional for cloud LLM providers:
export ANTHROPIC_API_KEY="..."
export OPENAI_API_KEY="..."
```

---

## Project layout

```text
paisa/
├── src/paisa_trader/           # Main Python package (31 modules)
│   ├── ai_harness/             # LLM decision pipeline
│   ├── web_static/             # Rule replay dashboard assets
│   └── static/ai_dashboard/    # AI replay dashboard assets
├── tests/                      # 15 pytest modules (70 tests)
├── models/                     # Trained XGB/LGBM pickles per symbol
├── data/processed/             # Cached candles (gitignored)
├── reports/                    # Backtest, AI, shadow outputs (gitignored)
├── StockSharp/                 # C# samples + bridge consumer
├── docs/                       # quant_source_mapping.md, etc.
├── paisa.toml                  # Runtime config (AI, ML, sentiment)
├── pyproject.toml              # Package metadata v0.2.0
├── README.md                   # User-facing quick start
├── automated_intraday_india_plan.md   # Long-term architecture plan
└── PROJECT_STATUS.md           # This document
```

---

## Known limitations

1. **No real orders** — paper broker only; no demat, RMS, or margin modeling.
2. **Read-only Upstox** — REST historical + quote snapshots; no WebSocket live feed or order APIs.
3. **Synthetic depth** — estimated from spread/volume, not order book.
4. **No shorting** — bearish signals exit or block longs only.
5. **Delayed data** — free/delayed sources; not licensed tick data.
6. **AI latency** — local LLM backtests are slow (~3–4 decisions/min on CPU).
7. **Research stage** — hit rate and accuracy are diagnostic; net expectancy after costs is the real metric.
8. **Secrets** — API tokens must stay in environment variables, never in git.

---

## Recommended next steps

### Near term

1. Complete AI backtest runs and review calibration + trade gate thresholds.
2. Beat `ma-cross` baseline on out-of-sample windows before adding model complexity.
3. Wire FinBERT to live news headlines (currently scores on demand, not streamed).
4. Land uncommitted Upstox + ML changes with `.env.example`.

### Medium term

1. Upstox WebSocket for live LTP/full feed in replay dashboards.
2. Broker adapter interface: `SimulatedBrokerAdapter` vs future `UpstoxBrokerAdapter`.
3. Scheduled shadow runs (`cron paisa shadow`) for EOD reports.
4. Walk-forward validation and net-expectancy metrics in reports.

### Long term

See [automated_intraday_india_plan.md](automated_intraday_india_plan.md) for the full India intraday roadmap: licensed tick/depth data, SEBI/NSE algo compliance, multi-broker support, production kill switches, and audit logging.

---

## Safety & compliance

- This is a **research harness**, not investment advice.
- Market data licensing must be verified before any commercial use.
- Rotate API secrets if exposed in chat or logs.
- All simulated fills use **next-bar open** — no lookahead in the broker.

---

*Update this document when major features land or the architecture changes.*
