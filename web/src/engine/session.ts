// One play-through: account, orders, loss-limit alerts, journal and process tracking
// (port of trade_sim/session.py).

import { Account, DividendEvent, Fill, Order, Rejection, SplitEvent, StepResult, makeOrder } from './account';
import * as ex from './execution';
import { Config, Costs, Side, costsFromConfig } from './execution';
import { MarketData, MarketView } from './market';
import { money, pyRound } from './util';

export class SessionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SessionError';
  }
}

export interface AlertEpisode {
  ticker: string;
  startIdx: number;
  startDate: string;
  loss: number;
  soldIdx: number | null;
  recoveredIdx: number | null;
  reviewedIdx: number | null;
  endIdx: number | null;
}

export interface DayReport {
  step: StepResult;
  newAlerts: AlertEpisode[];
  ongoingAlerts: string[];
  journalError: string | null;
}

/** A journal row; keys match journal.csv columns. */
export type JournalRow = Record<string, string | number | boolean>;
export type JournalSink = (row: JournalRow) => void;

export type { DividendEvent, SplitEvent };

export class Session {
  readonly costs: Costs;
  readonly tickers: string[];
  readonly view: MarketView;
  readonly account: Account;
  readonly lossLimit: number;
  entries: JournalRow[] = [];
  episodes: AlertEpisode[] = [];
  private active = new Map<string, AlertEpisode>();
  equityCurve: [string, number][];
  ended = false;
  maxWeights: number[] = [];
  peakEquity: number;
  maxDrawdown = 0;
  openSince = new Map<string, number>();
  longestHoldDays = 0;
  dividendsReceived = 0;
  splitsSeen = 0;
  broadFunds: Set<string>;
  missionsDone = new Map<string, string>();

  constructor(private readonly market: MarketData, readonly cfg: Config, readonly startIdx: number,
    readonly sessionId: string, public journalSink: JournalSink | null = null, endIdx: number | null = null) {
    this.costs = costsFromConfig(cfg);
    this.tickers = cfg.tickers.filter((t) => market.tickers.includes(t));
    this.view = market.view(startIdx, endIdx);
    this.account = new Account(this.view, this.costs, cfg.starting_cash_usd);
    this.lossLimit = cfg.alerts.loss_limit_pct / 100;
    this.equityCurve = [[this.date, this.account.equity()]];
    this.peakEquity = this.account.equity();
    this.broadFunds = new Set([cfg.benchmark_ticker ?? '']);
  }

  get date(): string {
    return this.view.date;
  }

  checkTicker(ticker: string): string {
    const t = ticker.toUpperCase();
    if (!this.tickers.includes(t)) {
      throw new SessionError(`Unknown ticker '${ticker}'. Tradable tickers: ${this.tickers.join(', ')}`);
    }
    return t;
  }

  availableCash(): number {
    return this.account.cash - this.account.reservedCash();
  }

  private context(ticker: string): Order['context'] {
    const move = this.view.trailingReturn(ticker, this.cfg.review.large_move_lookback_days);
    return { prior_move: move, loss_alert_active: this.active.has(ticker) };
  }

  validateBuy(ticker: string, amount: number): string {
    const t = this.checkTicker(ticker);
    if (amount <= 0) throw new SessionError('Amount must be a positive number of US dollars, e.g. buy AAPL 1000');
    const avail = this.availableCash();
    if (amount > avail + 1e-9) throw new SessionError(`Not enough cash: $${money(avail)} available after pending orders.`);
    if (amount <= this.costs.feePerOrder) {
      throw new SessionError(`Amount must be more than the $${this.costs.feePerOrder.toFixed(2)} order fee.`);
    }
    if (!this.view.hasNext()) throw new SessionError('This is the last day of available data; no further fills are possible.');
    return t;
  }

  validateSell(ticker: string, amount: number | 'all'): string {
    const t = this.checkTicker(ticker);
    if (this.account.shares(t) <= 0) throw new SessionError(`You hold no ${t} shares.`);
    if (amount !== 'all' && amount <= 0) {
      throw new SessionError('Amount must be a positive US dollar amount or "all", e.g. sell AAPL all');
    }
    if (!this.view.hasNext()) throw new SessionError('This is the last day of available data; no further fills are possible.');
    return t;
  }

  place(side: Side, ticker: string, amount: number | 'all', reason: string, proveWrong: string): Order {
    const t = side === 'buy' ? this.validateBuy(ticker, amount as number) : this.validateSell(ticker, amount);
    if (!reason.trim()) throw new SessionError('A reason is required for every trade.');
    const order = makeOrder(t, side, amount, this.date, reason.trim(), proveWrong.trim(), this.context(t));
    this.account.submit(order);
    return order;
  }

  cancelAll(): Order[] {
    return this.account.cancelAll();
  }

  weights(): Map<string, number> {
    const eq = this.account.equity();
    const w = new Map<string, number>();
    if (eq <= 0) return w;
    for (const [t, p] of this.account.positions) if (p.shares > 0) w.set(t, (p.shares * this.view.close(t)) / eq);
    return w;
  }

  drawdown(): number {
    return this.peakEquity > 0 ? Math.max(0, 1 - this.account.equity() / this.peakEquity) : 0;
  }

  isAlertActive(ticker: string): boolean {
    return this.active.has(ticker);
  }

