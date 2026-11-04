// In-browser replacement for the Flask API in trade_sim/web.py. Same routes, same JSON,
// so the UI is identical; everything runs locally and only reads data up to "today".

import GLOSSARY from './content/glossary.json';
import LESSONS from './content/lessons.json';
import SCENARIOS from './content/scenarios.json';
import { configWith } from './config';
import { Tip, eventTips, tip, tradePreview } from './engine/coach';
import { DataError, Frame, NetworkError, parseCsv, toAsTraded } from './engine/data';
import { Config, Side } from './engine/execution';
import { MarketData } from './engine/market';
import { MISSIONS, MISSION_BY_ID, Mission, missionStatus, updateMissions } from './engine/missions';
import { PRACTICE_BENCHMARK, PRACTICE_TICKERS, makePracticeMarket } from './engine/practice';
import { Report, computeReport } from './engine/report';
import { Rng, randomSeed } from './engine/rng';
import { processScore } from './engine/scoring';
import { DayReport, Session, SessionError } from './engine/session';
import { money, pct, pairwiseSum } from './engine/util';
import { requiredWarmup } from './engine/walkforward';
import { JournalStore, KV, ProgressStore } from './storage';

export const DISCLAIMER =
  'EDUCATIONAL USE ONLY. This simulator does not give buy or sell advice. Past performance does not predict ' +
  'future results. The trend bot is a comparison that follows past trends, not a recommendation.';

export class ApiError extends Error {
  constructor(message: string, readonly status = 400, readonly offline = false) {
    super(message);
  }
}

interface Scenario {
  id: string; title: string; period: string; start: string; days: number; tickers: string[];
  benchmark: string; difficulty: string; briefing: string; debrief: string; lessons: string[];
}
const SCENARIO_LIST = SCENARIOS as Scenario[];
const SCENARIO_BY_ID = new Map(SCENARIO_LIST.map((s) => [s.id, s]));

interface Lesson {
  id: string; title: string; level: string; minutes: number; summary: string; body: unknown[];
  quiz: { q: string; options: string[]; answer: number; explain: string }[];
}
const LESSON_LIST = LESSONS as Lesson[];

const FINDING_LESSONS: Record<string, string> = {
  'Trading right after a large move': 'biases',
  'Skipping the loss limit': 'exit-plans',
  'Adding to a position that was past the loss limit': 'exit-plans',
  'Short or missing journal reasoning': 'process',
  'Frequent trading': 'costs',
};

export type Loader = (ticker: string) => Promise<string>;

/** Fetches published CSVs (web/public/data/market/*.csv). */
export function fetchLoader(baseUrl: string): Loader {
  return async (ticker) => {
    let res: Response;
    try {
      res = await fetch(`${baseUrl}data/market/${ticker}.csv`, { cache: 'no-cache' });
    } catch (e) {
      throw new NetworkError(`Could not download market data for ${ticker} (${(e as Error).message}). Check your connection.`);
    }
    if (res.status === 404) {
      throw new DataError(
        `Market data for ${ticker} has not been published yet. Run the "Update market data" workflow on GitHub, ` +
          'or play the practice market, which needs no data.',
      );
    }
    if (!res.ok) throw new NetworkError(`Could not download market data for ${ticker} (HTTP ${res.status}).`);
    return res.text();
  };
}

interface Game {
  session: Session;
  mode: string;
  scenario: Scenario | null;
  seed: number | null;
  log: { date: string; type: string; text: string }[];
  coach: Tip[];
  seen: Set<string>;
  report: Report | null;
  xpStart: number;
}

export interface ReplayLog {
  create: Record<string, unknown>;
  actions: { path: string; body: Record<string, unknown> }[];
}

export class LocalApi {
  readonly progress: ProgressStore;
  readonly journal: JournalStore;
  private games = new Map<string, Game>();
  private frames = new Map<string, Frame>();
  private markets = new Map<string, MarketData>();
  private failure: { at: number; error: Error } | null = null;
  private readonly cfg: Config;

  constructor(private loader: Loader, kv: KV, cfgOverrides: Record<string, unknown> = {}) {
    this.progress = new ProgressStore(kv);
    this.journal = new JournalStore(kv);
    this.cfg = configWith(cfgOverrides);
  }

