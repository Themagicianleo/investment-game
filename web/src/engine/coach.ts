// Coach: cost/risk previews before a trade and explanations after events
// (port of trade_sim/education/coach.py). Explains; never says buy, sell or hold.

import * as ex from './execution';
import { Side } from './execution';
import { DayReport, Session, SessionError } from './session';
import { money, pct } from './util';

export interface Tip {
  id: string;
  title: string;
  text: string;
  lesson: string | null;
  kind: 'info' | 'warn' | 'alert' | 'success';
  date: string | null;
}

export const tip = (id: string, title: string, text: string, lesson: string | null = null,
  kind: Tip['kind'] = 'info', date: string | null = null): Tip => ({ id, title, text, lesson, kind, date });

export interface Preview {
  ticker: string;
  side: Side;
  close: number;
  est_fill: number;
  shares: number;
  gross: number;
  fee: number;
  fx: number;
  spread_slippage: number;
  costs: number;
  cost_pct: number | null;
  break_even_move: number | null;
  weight_after: number;
  value_after: number;
  loss_if_down_20: number;
  loss_if_down_20_pct_portfolio: number;
  cash_after: number;
  prior_move: number | null;
  note: string;
  tips: Tip[];
}

export function tradePreview(s: Session, side: Side, ticker: string, amount: number | 'all'): Preview {
  const t = s.checkTicker(ticker);
  const c = s.costs;
  const close = s.view.close(t);
  const equity = s.account.equity();
  const pos = s.account.positions.get(t);
  const held = pos ? pos.shares : 0;
  const estPrice = ex.fillPrice(close, side, c);
  const adverse = c.halfSpread + c.slippage;
  let shares: number, gross: number, fee: number, fx: number, spreadCost: number, sharesAfter: number, cashAfter: number;
  let breakEven: number | null = null;

  if (side === 'buy') {
    if (typeof amount !== 'number' || !Number.isFinite(amount)) throw new SessionError('Enter a US dollar amount to preview the order.');
    const budget = Math.min(amount, Math.max(s.availableCash(), 0));
    shares = ex.buyQuantity(budget, estPrice, c);
    let total: number;
    [gross, fee, fx, total] = ex.buyTotal(shares, estPrice, c);
    spreadCost = shares * close * adverse;
    sharesAfter = held + shares;
    cashAfter = s.availableCash() - total;
    const exitCost = c.feePerOrder + shares * close * adverse;
    const roundTrip = fee + fx + spreadCost + exitCost;
    breakEven = gross > 0 ? roundTrip / gross : null;
  } else {
    if (held <= 0) throw new SessionError(`You hold no ${t} shares.`);
    shares = amount === 'all' ? held : ex.sellQuantity(amount, estPrice, held, c);
    let net: number;
    [gross, fee, net] = ex.sellTotal(shares, estPrice, c);
    fx = 0;
    spreadCost = shares * close * adverse;
    sharesAfter = held - shares;
    cashAfter = s.availableCash() + net;
  }

  const valueAfter = sharesAfter * close;
  const p: Preview = {
    ticker: t, side, close, est_fill: estPrice, shares, gross, fee, fx, spread_slippage: spreadCost,
    costs: fee + fx + spreadCost, cost_pct: gross > 0 ? (fee + fx + spreadCost) / gross : null,
    break_even_move: breakEven, weight_after: equity > 0 ? valueAfter / equity : 0, value_after: valueAfter,
    loss_if_down_20: valueAfter * 0.2, loss_if_down_20_pct_portfolio: equity ? (valueAfter * 0.2) / equity : 0,
    cash_after: cashAfter, prior_move: s.view.trailingReturn(t, s.cfg.review.large_move_lookback_days),
    note: "Estimate only. It uses today's close; your order fills at tomorrow's open, which nobody can see yet.",
    tips: [],
  };
  p.tips = tradeTips(s, side, t, p);
  return p;
}

