"""Aligned market data plus a ``MarketView`` that can only see the past.

``MarketData`` holds every row for every ticker. Nothing in the simulator reads
it directly during a replay: sessions and bots receive a ``MarketView``, whose
accessors only ever return rows up to and including the current day. The
next day's open price only becomes visible after ``advance()``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data import COLUMNS, DataError


class EndOfData(Exception):
    """Raised when trying to advance past the last available day."""


class MarketData:
    def __init__(self, frames: dict[str, pd.DataFrame]):
        if not frames:
            raise DataError("No tickers loaded")
        common = None
        for df in frames.values():
            common = df.index if common is None else common.intersection(df.index)
        if common is None or len(common) == 0:
            raise DataError("The tickers have no trading dates in common")
        self.dates = pd.DatetimeIndex(common).sort_values()
        self.tickers = list(frames)
        self._arrays: dict[str, dict[str, np.ndarray]] = {}
        for ticker, df in frames.items():
            aligned = df.loc[self.dates, COLUMNS]
            self._arrays[ticker] = {col: aligned[col].to_numpy(dtype=float) for col in COLUMNS}

    def __len__(self) -> int:
        return len(self.dates)

    def eligible_start_indices(self, warmup_days: int, min_years_after: float) -> np.ndarray:
        """Indices with ``warmup_days`` of history before them and ``min_years_after`` years of data after."""
        last = self.dates[-1]
        latest_start = last - pd.DateOffset(days=int(round(365.25 * min_years_after)))
        idx = np.arange(len(self.dates))
        mask = (idx >= warmup_days) & (self.dates <= latest_start)
        return idx[mask]

    def random_start_index(self, rng: np.random.Generator, warmup_days: int, min_years_after: float) -> int:
        eligible = self.eligible_start_indices(warmup_days, min_years_after)
        if len(eligible) == 0:
            raise DataError(
                f"Not enough common history: need {warmup_days} trading days before the start date "
                f"and {min_years_after} years after it. Try fewer or older tickers."
            )
        return int(rng.choice(eligible))

    def view(self, idx: int, end_idx: int | None = None) -> "MarketView":
        return MarketView(self, idx, end_idx)


class MarketView:
    """Read-only window onto ``MarketData`` that ends at the current day."""

    def __init__(self, market: MarketData, idx: int, end_idx: int | None = None):
        last = len(market) - 1 if end_idx is None else min(end_idx, len(market) - 1)
        if not 0 <= idx <= last:
            raise IndexError("start index out of range")
        self.__market = market  # name-mangled: not part of the public surface
        self.__idx = idx
        self.__last = last
        self.start_idx = idx

    # --- position in time -------------------------------------------------
    @property
    def idx(self) -> int:
        return self.__idx

    @property
    def date(self) -> pd.Timestamp:
        return self.__market.dates[self.__idx]

    @property
    def tickers(self) -> list[str]:
        return list(self.__market.tickers)

    def has_next(self) -> bool:
        return self.__idx < self.__last

    def advance(self) -> pd.Timestamp:
        if not self.has_next():
            raise EndOfData("No more data after " + self.date.strftime("%Y-%m-%d"))
        self.__idx += 1
        return self.date

    def days_elapsed(self) -> int:
        return self.__idx - self.start_idx

    # --- data access (never beyond the current day) -------------------------
    def _arr(self, ticker: str, col: str) -> np.ndarray:
        try:
            series = self.__market._arrays[ticker][col]
        except KeyError:
            raise KeyError(f"Unknown ticker '{ticker}'. Available: {', '.join(self.tickers)}")
        return series[: self.__idx + 1]

    def today(self, ticker: str) -> dict[str, float]:
        return {col: float(self._arr(ticker, col)[-1]) for col in COLUMNS}

    def close(self, ticker: str) -> float:
        return float(self._arr(ticker, "Close")[-1])

    def open(self, ticker: str) -> float:
        return float(self._arr(ticker, "Open")[-1])

    def history(self, ticker: str, n: int | None = None) -> pd.DataFrame:
        """As-traded rows up to and including today (a copy)."""
        stop = self.__idx + 1
        start = 0 if n is None else max(0, stop - n)
        dates = self.__market.dates[start:stop]
        data = {col: self._arr(ticker, col)[start:stop].copy() for col in COLUMNS}
        return pd.DataFrame(data, index=dates)

    def adjusted_closes(self, ticker: str, n: int | None = None) -> np.ndarray:
        """Closes adjusted only for splits that have already happened (by today).

        Lets moving averages and returns be computed across a split without
        peeking at splits that occur later.
        """
        closes = self._arr(ticker, "Close")
        ratios = self._arr(ticker, "Stock Splits")
        ratios = np.where(ratios > 0, ratios, 1.0)
        cum = np.cumprod(ratios)
        adj = closes * cum / cum[-1]
        return adj if n is None else adj[-n:]

    def trailing_return(self, ticker: str, days: int) -> float | None:
        """Fractional change in (split-adjusted) close over the last ``days`` trading days."""
        adj = self.adjusted_closes(ticker, days + 1)
        if len(adj) < days + 1:
            return None
        return float(adj[-1] / adj[0] - 1.0)

    def moving_average(self, ticker: str, length: int) -> float | None:
        adj = self.adjusted_closes(ticker, length)
        if len(adj) < length:
            return None
        return float(adj.mean())
