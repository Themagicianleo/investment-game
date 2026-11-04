"""Download daily data for every ticker the web app uses into web/public/data/market/.

Run by .github/workflows/update-market-data.yml (GitHub's runners can reach Yahoo Finance).
Files are only replaced if every ticker downloads successfully, so a Yahoo outage never
publishes a partial data set.

    python scripts/fetch_market_data.py [--out web/public/data/market] [--since 1998-01-01]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trade_sim.config import DEFAULTS  # noqa: E402
from trade_sim.data import DataError, download  # noqa: E402
from trade_sim.education.scenarios import SCENARIOS  # noqa: E402


def all_tickers() -> list[str]:
    tickers = list(DEFAULTS["tickers"]) + [DEFAULTS["benchmark_ticker"]]
    for sc in SCENARIOS:
        tickers += sc["tickers"] + [sc["benchmark"]]
    return list(dict.fromkeys(t.upper() for t in tickers))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "web" / "public" / "data" / "market"))
    # Scenarios start in 2000 and need ~1 year of history before that for the moving averages.
    ap.add_argument("--since", default="1998-01-01")
    args = ap.parse_args()

    frames, errors = {}, []
    for t in all_tickers():
        try:
            df = download(t)
            # Trimming early rows is safe: as-traded prices only depend on splits *after* each row.
            frames[t] = df[df.index >= args.since]
            print(f"{t}: {len(frames[t])} rows {frames[t].index[0].date()} .. {frames[t].index[-1].date()}")
        except DataError as exc:
            errors.append(f"{t}: {exc}")
    if errors:
        print("Download failed; existing data left unchanged:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"updated": date.today().isoformat(), "source": "Yahoo Finance via yfinance", "tickers": {}}
    for t, df in frames.items():
        df.to_csv(out / f"{t}.csv", index_label="Date", float_format="%.6g", date_format="%Y-%m-%d")
        manifest["tickers"][t] = {"first": str(df.index[0].date()), "last": str(df.index[-1].date()), "rows": len(df)}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {len(frames)} files to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
