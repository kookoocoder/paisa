from __future__ import annotations

import gzip
import json
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests
import yfinance as yf

from .config import RAW_DIR, PROCESSED_DIR, ensure_dirs


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

UPSTOX_BASE_URL = "https://api.upstox.com/v2"
UPSTOX_INSTRUMENTS_URL = (
    "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
)
UPSTOX_INSTRUMENTS_CACHE = RAW_DIR / "upstox_complete.json.gz"
UPSTOX_INTERVALS = {
    "1m": "1minute",
    "5m": "5minute",
    "15m": "15minute",
    "30m": "30minute",
    "60m": "1hour",
    "1h": "1hour",
    "1d": "day",
    "1wk": "week",
    "1mo": "month",
}


@dataclass(frozen=True)
class CandleRequest:
    symbols: list[str]
    period: str = "6mo"
    interval: str = "1d"
    source: str = "yfinance"

    def validate(self) -> None:
        if not self.symbols:
            raise ValueError("At least one symbol is required.")
        if self.source not in {"yfinance", "upstox"}:
            raise ValueError(f"Unsupported candle source: {self.source}")
        if self.source == "yfinance" and self.interval not in VALID_INTERVALS:
            raise ValueError(f"Unsupported yfinance interval: {self.interval}")
        if self.source == "upstox" and self.interval not in UPSTOX_INTERVALS:
            raise ValueError(f"Unsupported Upstox interval: {self.interval}")


