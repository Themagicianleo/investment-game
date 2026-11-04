// Daily price data: CSV parsing, cleaning and split handling (port of trade_sim/data.py).
//
// CSVs hold Yahoo's split-adjusted prices (not dividend-adjusted; Adj Close is never used
// because it embeds future dividends). toAsTraded rebuilds the prices that actually traded.

export const COLUMNS = ['Open', 'High', 'Low', 'Close', 'Volume', 'Dividends', 'Stock Splits'] as const;
export type Column = (typeof COLUMNS)[number];

export interface Frame {
  dates: string[]; // YYYY-MM-DD, ascending
  cols: Record<Column, number[]>;
}

export class DataError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'DataError';
  }
}

export class NetworkError extends DataError {
  constructor(message: string) {
    super(message);
    this.name = 'NetworkError';
  }
}

function emptyCols(): Record<Column, number[]> {
  return { Open: [], High: [], Low: [], Close: [], Volume: [], Dividends: [], 'Stock Splits': [] };
}

export function parseCsv(text: string, ticker: string): Frame {
  const lines = text.split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (lines.length < 2) throw new DataError(`No usable price rows for ${ticker}`);
  const header = lines[0].split(',').map((h) => h.trim());
  const dateIdx = header.indexOf('Date');
  const missing = ['Open', 'High', 'Low', 'Close'].filter((c) => !header.includes(c));
  if (dateIdx < 0 || missing.length) {
    throw new DataError(`Data for ${ticker} is missing columns: ${missing.join(', ') || 'Date'}`);
  }
  const colIdx = Object.fromEntries(COLUMNS.map((c) => [c, header.indexOf(c)])) as Record<Column, number>;
  const rows = new Map<string, number[]>();
  for (const line of lines.slice(1)) {
    const cells = line.split(',');
    const date = cells[dateIdx].trim().slice(0, 10);
    const vals = COLUMNS.map((c) => {
      const i = colIdx[c];
      if (i < 0) return c === 'Open' || c === 'Close' ? NaN : 0;
      const v = cells[i] === undefined || cells[i].trim() === '' ? NaN : Number(cells[i]);
      if (Number.isNaN(v) && (c === 'Volume' || c === 'Dividends' || c === 'Stock Splits')) return 0;
      return v;
    });
    rows.set(date, vals); // later duplicates win, like keep="last"
  }
  const frame: Frame = { dates: [], cols: emptyCols() };
  for (const date of [...rows.keys()].sort()) {
    const v = rows.get(date)!;
    const open = v[0];
    const close = v[3];
    if (Number.isNaN(open) || Number.isNaN(close) || open <= 0 || close <= 0) continue;
    frame.dates.push(date);
    COLUMNS.forEach((c, i) => frame.cols[c].push(v[i]));
  }
  if (!frame.dates.length) throw new DataError(`No usable price rows for ${ticker}`);
  return frame;
}

/** Undo the split adjustment: each row is multiplied by the product of all later split ratios. */
export function toAsTraded(f: Frame): Frame {
  const n = f.dates.length;
  const ratios = f.cols['Stock Splits'].map((r) => (r > 0 ? r : 1));
  const later = new Array<number>(n);
  let acc = 1;
  for (let i = n - 1; i >= 0; i--) {
    acc *= ratios[i];
    later[i] = acc;
  }
  const factor = later.slice(1).concat([1]);
  const cols = emptyCols();
  for (const c of COLUMNS) {
    const src = f.cols[c];
    if (c === 'Volume') cols[c] = src.map((v, i) => v / factor[i]);
    else if (c === 'Stock Splits') cols[c] = src.slice();
    else cols[c] = src.map((v, i) => v * factor[i]);
  }
  return { dates: f.dates.slice(), cols };
}
