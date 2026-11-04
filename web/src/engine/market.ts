// Aligned market data and a MarketView that can only see the past (port of trade_sim/market.py).

import { COLUMNS, Column, DataError, Frame } from './data';
import { Rng } from './rng';
import { addDays, pairwiseSum, pyRound } from './util';

export class EndOfData extends Error {}

export class MarketData {
  readonly dates: string[];
  readonly tickers: string[];
  private readonly arrays: Map<string, Record<Column, number[]>>;
  private readonly splitCum = new Map<string, number[]>();

  constructor(frames: Map<string, Frame>) {
    if (!frames.size) throw new DataError('No tickers loaded');
    let common: string[] | null = null;
    for (const f of frames.values()) {
      const s = new Set(f.dates);
      common = common === null ? [...s] : common.filter((d) => s.has(d));
    }
    if (!common || !common.length) throw new DataError('The tickers have no trading dates in common');
    this.dates = [...new Set(common)].sort();
    this.tickers = [...frames.keys()];
    this.arrays = new Map();
    for (const [t, f] of frames) {
      const pos = new Map(f.dates.map((d, i) => [d, i]));
      const idx = this.dates.map((d) => pos.get(d)!);
      const cols = {} as Record<Column, number[]>;
      for (const c of COLUMNS) cols[c] = idx.map((i) => f.cols[c][i]);
      this.arrays.set(t, cols);
    }
  }

  get length(): number {
    return this.dates.length;
  }

  /** @internal used only by MarketView */
  series(ticker: string, col: Column): number[] {
    const a = this.arrays.get(ticker);
    if (!a) throw new Error(`Unknown ticker '${ticker}'. Available: ${this.tickers.join(', ')}`);
    return a[col];
  }

  /** @internal cumulative product of split ratios from the first row (same order as numpy.cumprod). */
  cumSplits(ticker: string): number[] {
    let cum = this.splitCum.get(ticker);
    if (!cum) {
      cum = [];
      let acc = 1;
      for (const r of this.series(ticker, 'Stock Splits')) {
        acc *= r > 0 ? r : 1;
        cum.push(acc);
      }
      this.splitCum.set(ticker, cum);
    }
    return cum;
  }

  eligibleStartIndices(warmupDays: number, minYearsAfter: number): number[] {
    const latest = addDays(this.dates[this.dates.length - 1], -pyRound(365.25 * minYearsAfter));
    const out: number[] = [];
    this.dates.forEach((d, i) => {
      if (i >= warmupDays && d <= latest) out.push(i);
    });
    return out;
  }

  randomStartIndex(rng: Rng, warmupDays: number, minYearsAfter: number): number {
    const eligible = this.eligibleStartIndices(warmupDays, minYearsAfter);
    if (!eligible.length) {
      throw new DataError(
        `Not enough common history: need ${warmupDays} trading days before the start date ` +
          `and ${minYearsAfter} years after it. Try fewer or older tickers.`,
      );
    }
    return rng.choice(eligible);
  }

  view(idx: number, endIdx: number | null = null): MarketView {
    return new MarketView(this, idx, endIdx);
  }
}

export interface Row {
  Open: number;
  High: number;
  Low: number;
  Close: number;
  Volume: number;
  Dividends: number;
  'Stock Splits': number;
}

/** Read-only window onto MarketData that ends at the current day. */
export class MarketView {
  #market: MarketData;
  #idx: number;
  #last: number;
  readonly startIdx: number;

  constructor(market: MarketData, idx: number, endIdx: number | null = null) {
    const last = endIdx === null ? market.length - 1 : Math.min(endIdx, market.length - 1);
    if (idx < 0 || idx > last) throw new RangeError('start index out of range');
    this.#market = market;
    this.#idx = idx;
    this.#last = last;
    this.startIdx = idx;
  }

  get idx(): number {
    return this.#idx;
  }

  get date(): string {
    return this.#market.dates[this.#idx];
  }

  get tickers(): string[] {
    return [...this.#market.tickers];
  }

  hasNext(): boolean {
    return this.#idx < this.#last;
  }

  advance(): string {
    if (!this.hasNext()) throw new EndOfData(`No more data after ${this.date}`);
    this.#idx += 1;
    return this.date;
  }

  daysElapsed(): number {
    return this.#idx - this.startIdx;
  }

  private at(ticker: string, col: Column): number {
    return this.#market.series(ticker, col)[this.#idx];
  }

  today(ticker: string): Row {
    const r = {} as Row;
    for (const c of COLUMNS) r[c] = this.at(ticker, c);
    return r;
  }

  close(ticker: string): number {
    return this.at(ticker, 'Close');
  }

  open(ticker: string): number {
    return this.at(ticker, 'Open');
  }

  /** As-traded rows up to and including today. */
  history(ticker: string, n: number | null = null): { dates: string[]; cols: Record<Column, number[]> } {
    const stop = this.#idx + 1;
    const start = n === null ? 0 : Math.max(0, stop - n);
    const cols = {} as Record<Column, number[]>;
    for (const c of COLUMNS) cols[c] = this.#market.series(ticker, c).slice(start, stop);
    return { dates: this.#market.dates.slice(start, stop), cols };
  }

  /** Closes adjusted only for splits that have already happened (by today). */
  adjustedCloses(ticker: string, n: number | null = null): number[] {
    const closes = this.#market.series(ticker, 'Close');
    const cum = this.#market.cumSplits(ticker);
    const stop = this.#idx + 1;
    const start = n === null ? 0 : Math.max(0, stop - n);
    const lastCum = cum[this.#idx];
    const adj = new Array<number>(stop - start);
    for (let i = start; i < stop; i++) adj[i - start] = (closes[i] * cum[i]) / lastCum;
    return adj;
  }

  trailingReturn(ticker: string, days: number): number | null {
    const adj = this.adjustedCloses(ticker, days + 1);
    if (adj.length < days + 1) return null;
    return adj[adj.length - 1] / adj[0] - 1;
  }

  movingAverage(ticker: string, length: number): number | null {
    const adj = this.adjustedCloses(ticker, length);
    if (adj.length < length) return null;
    return pairwiseSum(adj) / adj.length;
  }
}