  // --- data -----------------------------------------------------------------
  private async market(tickers: string[]): Promise<MarketData> {
    const uniq = [...new Set(tickers.map((t) => t.toUpperCase()))];
    const key = [...uniq].sort().join(',');
    const cached = this.markets.get(key);
    if (cached) return cached;
    if (this.failure && Date.now() - this.failure.at < 120_000) throw this.failure.error;
    const errors: string[] = [];
    let offline = false;
    await Promise.all(uniq.map(async (t) => {
      if (this.frames.has(t)) return;
      try {
        this.frames.set(t, toAsTraded(parseCsv(await this.loader(t), t)));
      } catch (e) {
        if (e instanceof NetworkError) offline = true;
        errors.push(`  - ${t}: ${(e as Error).message}`);
      }
    }));
    if (errors.length) {
      const err = offline ? new NetworkError('Could not load market data:\n' + errors.join('\n'))
        : new DataError('Could not load market data:\n' + errors.join('\n'));
      if (offline) this.failure = { at: Date.now(), error: err };
      throw err;
    }
    this.failure = null;
    const m = new MarketData(new Map(uniq.map((t) => [t, this.frames.get(t)!])));
    this.markets.set(key, m);
    return m;
  }

  // --- routing ------------------------------------------------------------------
  async handle(method: string, path: string, body: Record<string, unknown> = {}): Promise<unknown> {
    try {
      return await this.route(method.toUpperCase(), path.split('?')[0], body, new URLSearchParams(path.split('?')[1] ?? ''));
    } catch (e) {
      if (e instanceof ApiError) throw e;
      if (e instanceof SessionError) throw new ApiError(e.message, 400);
      if (e instanceof NetworkError) throw new ApiError(`Network error: ${e.message}`, 503, true);
      if (e instanceof DataError) throw new ApiError(`Data error: ${e.message}`, 503);
      throw e;
    }
  }

  private async route(method: string, path: string, body: Record<string, unknown>, query: URLSearchParams): Promise<unknown> {
    let m: RegExpMatchArray | null;
    if (method === 'GET' && path === '/api/info') {
      return { disclaimer: DISCLAIMER, tickers: this.cfg.tickers, starting_cash: this.cfg.starting_cash_usd };
    }
    if (method === 'GET' && path === '/api/education') return this.education();
    if (method === 'GET' && path === '/api/progress') return this.progress.snapshot();
    if (method === 'POST' && path === '/api/progress/reset') {
      this.progress.reset();
      this.journal.clear();
      return this.progress.snapshot();
    }
    if (method === 'GET' && path === '/api/journal') return { csv: this.journal.csv() };
    if (method === 'POST' && (m = path.match(/^\/api\/lessons\/([\w-]+)\/quiz$/))) return this.quiz(m[1], body.answers);
    if (method === 'POST' && path === '/api/sessions') return this.newSession(body);
    if ((m = path.match(/^\/api\/sessions\/(\w+)(\/.*)?$/))) {
      const game = this.games.get(m[1]);
      if (!game) throw new ApiError('Session not found. Start a new one.', 404);
      const sub = m[2] ?? '';
      let h: RegExpMatchArray | null;
      if (method === 'GET' && sub === '') return this.state(game);
      if (method === 'GET' && (h = sub.match(/^\/history\/([\w.-]+)$/))) {
        const days = query.get('days') ? Number(query.get('days')) : null;
        if (days !== null && !(days >= 1)) throw new ApiError('days must be at least 1');
        return this.history(game.session, h[1], days);
      }
      if (method === 'GET' && sub === '/report') {
        if (!game.report) throw new ApiError('The report is available once the session has ended.', 409);
        return game.report;
      }
      if (method === 'POST' && sub === '/end') return this.end(game);
      if (method === 'POST') {
        if (game.session.ended) throw new ApiError('This session has ended. Start a new session to keep practising.', 409);
        if (sub === '/preview') return this.preview(game, body);
        if (sub === '/orders') return this.order(game, body);
        if (sub === '/review') return this.reviewPos(game, body);
        if (sub === '/cancel') return this.cancel(game);
        if (sub === '/hold') return this.hold(game, body);
      }
    }
    throw new ApiError(`Not found: ${method} ${path}`, 404);
  }

