# Paisa Trader — Project Status

**Document date:** 21 June 2026  
**Version:** 0.1.0  
**Stage:** Research & paper-trading harness (no live broker execution)

---

## Executive summary

Paisa Trader is a **no-demat, paper-only** research platform for Indian equity intraday experiments. It ingests delayed or broker-backed market data, runs rule-based and AI-driven strategies through a simulated broker with India-style costs, and exposes results through CLI tools, web dashboards, and HTML reports.

The project is **functional for research and replay** but **not production-ready for live trading**. Core pipelines work end-to-end; broker integration (Upstox) is partial; AI evaluation is ongoing.

---

## What works today

| Area | Status | Notes |
|------|--------|-------|
| Data download (`yfinance`) | ✅ Working | NSE/BSE symbols via `.NS` / `.BO` suffixes |
| Data download (Upstox) | ✅ Implemented | Needs `UPSTOX_ANALYTICS_TOKEN` or OAuth token |
| NSE bhavcopy fetch | ✅ Working | Fallback URL strategy for archive format changes |
| Simulated broker | ✅ Working | Long-only, next-bar open fills, India cost stack |
| Rule-based backtests | ✅ Working | `buy-hold`, `ma-cross`, `mean-reversion` |
| Shadow paper sessions | ✅ Working | Refresh + report + optional StockSharp export |
| Streamlit dashboard | ✅ Working | Delayed data refresh, indicators, synthetic depth |
| Intraday web replay (`paisa web`) | ✅ Working | WebSocket replay on port 8080 |
| AI web harness (`paisa ai-web`) | ✅ Working | LM Studio / mock / Claude / OpenAI providers |
| AI backtest + HTML report | ✅ Working | Slow on local LLM; accuracy metrics in report |
| StockSharp bridge export | ✅ Working | CSV + manifest for .NET paper harness |
| Confidence calibration | ✅ Working | Rolling calibration from settled predictions |
| FinBERT (sentiment) | ✅ Installed | Via Hugging Face `ProsusAI/finbert`; not wired into pipeline |
| Test suite | ✅ Passing | 12 test modules, 54+ tests |

---

## Architecture overview

```text
Data sources                Feature / signal layer          Execution & reporting
─────────────────          ───────────────────────         ─────────────────────
yfinance (delayed)    →    indicators.py              →   SimulatedBroker (broker.py)
Upstox REST (token)   →    intelligence.py            →   backtest.py / replay.py
NSE bhavcopy          →    wavetrail.py               →   shadow.py / report.py
                          ai_harness/ (LLM decisions)  →   ai_session.py / ai_web_server.py
                          calibration.py             →   bridge.py → StockSharp
```

### Key modules (`src/paisa_trader/`)

| Module | Role |
|--------|------|
| `data.py` | Candle download, normalization, Upstox instrument lookup |
| `broker.py` | Paper fills, spread, slippage, STT/GST/brokerage costs |
| `indicators.py` | SMA, EMA, RSI, Bollinger, ATR, regime helpers |
| `intelligence.py` | Next-move scoring, filters, `MarketSnapshot`, synthetic depth |
| `strategies.py` | Baseline strategies for benchmarking |
| `replay.py` | Autonomous intraday replay engine |
| `web_app.py` | FastAPI dashboard for rule-based replay |
| `ai_web_server.py` | FastAPI dashboard for AI-driven replay |
| `ai_session.py` | Batch AI backtest + HTML report generation |
| `ai_harness/` | LLM runner, prompt builder, decision parser/router, prediction tracker |
| `calibration.py` | Confidence calibration from historical hit rates |
| `bridge.py` | StockSharp package export |
| `nse.py` | NSE equity bhavcopy ingestion |

---

## Data sources

### 1. Yahoo Finance (`yfinance`) — default

- **Cost:** Free, no broker account
- **Coverage:** NSE/BSE cash symbols (e.g. `RELIANCE.NS`)
- **Limitation:** Delayed; not exchange-authoritative; depth is synthetic

### 2. Upstox — recently integrated (uncommitted)

- **Setup:** Developer app + Analytics Token (`UPSTOX_ANALYTICS_TOKEN`)
- **CLI:** `paisa download --source upstox`, `paisa upstox-quote`
- **Capabilities:** Historical candles, full/LTP market quotes
- **Not yet:** Live WebSocket feed, order placement, portfolio APIs

### 3. NSE public reports

- **CLI:** `paisa nse-bhavcopy --date YYYY-MM-DD`
- **Use:** EOD validation, reference data

---

## AI & ML stack

### Local LLM (configured default)

`paisa.toml` points to **LM Studio** at `http://127.0.0.1:1234`:

```toml
[ai_harness]
model_provider = "lmstudio"
model_name = "auto"
local_url = "http://127.0.0.1:1234"
decision_min_confidence = 0.65
symbols = ["RELIANCE.NS", "TCS.NS", "INFY.NS"]
```

Supported providers: `mock`, `lmstudio`, `local` (Ollama), `claude`, `openai`.

### FinBERT

- **Installed:** `transformers`, `torch`, `accelerate`; model `ProsusAI/finbert` cached locally
- **Status:** Verified inference works; **not integrated** into trading pipeline or dashboards yet

### Prediction accuracy tracking

The AI harness tracks:

- Directional hit rate (UP/DOWN vs actual move)
- Rolling accuracy by horizon (`+5m`, `+15m`) and regime
- Confidence calibration (ECE, adjusted thresholds)

---

## CLI reference

