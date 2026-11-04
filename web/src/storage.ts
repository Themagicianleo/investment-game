// Browser persistence: learner progress (XP, lessons, missions, history) and the trade journal.
// Everything lives in this browser's localStorage; nothing is sent anywhere.

import LESSONS from './content/lessons.json';
import PROGRESS from './content/progress.json';
import { MISSION_BY_ID } from './engine/missions';
import { JournalRow } from './engine/session';

export interface KV {
  get(key: string): string | null;
  set(key: string, value: string): void;
  remove(key: string): void;
}

export function browserKV(): KV {
  const mem = new Map<string, string>();
  return {
    get: (k) => {
      try { return localStorage.getItem(k); } catch { return mem.get(k) ?? null; }
    },
    set: (k, v) => {
      try { localStorage.setItem(k, v); } catch { mem.set(k, v); }
    },
    remove: (k) => {
      try { localStorage.removeItem(k); } catch { mem.delete(k); }
    },
  };
}

export function memoryKV(): KV {
  const mem = new Map<string, string>();
  return { get: (k) => mem.get(k) ?? null, set: (k, v) => void mem.set(k, v), remove: (k) => void mem.delete(k) };
}

const LEVELS = PROGRESS.levels as [number, string][];

export function levelInfo(xp: number) {
  let idx = 0;
  LEVELS.forEach(([need], i) => { if (xp >= need) idx = i; });
  const [need, name] = LEVELS[idx];
  return { level: idx + 1, name, xp, level_start: need, next_level_at: idx + 1 < LEVELS.length ? LEVELS[idx + 1][0] : null };
}

interface ProgressData {
  xp: number;
  lessons: Record<string, { passed: boolean; best: number; total: number; date: string }>;
  missions: Record<string, number>;
  sessions: Record<string, unknown>[];
}

const today = () => new Date().toISOString().slice(0, 10);

export class ProgressStore {
  private data: ProgressData;
  /** While true (during a session replay) nothing is recorded. */
  frozen = false;

  constructor(private kv: KV, private key = 'trade-sim-progress') {
    this.data = this.load();
  }

  private empty(): ProgressData {
    return { xp: 0, lessons: {}, missions: {}, sessions: [] };
  }

  private load(): ProgressData {
    try {
      const raw = this.kv.get(this.key);
      if (!raw) return this.empty();
      return { ...this.empty(), ...JSON.parse(raw) };
    } catch {
      return this.empty();
    }
  }

  private save(): void {
    if (!this.frozen) this.kv.set(this.key, JSON.stringify(this.data));
  }

  snapshot() {
    const d = structuredClone(this.data);
    return {
      ...d, level: levelInfo(d.xp), lessons_total: LESSONS.length,
      lessons_passed: Object.values(d.lessons).filter((v) => v.passed).length,
    };
  }

  recordQuiz(lessonId: string, result: { passed: boolean; score: number; total: number }): number {
    if (this.frozen) return 0;
    const prev = this.data.lessons[lessonId];
    const gained = result.passed && !prev?.passed ? PROGRESS.lesson_xp : 0;
    this.data.lessons[lessonId] = {
      passed: (prev?.passed ?? false) || result.passed, best: Math.max(prev?.best ?? 0, result.score),
      total: result.total, date: today(),
    };
    this.data.xp += gained;
    this.save();
    return gained;
  }

  recordMissions(ids: string[]): number {
    if (this.frozen) return 0;
    let gained = 0;
    for (const id of ids) {
      const xp = MISSION_BY_ID.get(id)!.xp;
      const count = this.data.missions[id] ?? 0;
      gained += count === 0 ? xp : Math.trunc(xp * PROGRESS.repeat_mission_share);
      this.data.missions[id] = count + 1;
    }
    this.data.xp += gained;
    this.save();
    return gained;
  }

  recordSession(summary: Record<string, unknown>): number {
    if (this.frozen) return 0;
    this.data.sessions.push({ ...summary, played: today() });
    this.data.sessions = this.data.sessions.slice(-100);
    this.data.xp += PROGRESS.session_xp;
    this.save();
    return PROGRESS.session_xp;
  }

  reset(): void {
    this.data = this.empty();
    this.kv.remove(this.key);
  }
}

export const JOURNAL_FIELDS = [
  'session_id', 'status', 'placed_date', 'fill_date', 'action', 'ticker', 'shares', 'open_price', 'fill_price',
  'fee_usd', 'fx_cost_usd', 'spread_slippage_usd', 'cash_change_usd', 'prior_move_pct', 'loss_alert_active',
  'reason', 'prove_wrong', 'note',
];

function csvCell(v: unknown): string {
  let s = v === undefined || v === null ? '' : typeof v === 'boolean' ? (v ? 'True' : 'False') : String(v);
  if (/[",\n\r]/.test(s)) s = `"${s.replace(/"/g, '""')}"`;
  return s;
}

/** journal.csv kept in localStorage (same columns as the Python version). */
export class JournalStore {
  /** While true (during a session replay) rows are not stored again. */
  frozen = false;

  constructor(private kv: KV, private key = 'trade-sim-journal') {}

  append(row: JournalRow): void {
    if (this.frozen) return;
    const existing = this.kv.get(this.key) ?? JOURNAL_FIELDS.join(',') + '\n';
    this.kv.set(this.key, existing + JOURNAL_FIELDS.map((f) => csvCell(row[f])).join(',') + '\n');
  }

  csv(): string {
    return this.kv.get(this.key) ?? JOURNAL_FIELDS.join(',') + '\n';
  }

  clear(): void {
    this.kv.remove(this.key);
  }
}