  // --- education ------------------------------------------------------------------
  private education() {
    return {
      lessons: LESSON_LIST.map(({ quiz, ...rest }) => ({ ...rest, quiz: quiz.map(({ q, options }) => ({ q, options })) })),
      glossary: GLOSSARY,
      scenarios: SCENARIO_LIST.map(({ debrief: _debrief, ...rest }) => rest),
      missions: MISSIONS,
      practice_tickers: Object.fromEntries(Object.entries(PRACTICE_TICKERS).map(([t, v]) => [t, v[0]])),
      progress: this.progress.snapshot(),
    };
  }

  private quiz(id: string, answers: unknown) {
    const lesson = LESSON_LIST.find((l) => l.id === id);
    if (!lesson) throw new ApiError('Unknown lesson', 404);
    if (!Array.isArray(answers) || answers.length !== lesson.quiz.length) {
      throw new ApiError(`Expected ${lesson.quiz.length} answers`);
    }
    const results = lesson.quiz.map((q, i) => ({ correct: answers[i] === q.answer, answer: q.answer, explain: q.explain }));
    const score = results.filter((r) => r.correct).length;
    const result = { score, total: lesson.quiz.length, passed: score >= lesson.quiz.length - 1, results };
    const xp_gained = this.progress.recordQuiz(id, result);
    return { ...result, xp_gained, progress: this.progress.snapshot() };
  }

  // --- sessions -----------------------------------------------------------------------
  private async newSession(body: Record<string, unknown>) {
    const mode = String(body.mode ?? 'random');
    let seed: number;
    if (body.seed === '' || body.seed === null || body.seed === undefined) seed = this.cfg.session.seed ?? randomSeed();
    else {
      seed = Number(body.seed);
      if (!Number.isInteger(seed)) throw new ApiError('Seed must be a whole number (or leave it blank for a random start).');
    }
    const rng = new Rng(seed);
    const cfg: Config = structuredClone(this.cfg);
    let scenario: Scenario | null = null;
    let endIdx: number | null = null;
    let market: MarketData;
    let start: number;
    if (mode === 'practice') {
      market = makePracticeMarket(rng.integers(0, 2 ** 31));
      cfg.tickers = Object.keys(PRACTICE_TICKERS);
      cfg.benchmark_ticker = PRACTICE_BENCHMARK;
      start = market.randomStartIndex(rng, requiredWarmup(cfg), cfg.session.min_years_after_start);
    } else if (mode === 'scenario') {
      scenario = SCENARIO_BY_ID.get(String(body.scenario)) ?? null;
      if (!scenario) throw new ApiError('Unknown scenario');
      cfg.tickers = [...scenario.tickers];
      cfg.benchmark_ticker = scenario.benchmark;
      market = await this.market([...scenario.tickers, scenario.benchmark]);
      start = market.dates.findIndex((d) => d >= scenario!.start);
      if (start < requiredWarmup(cfg) || start < 0 || start >= market.length - 1) {
        throw new ApiError("Not enough data around this scenario's dates for these tickers.", 503);
      }
      endIdx = Math.min(market.length - 1, start + scenario.days);
    } else if (mode === 'random') {
      market = await this.market([...cfg.tickers, cfg.benchmark_ticker]);
      start = market.randomStartIndex(rng, requiredWarmup(cfg), cfg.session.min_years_after_start);
    } else {
      throw new ApiError('mode must be random, scenario or practice');
    }
    // A replay pins the exact start date, so newly published data can't move it.
    if (typeof body.start_date === 'string') {
      const pinned = market.dates.indexOf(body.start_date);
      if (pinned < 0) throw new ApiError('Saved session start date is no longer in the data.', 409);
      start = pinned;
    }
    const id = Math.random().toString(16).slice(2, 10);
    const session = new Session(market, cfg, start, id, (row) => this.journal.append(row), endIdx);
    const game: Game = {
      session, mode, scenario, seed, log: [], coach: [], seen: new Set(), report: null,
      xpStart: typeof body.xp_start === 'number' ? body.xp_start : this.progress.snapshot().xp,
    };
    this.games.set(id, game);
    const d = session.date;
    if (mode === 'practice') game.log.push({ date: d, type: 'info', text: 'Practice market: fictional companies with randomly generated prices.' });
    if (session.account.fxPaid) {
      game.log.push({ date: d, type: 'info', text: `SGD→USD conversion of your deposit cost $${money(session.account.fxPaid)} (FX spread).` });
    }
    game.log.push({ date: d, type: 'info', text: `Session started on ${d} with $${money(session.account.startingCash)}.` });
    game.coach.push(tip('welcome', 'How to approach this session',
      'Before each trade, decide how much of your portfolio it deserves and write down what would prove you ' +
        'wrong. The order ticket previews costs and risk; the coach explains events as they happen. Your ' +
        'process score rewards discipline, not luck.', 'process', 'info', d));
    return this.state(game);
  }

