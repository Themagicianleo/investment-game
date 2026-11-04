import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from trade_sim import data
from trade_sim.data import NetworkError

from conftest import make_frame

ROOT = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location("fetch_market_data", ROOT / "scripts" / "fetch_market_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_writes_trimmed_csvs_and_manifest(tmp_path, monkeypatch):
    mod = _mod()
    monkeypatch.setattr(mod, "download", lambda t: make_frame(n=600, start="1997-06-02"))
    monkeypatch.setattr(sys, "argv", ["x", "--out", str(tmp_path), "--since", "1998-01-01"])
    assert mod.main() == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert set(manifest["tickers"]) == set(mod.all_tickers())
    df = data.load_ticker("SPY", tmp_path)  # reads back through the normal cache path
    assert df.index[0] >= pd.Timestamp("1998-01-01")
    assert (tmp_path / "SPY.csv").read_text().splitlines()[1][:10].count("-") == 2  # YYYY-MM-DD dates


def test_leaves_data_unchanged_if_any_ticker_fails(tmp_path, monkeypatch):
    mod = _mod()

    def flaky(t):
        if t == "GLD":
            raise NetworkError("offline")
        return make_frame(n=50)

    monkeypatch.setattr(mod, "download", flaky)
    monkeypatch.setattr(sys, "argv", ["x", "--out", str(tmp_path)])
    assert mod.main() == 1
    assert list(tmp_path.iterdir()) == []
