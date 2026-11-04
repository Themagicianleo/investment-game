// Missions that reward good process (port of trade_sim/education/missions.py).

import MISSIONS_JSON from '../content/missions.json';
import { processScore, tradingCosts } from './scoring';
import { Session } from './session';

export interface Mission {
  id: string;
  title: string;
  xp: number;
  lesson: string;
  desc: string;
}

export const MISSIONS: Mission[] = MISSIONS_JSON as Mission[];
export const MISSION_BY_ID = new Map(MISSIONS.map((m) => [m.id, m]));

function achieved(s: Session, atEnd: boolean): Set<string> {
  const done = new Set<string>();
  const filled = s.entries.filter((e) => e.status === 'filled');
  if (filled.some((e) => String(e.prove_wrong ?? '').trim().length >= 15)) done.add('plan-first');
  const w = s.weights();
  const single = [...w].filter(([t]) => !s.broadFunds.has(t)).map(([, v]) => v);
  if (w.size >= 3 && single.every((v) => v <= 0.4)) done.add('diversify');
  if (s.dividendsReceived > 0) done.add('dividend');
  if (s.entries.some((e) => e.status === 'review')) done.add('review');
  const grace = s.cfg.alerts.loss_limit_grace_days;
  if (s.episodes.some((ep) => [ep.soldIdx, ep.reviewedIdx].some((x) => x !== null && x <= ep.startIdx + grace))) {
    done.add('alert-response');
  }
  if (s.longestHoldDays >= 60) done.add('patience');
  if (s.view.daysElapsed() >= 252) done.add('full-year');
  if (atEnd && filled.length >= 3) {
    if (tradingCosts(s) / s.account.startingCash < 0.005) done.add('cost-control');
    const score = processScore(s).score;
    if (score !== null && score >= 80) done.add('disciplined');
  }
  return done;
}

/** Record newly completed missions on the session and return them. */
export function updateMissions(s: Session, atEnd = false): Mission[] {
  const ids = [...achieved(s, atEnd)].filter((id) => !s.missionsDone.has(id)).sort();
  for (const id of ids) s.missionsDone.set(id, s.date);
  return ids.map((id) => MISSION_BY_ID.get(id)!);
}

export function missionStatus(s: Session) {
  return MISSIONS.map((m) => ({ ...m, done: s.missionsDone.has(m.id), date: s.missionsDone.get(m.id) ?? null }));
}