  private state(game: Game) {
    const s = game.session;
    const v = s.view;
    const a = s.account;
    const weights = s.weights();
    const positions = [...a.positions].filter(([, p]) => p.shares > 0).map(([t, p]) => {
      const close = v.close(t);
      return {
        ticker: t, shares: p.shares, avg_cost: p.avgCost, close, value: p.shares * close,
        pl: p.avgCost ? close / p.avgCost - 1 : 0, weight: weights.get(t) ?? 0, alert: s.alertLoss(t) !== null,
        held_days: s.openSince.has(t) ? v.idx - s.openSince.get(t)! : 0,
      };
    });
    const market = s.tickers.map((t) => {
      const today = v.today(t);
      return {
        ticker: t, open: today.Open, high: today.High, low: today.Low, close: today.Close,
        change_1d: v.trailingReturn(t, 1), change_20d: v.trailingReturn(t, 20),
        description: game.mode === 'practice' && PRACTICE_TICKERS[t] ? PRACTICE_TICKERS[t][0] : null,
      };
    });
    const c = s.costs;
    const sc = game.scenario;
    return {
      id: s.sessionId, mode: game.mode, seed: game.seed, xp_start: game.xpStart,
      scenario: sc ? { id: sc.id, title: sc.title, period: sc.period, briefing: sc.briefing, lessons: sc.lessons } : null,
      date: s.date, start_date: s.equityCurve[0][0], day: v.daysElapsed(), has_next: v.hasNext(), ended: s.ended,
      starting_cash: a.startingCash, cash: a.cash, available_cash: s.availableCash(), holdings: a.marketValue(),
      equity: a.equity(), return: a.totalReturn(), drawdown: s.drawdown(), max_drawdown: s.maxDrawdown,
      positions,
      pending: a.pending.map((o) => ({ side: o.side, ticker: o.ticker, amount: o.amount, reason: o.reason, placed: o.placedDate })),
      market,
      costs: { fees: a.feesPaid, fx: a.fxPaid, spread_slippage: a.spreadSlippagePaid, dividend_tax: a.dividendTaxPaid, dividends_net: a.dividendsNet },
      equity_curve: s.equityCurve,
      journal: s.entries,
      log: game.log.slice(-200),
      coach: game.coach.slice(-12),
      missions: missionStatus(s),
      process: processScore(s),
      rules: {
        tickers: s.tickers, fee: c.feePerOrder, half_spread: c.halfSpread, slippage: c.slippage, fx_mode: c.fxMode,
        fx_spread: c.fxSpread, withholding: c.withholdingTax, loss_limit: s.lossLimit, fractional: c.fractional,
        benchmark: s.cfg.benchmark_ticker,
      },
    };
  }

  private history(s: Session, ticker: string, days: number | null) {
    const t = s.checkTicker(ticker);
    const full = s.view.adjustedCloses(t);
    const h = s.view.history(t, days);
    const n = h.dates.length;
    const ma = (len: number) => full.slice(full.length - n).map((_, i) => {
      const end = full.length - n + i + 1; // trailing window ending today-or-earlier: past rows only
      return end >= len ? Math.round((pairwiseSum(full, end - len, len) / len) * 1e4) / 1e4 : null;
    });
    const dateSet = new Set(h.dates);
    const r4 = (x: number) => Math.round(x * 1e4) / 1e4;
    return {
      ticker: t, as_of: s.date, dates: h.dates,
      open: h.cols.Open.map(r4), high: h.cols.High.map(r4), low: h.cols.Low.map(r4), close: h.cols.Close.map(r4),
      adj_close: full.slice(full.length - n).map(r4), volume: h.cols.Volume.map(Math.round),
      dividends: h.cols.Dividends, splits: h.cols['Stock Splits'], ma50: ma(50), ma200: ma(200),
      markers: s.entries
        .filter((e) => e.status === 'filled' && e.ticker === t && dateSet.has(String(e.fill_date)))
        .map((e) => ({ date: e.fill_date, side: e.action, price: e.fill_price, shares: e.shares })),
    };
  }

