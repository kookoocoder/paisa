from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yfinance as yf

from .config import PROCESSED_DIR, ensure_dirs


VALID_INTERVALS = {
    "1m",
    "2m",
    "5m",
    "15m",
    "30m",
    "60m",
    "90m",
    "1h",
    "1d",
    "5d",
    "1wk",
    "1mo",
    "3mo",
}


@dataclass(frozen=True)
class CandleRequest:
    symbols: list[str]
    period: str = "6mo"
    interval: str = "1d"

    def validate(self) -> None:
        if not self.symbols:
            raise ValueError("At least one symbol is required.")
        if self.interval not in VALID_INTERVALS:
            raise ValueError(f"Unsupported yfinance interval: {self.interval}")


def safe_symbol(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", symbol)


def candle_path(symbol: str, period: str, interval: str) -> Path:
    return PROCESSED_DIR / f"{safe_symbol(symbol)}__{period}__{interval}.parquet"


def normalize_ohlcv(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if df.empty:
        raise ValueError(f"No data returned for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] if col[0] else col[-1] for col in df.columns]

    renamed = {c: c.lower().replace(" ", "_") for c in df.columns}
    out = df.rename(columns=renamed).copy()
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"{symbol} data missing required columns: {sorted(missing)}")

    out.index = pd.to_datetime(out.index)
    out.index.name = "timestamp"
    out = out.reset_index()
    out["symbol"] = symbol
    out = out[["timestamp", "symbol", "open", "high", "low", "close", "volume"]]
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out.sort_values("timestamp").drop_duplicates(["timestamp", "symbol"])
    return out


def download_symbol(symbol: str, period: str, interval: str) -> pd.DataFrame:
    raw = yf.download(
        symbol,
        period=period,
        interval=interval,
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    return normalize_ohlcv(raw, symbol)


def download_candles(request: CandleRequest, force: bool = False) -> dict[str, Path]:
    request.validate()
    ensure_dirs()
    written: dict[str, Path] = {}
    for symbol in request.symbols:
        path = candle_path(symbol, request.period, request.interval)
        if path.exists() and not force:
            written[symbol] = path
            continue
        df = download_symbol(symbol, request.period, request.interval)
        df.to_parquet(path, index=False)
        written[symbol] = path
    return written


def load_candles(symbol: str, period: str, interval: str) -> pd.DataFrame:
    path = candle_path(symbol, period, interval)
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. Run download first or pass --download to backtest."
        )
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)