def safe_symbol(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", symbol)


def candle_path(symbol: str, period: str, interval: str, source: str = "yfinance") -> Path:
    source_prefix = "" if source == "yfinance" else f"{safe_symbol(source)}__"
    return PROCESSED_DIR / f"{source_prefix}{safe_symbol(symbol)}__{period}__{interval}.parquet"


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


def upstox_access_token() -> str:
    """Return the Upstox bearer token from environment variables.

    Args:
        None.

    Returns:
        A bearer token suitable for Upstox data APIs.

    Example:
        ``export UPSTOX_ANALYTICS_TOKEN=...`` then call ``upstox_access_token()``.
    """
    for env_name in ("UPSTOX_ANALYTICS_TOKEN", "UPSTOX_ACCESS_TOKEN", "UPSTOX_BEARER_TOKEN"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    raise RuntimeError(
        "Missing Upstox token. Set UPSTOX_ANALYTICS_TOKEN for market data "
        "or UPSTOX_ACCESS_TOKEN for an OAuth access token."
    )


def upstox_headers() -> dict[str, str]:
    """Build headers for Upstox REST data API calls.

    Args:
        None.

    Returns:
        JSON request headers with the configured bearer token.

    Example:
        ``requests.get(url, headers=upstox_headers())`` calls Upstox REST APIs.
    """
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {upstox_access_token()}",
    }


def load_upstox_instruments(force_refresh: bool = False, timeout: int = 30) -> list[dict[str, Any]]:
    """Load the Upstox BOD instrument master from local cache or Upstox.

    Args:
        force_refresh: Download a fresh instrument file even when a cache exists.
        timeout: HTTP timeout in seconds.

    Returns:
        A list of instrument dictionaries from the Upstox JSON master.

    Example:
        ``load_upstox_instruments()[0]["instrument_key"]`` returns an API key.
    """
    ensure_dirs()
    if force_refresh or not UPSTOX_INSTRUMENTS_CACHE.exists():
        response = requests.get(UPSTOX_INSTRUMENTS_URL, timeout=timeout)
        response.raise_for_status()
        UPSTOX_INSTRUMENTS_CACHE.write_bytes(response.content)

    content = UPSTOX_INSTRUMENTS_CACHE.read_bytes()
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Unexpected Upstox instrument master format.")
    return payload


def upstox_instrument_key(symbol: str, instruments: list[dict[str, Any]] | None = None) -> str:
    """Resolve a common NSE/BSE symbol to an Upstox instrument key.

    Args:
        symbol: Symbol such as ``RELIANCE.NS`` or a raw ``NSE_EQ|...`` key.
        instruments: Optional preloaded Upstox instrument master.

    Returns:
        The Upstox ``instrument_key`` for market data APIs.

    Example:
        ``upstox_instrument_key("RELIANCE.NS")`` returns ``NSE_EQ|INE002A01018``.
    """
    if "|" in symbol:
        return symbol

    normalized = symbol.upper()
    if normalized.endswith(".NS"):
        trading_symbol = normalized.removesuffix(".NS")
        segment = "NSE_EQ"
    elif normalized.endswith(".BO"):
        trading_symbol = normalized.removesuffix(".BO")
        segment = "BSE_EQ"
    else:
        trading_symbol = normalized
        segment = "NSE_EQ"

    instruments = instruments if instruments is not None else load_upstox_instruments()
    matches = [
        item
        for item in instruments
        if item.get("segment") == segment
        and str(item.get("trading_symbol", "")).upper() == trading_symbol
        and item.get("instrument_key")
    ]
    if not matches:
        raise ValueError(f"Could not resolve Upstox instrument key for {symbol}")

    eq_matches = [item for item in matches if item.get("instrument_type") == "EQ"]
    return str((eq_matches or matches)[0]["instrument_key"])


def download_upstox_symbol(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Download historical candles from Upstox and normalize them.

    Args:
        symbol: Common symbol or raw Upstox instrument key.
        period: Lookback window such as ``5d`` or ``6mo``.
        interval: Candle interval such as ``1m``, ``5m``, or ``1d``.

    Returns:
        Normalized OHLCV candles compatible with the backtest pipeline.

    Example:
        ``download_upstox_symbol("RELIANCE.NS", "5d", "5m")`` returns candles.
    """
    to_date = date.today()
    from_date = to_date - _period_to_timedelta(period)
    instrument_key = upstox_instrument_key(symbol)
    upstox_interval = UPSTOX_INTERVALS[interval]
    encoded_key = quote(instrument_key, safe="")
    url = (
        f"{UPSTOX_BASE_URL}/historical-candle/"
        f"{encoded_key}/{upstox_interval}/{to_date.isoformat()}/{from_date.isoformat()}"
    )
    response = requests.get(url, headers=upstox_headers(), timeout=30)
    response.raise_for_status()
    return _normalize_upstox_candles(response.json(), symbol)


def fetch_upstox_quotes(symbols: list[str], full: bool = True) -> dict[str, Any]:
    """Fetch current Upstox market quotes for one or more symbols.

    Args:
        symbols: Common symbols or raw Upstox instrument keys.
        full: Fetch full quotes when true, otherwise only LTP quotes.

    Returns:
        The ``data`` object returned by Upstox.

    Example:
        ``fetch_upstox_quotes(["RELIANCE.NS"], full=False)`` returns LTP data.
    """
    if not symbols:
        raise ValueError("At least one symbol is required.")

    instruments = load_upstox_instruments()
    keys = [upstox_instrument_key(symbol, instruments) for symbol in symbols]
    endpoint = "quotes" if full else "ltp"
    response = requests.get(
        f"{UPSTOX_BASE_URL}/market-quote/{endpoint}",
        params={"instrument_key": ",".join(keys)},
        headers=upstox_headers(),
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    return dict(payload.get("data", payload))


def download_candles(request: CandleRequest, force: bool = False) -> dict[str, Path]:
    request.validate()
    ensure_dirs()
    written: dict[str, Path] = {}
    for symbol in request.symbols:
        path = candle_path(symbol, request.period, request.interval, request.source)
        if path.exists() and not force:
            written[symbol] = path
            continue
        if request.source == "upstox":
            df = download_upstox_symbol(symbol, request.period, request.interval)
        else:
            df = download_symbol(symbol, request.period, request.interval)
        df.to_parquet(path, index=False)
        written[symbol] = path
    return written


def load_candles(symbol: str, period: str, interval: str, source: str = "yfinance") -> pd.DataFrame:
    path = candle_path(symbol, period, interval, source)
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. Run download first or pass --download to backtest."
        )
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def _period_to_timedelta(period: str) -> timedelta:
    match = re.fullmatch(r"(\d+)(d|wk|mo|y)", period.strip())
    if not match:
        raise ValueError("Upstox period must look like 5d, 3mo, or 1y.")
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "d":
        return timedelta(days=value)
    if unit == "wk":
        return timedelta(weeks=value)
    if unit == "mo":
        return timedelta(days=31 * value)
    return timedelta(days=366 * value)


def _normalize_upstox_candles(payload: dict[str, Any], symbol: str) -> pd.DataFrame:
    data = payload.get("data", payload)
    raw_candles = data.get("candles") if isinstance(data, dict) else data
    if raw_candles is None:
        raise ValueError(f"No Upstox candles returned for {symbol}")

    rows: list[dict[str, Any]] = []
    for candle in raw_candles:
        if isinstance(candle, dict):
            ohlcv = candle.get("ohlcv", [])
            rows.append(
                {
                    "timestamp": candle.get("timestamp"),
                    "symbol": symbol,
                    "open": candle.get("open", ohlcv[0] if len(ohlcv) > 0 else None),
                    "high": candle.get("high", ohlcv[1] if len(ohlcv) > 1 else None),
                    "low": candle.get("low", ohlcv[2] if len(ohlcv) > 2 else None),
                    "close": candle.get("close", ohlcv[3] if len(ohlcv) > 3 else None),
                    "volume": candle.get("volume", ohlcv[4] if len(ohlcv) > 4 else 0),
                }
            )
        elif isinstance(candle, list | tuple) and len(candle) >= 5:
            rows.append(
                {
                    "timestamp": candle[0],
                    "symbol": symbol,
                    "open": candle[1],
                    "high": candle[2],
                    "low": candle[3],
                    "close": candle[4],
                    "volume": candle[5] if len(candle) > 5 else 0,
                }
            )

    if not rows:
        raise ValueError(f"No Upstox candles returned for {symbol}")

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(None)
    for column in ("open", "high", "low", "close", "volume"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df[["timestamp", "symbol", "open", "high", "low", "close", "volume"]]
    return df.dropna(subset=["open", "high", "low", "close"]).sort_values("timestamp")
