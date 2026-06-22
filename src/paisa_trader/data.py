from __future__ import annotations

import gzip
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, overload
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .config import PROCESSED_DIR, RAW_DIR, ensure_dirs


UPSTOX_BASE_URL = "https://api.upstox.com/v2"
UPSTOX_INSTRUMENTS_URL = (
    "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
)
UPSTOX_INSTRUMENTS_CACHE = RAW_DIR / "upstox_complete.json.gz"
IST = ZoneInfo("Asia/Kolkata")

UPSTOX_INTERVALS = {
    "1m": "1minute",
    "1minute": "1minute",
    "5m": "5minute",
    "5minute": "5minute",
    "15m": "15minute",
    "15minute": "15minute",
    "30m": "30minute",
    "30minute": "30minute",
    "60m": "60minute",
    "1h": "60minute",
    "60minute": "60minute",
    "1d": "1day",
    "1day": "1day",
}

COMMON_INSTRUMENT_KEYS = {
    "NIFTY50": "NSE_INDEX|Nifty 50",
    "NIFTY": "NSE_INDEX|Nifty 50",
    "^NSEI": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "^NSEBANK": "NSE_INDEX|Nifty Bank",
    "RELIANCE": "NSE_EQ|INE002A01018",
    "RELIANCE.NS": "NSE_EQ|INE002A01018",
    "TCS": "NSE_EQ|INE467B01029",
    "TCS.NS": "NSE_EQ|INE467B01029",
    "INFY": "NSE_EQ|INE009A01021",
    "INFY.NS": "NSE_EQ|INE009A01021",
    "HDFCBANK": "NSE_EQ|INE040A01034",
    "HDFCBANK.NS": "NSE_EQ|INE040A01034",
    "ICICIBANK": "NSE_EQ|INE090A01021",
    "ICICIBANK.NS": "NSE_EQ|INE090A01021",
    "WIPRO": "NSE_EQ|INE075A01022",
    "WIPRO.NS": "NSE_EQ|INE075A01022",
    "SBIN": "NSE_EQ|INE062A01020",
    "SBIN.NS": "NSE_EQ|INE062A01020",
    "AXISBANK": "NSE_EQ|INE238A01034",
    "AXISBANK.NS": "NSE_EQ|INE238A01034",
    "LT": "NSE_EQ|INE018A01030",
    "ITC": "NSE_EQ|INE154A01025",
    "BHARTIARTL": "NSE_EQ|INE397D01024",
}


@dataclass(frozen=True)
class CandleRequest:
    symbols: list[str]
    period: str = "5d"
    interval: str = "5minute"

    def validate(self) -> None:
        if not self.symbols:
            raise ValueError("At least one symbol is required.")
        _normalize_interval(self.interval)


