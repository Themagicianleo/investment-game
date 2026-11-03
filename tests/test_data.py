import pandas as pd
import pytest

from trade_sim import data
from trade_sim.data import DataError, NetworkError

from conftest import make_frame


def test_cache_is_used_instead_of_downloading(tmp_path):
    calls = []

    def downloader(ticker):
        calls.append(ticker)
        return make_frame(n=50)

    first = data.load_ticker("AAA", tmp_path, downloader=downloader)
    second = data.load_ticker("AAA", tmp_path, downloader=downloader)
    assert calls == ["AAA"]
    assert (tmp_path / "AAA.csv").exists()
    pd.testing.assert_frame_equal(first, second, check_freq=False)


def test_refresh_redownloads(tmp_path):
    calls = []
    downloader = lambda t: calls.append(t) or make_frame(n=20)
    data.load_ticker("AAA", tmp_path, downloader=downloader)
    data.load_ticker("AAA", tmp_path, refresh=True, downloader=downloader)
    assert calls == ["AAA", "AAA"]


def test_network_failure_gives_clear_message(tmp_path):
    def offline(ticker):
        raise NetworkError(f"Could not download {ticker}: connection refused")

    with pytest.raises(NetworkError) as err:
        data.load_market_frames(["AAA", "BBB"], tmp_path, downloader=offline)
    msg = str(err.value)
    assert "AAA" in msg and "BBB" in msg and "No cached copy" in msg


def test_corrupt_cache_gives_clear_message(tmp_path):
    (tmp_path / "AAA.csv").write_text("not,a,valid\nfile")
    with pytest.raises(DataError, match="unreadable|missing"):
        data.load_ticker("AAA", tmp_path, downloader=lambda t: make_frame())


def test_adj_close_is_dropped_to_avoid_dividend_lookahead():
    f = make_frame(n=10)
    f["Adj Close"] = f["Close"] * 0.9
    assert "Adj Close" not in data._normalise(f, "AAA").columns


def test_to_as_traded_undoes_split_adjustment():
    f = make_frame(n=6)
    f[["Open", "High", "Low", "Close"]] = 10.0
    f["Dividends"] = 0.0
    f.iloc[1, f.columns.get_loc("Dividends")] = 0.25
    f.iloc[3, f.columns.get_loc("Stock Splits")] = 4.0  # 4-for-1 on row 3
    out = data.to_as_traded(f)
    assert list(out["Close"]) == [40.0, 40.0, 40.0, 10.0, 10.0, 10.0]
    assert out["Dividends"].iloc[1] == pytest.approx(1.0)
