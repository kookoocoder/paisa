# Review And Build Plan: India Intraday AI Trading Harness

Date: 2026-06-19

Scope: critique Claude's response in `/Users/earner8online@gmail.com/Downloads/2026-06-19_21-12-57_Claude_Chat_AI-powered_stock_market_harness_with_live_trading_indicators.md`, assess StockSharp/StockSharp, and propose a serious plan for automated intraday prediction/trading in India.

Important note: this is engineering and research planning, not financial advice. The system should be built to prove or disprove an edge before risking capital.

## Executive Verdict

Claude's answer is useful as a first whiteboard sketch, but it is not enough to build a useful trading system. It correctly pushes away from Pine Script as the core harness and correctly says the AI should consume structured numerical data rather than chart screenshots. But it is too US-centric, too LLM-centric, and too casual about the hard parts that decide whether an intraday system works in India:

- licensed tick/depth data,
- point-in-time instrument metadata,
- Indian fees, STT, GST, stamp duty, and brokerage,
- NSE/BSE sessions, auctions, circuit limits, and derivatives rules,
- SEBI/NSE retail algo compliance,
- broker API limits and static IP controls,
- realistic backtesting with spread, slippage, latency, partial fills, rejections, and queue assumptions,
- risk controls, kill switches, monitoring, and audit logs.

The corrected plan is: use StockSharp or a similar event-driven engine for strategy, backtesting, storage, risk, and execution abstractions; use Python for research/ML where it is strongest; use an India broker adapter for live execution; and treat data engineering plus execution simulation as the main project, not as plumbing.

## Critique Of Claude's Response

### What Claude Got Right

1. Pine Script should not be the system core.

Claude says Pine Script is a poor fit for a programmatic AI harness. That is directionally correct. TradingView can be useful for visual research and alerts, and TradingView Lightweight Charts can be useful in a custom UI, but Pine Script is not where a serious automated research/live execution stack should live.

2. The model should receive structured data, not screenshots.

Claude's line that the AI needs numerical features such as RSI, MACD, order-book imbalance, and regime is right. For production, this should become a typed feature contract with timestamps, source, validity window, and stale-data checks.

3. Regime awareness and order-flow features matter.

Claude's second answer correctly notes that one strategy will not work in all regimes, and that order flow/microstructure can matter more than candle patterns for short horizons.

4. The warning about "maximum accuracy" is good.

Markets are noisy and adaptive. A useful system should optimize expected value after costs and risk, not simply directional accuracy.

### Major Problems

1. The data/vendor advice is US-centric and partially irrelevant for India.

Claude recommends Alpaca, Polygon, FRED, NewsAPI, Unusual Whales, and Quiver. Those are not the right first choices for Indian NSE/BSE intraday execution. For India, the plan should start with NSE/BSE-authorized data, Indian broker APIs, corporate actions, contract masters, bhavcopies, tick/order-book vendors, and broker execution constraints.

2. It underestimates data licensing and historical tick/depth requirements.

Broker candles and quote streams are not enough to prove an intraday edge. A useful system needs licensed historical intraday/tick/order-book data, point-in-time instrument identity, expired derivatives, corporate actions, and survivorship-safe universes.

3. It treats the LLM as a decision maker too early.

An LLM should not be the primary trade-entry model at first. Use it as an analyst/controller layer for summarization, diagnostics, hypothesis generation, and explaining model state. Primary trade signals should start with measurable statistical/ML models whose calibration and net edge can be validated.

4. It says "chain-of-thought prompting" as if that improves trading validity.

For a production system, require structured rationale fields and disqualifiers, but do not depend on hidden reasoning text as evidence of correctness. The evidence is out-of-sample net performance, calibration, drift behavior, and operational reliability.

5. It suggests an ensemble of XGBoost + LSTM + Transformer without first demanding baselines.

This is a classic path to a toy project. The first models should be no-trade, random with same turnover, simple momentum/reversal, VWAP/mean-reversion, logistic regression, and gradient boosted trees. Deep learning only earns a place after the data and validation prove there is a signal deep models can exploit.

6. It gives a vague 55-65% directional accuracy target.

Accuracy alone is a weak metric. A 52% model can be excellent or worthless depending on payoff, spread, fees, slippage, turnover, and calibration. The target should be net expected value and risk-adjusted performance after realistic costs.

7. It omits India-specific costs.

India intraday backtests must include brokerage, STT, exchange transaction charges, GST, SEBI fee, stamp duty, bid-ask spread, slippage, and failed-fill assumptions. For options, STT and transaction charges can completely change whether a short-horizon signal is viable.