function tradeTips(s: Session, side: Side, t: string, p: Preview): Tip[] {
  const tips: Tip[] = [];
  const c = s.costs;
  const large = s.cfg.review.large_move_pct / 100;
  const lookback = s.cfg.review.large_move_lookback_days;
  const move = p.prior_move;
  const pos = s.account.positions.get(t);

  if (p.shares <= 0) {
    tips.push(tip('no-shares', 'This order would buy nothing',
      `At about $${money(p.est_fill)} a share, the amount is too small for one whole share after the ` +
        `$${c.feePerOrder.toFixed(2)} fee.`, 'orders', 'warn'));
    return tips;
  }
  if (side === 'buy' && p.gross > 0 && c.feePerOrder / p.gross >= 0.005) {
    tips.push(tip('fee-drag', 'The flat fee is a big slice of this order',
      `The $${c.feePerOrder.toFixed(2)} fee is ${pct(c.feePerOrder / p.gross)} of this order. Including ` +
        `selling later, the price must rise about ${pct(p.break_even_move ?? 0)} just to break even.`, 'costs', 'warn'));
  }
  if (side === 'buy' && p.weight_after > 0.4 && s.broadFunds.has(t)) {
    tips.push(tip('broad-fund', 'A large weight in a broad fund',
      `${t} would be about ${pct(p.weight_after, 0)} of your portfolio. Because it is a broad index fund ` +
        'holding many companies, this is far less concentrated than the same weight in one stock, ' +
        'though it still moves with the whole market.', 'sizing'));
  } else if (side === 'buy' && p.weight_after > 0.4) {
    tips.push(tip('concentration', 'Concentration check',
      `After this trade ${t} would be about ${pct(p.weight_after, 0)} of your portfolio. A 20% fall in ` +
        `${t} alone would cost about ${pct(p.loss_if_down_20_pct_portfolio)} of everything you have. ` +
        'There is no right answer; just make sure the size matches your conviction and your exit plan.', 'sizing', 'warn'));
  }
  if (move !== null && side === 'buy' && move >= large) {
    tips.push(tip('chasing', 'Buying after a sharp rise',
      `${t} is up ${pct(move)} over the last ${lookback} trading days. Recent moves feel predictive ` +
        '(recency bias / FOMO). Would your reason for this trade still exist without that move?', 'biases', 'warn'));
  }
  if (move !== null && side === 'sell' && move <= -large) {
    tips.push(tip('panic', 'Selling after a sharp drop',
      `${t} is down ${pct(Math.abs(move))} over the last ${lookback} trading days. Has your 'what would prove me ` +
        "wrong' condition actually happened, or is the drop itself the reason? Either can be valid. Write down which.",
      'biases', 'warn'));
  }
  if (side === 'buy' && s.alertLoss(t) !== null) {
    tips.push(tip('averaging-down', 'Adding to a position past your loss limit',
      `${t} is past your ${pct(s.lossLimit, 0)} loss limit. Adding more is sometimes deliberate, but it is also ` +
        'a classic way small mistakes grow. Compare this reason with your original one.', 'exit-plans', 'warn'));
  }
  if (side === 'sell' && pos && pos.avgCost > 0) {
    const pl = s.view.close(t) / pos.avgCost - 1;
    const opened = s.openSince.get(t);
    if (opened !== undefined && s.view.idx - opened < 5) {
      tips.push(tip('quick-flip', 'A very short holding period',
        `You opened ${t} ${s.view.idx - opened} trading day(s) ago. Quick round trips pay the full ` +
          'costs twice for little time in the market.', 'costs'));
    }
    if (pl >= 0.2) {
      tips.push(tip('disposition', 'Selling a winner',
        `${t} is ${pct(pl, 0)} above your cost. Is this sale about the investment's prospects, or about ` +
          'locking in the good feeling of a gain? (the disposition effect)', 'biases'));
    }
  }
  if (side === 'buy' && p.cash_after < 0.02 * s.account.equity()) {
    tips.push(tip('all-in', 'Nearly fully invested',
      "After this order you would have almost no cash left. That's a valid choice, but it leaves no room " +
        'to act on new information or to absorb costs.', 'sizing'));
  }
  return tips;
}

export function eventTips(s: Session, reports: DayReport[], seen: Set<string>): Tip[] {
  const tips: Tip[] = [];
  const once = (id: string) => {
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  };
  for (const rep of reports) {
    const st = rep.step;
    const d = st.date;
    if (st.fills.length && once('first-fill')) {
      const f = st.fills[0];
      tips.push(tip('first-fill', 'Your first fill',
        `Your ${f.order.side} of ${f.order.ticker} filled at $${money(f.price)}, while the open was ` +
          `$${money(f.openPrice)}. The difference is half the spread plus slippage, a cost you pay on ` +
          'every market order.', 'orders', 'info', d));
    }
    for (const dv of st.dividends) {
      if (once('dividend')) {
        tips.push(tip('dividend', 'You received a dividend',
          `${dv.ticker} went ex-dividend today, so its price drops by about $${dv.perShare.toFixed(2)}, the cash ` +
            `that left the company. You received $${money(dv.gross)} gross, minus ` +
            `${pct(s.costs.withholdingTax, 0)} US withholding tax = $${money(dv.net)}. Dividends are part of ` +
            'total return, not extra money on top of it.', 'dividends', 'info', d));
      }
    }
    for (const sp of st.splits) {
      if (once(`split-${sp.ticker}`)) {
        tips.push(tip('split', `${sp.ticker} split ${sp.ratio}-for-1`,
          `You now hold ${sp.sharesAfter} shares instead of ${sp.sharesBefore}, and the price ` +
            'is divided by the same ratio. Your position\'s value did not change.', 'dividends', 'info', d));
      }
    }
    for (const r of st.rejections) {
      tips.push(tip('rejected', 'An order could not fill',
        `Your ${r.order.side} of ${r.order.ticker} was not filled: ${r.reason}. Prices can open ` +
          'differently from the previous close, so an amount that looked enough may not be.', 'orders', 'warn', d));
    }
    for (const ep of rep.newAlerts) {
      tips.push(tip('loss-alert', `${ep.ticker} hit your loss limit`,
        `${ep.ticker} is ${pct(ep.loss)} below your average cost. This is the moment your earlier self ` +
          'planned for. Re-read what you said would prove you wrong. Then act on it, or use ' +
          "'Review' to write down why your thesis still holds. Looking away is the one response " +
          'that is never part of a plan.', 'exit-plans', 'alert', d));
    }
  }
  const dd = s.drawdown();
  for (const level of [0.1, 0.2, 0.3]) {
    if (dd >= level && once(`drawdown-${Math.trunc(level * 100)}`)) {
      const need = 1 / (1 - dd) - 1;
      tips.push(tip('drawdown', `Your portfolio is ${pct(dd, 0)} below its peak`,
        `Drawdowns are asymmetric: from here you need +${pct(need, 0)} just to get back to your high. ` +
          'How does this feel compared with what you expected when you started? That gap is ' +
          'worth noting in your journal.', 'drawdowns', 'warn', s.date));
    }
  }
  return tips;
}
