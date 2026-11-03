"""Append trade journal entries to journal.csv."""

from __future__ import annotations

import csv
from pathlib import Path

FIELDS = [
    "session_id", "status", "placed_date", "fill_date", "action", "ticker", "shares",
    "open_price", "fill_price", "fee_usd", "fx_cost_usd", "spread_slippage_usd", "cash_change_usd",
    "prior_move_pct", "loss_alert_active", "reason", "prove_wrong", "note",
]


class JournalError(Exception):
    pass


def append(path: str | Path, row: dict) -> None:
    path = Path(path)
    new = not path.exists() or path.stat().st_size == 0
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
            if new:
                writer.writeheader()
            writer.writerow({k: row.get(k, "") for k in FIELDS})
    except OSError as exc:
        raise JournalError(f"Could not write to journal file {path}: {exc}") from exc
