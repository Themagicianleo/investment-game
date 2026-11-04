import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ApiError, LocalApi, Loader } from '../src/api';
import GLOSSARY from '../src/content/glossary.json';
import LESSONS from '../src/content/lessons.json';
import { DataError } from '../src/engine/data';
import { makePracticeMarket } from '../src/engine/practice';
import { memoryKV } from '../src/storage';

const GOLDEN = join(__dirname, 'golden', 'market');
// Real-data modes are tested with the golden fixture CSVs standing in for the default tickers.
const MAP: Record<string, string> = { VT: 'VT', AAPL: 'AAA', NVDA: 'BBB', INTC: 'CCC', AMD: 'AAA' };
const fixtureLoader: Loader = async (t) => {
  if (!MAP[t]) throw new DataError(`Market data for ${t} has not been published yet.`);
  return readFileSync(join(GOLDEN, `${MAP[t]}.csv`), 'utf8');
};
const offlineLoader: Loader = async () => { throw new DataError('Market data has not been published yet.'); };

const newApi = (loader: Loader = fixtureLoader, kv = memoryKV()) => new LocalApi(loader, kv);
type State = any;

async function order(api: LocalApi, id: string, body: Record<string, unknown>) {
  return api.handle('POST', `/api/sessions/${id}/orders`, {
    side: 'buy', reason: 'a long and specific reason', prove_wrong: 'falls 15% below my cost', ...body,
  }) as Promise<State>;
}