  reviewPosition(ticker: string, note: string): JournalRow {
    const t = this.checkTicker(ticker);
    const pos = this.account.positions.get(t);
    if (!pos || pos.shares <= 0) throw new SessionError(`You hold no ${t} shares to review.`);
    if (!note.trim()) {
      throw new SessionError("Write a short note: does your original reason still hold? Has your 'prove me wrong' condition happened?");
    }
    const ep = this.active.get(t);
    if (ep && ep.reviewedIdx === null) ep.reviewedIdx = this.view.idx;
    const row: JournalRow = {
      session_id: this.sessionId, status: 'review', placed_date: this.date, fill_date: this.date,
      action: 'review', ticker: t, shares: pyRound(pos.shares, 6), loss_alert_active: ep !== undefined,
      reason: note.trim(), prove_wrong: '',
      note: `close $${money(this.view.close(t))}, avg cost $${money(pos.avgCost)}`,
    };
    this.entries.push(row);
    this.write(row);
    return row;
  }

  advance(): DayReport {
    const step = this.account.step();
    const report: DayReport = { step, newAlerts: [], ongoingAlerts: [], journalError: null };
    for (const fill of step.fills) {
      report.journalError = this.record(fill) ?? report.journalError;
      const ep = this.active.get(fill.order.ticker);
      if (fill.order.side === 'sell' && ep && ep.soldIdx === null) ep.soldIdx = this.view.idx;
    }
    for (const rej of step.rejections) report.journalError = this.record(rej) ?? report.journalError;
    this.updateAlerts(report);
    this.track(report);
    return report;
  }

  private track(report: DayReport): void {
    const idx = this.view.idx;
    const eq = this.account.equity();
    this.equityCurve.push([this.date, eq]);
    this.peakEquity = Math.max(this.peakEquity, eq);
    this.maxDrawdown = Math.max(this.maxDrawdown, this.drawdown());
    const w = this.weights();
    const single = [...w].filter(([t]) => !this.broadFunds.has(t)).map(([, v]) => v);
    this.maxWeights.push(single.length ? Math.max(...single) : w.size ? 1e-9 : 0);
    for (const t of this.tickers) {
      const held = this.account.shares(t) > 0;
      if (held && !this.openSince.has(t)) this.openSince.set(t, idx);
      else if (!held && this.openSince.has(t)) {
        this.longestHoldDays = Math.max(this.longestHoldDays, idx - this.openSince.get(t)!);
        this.openSince.delete(t);
      }
    }
    for (const start of this.openSince.values()) this.longestHoldDays = Math.max(this.longestHoldDays, idx - start);
    this.dividendsReceived += report.step.dividends.length;
    this.splitsSeen += report.step.splits.length;
  }

  hold(days = 1): [DayReport[], string | null] {
    if (days < 1) throw new SessionError('days must be at least 1');
    const reports: DayReport[] = [];
    for (let i = 0; i < days; i++) {
      if (!this.view.hasNext()) return [reports, 'No more data: this is the last available trading day.'];
      const rep = this.advance();
      reports.push(rep);
      if (rep.newAlerts.length && i < days - 1) {
        return [reports, `Stopped after ${i + 1} day(s) because of a loss-limit alert.`];
      }
    }
    return [reports, null];
  }

  private updateAlerts(report: DayReport): void {
    const idx = this.view.idx;
    for (const t of this.tickers) {
      const pos = this.account.positions.get(t);
      const held = pos !== undefined && pos.shares > 0;
      const breached = held && ex.lossLimitBreached(this.view.close(t), pos!.avgCost, this.lossLimit);
      const ep = this.active.get(t);
      if (breached && !ep) {
        const n: AlertEpisode = {
          ticker: t, startIdx: idx, startDate: this.date, loss: ex.lossFromCost(this.view.close(t), pos!.avgCost),
          soldIdx: null, recoveredIdx: null, reviewedIdx: null, endIdx: null,
        };
        this.active.set(t, n);
        this.episodes.push(n);
        report.newAlerts.push(n);
      } else if (breached) {
        report.ongoingAlerts.push(t);
      } else if (ep) {
        if (held) ep.recoveredIdx = idx;
        ep.endIdx = idx;
        this.active.delete(t);
      }
    }
  }

  alertLoss(ticker: string): number | null {
    const pos = this.account.positions.get(ticker);
    if (!this.active.has(ticker) || !pos || pos.shares <= 0) return null;
    return ex.lossFromCost(this.view.close(ticker), pos.avgCost);
  }

  private record(item: Fill | Rejection): string | null {
    const o = item.order;
    const move = o.context.prior_move;
    const row: JournalRow = {
      session_id: this.sessionId, placed_date: o.placedDate, fill_date: item.date, action: o.side, ticker: o.ticker,
      prior_move_pct: move === null || move === undefined ? '' : pyRound(move * 100, 2),
      loss_alert_active: o.context.loss_alert_active ?? false, reason: o.reason, prove_wrong: o.proveWrong,
    };
    if ('shares' in item) {
      Object.assign(row, {
        status: 'filled', shares: pyRound(item.shares, 6), open_price: pyRound(item.openPrice, 4),
        fill_price: pyRound(item.price, 4), fee_usd: pyRound(item.fee, 2), fx_cost_usd: pyRound(item.fx, 2),
        spread_slippage_usd: pyRound(item.spreadSlippage, 2), cash_change_usd: pyRound(item.cashChange, 2),
      });
    } else {
      Object.assign(row, { status: 'rejected', note: item.reason });
    }
    this.entries.push(row);
    return this.write(row);
  }

  private write(row: JournalRow): string | null {
    try {
      this.journalSink?.(row);
      return null;
    } catch (e) {
      return `Could not save the journal entry: ${(e as Error).message}`;
    }
  }

  /** Market plus the session's [start, today] index range, for end-of-session benchmarks. */
  boundedMarket(): [MarketData, number, number] {
    return [this.market, this.startIdx, this.view.idx];
  }
}
