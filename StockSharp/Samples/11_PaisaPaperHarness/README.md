# Paisa Paper Harness For StockSharp

This sample is the StockSharp-facing half of the no-demat workflow.

The Python project at the repository root downloads free/delayed market data with `yfinance`, fetches NSE public reports, computes automated strategy targets, and exports a bridge package:

```bash
.venv/bin/paisa stocksharp-export \
  --symbols RELIANCE.NS TCS.NS INFY.NS \
  --period 3mo \
  --interval 1d \
  --strategy ma-cross
```

That command writes files under:

```text
reports/stocksharp_bridge/<strategy>_<timestamp>/
  manifest.json
  *_candles.csv
  *_signals.csv
  *_fills.csv
  *_equity.csv
  summary.csv
```

This C# sample reads the bridge package and performs deterministic paper execution over the exported candles/signals. It is intentionally file-based because the current no-demat path has no live broker connector/API key.

## Run

Install the .NET SDK first. This workspace has been verified with .NET SDK 10.0.301.

```bash
dotnet run --project StockSharp/Samples/11_PaisaPaperHarness/11_PaisaPaperHarness.csproj \
  -- reports/stocksharp_bridge/ma-cross_YYYYMMDD_HHMMSS/manifest.json
```

## Why This Exists

The original project plan said:

- Python handles free data, features, and research.
- StockSharp acts as the event-driven trading/paper execution harness.
- Live broker adapters can be added later when broker credentials exist.

This sample is that bridge. It is not live trading and does not place real orders.