  private parseAmount(side: string, raw: unknown): number | 'all' {
    if (side === 'sell' && String(raw).toLowerCase() === 'all') return 'all';
    const amount = Number(String(raw ?? '').replace(/,/g, '').replace(/^\$/, ''));
    if (String(raw ?? '').trim() === '' || Number.isNaN(amount)) {
      throw new ApiError('Amount must be a US dollar amount' + (side === 'sell' ? ' or "all"' : ''));
    }
    if (!Number.isFinite(amount) || amount <= 0) throw new ApiError('Amount must be a positive number of US dollars');
    return amount;
  }

  private side(body: Record<string, unknown>): Side {
    const side = String(body.side ?? '').toLowerCase();
    if (side !== 'buy' && side !== 'sell') throw new ApiError("side must be 'buy' or 'sell'");
    return side;
  }

  private preview(game: Game, body: Record<string, unknown>) {
    const side = this.side(body);
    return tradePreview(game.session, side, String(body.ticker ?? ''), this.parseAmount(side, body.amount));
  }

  private order(game: Game, body: Record<string, unknown>) {
    const s = game.session;
    const side = this.side(body);
    const amount = this.parseAmount(side, body.amount);
    const reason = String(body.reason ?? '').trim();
    const proveWrong = String(body.prove_wrong ?? '').trim();
    if (!reason) throw new ApiError('Please write a reason for this trade.');
    if (!proveWrong) throw new ApiError('Please write what would prove you wrong.');
    const ticker = String(body.ticker ?? '');
    const tips = tradePreview(s, side, ticker, amount).tips;
    const order = s.place(side, ticker, amount, reason, proveWrong);
    const amt = order.amount === 'all' ? 'all shares' : `$${money(order.amount)}`;
    game.log.push({ date: s.date, type: 'order', text: `Order queued: ${side} ${order.ticker} ${amt}. Fills at the next trading day's open.` });
    game.coach.push(...tips.filter((t) => t.kind === 'warn').map((t) => ({ ...t, date: s.date })));
    return this.state(game);
  }

  private reviewPos(game: Game, body: Record<string, unknown>) {
    const s = game.session;
    const row = s.reviewPosition(String(body.ticker ?? ''), String(body.note ?? ''));
    game.log.push({ date: s.date, type: 'review', text: `Position review written for ${row.ticker}.` });
    this.awardMissions(game);
    return this.state(game);
  }

  private cancel(game: Game) {
    const n = game.session.cancelAll().length;
    game.log.push({ date: game.session.date, type: 'info', text: `Cancelled ${n} pending order(s).` });
    return this.state(game);
  }

  private awardMissions(game: Game, atEnd = false): Mission[] {
    const fresh = updateMissions(game.session, atEnd);
    if (fresh.length) {
      const gained = this.progress.recordMissions(fresh.map((m) => m.id));
      for (const m of fresh) {
        game.coach.push(tip(`mission-${m.id}`, `Mission complete: ${m.title}`, `${m.desc} (+XP)`, m.lesson, 'success', game.session.date));
      }
      game.log.push({
        date: game.session.date, type: 'mission',
        text: 'Mission complete: ' + fresh.map((m) => m.title).join(', ') + (gained ? ` (+${gained} XP)` : ''),
      });
    }
    return fresh;
  }

