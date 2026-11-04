// Benchmarks only (port of trade_sim/bots.py). The trend bot follows PAST trends; it is a
// comparison, not a recommendation. Both use the same Account, costs and MarketView as the player.

import { Account, makeOrder } from './account';
import { Costs } from './execution';
import { MarketData, MarketView } from './market';

export interface TrendRule {
  shortMa: number;
  longMa: number;
}

export const ruleName = (r: TrendRule) => `MA${r.shortMa}/MA${r.longMa}`;

export interface BenchmarkResult {
  name: string;
  start: string;
  end: string;
  startingCash: number;
  finalValue: number;
  trades: number;
  fees: number;
  fx: number;
  spreadSlippage: number;
  dividendTax: number;
  maxDrawdown: number;
  totalReturn: number;
}

class Drawdown {
  peak: number;
  worst = 0;
  constructor(private acct: Account) {
    this.peak = acct.equity();
  }
  update(): void {
    const eq = this.acct.equity();
    this.peak = Math.max(this.peak, eq);
    this.worst = Math.max(this.worst, this.peak > 0 ? 1 - eq / this.peak : 0);
  }
}

function result(name: string, acct: Account, start: string, dd: Drawdown): BenchmarkResult {
  const finalValue = acct.equity();
  return {
    name, start, end: acct.view.date, startingCash: acct.startingCash, finalValue,
    trades: acct.orderCount, fees: acct.feesPaid, fx: acct.fxPaid, spreadSlippage: acct.spreadSlippagePaid,
    dividendTax: acct.dividendTaxPaid, maxDrawdown: dd.worst, totalReturn: finalValue / acct.startingCash - 1,
  };
}

export function trendSignal(view: MarketView, ticker: string, rule: TrendRule): boolean {
  const s = view.movingAverage(ticker, rule.shortMa);
  const l = view.movingAverage(ticker, rule.longMa);
  return s !== null && l !== null && s > l;
}

export function runTrendBot(market: MarketData, startIdx: number, endIdx: number, rule: TrendRule, costs: Costs,
  startingCash: number, tickers: string[] | null = null): BenchmarkResult {
  const ts = tickers ?? market.tickers;
  const view = market.view(startIdx, endIdx);
  const acct = new Account(view, costs, startingCash);
  const dd = new Drawdown(acct);
  while (view.hasNext()) {
    const slot = acct.equity() / ts.length;
    for (const t of ts) {
      const on = trendSignal(view, t, rule);
      const held = acct.shares(t) > 0;
      if (held && !on) acct.submit(makeOrder(t, 'sell', 'all', view.date));
      else if (on && !held) acct.submit(makeOrder(t, 'buy', slot, view.date));
    }
    acct.step();
    dd.update();
  }
  return result(`Trend bot ${ruleName(rule)}`, acct, market.dates[startIdx], dd);
}

export function runBuyAndHold(market: MarketData, ticker: string, startIdx: number, endIdx: number, costs: Costs,
  startingCash: number): BenchmarkResult {
  const view = market.view(startIdx, endIdx);
  const acct = new Account(view, costs, startingCash);
  const dd = new Drawdown(acct);
  if (view.hasNext()) {
    acct.submit(makeOrder(ticker, 'buy', acct.cash, view.date));
    while (view.hasNext()) {
      acct.step();
      dd.update();
    }
  }
  return result(`Buy & hold ${ticker}`, acct, market.dates[startIdx], dd);
}
