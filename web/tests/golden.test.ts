// The TypeScript engine must reproduce the Python engine exactly on the golden fixtures
// produced by scripts/make_golden.py (same data, same scripted sessions).

import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { eventTips, tradePreview } from '../src/engine/coach';
import { parseCsv, toAsTraded, Frame } from '../src/engine/data';
import { MarketData } from '../src/engine/market';
import { updateMissions } from '../src/engine/missions';
import { computeReport } from '../src/engine/report';
import { processScore } from '../src/engine/scoring';
import { Session } from '../src/engine/session';
import { configWith } from '../src/config';

const DIR = join(__dirname, 'golden');
const expected = JSON.parse(readFileSync(join(DIR, 'expected.json'), 'utf8'));

function loadMarket(): MarketData {
  const frames = new Map<string, Frame>();
  for (const t of ['AAA', 'BBB', 'CCC', 'VT']) {
    frames.set(t, toAsTraded(parseCsv(readFileSync(join(DIR, 'market', `${t}.csv`), 'utf8'), t)));
  }
  return new MarketData(frames);
}

function summary(s: Session) {
  const a = s.account;
  const positions: Record<string, [number, number]> = {};
  for (const t of [...a.positions.keys()].sort()) {
    const p = a.positions.get(t)!;
    if (p.shares > 0) positions[t] = [p.shares, p.avgCost];
  }
  return {
    date: s.date, idx: s.view.idx, cash: a.cash, equity: a.equity(), fees: a.feesPaid, fx: a.fxPaid,
    spread: a.spreadSlippagePaid, div_tax: a.dividendTaxPaid, div_net: a.dividendsNet, orders: a.orderCount,
    positions, max_drawdown: s.maxDrawdown, episodes: s.episodes.length, longest_hold: s.longestHoldDays,
    process: processScore(s).score,
  };
}

/** Deep comparison: numbers within 1e-9 relative, everything else exact. */
function close(actual: unknown, exp: unknown, path = '$'): void {
  if (typeof exp === 'number' || typeof actual === 'number') {
    expect(typeof actual, path).toBe('number');
    const a = actual as number;
    const e = exp as number;
    expect(Math.abs(a - e) <= 1e-9 * Math.max(1, Math.abs(e)), `${path}: ${a} vs ${e}`).toBe(true);
  } else if (Array.isArray(exp)) {
    expect(Array.isArray(actual), path).toBe(true);
    expect((actual as unknown[]).length, `${path} length`).toBe(exp.length);
    exp.forEach((v, i) => close((actual as unknown[])[i], v, `${path}[${i}]`));
  } else if (exp && typeof exp === 'object') {
    const a = actual as Record<string, unknown>;
    expect(Object.keys(a ?? {}).sort(), `${path} keys`).toEqual(Object.keys(exp).sort());
    for (const k of Object.keys(exp)) close(a[k], (exp as Record<string, unknown>)[k], `${path}.${k}`);
  } else {
    expect(actual, path).toEqual(exp);
  }
}

describe('golden: TypeScript engine matches the Python engine', () => {
  it('has fixtures', () => {
    expect(readdirSync(join(DIR, 'market')).length).toBe(4);
    expect(Object.keys(expected).length).toBeGreaterThan(0);
  });

  for (const [name, run] of Object.entries<any>(expected)) {
    it(`script "${name}"`, () => {
      const market = loadMarket();
      const cfg = configWith(run.overrides, { tickers: ['AAA', 'BBB', 'CCC', 'VT'] });
      const s = new Session(market, cfg, run.start_idx, 'golden');
      const seen = new Set<string>();
      close(summary(s), run.start, 'start');
      run.steps.forEach((rec: any, i: number) => {
        const [kind, ...args] = rec.step;
        const path = `steps[${i}] ${kind}`;
        if (kind === 'preview') {
          const p = tradePreview(s, args[0], args[1], args[2]);
          const picked = Object.fromEntries(Object.keys(rec.preview).map((k) => [k, (p as any)[k]]));
          close(picked, rec.preview, `${path}.preview`);
          expect(p.tips.map((t) => t.id), `${path}.tips`).toEqual(rec.tips);
        } else if (kind === 'buy' || kind === 'sell') {
          s.place(kind, args[0], args[1], args[2], args[3]);
        } else if (kind === 'review') {
          s.reviewPosition(args[0], args[1]);
          expect(updateMissions(s).map((m) => m.id), `${path}.missions`).toEqual(rec.missions);
        } else if (kind === 'hold') {
          const reports = [];
          const stops: string[] = [];
          while (reports.length < args[0] && s.view.hasNext()) {
            const [more, stopped] = s.hold(args[0] - reports.length);
            reports.push(...more);
            if (stopped) stops.push(stopped);
          }
          expect(reports.length, `${path}.advanced`).toBe(rec.advanced);
          expect(stops, `${path}.stopped`).toEqual(rec.stopped);
          expect(eventTips(s, reports, seen).map((t) => t.id), `${path}.tips`).toEqual(rec.tips);
          expect(updateMissions(s).map((m) => m.id), `${path}.missions`).toEqual(rec.missions);
          close(reports.flatMap((r) => r.step.fills.map((f) => [f.order.ticker, f.order.side, f.shares, f.price])), rec.fills, `${path}.fills`);
          close(reports.flatMap((r) => r.step.dividends.map((d) => [d.ticker, d.net])), rec.dividends, `${path}.dividends`);
          close(reports.flatMap((r) => r.step.splits.map((x) => [x.ticker, x.ratio, x.sharesAfter])), rec.splits, `${path}.splits`);
          close(reports.flatMap((r) => r.newAlerts.map((e) => [e.ticker, e.startDate])), rec.alerts, `${path}.alerts`);
          close(summary(s), rec.summary, `${path}.summary`);
        }
      });
      expect(updateMissions(s, true).map((m) => m.id)).toEqual(run.end_missions);
      close(s.entries, run.entries, 'entries');
      close(computeReport(s), run.report, 'report');
    });
  }
});
