// Process score: decision habits, independent of outcome (port of trade_sim/education/scoring.py).

import * as ex from './execution';
import { Session } from './session';
import { fmtG, pct, pyRound, pySum } from './util';

const POINTS = 20;

export interface Component {
  key: string;
  name: string;
  max: number;
  lesson: string;
  detail: string;
  score: number | null;
}

export interface ProcessScore {
  score: number | null;
  components: Component[];
  note: string;
}

export function tradingCosts(s: Session): number {
  const a = s.account;
  const deposit = ex.depositFxCost(a.startingCash, s.costs);
  return a.feesPaid + a.spreadSlippagePaid + Math.max(0, a.fxPaid - deposit);
}

export function processScore(s: Session): ProcessScore {
  const filled = s.entries.filter((e) => e.status === 'filled');
  const components: Component[] = [];
  const grace = s.cfg.alerts.loss_limit_grace_days;
  const large = s.cfg.review.large_move_pct;
  const add = (key: string, name: string, frac: number | null, detail: string, lesson: string) => {
    components.push({
      key, name, max: POINTS, lesson, detail,
      score: frac === null ? null : pyRound(POINTS * Math.max(0, Math.min(1, frac)), 1),
    });
  };

  if (filled.length) {
    const good = filled.filter((e) => String(e.reason ?? '').trim().length >= 15 && String(e.prove_wrong ?? '').trim().length >= 10);
    add('journal', 'Clear written reasoning', good.length / filled.length,
      `${good.length} of ${filled.length} trades had a clear reason and a 'prove me wrong' condition.`, 'process');
    const calm = filled.filter((e) => e.prior_move_pct === '' || e.prior_move_pct === undefined || Math.abs(Number(e.prior_move_pct)) < large);
    add('calm', 'Not reacting to big moves', calm.length / filled.length,
      `${filled.length - calm.length} of ${filled.length} trades came right after a move of ${fmtG(large)}% or more.`, 'biases');
  } else {
    add('journal', 'Clear written reasoning', null, 'No trades yet.', 'process');
    add('calm', 'Not reacting to big moves', null, 'No trades yet.', 'biases');
  }

  // Python treats an index of 0 as falsy here; mirror that exactly.
  const due = s.episodes.filter((ep) => s.view.idx >= ep.startIdx + grace || !!ep.soldIdx || !!ep.reviewedIdx);
  if (due.length) {
    const ok = due.filter((ep) => [ep.soldIdx, ep.reviewedIdx, ep.recoveredIdx].some((x) => x !== null && x <= ep.startIdx + grace));
    add('exits', 'Responding to loss limits', ok.length / due.length,
      `${ok.length} of ${due.length} loss-limit alerts got a response (sale, review note or recovery) within ${grace} days.`,
      'exit-plans');
  } else {
    add('exits', 'Responding to loss limits', null, 'No loss-limit alerts to respond to.', 'exit-plans');
  }

  const invested = s.maxWeights.filter((w) => w > 0);
  if (invested.length) {
    const okDays = invested.filter((w) => w <= 0.4).length;
    add('sizing', 'Position sizing', okDays / invested.length,
      `On ${okDays} of ${invested.length} invested days no single stock was over 40% of your portfolio ` +
        `(a broad index fund such as ${s.cfg.benchmark_ticker} is already diversified, so it is not counted).`, 'sizing');
  } else {
    add('sizing', 'Position sizing', null, 'You have not held a position at a close yet.', 'sizing');
  }

  if (filled.length) {
    const ratio = tradingCosts(s) / s.account.startingCash;
    const frac = ratio <= 0.005 ? 1 : Math.max(0, 1 - (ratio - 0.005) / 0.015);
    add('costs', 'Keeping costs down', frac,
      `Trading costs were ${pct(ratio, 2)} of starting cash (full marks at 0.5% or less, none at 2% or more).`, 'costs');
  } else {
    add('costs', 'Keeping costs down', null, 'No trades yet.', 'costs');
  }

  const scored = components.filter((c) => c.score !== null);
  const total = scored.length && filled.length
    ? pyRound((100 * pySum(scored.map((c) => c.score as number))) / (POINTS * scored.length))
    : null;
  return { score: total, components, note: 'Measures decision habits, not profit. A good process can lose money and a bad one can win.' };
}
