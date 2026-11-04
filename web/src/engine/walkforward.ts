// Walk-forward evaluation of the trend bot's MA lengths (port of trade_sim/walkforward.py).
// Lengths are chosen on the first part only, then run unchanged on the rest.

import { BenchmarkResult, TrendRule, runTrendBot } from './bots';
import { Config, Costs } from './execution';
import { MarketData } from './market';

export interface Split {
  trainStart: number;
  trainEnd: number;
  testStart: number;
  testEnd: number;
}

export const trainLen = (s: Split) => s.trainEnd - s.trainStart + 1;
export const testLen = (s: Split) => s.testEnd - s.testStart + 1;

export function walkForwardSplit(startIdx: number, endIdx: number, trainFraction = 0.7, minDays = 20): Split {
  if (!(trainFraction > 0 && trainFraction < 1)) throw new RangeError('train_fraction must be between 0 and 1');
  const n = endIdx - startIdx + 1;
  const nTrain = Math.trunc(n * trainFraction);
  if (nTrain < minDays || n - nTrain < minDays) {
    throw new RangeError(
      `Period of ${n} trading days is too short for a walk-forward split ` +
        `(need at least ${minDays} days in each part).`,
    );
  }
  const trainEnd = startIdx + nTrain - 1;
  return { trainStart: startIdx, trainEnd, testStart: trainEnd + 1, testEnd: endIdx };
}

export function candidateRules(shortGrid: number[], longGrid: number[]): TrendRule[] {
  const uniq = (xs: number[]) => [...new Set(xs)].sort((a, b) => a - b);
  const out: TrendRule[] = [];
  for (const s of uniq(shortGrid)) for (const l of uniq(longGrid)) if (s < l) out.push({ shortMa: s, longMa: l });
  return out;
}

export interface WalkForwardResult {
  split: Split;
  chosen: TrendRule;
  inSample: BenchmarkResult;
  outOfSample: BenchmarkResult;
  defaultRule: TrendRule;
  defaultInSample: BenchmarkResult;
  defaultOutOfSample: BenchmarkResult;
  table: [TrendRule, number][];
}

export function selectRule(market: MarketData, split: Split, rules: TrendRule[], costs: Costs, cash: number,
  tickers: string[] | null = null): [TrendRule, [TrendRule, number][]] {
  const table: [TrendRule, number][] = rules.map((r) => [
    r, runTrendBot(market, split.trainStart, split.trainEnd, r, costs, cash, tickers).totalReturn,
  ]);
  let best = table[0];
  for (const row of table) if (row[1] > best[1]) best = row; // first maximum wins, like Python's max()
  return [best[0], table];
}

export function runWalkForward(market: MarketData, startIdx: number, endIdx: number, cfg: Config, costs: Costs,
  cash: number, tickers: string[] | null = null): WalkForwardResult {
  const tb = cfg.trend_bot;
  const wf = tb.walk_forward;
  const split = walkForwardSplit(startIdx, endIdx, wf.train_fraction);
  const [chosen, table] = selectRule(market, split, candidateRules(wf.short_grid, wf.long_grid), costs, cash, tickers);
  const def: TrendRule = { shortMa: tb.short_ma, longMa: tb.long_ma };
  const run = (r: TrendRule, a: number, b: number) => runTrendBot(market, a, b, r, costs, cash, tickers);
  return {
    split, chosen, table, defaultRule: def,
    inSample: run(chosen, split.trainStart, split.trainEnd),
    outOfSample: run(chosen, split.testStart, split.testEnd),
    defaultInSample: run(def, split.trainStart, split.trainEnd),
    defaultOutOfSample: run(def, split.testStart, split.testEnd),
  };
}

export function requiredWarmup(cfg: Config): number {
  return Math.max(cfg.trend_bot.long_ma, ...cfg.trend_bot.walk_forward.long_grid);
}