  private dayEvents(s: Session, rep: DayReport) {
    const st = rep.step;
    const d = st.date;
    const ev: { date: string; type: string; text: string }[] = [];
    for (const sp of st.splits) ev.push({ date: d, type: 'split', text: `${sp.ticker} ${sp.ratio}-for-1 split: ${sp.sharesBefore} → ${sp.sharesAfter} shares` });
    for (const dv of st.dividends) {
      ev.push({ date: d, type: 'dividend', text: `${dv.ticker} dividend $${dv.perShare.toFixed(4)}/sh × ${dv.shares}: gross $${money(dv.gross)}, withholding tax −$${money(dv.tax)}, credited $${money(dv.net)}` });
    }
    for (const f of st.fills) {
      const fx = f.fx ? `, FX $${money(f.fx)}` : '';
      const cash = (f.cashChange >= 0 ? '+' : '-') + money(Math.abs(f.cashChange));
      ev.push({ date: d, type: 'fill', text: `${f.order.side.toUpperCase()} ${f.shares} ${f.order.ticker} @ $${f.price.toFixed(4)} (open $${f.openPrice.toFixed(4)}), fee $${f.fee.toFixed(2)}${fx}, cash ${cash}` });
    }
    for (const r of st.rejections) ev.push({ date: d, type: 'rejected', text: `${r.order.side} ${r.order.ticker} not filled: ${r.reason}` });
    for (const ep of rep.newAlerts) {
      ev.push({ date: d, type: 'alert', text: `Loss-limit alert: ${ep.ticker} is ${pct(ep.loss)} below your average cost (limit ${pct(s.lossLimit, 0)}). Nothing is sold automatically.` });
    }
    if (rep.journalError) ev.push({ date: d, type: 'warning', text: rep.journalError });
    return ev;
  }

  private hold(game: Game, body: Record<string, unknown>) {
    const days = Number(body.days ?? 1);
    if (!Number.isInteger(days)) throw new ApiError('days must be a whole number');
    if (days < 1 || days > 1000) throw new ApiError('days must be between 1 and 1000');
    const s = game.session;
    const [reports, stopped] = s.hold(days);
    const events = reports.flatMap((r) => this.dayEvents(s, r));
    game.log.push(...events);
    const newTips = eventTips(s, reports, game.seen);
    game.coach.push(...newTips);
    const newMissions = this.awardMissions(game);
    if (stopped) game.log.push({ date: s.date, type: 'info', text: stopped });
    return { ...this.state(game), advanced: reports.length, stopped, new_events: events, new_tips: newTips, new_missions: newMissions };
  }

  private end(game: Game) {
    if (!game.report) {
      const s = game.session;
      this.awardMissions(game, true);
      const rep = computeReport(s);
      rep.mode = game.mode;
      rep.scenario = game.scenario
        ? { id: game.scenario.id, title: game.scenario.title, period: game.scenario.period, debrief: game.scenario.debrief }
        : null;
      rep.missions_completed = [...s.missionsDone].map(([id, date]) => ({ ...MISSION_BY_ID.get(id)!, date }));
      rep.recommended_lessons = this.recommended(rep, game);
      this.progress.recordSession({
        mode: game.mode, scenario: game.scenario?.title ?? null, start: rep.start, end: rep.end, days: rep.trading_days,
        return: rep.user.return, benchmark_return: rep.benchmark?.return ?? null, max_drawdown: rep.user.max_drawdown,
        process_score: rep.process.score, trades: rep.user.orders,
      });
      const snap = this.progress.snapshot();
      rep.xp = { gained_now: snap.xp - game.xpStart, level: snap.level };
      game.report = rep;
      s.ended = true;
    }
    return { state: this.state(game), report: game.report };
  }

  private recommended(rep: Report, game: Game): string[] {
    const recs = rep.review.filter((f) => FINDING_LESSONS[f.title]).map((f) => FINDING_LESSONS[f.title]);
    const weak = rep.process.components
      .filter((c) => c.score !== null && c.score < c.max * 0.6)
      .sort((a, b) => (a.score as number) - (b.score as number));
    recs.push(...weak.map((c) => c.lesson));
    if (rep.benchmark && rep.user.return < rep.benchmark.return) recs.push('benchmarks');
    if (rep.walk_forward) recs.push('backtesting');
    if (game.scenario) recs.push(...game.scenario.lessons);
    const ids = new Set(LESSON_LIST.map((l) => l.id));
    return [...new Set(recs.filter((r) => ids.has(r)))].slice(0, 3);
  }

  /** Rebuild a session from its saved creation request and actions (after a page reload). */
  async replay(log: ReplayLog): Promise<unknown> {
    this.progress.frozen = true;
    this.journal.frozen = true;
    try {
      const state = (await this.handle('POST', '/api/sessions', log.create)) as { id: string };
      for (const a of log.actions) await this.handle('POST', `/api/sessions/${state.id}${a.path}`, a.body);
      return this.handle('GET', `/api/sessions/${state.id}`);
    } finally {
      this.progress.frozen = false;
      this.journal.frozen = false;
    }
  }
}