8. It omits SEBI/NSE retail algo controls.

As of 2026, Indian retail API/algo trading needs static IP whitelisting, API-key traceability, broker controls, order tagging/registration rules, and order-rate throttling. The system must be built as an auditable controlled execution system, not just a Python loop.

9. It omits broker operational reality.

Live trading has disconnects, duplicate events, stale ticks, rejected orders, RMS blocks, order freeze quantities, rate limits, token/session refreshes, WebSocket gaps, and broker outages. Those are core design inputs.

10. It does not mention StockSharp.

The user referenced StockSharp. Claude's plan should have assessed whether StockSharp can provide a serious event-driven engine instead of inventing everything in FastAPI/Redis/Python.

## StockSharp Fit Assessment

The cloned repo is at `/Users/earner8online@gmail.com/Documents/Projects/paisa/StockSharp`.

Evidence from repo/docs:

- `README.md` describes StockSharp as a C# API/platform for automated trading, strategy testing, market data storage, indicators, and connectors.
- The repo includes strategy abstractions under `Algo.Strategies`.
- It includes backtesting/emulation under `Algo.Testing`, including `HistoryEmulationConnector`, `MarketEmulator`, and `HistoryMarketDataManager`.
- It includes modules for commissions, slippage, risk, PnL, statistics, candles, indicators, storage, and optimization.
- `Connectors/README.md` says this repo contains connector examples and that complete connectors are distributed through StockSharp Store/NuGet. The checked-out examples are mostly crypto and non-India connectors; no Zerodha/Upstox/Dhan/Angel India connector is visible in this repo.

Best use of StockSharp:

- event-driven strategy shell,
- historical replay and emulation,
- risk/commission/slippage abstractions,
- market data storage/replay,
- order lifecycle modeling,
- live execution adapter interface,
- C# production engine for strategies that need strong type safety and event handling.

Gaps to fill:

- India broker adapters: DhanHQ, Upstox, Zerodha, FYERS, Angel One, or IBKR India.
- India market calendar/session/circuit/contract metadata.
- Full India fee model: brokerage, STT, exchange transaction charge, SEBI fee, GST, stamp duty.
- Licensed tick/depth data ingestion.
- Point-in-time instrument master and corporate actions.
- Python research/ML pipeline integration.
- Monitoring, model registry, replay/audit store, and deployment automation.

Recommended architecture with StockSharp:

```text
Licensed historical/live data
  -> raw immutable store
  -> normalized market-data lake
  -> StockSharp historical replay / emulation
  -> feature generation parity layer
  -> Python research + model training
  -> model service emits calibrated signal
  -> StockSharp strategy/risk/execution engine
  -> broker adapter
  -> order/fill/audit store
  -> monitoring + kill switches
```

Use StockSharp for the trading engine, not necessarily for every ML experiment. Keep Python for research tooling because Polars/Pandas/scikit-learn/XGBoost/PyTorch are faster to iterate with.

## India Intraday Constraints To Design Around

Market/session:

- NSE/BSE cash equity, equity futures, index futures, stock options, and index options have different liquidity, cost, settlement, and risk behavior.
- Normal NSE equity cash trading is 09:15-15:30 IST, with pre-open and closing sessions. Check current NSE timing circulars before go-live because F&O timing changes are being introduced.
- T+0 cash settlement exists only for selected securities; T+1 remains central for cash equities.
- Derivatives have expiry, lot size, tick size, price band/operating range, margin, and freeze quantity constraints.

Costs:

- Include brokerage, STT, exchange transaction charge, SEBI turnover fee, GST, stamp duty, bid-ask spread, slippage, and failed-fill opportunity cost.
- Report gross and net results separately. A strategy that only works gross is not viable.

Data:

- Broker APIs are useful for execution and basic live feed.
- Serious research needs licensed historical tick/depth data and point-in-time metadata.
- Store raw data unmodified; derive bars/features later.
- Avoid survivorship bias from today's index constituents or listed universe.

Regulation/compliance:

- Design around static IP, API-key traceability, OAuth/2FA/session handling, broker-side controls, order tagging, rate limits, and audit logs.
- Keep well below 10 orders/sec for unregistered retail automation unless broker/exchange registration explicitly allows more.

Execution:

- Prefer limit/IOC logic over blind market orders in thin names/options.
- Model order rejections, partial fills, queue position, latency, spread crossing, stale data, and broker reconnect behavior.
- Add a manual kill switch and automated data/model/execution/risk kill switches.

