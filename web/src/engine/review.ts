// Common decision-making patterns in the journal (port of trade_sim/review.py).
// Observations to reflect on, never instructions.

import { Config } from './execution';
import { AlertEpisode, JournalRow } from './session';
import { fmtG, pct } from './util';

export interface Finding {
  title: string;
  count: number;
  detail: string;
}

const vague = (text: unknown, minLen: number) => String(text ?? '').trim().length < minLen;

export function review(entries: JournalRow[], episodes: AlertEpisode[], cfg: Config, endIdx: number,
  sessionDays: number, feesAndCosts: number, startingCash: number): Finding[] {
  const filled = entries.filter((e) => e.status === 'filled');
  const large = cfg.review.large_move_pct;
  const lookback = cfg.review.large_move_lookback_days;
  const grace = cfg.alerts.loss_limit_grace_days;
  const findings: Finding[] = [];
  const move = (e: JournalRow) => (e.prior_move_pct === '' || e.prior_move_pct === undefined ? null : Number(e.prior_move_pct));

  const chased = filled.filter((e) => e.action === 'buy' && (move(e) ?? 0) >= large);
  const dumped = filled.filter((e) => e.action === 'sell' && (move(e) ?? 0) <= -large);
  const otherBig = filled.filter((e) => !chased.includes(e) && !dumped.includes(e) && Math.abs(move(e) ?? 0) >= large);
  const nBig = chased.length + dumped.length + otherBig.length;
  if (nBig) {
    findings.push({
      title: 'Trading right after a large move', count: nBig,
      detail: `${chased.length} buy(s) after a rise of ${fmtG(large)}%+ and ${dumped.length} sell(s) after a fall of ` +
        `${fmtG(large)}%+ over the previous ${lookback} trading days (${otherBig.length} other). Big recent moves ` +
        'can pull attention and urgency; check whether your written reason still holds without them.',
    });
  }

  const skipped = episodes.filter((ep) => {
    const deadline = ep.startIdx + grace;
    const acted = (ep.soldIdx !== null && ep.soldIdx <= deadline) || (ep.reviewedIdx !== null && ep.reviewedIdx <= deadline);
    const recovered = ep.recoveredIdx !== null && ep.recoveredIdx <= deadline;
    return !acted && !recovered && endIdx >= deadline;
  });
  if (skipped.length) {
    const names = [...new Set(skipped.map((ep) => ep.ticker))].sort().join(', ');
    findings.push({
      title: 'Skipping the loss limit', count: skipped.length,
      detail: `A loss-limit alert fired and within ${grace} trading days the position was not reduced, ` +
        `not reviewed in the journal, and did not recover (${names}). Holding can be a deliberate choice; the question is whether your ` +
        "'what would prove me wrong' note had already come true.",
    });
  }

  const averagingDown = filled.filter((e) => e.action === 'buy' && e.loss_alert_active === true);
  if (averagingDown.length) {
    findings.push({
      title: 'Adding to a position that was past the loss limit', count: averagingDown.length,
      detail: 'You bought more of a holding while its loss-limit alert was active. Compare the new reason with ' +
        'the original one for that position.',
    });
  }

  const thin = filled.filter((e) => vague(e.reason, 15) || vague(e.prove_wrong, 10));
  if (thin.length) {
    findings.push({
      title: 'Short or missing journal reasoning', count: thin.length,
      detail: "Some entries had a very short reason or no clear 'what would prove me wrong' condition, which " +
        'makes it hard to review the decision later.',
    });
  }

  const months = Math.max(sessionDays / 21, 1);
  const perMonth = filled.length / months;
  const costShare = feesAndCosts / startingCash;
  if (filled.length >= 6 && (perMonth > 4 || costShare > 0.01)) {
    findings.push({
      title: 'Frequent trading', count: filled.length,
      detail: `${filled.length} trades in about ${months.toFixed(1)} month(s) (${perMonth.toFixed(1)}/month); trading costs were ` +
        `${pct(costShare, 2)} of starting cash.`,
    });
  }
  return findings.sort((a, b) => b.count - a.count);
}
