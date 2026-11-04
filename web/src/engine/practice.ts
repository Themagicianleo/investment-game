// Practice market: FICTIONAL tickers with randomly generated prices (same design as
// trade_sim/education/practice.py, with its own seeded generator). Works offline; the prices
// say nothing about any real security.

import PRACTICE from '../content/practice.json';
import { COLUMNS, Column, Frame } from './data';
import { MarketData } from './market';
import { Rng } from './rng';
import { addDays, dayNumber } from './util';

type Spec = [string, number, number, number, number]; // description, beta, idio vol, extra drift, dividend yield
export const PRACTICE_TICKERS = PRACTICE.tickers as unknown as Record<string, Spec>;
export const PRACTICE_BENCHMARK: string = PRACTICE.benchmark;

const REGIMES: Record<string, [number, number, number, number]> = {
  bull: [0.0007, 0.008, 120, 400],
  chop: [0.0, 0.011, 60, 200],
  bear: [-0.0011, 0.016, 60, 200],
  crash: [-0.006, 0.032, 10, 30],
  rebound: [0.003, 0.02, 20, 60],
};
const NEXT: Record<string, string[]> = {
  bull: ['bull', 'chop', 'bear', 'crash'], chop: ['bull', 'bear', 'chop'],
  bear: ['crash', 'chop', 'rebound'], crash: ['rebound', 'bear'], rebound: ['bull', 'chop'],
};

function businessDays(start: string, n: number): string[] {
  const out: string[] = [];
  let d = start;
  while (out.length < n) {
    const wd = new Date(dayNumber(d) * 86_400_000).getUTCDay();
    if (wd !== 0 && wd !== 6) out.push(d);
    d = addDays(d, 1);
  }
  return out;
}

function marketFactor(rng: Rng, n: number): number[] {
  const out: number[] = [];
  let regime = 'bull';
  while (out.length < n) {
    const [mu, sd, lo, hi] = REGIMES[regime];
    const len = rng.integers(lo, hi + 1);
    for (let i = 0; i < len; i++) out.push(rng.normal(mu, sd));
    regime = rng.choice(NEXT[regime]);
  }
  return out.slice(0, n);
}

export function makePracticeMarket(seed: number, nDays = 2600, start = '2001-01-02'): MarketData {
  const rng = new Rng(seed);
  const dates = businessDays(start, nDays);
  const mkt = marketFactor(rng, nDays);
  const bubbleStart = rng.integers(Math.floor(nDays / 2), nDays - 400);
  const hypeExtra = new Array<number>(nDays).fill(0);
  for (let i = bubbleStart; i < bubbleStart + 180; i++) hypeExtra[i] = 0.006;
  for (let i = bubbleStart + 180; i < bubbleStart + 330; i++) hypeExtra[i] = -0.009;

  const frames = new Map<string, Frame>();
  for (const [ticker, [, beta, ivol, drift, divYield]] of Object.entries(PRACTICE_TICKERS)) {
    const cols = Object.fromEntries(COLUMNS.map((c) => [c, [] as number[]])) as Record<Column, number[]>;
    let price = rng.uniform(20, 120);
    let splitDone = false;
    for (let i = 0; i < nDays; i++) {
      const r = beta * mkt[i] + rng.normal(drift, ivol) + (ticker === 'HYPE' ? hypeExtra[i] : 0);
      const prev = price;
      let open = prev * Math.exp(rng.normal(0, 0.35 * (Math.abs(r) + 0.004)));
      let close = prev * Math.exp(r);
      let div = 0;
      let split = 0;
      if (divYield && i > 0 && i % 63 === 0) {
        div = Math.round(((prev * divYield) / 4) * 1e4) / 1e4;
        open -= div;
        close -= div;
      }
      if (!splitDone && (ticker === 'ROKT' || ticker === 'HYPE') && prev > 300) {
        split = 4;
        splitDone = true;
        open /= 4;
        close /= 4;
      }
      const spread = Math.abs(rng.normal(0, 0.006));
      close = Math.max(close, 0.5);
      open = Math.max(open, 0.5);
      cols.Open.push(open);
      cols.High.push(Math.max(open, close) * (1 + spread));
      cols.Low.push(Math.max(Math.min(open, close) * (1 - spread), 0.4));
      cols.Close.push(close);
      cols.Volume.push(rng.integers(200_000, 2_000_000));
      cols.Dividends.push(div);
      cols['Stock Splits'].push(split);
      price = close;
    }
    frames.set(ticker, { dates, cols });
  }
  return new MarketData(frames);
}