## Broker/API Shortlist

Shortlist for first implementation:

1. DhanHQ
   - Strong live feed and sandbox story.
   - Useful if depth and broad instrument streaming matter.
   - Verify current order-rate policy before production.

2. Upstox
   - Good sandbox-first development path and free API positioning.
   - Verify WebSocket modes, subscription caps, and sandbox endpoint coverage.

3. Zerodha Kite Connect
   - Mature ecosystem and good docs.
   - No official sandbox; data tier costs money.
   - Good conservative benchmark integration.

Secondary/backups:

- FYERS: useful free API, but validate rate limits, support, and paper trading story.
- Angel One SmartAPI: broad/free, but validate reliability and rate-limit behavior hard.
- IBKR India: valuable for global/IBKR workflows, heavier for India-only retail intraday.

Design principle: implement a broker abstraction so the model/risk engine is not married to one broker.

## No Demat Account Path: Free Data And Paper Trading

If no demat/trading account is available, do not block the project. Build the system in research and paper mode first.

What is still possible:

- historical research,
- indicator and feature pipelines,
- AI/human market dashboard,
- event-driven backtesting,
- paper trading,
- model evaluation,
- replay/audit infrastructure,
- simulated broker adapter.

What is not possible yet:

- real order placement on NSE/BSE through Indian broker APIs,
- real order/fill reconciliation,
- broker RMS/margin behavior testing,
- true live execution latency measurement.

Recommended no-live-orders data stack:

1. Upstox REST API
   - Requires `UPSTOX_ANALYTICS_TOKEN` from a broker-backed Upstox developer app.
   - Works with native instrument keys resolved from plain symbols such as `RELIANCE`.
   - Suitable for historical and same-session candle research.
   - Broker-backed data is the project foundation; do not add unofficial scraper fallbacks.

2. NSE public reports and bhavcopies
   - Official exchange source for EOD and historical reports.
   - No broker account required.
   - Better for daily historical validation than intraday prediction.
   - Use for bhavcopy ingestion, symbol/reference sanity checks, and EOD backtests.

3. Alpha Vantage free tier
   - Free API key, no demat required.
   - Very limited free quota; currently advertised as 25 requests/day.
   - Global coverage, but Indian symbol coverage can be inconsistent, so treat it as secondary.

4. Twelve Data / Marketstack / similar freemium APIs
   - May cover Indian exchanges on delayed/EOD plans.
   - Free quotas are small and terms vary.
   - Useful only for prototyping; verify symbol coverage and license before relying on them.

5. Manual CSV imports
   - Download NSE reports, Upstox exports, or other licensed datasets.
   - Good for deterministic backtests and reproducible experiments.

Architecture change for no-demat mode:

```text
Upstox historical data source
  -> normalized local store
  -> feature pipeline
  -> model/backtest
  -> simulated broker
  -> paper fills
  -> dashboard and audit log
```

Add a `BrokerAdapter` interface with at least two implementations:

- `SimulatedBrokerAdapter`: fills orders using historical candles or broker-backed quote snapshots with configurable spread, slippage, latency, and partial-fill assumptions.
- `LiveBrokerAdapter`: later implementation for DhanHQ, Upstox, Zerodha, etc.

This keeps the project useful now and prevents rewrites later.

Recommended first no-demat deliverable:

- Use Upstox for Nifty 50 cash symbols and index data.
- Use NSE public reports for EOD reference data.
- Build a local Parquet/SQLite store.
- Build a paper broker with realistic costs/slippage assumptions.
- Run simple no-trade/random/momentum/mean-reversion baselines.
- Produce a daily report with gross vs net simulated PnL.

Do not use this mode to claim real tradability. It is for building the research and control system until a real broker account/API can be connected.

## Data Plan

Minimum viable but serious data stack:

1. Reference data
   - instrument master with point-in-time validity,
   - NSE/BSE symbol, token, ISIN, lot size, tick size, expiry, strike, option type,
   - corporate actions,
   - index constituents if testing index universes,
   - trading calendar and special sessions.

2. Historical market data
   - start with 1-minute bars only for slow prototypes,
   - move to tick/trade data for intraday validation,
   - add L2/L3/order-book history for order-flow strategies,
   - include expired derivatives and delisted/renamed symbols where relevant.

3. Live data
   - broker WebSocket for execution-linked monitoring,
   - licensed/vendor feed for source-of-truth if budget allows,
   - sequence/gap/staleness checks.