def safe_symbol(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", symbol)


def candle_path(symbol: str, period: str, interval: str) -> Path:
    return PROCESSED_DIR / f"{safe_symbol(symbol)}__{period}__{_normalize_interval(interval)}.parquet"


def get_upstox_client():
    """Return a configured Upstox HistoryApi client.

    Args:
        None.

    Returns:
        A configured ``upstox_client.HistoryApi`` instance.

    Example:
        ``get_upstox_client().get_historical_candle_data1(...)`` fetches candles.
    """
    token = os.environ.get("UPSTOX_ANALYTICS_TOKEN", "").strip()
    if not token:
        raise EnvironmentError("Set UPSTOX_ANALYTICS_TOKEN before requesting Upstox market data.")
    try:
        import upstox_client
    except ImportError as exc:
        raise RuntimeError("Install upstox-python-sdk to use the Upstox HistoryApi client.") from exc

    configuration = upstox_client.Configuration()
    configuration.access_token = token
    return upstox_client.HistoryApi(upstox_client.ApiClient(configuration))


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


def resolve_instrument_key(symbol: str) -> str:
    """Resolve a human-readable symbol to an Upstox instrument key.

    Args:
        symbol: Plain symbol such as ``RELIANCE`` or a legacy ``RELIANCE.NS`` value.

    Returns:
        The Upstox ``instrument_key`` for market data APIs.

    Example:
        ``resolve_instrument_key("RELIANCE")`` returns ``NSE_EQ|INE002A01018``.
    """
    if "|" in symbol:
        return symbol

    normalized = symbol.upper()
    if normalized in COMMON_INSTRUMENT_KEYS:
        return COMMON_INSTRUMENT_KEYS[normalized]

    trading_symbol = normalized.removesuffix(".NS").removesuffix(".BO")
    segment = "BSE_EQ" if normalized.endswith(".BO") else "NSE_EQ"

    try:
        instruments = load_upstox_instruments()
    except Exception as exc:
        raise ValueError(
            f"Could not resolve {symbol}. Use a known symbol, an Upstox instrument key, "
            "or refresh the Upstox instrument master."
        ) from exc
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


def upstox_instrument_key(symbol: str, instruments: list[dict[str, Any]] | None = None) -> str:
    """Backward-compatible alias for ``resolve_instrument_key``."""
    if instruments is None:
        return resolve_instrument_key(symbol)
    if "|" in symbol:
        return symbol
    normalized = symbol.upper()
    if normalized in COMMON_INSTRUMENT_KEYS:
        return COMMON_INSTRUMENT_KEYS[normalized]
    trading_symbol = normalized.removesuffix(".NS").removesuffix(".BO")
    segment = "BSE_EQ" if normalized.endswith(".BO") else "NSE_EQ"
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
    """Download Upstox candles and return the cached lowercase app frame.

    Args:
        symbol: Common symbol or raw Upstox instrument key.
        period: Lookback window such as ``5d`` or ``6mo``.
        interval: Candle interval such as ``5minute`` or legacy ``5m``.

    Returns:
        Normalized OHLCV candles compatible with the backtest pipeline.

    Example:
        ``download_upstox_symbol("RELIANCE", "5d", "5minute")`` returns candles.
    """
    return _canonical_to_cache_frame(download_candles(symbol, interval=interval, period=period), symbol)


def _fetch_upstox_candles(
    symbol: str,
    interval: str,
    from_date: str,
    to_date: str,
) -> pd.DataFrame:
    instrument_key = resolve_instrument_key(symbol)
    upstox_interval = _normalize_interval(interval)
    encoded_key = quote(instrument_key, safe="")
    url = (
        f"{UPSTOX_BASE_URL}/historical-candle/"
        f"{encoded_key}/{upstox_interval}/{to_date}/{from_date}"
    )
    response = requests.get(url, headers=upstox_headers(), timeout=30)
    response.raise_for_status()
    return _normalize_upstox_candles(response.json(), symbol)


def nse_equity_universe(force_refresh: bool = False) -> list[dict[str, str]]:
    """Return all NSE equity instruments from the Upstox BOD master.

    Args:
        force_refresh: Download a fresh instrument file even when a cache exists.

    Returns:
        A list of dicts with ``symbol``, ``instrument_key``, and ``name`` fields.

    Example:
        ``nse_equity_universe()[0]["symbol"]`` returns a NSE trading symbol.
    """
    instruments = load_upstox_instruments(force_refresh=force_refresh)
    universe: list[dict[str, str]] = []
    for item in instruments:
        if item.get("segment") != "NSE_EQ" or item.get("instrument_type") != "EQ":
            continue
        trading_symbol = str(item.get("trading_symbol", "")).strip()
        instrument_key = str(item.get("instrument_key", "")).strip()
        if not trading_symbol or not instrument_key:
            continue
        universe.append(
            {
                "symbol": trading_symbol,
                "instrument_key": instrument_key,
                "name": str(item.get("name", "")).strip(),
            }
        )
    return sorted(universe, key=lambda row: row["symbol"])


def fetch_upstox_quotes_by_keys(
    instrument_keys: list[str],
    *,
    full: bool = True,
    batch_size: int = 500,
) -> dict[str, Any]:
    """Fetch Upstox quotes for raw instrument keys in API-sized batches.

    Args:
        instrument_keys: Upstox instrument keys such as ``NSE_EQ|INE002A01018``.
        full: Fetch full quotes when true, otherwise only LTP quotes.
        batch_size: Maximum keys per Upstox request.

    Returns:
        A merged ``instrument_key -> quote`` mapping from Upstox.

    Example:
        ``fetch_upstox_quotes_by_keys(["NSE_EQ|INE002A01018"], full=False)``
        returns the latest LTP payload.
    """
    if not instrument_keys:
        return {}
    endpoint = "quotes" if full else "ltp"
    merged: dict[str, Any] = {}
    for start in range(0, len(instrument_keys), batch_size):
        chunk = instrument_keys[start : start + batch_size]
        response = requests.get(
            f"{UPSTOX_BASE_URL}/market-quote/{endpoint}",
            params={"instrument_key": ",".join(chunk)},
            headers=upstox_headers(),
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        merged.update(dict(payload.get("data", payload)))
    return merged


def quote_snapshot(instrument_key: str, raw: dict[str, Any], trading_symbol: str) -> dict[str, Any]:
    """Normalize one Upstox quote payload into a compact market row.

    Args:
        instrument_key: Upstox instrument key for the quote.
        raw: Raw quote object returned by Upstox.
        trading_symbol: Human-readable NSE trading symbol.

    Returns:
        A dict with LTP, OHLC, volume, and day change percent fields.

    Example:
        ``quote_snapshot(key, raw, "RELIANCE")["ltp"]`` returns the last traded price.
    """
    ohlc = raw.get("ohlc") or {}
    ltp = float(raw.get("last_price", raw.get("ltp", 0.0)) or 0.0)
    prev_close = float(ohlc.get("close", ltp) or ltp or 0.0)
    change_pct = ((ltp - prev_close) / prev_close * 100.0) if prev_close else 0.0
    return {
        "symbol": trading_symbol,
        "instrument_key": instrument_key,
        "name": str(raw.get("name", "")).strip(),
        "ltp": round(ltp, 4),
        "open": round(float(ohlc.get("open", raw.get("open", 0.0)) or 0.0), 4),
        "high": round(float(ohlc.get("high", raw.get("high", 0.0)) or 0.0), 4),
        "low": round(float(ohlc.get("low", raw.get("low", 0.0)) or 0.0), 4),
        "prev_close": round(prev_close, 4),
        "change_pct": round(change_pct, 4),
        "volume": int(raw.get("volume", raw.get("volume_traded", 0)) or 0),
        "timestamp": str(raw.get("timestamp", raw.get("last_trade_time", ""))),
    }


def fetch_upstox_quotes(symbols: list[str], full: bool = True) -> dict[str, Any]:
    """Fetch current Upstox market quotes for one or more symbols.

    Args:
        symbols: Common symbols or raw Upstox instrument keys.
        full: Fetch full quotes when true, otherwise only LTP quotes.

    Returns:
        The ``data`` object returned by Upstox.

    Example:
        ``fetch_upstox_quotes(["RELIANCE"], full=False)`` returns LTP data.
    """
    if not symbols:
        raise ValueError("At least one symbol is required.")

    keys = [resolve_instrument_key(symbol) for symbol in symbols]
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


def get_live_quote(symbol: str) -> dict[str, Any]:
    """Fetch the current Upstox quote for a symbol.

    Args:
        symbol: Plain symbol or Upstox instrument key.

    Returns:
        A dict with symbol, LTP, OHLC, volume, and timestamp fields.

    Example:
        ``get_live_quote("RELIANCE")["ltp"]`` returns the last traded price.
    """
    quotes = fetch_upstox_quotes([symbol], full=True)
    raw = next(iter(quotes.values()), {})
    ohlc = raw.get("ohlc") or {}
    return {
        "symbol": symbol,
        "ltp": float(raw.get("last_price", raw.get("ltp", 0.0)) or 0.0),
        "open": float(ohlc.get("open", raw.get("open", 0.0)) or 0.0),
        "high": float(ohlc.get("high", raw.get("high", 0.0)) or 0.0),
        "low": float(ohlc.get("low", raw.get("low", 0.0)) or 0.0),
        "close": float(ohlc.get("close", raw.get("close", 0.0)) or 0.0),
        "volume": int(raw.get("volume", raw.get("volume_traded", 0)) or 0),
        "timestamp": str(raw.get("timestamp", raw.get("last_trade_time", ""))),
    }


@overload
def download_candles(request: CandleRequest, force: bool = False) -> dict[str, Path]: ...


@overload
def download_candles(
    request: str,
    interval: str = "5minute",
    from_date: str | None = None,
    to_date: str | None = None,
    period: str = "5d",
) -> pd.DataFrame: ...


def download_candles(
    request: CandleRequest | str,
    force: bool = False,
    interval: str = "5minute",
    from_date: str | None = None,
    to_date: str | None = None,
    period: str = "5d",
) -> dict[str, Path] | pd.DataFrame:
    if isinstance(request, str):
        return _download_candles_frame(request, interval, from_date, to_date, period)

    request.validate()
    ensure_dirs()
    written: dict[str, Path] = {}
    for symbol in request.symbols:
        path = candle_path(symbol, request.period, request.interval)
        if path.exists() and not force:
            written[symbol] = path
            continue
        df = download_upstox_symbol(symbol, request.period, request.interval)
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


def _symbol_cache_candidates(symbol: str) -> list[str]:
    normalized = symbol.upper()
    candidates = [normalized]
    if not normalized.endswith(".NS"):
        candidates.append(f"{normalized}.NS")
    else:
        candidates.append(normalized.removesuffix(".NS"))
    seen: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.append(candidate)
    return seen


def load_candles_cached(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Load cached candles, trying common NSE symbol filename variants.

    Args:
        symbol: Plain or ``.NS`` suffixed symbol.
        period: Cached lookback window such as ``5d``.
        interval: Cached interval such as ``5minute``.

    Returns:
        Cached OHLCV candles for the first matching parquet file.

    Example:
        ``load_candles_cached("RELIANCE", "5d", "5minute")`` reads ``RELIANCE.NS`` cache too.
    """
    for candidate in _symbol_cache_candidates(symbol):
        path = candle_path(candidate, period, interval)
        if path.exists():
            return load_candles(candidate, period, interval)
    raise FileNotFoundError(
        f"No cached candles for {symbol} ({period}, {interval}). Run `paisa download` first."
    )


def get_intraday_candles(symbol: str, interval: str = "5minute") -> pd.DataFrame:
    """Fetch today's intraday Upstox candles.

    Args:
        symbol: Plain symbol or Upstox instrument key.
        interval: Upstox interval such as ``5minute``.

    Returns:
        A canonical OHLCV DataFrame indexed by IST timestamp.

    Example:
        ``get_intraday_candles("RELIANCE").tail(1)`` returns the latest bar.
    """
    today = datetime.now(IST).date().isoformat()
    return _download_candles_frame(symbol, interval, today, today, "1d")


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


def _normalize_interval(interval: str) -> str:
    normalized = interval.strip().lower()
    if normalized not in UPSTOX_INTERVALS:
        raise ValueError(
            "Unsupported Upstox interval. Use one of: "
            "1minute, 5minute, 15minute, 30minute, 60minute, 1day."
        )
    return UPSTOX_INTERVALS[normalized]


def _date_range(from_date: str | None, to_date: str | None, period: str) -> tuple[str, str]:
    if from_date and to_date:
        return from_date, to_date
    end = date.today()
    start = end - _period_to_timedelta(period)
    return start.isoformat(), end.isoformat()


def _download_candles_frame(
    symbol: str,
    interval: str = "5minute",
    from_date: str | None = None,
    to_date: str | None = None,
    period: str = "5d",
) -> pd.DataFrame:
    start, end = _date_range(from_date, to_date, period)
    app_frame = _fetch_upstox_candles(symbol, interval, start, end)
    if app_frame.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"]).rename_axis("timestamp")
    canonical = app_frame.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
    canonical.index = pd.to_datetime(canonical.index)
    if canonical.index.tz is None:
        canonical.index = canonical.index.tz_localize(IST)
    else:
        canonical.index = canonical.index.tz_convert(IST)
    canonical = canonical.rename(
        columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
    )
    canonical = canonical[pd.to_numeric(canonical["Volume"], errors="coerce").fillna(0) > 0]
    return canonical.sort_index()


def _canonical_to_cache_frame(candles: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if candles.empty:
        return pd.DataFrame(columns=["timestamp", "symbol", "open", "high", "low", "close", "volume"])
    out = candles.rename(
        columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
    ).copy()
    out.index.name = "timestamp"
    out = out.reset_index()
    out["timestamp"] = pd.to_datetime(out["timestamp"]).dt.tz_convert(IST).dt.tz_localize(None)
    out["symbol"] = symbol
    return out[["timestamp", "symbol", "open", "high", "low", "close", "volume"]].sort_values("timestamp")


def _normalize_upstox_candles(payload: dict[str, Any], symbol: str) -> pd.DataFrame:
    data = payload.get("data", payload)
    raw_candles = data.get("candles") if isinstance(data, dict) else data
    if raw_candles is None:
        return pd.DataFrame(columns=["timestamp", "symbol", "open", "high", "low", "close", "volume"])

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
        return pd.DataFrame(columns=["timestamp", "symbol", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(IST)
    for column in ("open", "high", "low", "close", "volume"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df[["timestamp", "symbol", "open", "high", "low", "close", "volume"]]
    return (
        df.dropna(subset=["open", "high", "low", "close"])
        .sort_values("timestamp")
        .drop_duplicates(["timestamp", "symbol"])
    )
