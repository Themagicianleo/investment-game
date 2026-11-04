// Small helpers that reproduce Python behaviour the engine relies on.

/** Python's round(x, n): round half to even on exact ties. */
export function pyRound(x: number, n = 0): number {
  if (!Number.isFinite(x)) return x;
  const f = 10 ** n;
  const m = x * f;
  const frac = m - Math.floor(m);
  if (frac === 0.5 && m / f === x) {
    const fl = Math.floor(m);
    return (fl % 2 === 0 ? fl : fl + 1) / f;
  }
  return n === 0 ? Math.round(m) : Number(x.toFixed(n));
}

/**
 * numpy's pairwise summation (used by ndarray.sum / mean for float64), so moving
 * averages match the Python engine bit for bit.
 */
export function pairwiseSum(a: ArrayLike<number>, start = 0, n = a.length - start): number {
  if (n < 8) {
    let res = -0.0;
    for (let i = 0; i < n; i++) res += a[start + i];
    return res;
  }
  if (n <= 128) {
    const r = [0, 0, 0, 0, 0, 0, 0, 0];
    for (let j = 0; j < 8; j++) r[j] = a[start + j];
    let i = 8;
    for (; i < n - (n % 8); i += 8) for (let j = 0; j < 8; j++) r[j] += a[start + i + j];
    let res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
    for (; i < n; i++) res += a[start + i];
    return res;
  }
  let n2 = Math.floor(n / 2);
  n2 -= n2 % 8;
  return pairwiseSum(a, start, n2) + pairwiseSum(a, start + n2, n - n2);
}

/** Python's sum(): a left fold starting from 0. */
export function pySum(xs: Iterable<number>): number {
  let s = 0;
  for (const x of xs) s += x;
  return s;
}

/** f"{x:,.2f}" */
export function money(x: number): string {
  return x.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** f"{x:.Np%}" */
export function pct(x: number, digits = 1): string {
  return `${(x * 100).toFixed(digits)}%`;
}

/** f"{x:g}" for the simple values used in messages. */
export function fmtG(x: number): string {
  return String(Number(x.toPrecision(6)));
}

/** "YYYY-MM-DD" -> days since epoch (UTC). */
export function dayNumber(date: string): number {
  return Date.UTC(+date.slice(0, 4), +date.slice(5, 7) - 1, +date.slice(8, 10)) / 86_400_000;
}

export function addDays(date: string, days: number): string {
  return new Date((dayNumber(date) + days) * 86_400_000).toISOString().slice(0, 10);
}
