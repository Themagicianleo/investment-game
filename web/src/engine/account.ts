// Account shared by the player and the benchmark bots (port of trade_sim/account.py).
// step(): advance a day, apply splits, credit dividends to shares held before the open,
// then fill yesterday's orders (sells first) at today's open plus spread and slippage.

import * as ex from './execution';
import { Costs, Side } from './execution';
import { MarketView } from './market';
import { money, pySum } from './util';

export interface Position {
  shares: number;
  avgCost: number;
}

export interface Order {
  ticker: string;
  side: Side;
  amount: number | 'all';
  placedDate: string;
  reason: string;
  proveWrong: string;
  context: { prior_move?: number | null; loss_alert_active?: boolean };
}

export interface Fill {
  order: Order;
  date: string;
  shares: number;
  openPrice: number;
  price: number;
  gross: number;
  fee: number;
  fx: number;
  spreadSlippage: number;
  cashChange: number;
}

export interface Rejection {
  order: Order;
  date: string;
  reason: string;
}

export interface DividendEvent {
  ticker: string;
  date: string;
  perShare: number;
  shares: number;
  gross: number;
  tax: number;
  net: number;
}

export interface SplitEvent {
  ticker: string;
  date: string;
  ratio: number;
  sharesBefore: number;
  sharesAfter: number;
}

export interface StepResult {
  date: string;
  splits: SplitEvent[];
  dividends: DividendEvent[];
  fills: Fill[];
  rejections: Rejection[];
}

export function makeOrder(ticker: string, side: Side, amount: number | 'all', placedDate: string,
  reason = '', proveWrong = '', context: Order['context'] = {}): Order {
  return { ticker, side, amount, placedDate, reason, proveWrong, context };
}

export class Account {
  readonly positions = new Map<string, Position>();
  pending: Order[] = [];
  feesPaid = 0;
  fxPaid = 0;
  spreadSlippagePaid = 0;
  dividendTaxPaid = 0;
  dividendsNet = 0;
  orderCount = 0;
  cash: number;

  constructor(readonly view: MarketView, readonly costs: Costs, readonly startingCash: number) {
    const depositFx = ex.depositFxCost(startingCash, costs);
    this.fxPaid += depositFx;
    this.cash = startingCash - depositFx;
  }

  shares(ticker: string): number {
    return this.positions.get(ticker)?.shares ?? 0;
  }

  marketValue(): number {
    const vals: number[] = [];
    for (const [t, p] of this.positions) if (p.shares > 0) vals.push(p.shares * this.view.close(t));
    return pySum(vals);
  }

  equity(): number {
    return this.cash + this.marketValue();
  }

  totalReturn(): number {
    return this.equity() / this.startingCash - 1;
  }

  reservedCash(): number {
    return pySum(this.pending.filter((o) => o.side === 'buy' && typeof o.amount === 'number').map((o) => o.amount as number));
  }

  submit(order: Order): void {
    this.pending.push(order);
  }

  cancelAll(): Order[] {
    const cancelled = this.pending;
    this.pending = [];
    return cancelled;
  }

  step(): StepResult {
    const date = this.view.advance();
    const result: StepResult = { date, splits: [], dividends: [], fills: [], rejections: [] };
    this.applySplits(result);
    this.creditDividends(result);
    const orders = this.pending;
    this.pending = [];
    const sorted = [...orders.filter((o) => o.side === 'sell'), ...orders.filter((o) => o.side !== 'sell')];
    for (const o of sorted) this.fill(o, result);
    return result;
  }

  private applySplits(result: StepResult): void {
    for (const [ticker, pos] of this.positions) {
      const ratio = this.view.today(ticker)['Stock Splits'];
      if (ratio > 0 && ratio !== 1 && pos.shares > 0) {
        const before = pos.shares;
        pos.shares = pos.shares * ratio;
        if (!this.costs.fractional) pos.shares = ex.roundShares(pos.shares, false);
        pos.avgCost = pos.avgCost / ratio;
        result.splits.push({ ticker, date: result.date, ratio, sharesBefore: before, sharesAfter: pos.shares });
      }
    }
  }

  private creditDividends(result: StepResult): void {
    for (const [ticker, pos] of this.positions) {
      const perShare = this.view.today(ticker).Dividends;
      if (perShare > 0 && pos.shares > 0) {
        const [gross, tax, net] = ex.netDividend(perShare, pos.shares, this.costs);
        this.cash += net;
        this.dividendTaxPaid += tax;
        this.dividendsNet += net;
        result.dividends.push({ ticker, date: result.date, perShare, shares: pos.shares, gross, tax, net });
      }
    }
  }

  private fill(order: Order, result: StepResult): void {
    const openPrice = this.view.open(order.ticker);
    const price = ex.fillPrice(openPrice, order.side, this.costs);
    let pos = this.positions.get(order.ticker);
    if (!pos) {
      pos = { shares: 0, avgCost: 0 };
      this.positions.set(order.ticker, pos);
    }
    let shares: number;
    let gross: number;
    let fee: number;
    let fx: number;
    let cashChange: number;
    if (order.side === 'buy') {
      const amount = order.amount as number;
      const budget = Math.min(amount, this.cash);
      shares = ex.buyQuantity(budget, price, this.costs);
      if (shares <= 0) {
        const why = this.cash < amount ? 'not enough cash' : 'amount too small for one share after fees';
        result.rejections.push({ order, date: result.date, reason: `${why} at fill price $${money(price)}` });
        return;
      }
      let total: number;
      [gross, fee, fx, total] = ex.buyTotal(shares, price, this.costs);
      pos.avgCost = (pos.avgCost * pos.shares + gross) / (pos.shares + shares);
      pos.shares += shares;
      this.cash -= total;
      cashChange = -total;
    } else {
      const held = pos.shares;
      if (held <= 0) {
        result.rejections.push({ order, date: result.date, reason: `no ${order.ticker} shares held` });
        return;
      }
      shares = order.amount === 'all' ? held : ex.sellQuantity(order.amount, price, held, this.costs);
      if (shares <= 0) {
        result.rejections.push({ order, date: result.date, reason: `amount is less than one share at $${money(price)}` });
        return;
      }
      let net: number;
      [gross, fee, net] = ex.sellTotal(shares, price, this.costs);
      fx = 0;
      pos.shares = held - shares;
      if (pos.shares <= 1e-9) {
        pos.shares = 0;
        pos.avgCost = 0;
      }
      this.cash += net;
      cashChange = net;
    }
    const slip = ex.spreadSlippageCost(shares, openPrice, price);
    this.feesPaid += fee;
    this.fxPaid += fx;
    this.spreadSlippagePaid += slip;
    this.orderCount += 1;
    result.fills.push({ order, date: result.date, shares, openPrice, price, gross, fee, fx, spreadSlippage: slip, cashChange });
  }
}
