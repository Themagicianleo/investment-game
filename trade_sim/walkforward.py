"""Walk-forward evaluation of the trend bot's moving-average lengths.

The MA lengths are chosen using ONLY the first ``train_fraction`` of the
period (in-sample) and then run, unchanged, on the remaining part
(out-of-sample). Both are reported: the in-sample number was picked because it
looked best on that data, so it is optimistic by construction and is not
evidence that the rule works.
"""

from __future__ import annotations

from dataclasses import dataclass

from .bots import BenchmarkResult, TrendRule, run_trend_bot
from .execution import Costs
from .market import MarketData


@dataclass(frozen=True)
class Split:
    train_start: int
    train_end: int
    test_start: int
    test_end: int

    @property
    def train_len(self) -> int:
        return self.train_end - self.train_start + 1

    @property
    def test_len(self) -> int:
        return self.test_end - self.test_start + 1


def walk_forward_split(start_idx: int, end_idx: int, train_fraction: float = 0.7, min_days: int = 20) -> Split:
    """Split the inclusive index range [start_idx, end_idx] into consecutive train/test blocks."""
    if not 0 < train_fraction < 1:
        raise ValueError("train_fraction must be between 0 and 1")
    n = end_idx - start_idx + 1
    n_train = int(n * train_fraction)
    if n_train < min_days or n - n_train < min_days:
        raise ValueError(
            f"Period of {n} trading days is too short for a walk-forward split "
            f"(need at least {min_days} days in each part)."
        )
    train_end = start_idx + n_train - 1
    return Split(start_idx, train_end, train_end + 1, end_idx)


def candidate_rules(short_grid: list[int], long_grid: list[int]) -> list[TrendRule]:
    return [TrendRule(s, l) for s in sorted(set(short_grid)) for l in sorted(set(long_grid)) if s < l]


@dataclass
class WalkForwardResult:
    split: Split
    chosen: TrendRule
    in_sample: BenchmarkResult
    out_of_sample: BenchmarkResult
    default_rule: TrendRule
    default_in_sample: BenchmarkResult
    default_out_of_sample: BenchmarkResult
    table: list[tuple[TrendRule, float]]  # in-sample return for every candidate


def select_rule(market: MarketData, split: Split, rules: list[TrendRule], costs: Costs, cash: float,
                tickers: list[str] | None = None) -> tuple[TrendRule, list[tuple[TrendRule, float]]]:
    """Pick the rule with the best in-sample return. Only the training block is simulated."""
    table = []
    for rule in rules:
        res = run_trend_bot(market, split.train_start, split.train_end, rule, costs, cash, tickers)
        table.append((rule, res.total_return))
    best = max(table, key=lambda row: row[1])[0]
    return best, table


def run_walk_forward(market: MarketData, start_idx: int, end_idx: int, cfg: dict, costs: Costs,
                     cash: float, tickers: list[str] | None = None) -> WalkForwardResult:
    tb = cfg["trend_bot"]
    wf = tb["walk_forward"]
    split = walk_forward_split(start_idx, end_idx, wf["train_fraction"])
    rules = candidate_rules(wf["short_grid"], wf["long_grid"])
    chosen, table = select_rule(market, split, rules, costs, cash, tickers)
    default = TrendRule(tb["short_ma"], tb["long_ma"])

    def run(rule, a, b):
        return run_trend_bot(market, a, b, rule, costs, cash, tickers)

    return WalkForwardResult(
        split=split,
        chosen=chosen,
        in_sample=run(chosen, split.train_start, split.train_end),
        out_of_sample=run(chosen, split.test_start, split.test_end),
        default_rule=default,
        default_in_sample=run(default, split.train_start, split.train_end),
        default_out_of_sample=run(default, split.test_start, split.test_end),
        table=table,
    )


def required_warmup(cfg: dict) -> int:
    """Trading days of history needed before a start date so every MA can be computed."""
    tb = cfg["trend_bot"]
    return max([tb["long_ma"], *tb["walk_forward"]["long_grid"]])
