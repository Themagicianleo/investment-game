// End-of-session report as plain data (port of compute_report in trade_sim/report.py).

import { BenchmarkResult, TrendRule, ruleName, runBuyAndHold, runTrendBot } from './bots';
import { review } from './review';
import { ProcessScore, processScore } from './scoring';
import { Session } from './session';
import { WalkForwardResult, runWalkForward, testLen, trainLen } from './walkforward';

export const NOTICES = [
  'Educational only. This simulator does not give buy or sell advice.',
  'Past performance does not predict future results.',
  'The trend bot is a comparison, not a recommendation.',
];

export const WALK_FORWARD_CAVEAT =
  'The in-sample figure was selected *because* it was the best on that data, so it is optimistic ' +
  'by construction. Only the out-of-sample figure is a (single, small) test, and it is not proof.';

export interface BenchDict {
  name: string;
  start: string;
  end: string;
  final_value: number;
  return: number;
  trades: number;
  costs: number;
  dividend_tax: number;
  max_drawdown: number;
}

const bench = (r: BenchmarkResult): BenchDict => ({
  name: r.name, start: r.start, end: r.end, final_value: r.finalValue, return: r.totalReturn, trades: r.trades,
  costs: r.fees + r.fx + r.spreadSlippage, dividend_tax: r.dividendTax, max_drawdown: r.maxDrawdown,
});

export function walkForwardDict(wf: WalkForwardResult, dates: string[]) {
  const s = wf.split;
  return {
    train: { start: dates[s.trainStart], end: dates[s.trainEnd], days: trainLen(s) },
    test: { start: dates[s.testStart], end: dates[s.testEnd], days: testLen(s) },
    chosen_rule: ruleName(wf.chosen),
    chosen_in_sample: wf.inSample.totalReturn,
    chosen_out_of_sample: wf.outOfSample.totalReturn,
    default_rule: ruleName(wf.defaultRule),
    default_in_sample: wf.defaultInSample.totalReturn,
    default_out_of_sample: wf.defaultOutOfSample.totalReturn,
    candidates: [...wf.table].sort((a, b) => b[1] - a[1]).map(([r, ret]) => ({ rule: ruleName(r), in_sample: ret })),
    caveat: WALK_FORWARD_CAVEAT,
  };
}

export interface Report {
  start: string;
  end: string;
  trading_days: number;
  user: Record<string, number>;
  trend_bot: BenchDict | null;
  trend_bot_rule: string | null;
  benchmark: BenchDict | null;
  tickers: BenchDict[];
  walk_forward: ReturnType<typeof walkForwardDict> | null;
  walk_forward_skipped: string | null;
  notices: string[];
  review: { title: string; count: number; detail: string }[];
  process: ProcessScore;
  [extra: string]: unknown;
}

export function computeReport(session: Session, includeBot = true): Report {
  const cfg = session.cfg;
  const acct = session.account;
  const [market, start, end] = session.boundedMarket();
  const dates = market.dates;
  const cash0 = acct.startingCash;
  const days = end - start;
  const tradingCosts = acct.feesPaid + acct.fxPaid + acct.spreadSlippagePaid;
  const rep: Report = {
    start: dates[start], end: dates[end], trading_days: days,
    user: {
      starting_cash: cash0, final_value: acct.equity(), return: acct.totalReturn(), orders: acct.orderCount,
      fees: acct.feesPaid, fx: acct.fxPaid, spread_slippage: acct.spreadSlippagePaid,
      dividend_tax: acct.dividendTaxPaid, dividends_net: acct.dividendsNet,
      total_fees_and_taxes: tradingCosts + acct.dividendTaxPaid, cancelled_orders: acct.pending.length,
      max_drawdown: session.maxDrawdown,
    },
    trend_bot: null, trend_bot_rule: null, benchmark: null, tickers: [], walk_forward: null,
    walk_forward_skipped: null, notices: NOTICES, review: [], process: processScore(session),
  };
  const botOn = includeBot && cfg.trend_bot.enabled;
  if (botOn) {
    const rule: TrendRule = { shortMa: cfg.trend_bot.short_ma, longMa: cfg.trend_bot.long_ma };
    rep.trend_bot = bench(runTrendBot(market, start, end, rule, session.costs, cash0, session.tickers));
    rep.trend_bot_rule = `Follows PAST trends: holds a ticker while its ${rule.shortMa}-day moving ` +
      `average is above its ${rule.longMa}-day moving average, otherwise cash.`;
  }
  if (market.tickers.includes(cfg.benchmark_ticker)) {
    rep.benchmark = bench(runBuyAndHold(market, cfg.benchmark_ticker, start, end, session.costs, cash0));
  }
  rep.tickers = session.tickers.map((t) => bench(runBuyAndHold(market, t, start, end, session.costs, cash0)));
  if (botOn && cfg.trend_bot.walk_forward.enabled) {
    try {
      rep.walk_forward = walkForwardDict(runWalkForward(market, start, end, cfg, session.costs, cash0, session.tickers), dates);
    } catch (e) {
      if (!(e instanceof RangeError)) throw e;
      rep.walk_forward_skipped = e.message;
    }
  }
  rep.review = review(session.entries, session.episodes, cfg, end, days, tradingCosts, cash0).slice(0, 3);
  rep.process = processScore(session);
  return rep;
}
