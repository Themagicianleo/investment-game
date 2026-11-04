// Pure cost and fill calculations (port of trade_sim/execution.py).

export type Side = 'buy' | 'sell';
const EPS = 1e-9;

export interface Costs {
  halfSpread: number; // fractions, not percent
  slippage: number;
  feePerOrder: number;
  fxSpread: number;
  fxMode: 'deposit' | 'per_buy' | 'none';
  withholdingTax: number;
  fractional: boolean;
}

export function costsFromConfig(cfg: Config): Costs {
  const c = cfg.costs;
  return {
    halfSpread: c.half_spread_pct / 100,
    slippage: c.slippage_pct / 100,
    feePerOrder: c.fee_per_order_usd,
    fxSpread: c.fx_spread_pct / 100,
    fxMode: c.fx_mode,
    withholdingTax: cfg.dividends.withholding_tax_pct / 100,
    fractional: cfg.fractional_shares,
  };
}

export interface Config {
  tickers: string[];
  benchmark_ticker: string;
  starting_cash_usd: number;
  fractional_shares: boolean;
  costs: {
    half_spread_pct: number;
    slippage_pct: number;
    fee_per_order_usd: number;
    fx_spread_pct: number;
    fx_mode: 'deposit' | 'per_buy' | 'none';
  };
  dividends: { withholding_tax_pct: number };
  alerts: { loss_limit_pct: number; loss_limit_grace_days: number };
  session: { min_years_after_start: number; seed: number | null };
  data: Record<string, string>;
  trend_bot: {
    enabled: boolean;
    short_ma: number;
    long_ma: number;
    walk_forward: { enabled: boolean; train_fraction: number; short_grid: number[]; long_grid: number[] };
  };
  review: { large_move_pct: number; large_move_lookback_days: number };
}

export const perBuyFx = (c: Costs) => (c.fxMode === 'per_buy' ? c.fxSpread : 0);

export function fillPrice(open: number, side: Side, c: Costs): number {
  const adverse = c.halfSpread + c.slippage;
  if (side === 'buy') return open * (1 + adverse);
  if (side === 'sell') return open * (1 - adverse);
  throw new Error(`side must be 'buy' or 'sell', got ${side}`);
}

export function roundShares(q: number, fractional: boolean): number {
  if (q <= 0) return 0;
  if (fractional) return Math.floor(q * 1e6 + EPS) / 1e6;
  return Math.floor(q + EPS);
}

export const fxCost = (gross: number, c: Costs) => gross * perBuyFx(c);
export const depositFxCost = (amount: number, c: Costs) => (c.fxMode === 'deposit' ? amount * c.fxSpread : 0);

export function buyQuantity(budget: number, price: number, c: Costs): number {
  const available = budget - c.feePerOrder;
  if (available <= 0 || price <= 0) return 0;
  return roundShares(available / (price * (1 + perBuyFx(c))), c.fractional);
}

export function sellQuantity(amount: number, price: number, held: number, c: Costs): number {
  if (price <= 0 || amount <= 0) return 0;
  return Math.min(roundShares(amount / price, c.fractional), held);
}

export const spreadSlippageCost = (shares: number, open: number, fill: number) => Math.abs(fill - open) * shares;

export function buyTotal(shares: number, price: number, c: Costs): [number, number, number, number] {
  const gross = shares * price;
  const fee = c.feePerOrder;
  const fx = fxCost(gross, c);
  return [gross, fee, fx, gross + fee + fx];
}

export function sellTotal(shares: number, price: number, c: Costs): [number, number, number] {
  const gross = shares * price;
  return [gross, c.feePerOrder, gross - c.feePerOrder];
}

export function netDividend(perShare: number, shares: number, c: Costs): [number, number, number] {
  const gross = perShare * shares;
  const tax = gross * c.withholdingTax;
  return [gross, tax, gross - tax];
}

export const lossFromCost = (price: number, avgCost: number) => (avgCost <= 0 ? 0 : 1 - price / avgCost);
export const lossLimitBreached = (price: number, avgCost: number, limit: number) =>
  lossFromCost(price, avgCost) >= limit - EPS;
