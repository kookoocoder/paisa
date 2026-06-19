from __future__ import annotations

import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests

from .config import RAW_DIR, ensure_dirs


NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass(frozen=True)
class BhavcopyResult:
    date: date
    path: Path
    attempted_urls: list[str]


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def equity_bhavcopy_urls(day: date) -> list[str]:
    mon = day.strftime("%b").upper()
    yyyy = day.strftime("%Y")
    ddmmmyyyy = day.strftime("%d%b%Y").upper()
    yyyymmdd = day.strftime("%Y%m%d")
    return [
        f"https://archives.nseindia.com/content/historical/EQUITIES/{yyyy}/{mon}/cm{ddmmmyyyy}bhav.csv.zip",
        f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{yyyy}/{mon}/cm{ddmmmyyyy}bhav.csv.zip",
        f"https://archives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip",
        f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip",
    ]


def fetch_equity_bhavcopy(day: date, timeout: int = 20) -> BhavcopyResult:
    ensure_dirs()
    attempted = equity_bhavcopy_urls(day)
    last_error: Exception | None = None
    for url in attempted:
        try:
            response = requests.get(url, headers=NSE_HEADERS, timeout=timeout)
            if response.status_code != 200:
                last_error = RuntimeError(f"{url} returned HTTP {response.status_code}")
                continue
            raw_path = RAW_DIR / f"nse_equity_bhavcopy_{day.isoformat()}.zip"
            raw_path.write_bytes(response.content)
            with zipfile.ZipFile(BytesIO(response.content)) as archive:
                csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
                if not csv_names:
                    raise RuntimeError("NSE bhavcopy zip did not contain a CSV file.")
                csv_path = RAW_DIR / f"nse_equity_bhavcopy_{day.isoformat()}.csv"
                csv_path.write_bytes(archive.read(csv_names[0]))
            return BhavcopyResult(date=day, path=csv_path, attempted_urls=attempted)
        except Exception as exc:  # keep trying alternate archive host
            last_error = exc
    raise RuntimeError(
        f"Unable to fetch NSE equity bhavcopy for {day}. "
        f"Attempted: {attempted}. Last error: {last_error}"
    )


def load_bhavcopy(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)