describe('LocalApi', () => {
  it('runs a practice session end to end without any market data', async () => {
    const api = newApi(offlineLoader);
    const st: State = await api.handle('POST', '/api/sessions', { mode: 'practice', seed: 7 });
    expect(st.mode).toBe('practice');
    expect(st.rules.tickers).toContain('GLOBX');
    expect(st.market[0].description).toMatch(/^Fictional/);
    await order(api, st.id, { ticker: 'STDY', amount: 3000 });
    const after: State = await api.handle('POST', `/api/sessions/${st.id}/hold`, { days: 30 });
    expect(after.positions[0].ticker).toBe('STDY');
    expect(after.new_missions.map((m: any) => m.id)).toContain('plan-first');
    const { report } = (await api.handle('POST', `/api/sessions/${st.id}/end`)) as any;
    expect(report.notices.join(' ')).toMatch(/not a recommendation/);
    expect(report.process.score).not.toBeNull();
    expect(report.xp.gained_now).toBeGreaterThanOrEqual(25);
    await expect(order(api, st.id, { ticker: 'STDY', amount: 100 })).rejects.toMatchObject({ status: 409 });
  });

  it('never returns dates after the current day', async () => {
    const api = newApi();
    let st: State = await api.handle('POST', '/api/sessions', { mode: 'random', seed: 3 });
    for (let i = 0; i < 3; i++) {
      st = await api.handle('POST', `/api/sessions/${st.id}/hold`, { days: 17 });
      const h: any = await api.handle('GET', `/api/sessions/${st.id}/history/AAPL`);
      expect(h.dates[h.dates.length - 1]).toBe(st.date);
      expect(h.dates.every((d: string) => d <= st.date)).toBe(true);
      const blob = JSON.stringify(st);
      const later = (blob.match(/\d{4}-\d{2}-\d{2}/g) ?? []).filter((d) => d > st.date);
      expect(later).toEqual([]);
    }
  });

  it('computes moving averages from past rows only', async () => {
    const api = newApi();
    const st: State = await api.handle('POST', '/api/sessions', { mode: 'random', seed: 4 });
    const h: any = await api.handle('GET', `/api/sessions/${st.id}/history/VT?days=60`);
    const closes = h.adj_close;
    const full: any = await api.handle('GET', `/api/sessions/${st.id}/history/VT`);
    const last50 = full.adj_close.slice(-50).reduce((a: number, b: number) => a + b, 0) / 50;
    expect(h.ma50[h.ma50.length - 1]).toBeCloseTo(last50, 3);
    expect(closes.length).toBe(60);
  });

  it('rebuilds a session exactly from its replay log without double-counting', async () => {
    const kv = memoryKV();
    const api = newApi(fixtureLoader, kv);
    const create = { mode: 'random', seed: 11 };
    const st: State = await api.handle('POST', '/api/sessions', create);
    const actions = [
      { path: '/orders', body: { side: 'buy', ticker: 'VT', amount: 4000, reason: 'core holding for the test', prove_wrong: 'drops 15% below cost' } },
      { path: '/hold', body: { days: 40 } },
      { path: '/orders', body: { side: 'sell', ticker: 'VT', amount: 'all', reason: 'exit', prove_wrong: 'n/a' } },
      { path: '/hold', body: { days: 5 } },
    ];
    let live: State = st;
    for (const a of actions) live = await api.handle('POST', `/api/sessions/${st.id}${a.path}`, a.body);
    const journalBefore = api.journal.csv();
    const xpBefore = api.progress.snapshot().xp;

    const restored: State = await newApi(fixtureLoader, kv).replay({
      create: { ...create, seed: st.seed, start_date: st.date, xp_start: st.xp_start }, actions,
    });
    for (const k of ['date', 'cash', 'equity', 'max_drawdown']) expect(restored[k]).toBe(live[k]);
    expect(restored.journal).toEqual(live.journal.map((r: any) => ({ ...r, session_id: restored.id })));
    expect(api.journal.csv()).toBe(journalBefore); // replay does not append rows again
    expect(api.progress.snapshot().xp).toBe(xpBefore); // or award XP again
  });

  it('grades quizzes and awards lesson XP once', async () => {
    const api = newApi();
    const lesson = LESSONS[0] as any;
    const answers = lesson.quiz.map((q: any) => q.answer);
    const r1: any = await api.handle('POST', `/api/lessons/${lesson.id}/quiz`, { answers });
    const r2: any = await api.handle('POST', `/api/lessons/${lesson.id}/quiz`, { answers });
    expect(r1.passed && r1.xp_gained === 50 && r2.xp_gained === 0).toBe(true);
    const edu: any = await api.handle('GET', '/api/education');
    expect(edu.lessons[0].quiz[0].answer).toBeUndefined();
    expect(edu.scenarios.every((s: any) => s.debrief === undefined)).toBe(true);
  });

  it('explains missing data and offers the practice market', async () => {
    const api = newApi(offlineLoader);
    const err: any = await api.handle('POST', '/api/sessions', { mode: 'random' }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(503);
    expect(err.message).toMatch(/not been published/);
    const sc: any = await api.handle('POST', '/api/sessions', { mode: 'scenario', scenario: 'covid' }).catch((e) => e);
    expect(sc.status).toBe(503);
  });

  it('validates orders with clear messages', async () => {
    const api = newApi();
    const st: State = await api.handle('POST', '/api/sessions', { mode: 'practice', seed: 1 });
    const bad = (b: Record<string, unknown>) => order(api, st.id, b).catch((e) => e.message);
    expect(await bad({ ticker: 'ZZZ', amount: 100 })).toMatch(/Unknown ticker/);
    expect(await bad({ ticker: 'GLOBX', amount: 99999 })).toMatch(/Not enough cash/);
    expect(await bad({ ticker: 'GLOBX', amount: 'abc' })).toMatch(/US dollar amount/);
    expect(await bad({ ticker: 'GLOBX', amount: 100, reason: '' })).toMatch(/reason/);
    expect(await bad({ side: 'sell', ticker: 'GLOBX', amount: 'all' })).toMatch(/hold no/);
  });

  it('downloads the journal as CSV and can reset progress', async () => {
    const api = newApi();
    const st: State = await api.handle('POST', '/api/sessions', { mode: 'practice', seed: 2 });
    await order(api, st.id, { ticker: 'GLOBX', amount: 1000, reason: 'has a comma, and "quotes"' });
    await api.handle('POST', `/api/sessions/${st.id}/hold`, { days: 1 });
    const { csv } = (await api.handle('GET', '/api/journal')) as any;
    expect(csv.split('\n')[0]).toMatch(/^session_id,status,placed_date/);
    expect(csv).toContain('"has a comma, and ""quotes"""');
    await api.handle('POST', '/api/progress/reset');
    expect(((await api.handle('GET', '/api/journal')) as any).csv.trim().split('\n')).toHaveLength(1);
  });
});

describe('practice market', () => {
  it('is deterministic for a seed and includes dividends and a split', () => {
    const a = makePracticeMarket(5);
    const b = makePracticeMarket(5);
    expect(a.dates).toEqual(b.dates);
    const va = a.view(a.length - 1);
    const vb = b.view(b.length - 1);
    expect(va.history('ROKT').cols.Close).toEqual(vb.history('ROKT').cols.Close);
    const divs = a.tickers.reduce((n, t) => n + va.history(t).cols.Dividends.filter((d) => d > 0).length, 0);
    expect(divs).toBeGreaterThan(10);
    expect(a.eligibleStartIndices(250, 2).length).toBeGreaterThan(100);
  });
});

describe('content', () => {
  it('every [[term]] used in lessons is in the glossary', () => {
    const text = JSON.stringify(LESSONS);
    for (const [, term] of text.matchAll(/\[\[(.+?)\]\]/g)) {
      expect(Object.keys(GLOSSARY)).toContain(term.toLowerCase());
    }
  });
});
