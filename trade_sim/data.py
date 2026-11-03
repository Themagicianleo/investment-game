"""Download daily OHLCV data from Yahoo Finance (via yfinance) and cache it as CSV.

Prices are stored the way Yahoo returns them with ``auto_adjust=False``:
split-adjusted, but *not* dividend-adjusted. ``Adj Close`` is dropped on
purpose: its historical values are computed using dividends paid *later*,
which would leak future information into the replay.

``to_as_traded`` then undoes the split adjustment so the replay shows the
prices that actually traded on each day, and share counts change on the
split date like they would in a real account.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS = ["Open", "High", "Low", "Close", "Volume", "Dividends", "Stock Splits"]


class DataError(Exception):
    """Missing, empty or unreadable market data."""


class NetworkError(DataError):
    """Data could not be downloaded (no connection, blocked host, Yahoo outage...)."""


def cache_path(cache_dir: str | Path, ticker: str) -> Path:
    return Path(cache_dir) / f"{ticker.upper()}.csv"


def _normalise(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    missing = [c for c in ("Open", "High", "Low", "Close") if c not in df.columns]
    if missing:
        raise DataError(f"Data for {ticker} is missing columns: {', '.join(missing)}")
    df = df.copy()
    for col in ("Volume", "Dividends", "Stock Splits"):
        if col not in df.columns:
            df[col] = 0.0
    df = df[COLUMNS]
    idx = pd.to_datetime(df.index, utc=True) if getattr(df.index, "tz", None) is not None else pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    df.index = pd.DatetimeIndex(idx).normalize()
    df.index.name = "Date"
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df[["Volume", "Dividends", "Stock Splits"]] = df[["Volume", "Dividends", "Stock Splits"]].fillna(0.0)
    df = df.dropna(subset=["Open", "Close"])
    df = df[(df["Open"] > 0) & (df["Close"] > 0)]
    if df.empty:
        raise DataError(f"No usable price rows for {ticker}")
    return df.astype(float)


def download(ticker: str) -> pd.DataFrame:
    """Download full daily history for ``ticker``. Raises NetworkError / DataError."""
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise DataError("yfinance is not installed. Run: pip install -r requirements.txt") from exc

    try:
        df = yf.Ticker(ticker).history(period="max", interval="1d", auto_adjust=False, actions=True)
    except Exception as exc:  # yfinance raises many different exception types
        raise NetworkError(
            f"Could not download {ticker} from Yahoo Finance ({type(exc).__name__}: {exc}). "
            "Check your internet connection and that finance.yahoo.com is reachable, then try again."
        ) from exc
    if df is None or df.empty:
        # yfinance often logs the error and returns an empty frame on network failure.
        raise NetworkError(
            f"Yahoo Finance returned no data for '{ticker}'. Either the ticker symbol is wrong, "
            "or the download failed (check your internet connection / firewall and try again)."
        )
    return _normalise(df, ticker)


def load_ticker(ticker: str, cache_dir: str | Path, refresh: bool = False, downloader=download) -> pd.DataFrame:
    """Return split-adjusted daily data for ``ticker``, using the CSV cache when present."""
    path = cache_path(cache_dir, ticker)
    if path.exists() and not refresh:
        try:
            df = pd.read_csv(path, index_col="Date", parse_dates=["Date"])
        except Exception as exc:
            raise DataError(
                f"Cached file {path} is unreadable ({exc}). Delete it or run `trade-sim fetch --refresh`."
            ) from exc
        return _normalise(df, ticker)

    try:
        df = downloader(ticker)
    except NetworkError as exc:
        raise NetworkError(f"{exc}\n(No cached copy found at {path}.)") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index_label="Date")
    return df


def to_as_traded(df: pd.DataFrame) -> pd.DataFrame:
    """Undo Yahoo's split adjustment so each row shows the prices that traded that day.

    For a row on date d, the as-traded price is the adjusted price times the
    product of every split ratio *after* d. Dividends are adjusted the same way;
    volume is divided by it.
    """
    df = df.copy()
    ratios = df["Stock Splits"].to_numpy(dtype=float)
    ratios = np.where(ratios > 0, ratios, 1.0)
    # factor[i] = product of ratios at rows i+1 .. end
    later = np.cumprod(ratios[::-1])[::-1]
    factor = np.append(later[1:], 1.0)
    for col in ("Open", "High", "Low", "Close", "Dividends"):
        df[col] = df[col] * factor
    df["Volume"] = df["Volume"] / factor
    return df


def load_market_frames(tickers: list[str], cache_dir: str | Path, refresh: bool = False, downloader=download) -> dict[str, pd.DataFrame]:
    """Load every ticker, collecting all failures into one clear error message."""
    frames: dict[str, pd.DataFrame] = {}
    errors: list[str] = []
    network_failure = False
    for ticker in dict.fromkeys(tickers):
        try:
            frames[ticker] = to_as_traded(load_ticker(ticker, cache_dir, refresh, downloader))
        except NetworkError as exc:
            network_failure = True
            errors.append(f"  - {ticker}: {exc}")
        except DataError as exc:
            errors.append(f"  - {ticker}: {exc}")
    if errors:
        cls = NetworkError if network_failure else DataError
        raise cls("Could not load market data:\n" + "\n".join(errors))
    return frames