```bash
paisa symbols                          # Default Nifty-style symbol list
paisa download [--source yfinance|upstox]
paisa upstox-quote [--ltp]             # Live quote snapshot (Upstox token required)
paisa nse-bhavcopy --date YYYY-MM-DD
paisa backtest [--strategy ma-cross]
paisa shadow [--export-bridge]
paisa stocksharp-export
paisa dashboard                        # Streamlit (port 8501)
paisa web                              # Rule-based replay (port 8080)
paisa ai-web                           # AI replay (port 8082)
paisa ai-backtest                      # Full historical AI run + report.html
paisa ai-report [--session-dir PATH]   # Regenerate HTML report
```

---

## Current experiments & metrics

### Rule-based replay (`ma-cross`, 5d / 5m)

On a recent `5d` / `5m` window for `RELIANCE.NS`, `TCS.NS`, `INFY.NS`:

- **Trade win rate:** ~11–29% per symbol (low; strategy underperforms on this window)
- **Live pooled portfolio:** briefly showed +5% mid-replay (shared cash pool across symbols)
- **Full per-symbol backtest:** slightly negative returns after costs

### AI backtest (LM Studio `liquid/lfm2.5-1.2b`)

Session: `reports/ai_sessions/session_20260621_150308`

| Metric | Value (interim ~40% complete) |
|--------|-------------------------------|
| Model calls completed | 448 / 1,125 |
| Prediction hit rate | ~42.5% |
| Directional predictions | 584 settled |
| Hits / misses | 248 / 336 |
| Trades taken | 0 (all HOLD so far) |
| Portfolio return | 0% |

**Report:** `reports/ai_sessions/session_20260621_150308/report.html`

> Local LLM backtests are slow (~3–4 decisions/min). A full 5d × 3-symbol run takes several hours.

---

## StockSharp integration

- Export: `paisa stocksharp-export` → `reports/stocksharp_bridge/<strategy>_<timestamp>/`
- Sample harness: `StockSharp/Samples/11_PaisaPaperHarness/`
- Verified with .NET SDK 10.0.301

The StockSharp tree in this repo is a **reference / bridge target**, not the primary Python runtime.

---

## Project layout

```text
paisa/
├── src/paisa_trader/       # Main Python package
├── tests/                  # pytest suite (12 modules)
├── data/                   # Generated candles (gitignored)
├── reports/                # Backtest, AI, shadow outputs (gitignored)
├── StockSharp/             # C# samples + bridge consumer
├── paisa.toml              # AI harness config
├── pyproject.toml          # Package metadata
├── README.md               # User-facing quick start
├── automated_intraday_india_plan.md   # Long-term architecture plan
└── PROJECT_STATUS.md       # This document
```

---

## Uncommitted work (as of 21 Jun 2026)

Local changes not yet committed:

| File | Change |
|------|--------|
| `src/paisa_trader/data.py` | Upstox data provider, instrument lookup, quotes |
| `src/paisa_trader/cli.py` | `--source upstox`, `upstox-quote` command |
| `src/paisa_trader/bridge.py` | Source-aware candle loading |
| `src/paisa_trader/ai_session.py` | HTML report accuracy cards |
| `tests/test_data.py` | Upstox parsing tests |
| `README.md` | Upstox usage section |
| `.gitignore` | `.env` patterns |

---

## Known limitations

1. **No real orders** — paper broker only; no demat/RMS/margin modeling.
2. **Delayed data by default** — `yfinance` is not live NSE feed.
3. **Synthetic depth** — estimated from spread/volume, not order book.
4. **No shorting** — bearish signals exit or block longs only.
5. **Upstox WebSocket** — not implemented; REST historical + quotes only.
6. **FinBERT** — installed but not connected to signals or AI prompts.
7. **AI latency** — local LLM backtests are too slow for intraday iteration without GPU or smaller windows.
8. **Secrets** — API keys/tokens must stay in environment variables, never in git.

---

## Recommended next steps

### Near term (1–2 weeks)

1. **Finish Upstox commit** — land data provider + tests + env example (`.env.example`).
2. **Wire FinBERT** — optional sentiment feature on news/headlines in `MarketSnapshot`.
3. **Complete AI backtest** — let `session_20260621_150308` finish; review final hit rate and calibration.
4. **Tune AI gate** — model returns all HOLD; review `decision_min_confidence` and prompt strictness.

### Medium term (1–2 months)

1. **Upstox WebSocket** — live LTP/full feed for replay dashboards.
2. **Broker adapter interface** — `SimulatedBrokerAdapter` vs future `UpstoxBrokerAdapter`.
3. **Scheduled shadow runs** — cron `paisa shadow` for EOD reports.
4. **Stronger baselines** — beat `ma-cross` before adding ML complexity.

### Long term

See `automated_intraday_india_plan.md` for the full India intraday roadmap (multi-broker, order flow, regime models, production controls).

---

## Environment setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,dashboard]"

# Optional: ML / FinBERT (already installed in current venv)
pip install transformers torch accelerate

# Optional: Upstox
export UPSTOX_ANALYTICS_TOKEN="your-token"

# Optional: LM Studio for AI
# Start LM Studio server on http://127.0.0.1:1234 with a chat model loaded
```

### Verify

```bash
pytest -q
paisa symbols
paisa download --symbols RELIANCE.NS --period 5d --interval 5m
```

---

## Safety & compliance notes

- This is a **research harness**, not investment advice.
- Market data from free sources may violate exchange terms for commercial use — verify licenses before production.
- Rotate any API secrets shared in chat or logs.
- Fills assume **next-bar open** — no lookahead in the simulated broker.

---

*Generated from the current workspace state. Update this document when major features land or architecture changes.*