4. Storage
   - raw immutable payloads,
   - normalized Parquet partitioned by date/exchange/segment/instrument,
   - ClickHouse/QuestDB/TimescaleDB for live/replay querying,
   - Redis only for short-lived live state, not as the research database.

Core tables:

- `instrument_master_pti`
- `trades`
- `quotes_l1`
- `order_book_l2`
- `bars_1s`
- `bars_1m`
- `corporate_actions`
- `index_constituents_pti`
- `orders`
- `fills`
- `model_decisions`
- `risk_decisions`
- `data_quality_events`

## Modeling Plan

Do not start with "AI predicts next move." Start with tradable labels and baselines.

Labels:

- fixed-horizon return after latency,
- up/down/flat with no-trade band,
- triple-barrier labels with stop/target/timeout,
- meta-labels to decide whether to take a candidate trade,
- expected net edge after cost threshold.

Features:

- returns over multiple horizons,
- realized volatility/range,
- VWAP distance,
- spread and depth,
- order-book imbalance,
- trade imbalance,
- relative volume,
- open interest and option Greeks where reliable,
- time-of-day and expiry flags,
- index/sector context,
- execution-state features such as current position and recent slippage.

Validation:

- strict as-of timestamps,
- walk-forward validation,
- purged/embargoed cross-validation for overlapping labels,
- no global preprocessing,
- final untouched test period,
- performance by regime, symbol, time of day, liquidity, and spread bucket.

Baselines before advanced ML:

- no-trade,
- random same-turnover,
- buy/hold intraday,
- simple momentum,
- simple mean reversion,
- VWAP deviation,
- logistic regression,
- gradient boosted trees.

Only after those are beaten net of costs should you add:

- XGBoost/LightGBM ensembles,
- sequence models for order-book/tick data,
- transformers only if dataset size and evaluation justify them,
- LLM as analyst/controller, not primary signal engine.

## Backtesting Requirements

The backtester must be event-driven and cost-aware.

Must model:

- bid/ask spread,
- market and limit orders,
- IOC/day validity,
- partial fills,
- queue/fill probability approximation,
- latency from signal to order to ack to fill,
- order rejections and rate limits,
- broker disconnects,
- fees/taxes/charges,
- price bands/circuit halts,
- expiry/lot-size/freeze quantity behavior,
- stale and missing data,
- position and margin constraints.

Metrics:

- net PnL,
- net Sharpe/Sortino,
- deflated Sharpe or other overfit-aware score,
- max drawdown,
- profit factor,
- average win/loss,
- PnL per trade,
- PnL per turnover,
- fill rate,
- slippage vs estimate,
- reject rate,
- capacity estimate,
- calibration and Brier score,
- performance by regime/liquidity/time bucket.

## Risk And Controls

Hard limits:

- max position per symbol,
- max gross/net exposure,
- max lots/contracts,
- max order size,
- max order rate below broker cap,
- max daily loss,
- max intraday drawdown,
- max open orders,
- max turnover,
- max consecutive rejects/losses,
- no-trade windows around events if needed.

Kill switches:

- data kill: stale feed, gaps, clock skew, abnormal spread/depth,
- model kill: NaN output, confidence spike, drift, stale model,
- execution kill: reject burst, duplicate orders, latency spike, broker disconnect,
- risk kill: loss/exposure/turnover breach,
- manual kill: cancel open orders and freeze or flatten by policy.

Audit:

- every decision logs features, model version, prediction, expected edge, risk decision, order intent, order response, fills, rejects, and realized outcome.
- replay any trading day from raw events.

## Phased Build Plan

### Phase 0: Scope The First Tradable Problem

Decision to make:

- cash equities, index futures, or index options?
- NSE only, BSE only, or both?
- advisory signals first or automated execution?
- intraday horizon: 1 min, 5 min, 15 min, or event/tick?
- initial universe: e.g. Nifty 50 cash, Nifty futures, or liquid near-ATM index options.

Recommended first scope:

- advisory/shadow mode first,
- NSE Nifty 50 cash or Nifty futures before options,
- 5-minute to 15-minute horizon,
- no overnight positions,
- one broker adapter first, with abstraction for a second later.

Exit criteria:

- written strategy mandate,
- approved risk limits,
- data source selected,
- broker selected,
- compliance checklist accepted.

### Phase 1: Data Foundation

Build:

- instrument master,
- trading calendar,
- corporate actions,
- historical bars/ticks ingestion,
- live WebSocket ingestion,
- raw and normalized storage,
- data-quality reports.

Exit criteria:

- replay at least 60 trading days,
- no unexplained missing bars for selected universe,
- data-quality dashboard,
- feature timestamps are as-of safe.

### Phase 2: StockSharp Prototype Harness

Build:

- StockSharp-based strategy shell,
- historical replay path,
- India fee/commission model,
- initial slippage model,
- basic risk rules,
- simple strategy baseline.

Exit criteria:

- backtest one simple baseline end to end,
- gross and net reports differ correctly,
- orders/fills/logs are persisted,
- replay is deterministic.

### Phase 3: Research Baselines

Build:

- Python feature pipeline,
- labels,
- baseline models,
- walk-forward validation,
- report generator.

Exit criteria:

- no-trade/random/simple-rule baselines documented,
- model beats baselines after costs in at least two walk-forward windows,
- performance is not concentrated in one day/symbol,
- final holdout remains untouched.

### Phase 4: Live Shadow System

Build:

- live data pipeline,
- model service,
- StockSharp live strategy in no-order mode,
- dashboard,
- audit logs,
- alerting.

Exit criteria:

- live signals match replay logic,
- no stale-feature trades,
- latency measured,
- daily post-market report generated,
- shadow PnL tracked against realistic fills.

### Phase 5: Broker Execution Adapter

Build:

- broker adapter for DhanHQ or Upstox first,
- order state machine,
- REST/WebSocket reconciliation,
- local rate limiter,
- static IP/session handling,
- paper/sandbox execution if available.

Exit criteria:

- can place/modify/cancel in sandbox or smallest-safe mode,
- rejects and disconnects handled,
- kill switch tested,
- audit log complete.

### Phase 6: Limited Capital Pilot

Build:

- strict risk cap,
- small size only,
- daily review pack,
- automatic stop after breach,
- manual intervention process.

Exit criteria:

- multiple weeks stable operations,
- realized slippage within expected band,
- no uncontrolled risk event,
- model calibration remains acceptable,
- net performance supports or rejects scaling.

### Phase 7: Scale And Governance

Build:

- model registry,
- experiment registry,
- promotion/rollback policy,
- capacity analysis,
- second broker/data source redundancy,
- incident postmortems.

Exit criteria:

- model promotion requires evidence,
- rollback tested,
- capacity curve estimated,
- monitoring reviewed daily,
- costs and slippage remain stable as size increases.

## Immediate Next Decisions For You

1. Choose first segment:
   - safer engineering path: Nifty 50 cash or Nifty futures,
   - harder but popular path: near-ATM Nifty/Bank Nifty options.

2. Choose first broker:
   - DhanHQ if sandbox/depth matters,
   - Upstox if sandbox/free API matters,
   - Zerodha if maturity matters and no sandbox is acceptable.

3. Choose data budget:
   - prototype: broker bars/WebSocket,
   - serious validation: paid historical tick/depth vendor,
   - production-grade: authorized exchange/vendor feed plus historical archive.

4. Choose first deliverable:
   - research-only backtester,
   - live market dashboard for AI/human observation,
   - shadow trading system,
   - broker execution adapter.

## Source Pointers

- Claude response: `/Users/earner8online@gmail.com/Downloads/2026-06-19_21-12-57_Claude_Chat_AI-powered_stock_market_harness_with_live_trading_indicators.md`
- StockSharp repo: `https://github.com/StockSharp/StockSharp`
- StockSharp docs/site: `https://doc.stocksharp.com`, `https://stocksharp.com`
- SEBI retail algo circular: `https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html`
- NSE algo trading page: `https://www.nseindia.com/static/trade/platform-services-non-neat-decision-support-tools-algorithm-trading`
- NSE market timings: `https://www.nseindia.com/static/market-data/market-timings`
- NSE Data and Analytics: `https://www.nseindia.com/static/nse-data-and-analytics`
- NSE real-time data subscription: `https://www.nseindia.com/static/market-data/real-time-data-subscription`
- Zerodha Kite docs: `https://kite.trade/docs/connect/v3/`
- Zerodha API/rate-limit FAQ: `https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/kite-connect-api-faqs`
- Upstox developer docs: `https://upstox.com/developer/api-documentation/api-overview`
- DhanHQ docs: `https://dhanhq.co/docs/v2/`
- Angel One SmartAPI docs: `https://smartapi.angelbroking.com/docs`
- FYERS API: `https://fyers.in/products/api`
