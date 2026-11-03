# trade-sim

A simulator, with a **web app** and a command-line interface, for practising **buy, hold and sell decisions** on US stocks and ETFs
by replaying real daily history one trading day at a time.

> **Educational use only.** trade-sim does not give buy or sell advice. Past performance does not
> predict future results. The trend bot is a comparison that follows past trends, not a
> recommendation.

Default tickers: `VT, AAPL, NVDA, INTC, AMD`.

## How it works

- **Data:** daily OHLCV, dividends and splits from Yahoo Finance via `yfinance`, cached as CSV in
  `data_cache/` so later runs don't download again.
- **Random start:** every session starts on a random historical date that has at least 2 years of
  data after it (and enough history before it for the 250-day moving averages the benchmarks use).
- **No lookahead:** the replay only ever shows data up to the current day. Prices, dividends and
  splits after today are hidden. `tests/test_no_lookahead.py` checks this by replacing every
  future row with junk and confirming that nothing visible changes.
- **Realistic fills:** market orders fill at the **next** trading day's open, moved against you by
  half the bid-ask spread (default 0.05%) plus slippage (default 0.05%).
- **Costs:** a flat US$0.99 fee per order and a 0.15% FX spread for converting SGD to USD, both
  taken from cash. Shares are rounded **down** to whole shares unless `fractional_shares: true`.
- **Dividends** are credited on the ex-dividend date, minus 30% US withholding tax. You only
  get a dividend for shares you held before that day's open.
- **Splits:** prices are shown as they actually traded. On a split date your share count and
  average cost are adjusted.
- **Loss-limit alert:** you get a warning when a position closes 15% or more below its average
  cost. Nothing is sold automatically.
- **Journal:** every buy and sell asks for a reason and for *what would prove you wrong*. Each
  fill (or rejected order) is appended to `journal.csv` with the date, fill price, fees and your
  notes.

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"              # or: pip install -r requirements.txt

cp config.example.yaml config.yaml   # your personal settings; git-ignored
trade-sim fetch                      # optional: download and cache the data first

trade-sim web --open                 # web app at http://127.0.0.1:8000
trade-sim play                       # or play in the terminal (same as plain `trade-sim`)
```

You can also run it without installing: `python -m trade_sim web`.

### Web app

`trade-sim web` starts a small local server and serves a single-page app. The page has:

- **Ticker cards** showing today's close and 1-day and 20-day changes, plus a price chart (3M to
  All) of past closes up to today. The chart is adjusted only for splits that have already
  happened, and a dashed line marks your average cost.
- **An order form.** Buy takes a USD amount; sell takes a USD amount or "Sell all". Both fields,
  *Why are you making this trade?* and *What would prove you wrong?*, are required.
- **Next day / +5 days / +1 month buttons.** You can also press **n** for the next day.
  Multi-day holds stop early when a loss-limit alert fires.
- **Account panels:** total value with an equity sparkline, cash, positions (highlighted when
  past the loss limit), costs so far, an activity log of fills, dividends, splits and alerts,
  and your journal.
- **End session,** which shows the full report: benchmarks, per-ticker buy-and-hold, the
  walk-forward table, the journal review and the notices.

It uses the same engine, `config.yaml`, data cache and `journal.csv` as the CLI. The browser
only receives data up to the session's current day, which `tests/test_web.py` checks. The page
is plain HTML/CSS/JS with no build step and no CDN, so it works offline once the data is cached.

Options: `--port 8000`, `--host 127.0.0.1`, `--open` (opens your browser), `--no-bot`,
`--refresh`. It listens only on localhost by default. Sessions live in the server's memory, so
restarting the server ends any open session. Your journal entries are already saved in
`journal.csv`.

### CLI

```text
trade-sim [-c CONFIG] play [--seed N] [--journal PATH] [--no-bot] [--refresh]
trade-sim [-c CONFIG] fetch [TICKER ...] [--refresh]
trade-sim [-c CONFIG] walkforward [--start YYYY-MM-DD] [--end YYYY-MM-DD]
trade-sim [-c CONFIG] web [--port 8000] [--host 127.0.0.1] [--open] [--no-bot] [--refresh]
```

- `--seed` makes the random start date reproducible.
- `--refresh` downloads the data again instead of using the cache.
- `walkforward` runs the trend-bot walk-forward test over the full common history.

Config lookup order: `--config`, then `./config.yaml`, then `./config.example.yaml`.

### Commands during a terminal session

| Command | Meaning |
|---|---|
| `buy <ticker> <USD amount>` | Market order. Fills at the next day's open. The amount includes the fee (and the FX cost in `per_buy` mode). |
| `sell <ticker> <USD amount \| all>` | Market order. Fills at the next day's open. |
| `hold [days]` | Advance one trading day, or several. A multi-day hold stops early if a loss-limit alert fires. |
| `status` | Cash, positions, pending orders, today's closes and costs so far. |
| `history <ticker> [days]` | Past daily prices up to today. |
| `journal` | This session's journal entries. |
| `cancel` | Cancel pending orders. |
| `end` | Finish and print the end-of-session report. |

## Configuration (`config.yaml`)

Every setting with its default is in [`config.example.yaml`](config.example.yaml). The main ones:

| Setting | Default | |
|---|---|---|
| `starting_cash_usd` | 10000 | |
| `fractional_shares` | false | false rounds share quantities down to whole shares |
| `costs.half_spread_pct` / `costs.slippage_pct` | 0.05 / 0.05 | percent |
| `costs.fee_per_order_usd` | 0.99 | |
| `costs.fx_spread_pct` | 0.15 | percent, SGD to USD |
| `costs.fx_mode` | deposit | `deposit`: your SGD deposit is converted once at the start. `per_buy`: every buy is funded by a fresh SGD conversion. `none`: no FX cost. |
| `dividends.withholding_tax_pct` | 30 | |
| `alerts.loss_limit_pct` | 15 | |
| `trend_bot.short_ma` / `long_ma` | 50 / 200 | |
| `trend_bot.walk_forward.train_fraction` | 0.7 | |

## Trend bot (benchmark only)

The bot runs on the same dates and data as you and pays the same fees, spread, slippage, FX and
dividend tax. It divides the account into one equal slot per ticker. It holds a ticker while that
ticker's 50-day moving average is above its 200-day moving average, and keeps that slot in cash
otherwise. Signals use each day's close and trade at the next open, just like your orders.

**Walk-forward check:** the end report (and `trade-sim walkforward`) chooses the moving-average
lengths using only the first 70% of the period, then runs them unchanged on the last 30%. Both
results are printed. The in-sample number is the best of many tries on that data, so it is
optimistic by construction. The out-of-sample number is one small test, not proof.

## End-of-session report

- Your final value, return, flat fees, FX costs, spread and slippage, and dividend tax.
- The trend bot's return and VT buy-and-hold over the same dates.
- The buy-and-hold return of each ticker over the period.
- The walk-forward in-sample and out-of-sample results.
- A short journal review of the most common patterns: trading right after a large move, skipping
  the loss limit, adding to a position past the loss limit, thin journal reasoning, and frequent
  trading.
- The educational-only, past-performance and benchmark-not-advice notices.

## Example session

The output below is shortened. The prices come from the **synthetic test data** used to develop
the tool, not from real market history, so ignore the numbers and look at the flow.

```text
$ trade-sim play --seed 3
EDUCATIONAL USE ONLY. This simulator does not give buy or sell advice. ...

Session ef97fb19: starting on 2015-03-25 with $10,000.00.
SGD->USD conversion of your deposit cost $15.00 (FX spread).

[2015-03-25] > buy VT 3000
  Reason for this trade: diversified core holding
  What would prove me wrong? VT falls 20% below my cost
  Order queued: buy VT $3,000.00. It fills at the next trading day's open (+/- 0.10% spread & slippage, $0.99 fee).

[2015-03-25] > hold
--- 2015-03-26 ---
  FILLED BUY 33 VT @ $90.3749 (open $90.2846) fee $0.99  cash -2,983.36
  Total value $9,949.23 (-0.51%)

[2015-03-26] > hold 200
--- 2015-04-30 ---
  DIVIDEND VT $0.4000/sh x 33: gross $13.20, withholding tax -$3.96, credited $9.24
--- 2015-09-08 ---
  !! LOSS-LIMIT ALERT: VT is 15.6% below your average cost (limit 15%). Nothing is sold automatically; ...
Stopped after 117 day(s) because of a loss-limit alert.

[2015-09-08] > end
==============================================================================
END OF SESSION REPORT
Period: 2015-03-25 to 2015-09-08 (119 trading days)
You
  Final value:        $9,588.88  (started with $10,000.00)
  Return:             -4.11%
  ...
Benchmarks over the same dates, same costs (comparisons, NOT recommendations)
  Trend bot MA50/MA200          -2.67%   ...
  Buy & hold VT                -15.03%   ...
Walk-forward check of the trend bot (benchmark only)
  Chosen rule MA20/MA150:   in-sample +1.38%   out-of-sample +0.04%
  ...
Journal review (patterns to reflect on, not advice)
  - Short or missing journal reasoning (x3): ...
Important
  * Educational only. This simulator does not give buy or sell advice.
  * Past performance does not predict future results.
  * The trend bot is a comparison, not a recommendation.
```

## Running the tests

```bash
pytest
```

The tests use synthetic data and need no network. They cover fees and FX, the spread and
slippage calculation, whole-share and fractional rounding, no-lookahead data access and next-open
fills, dividend withholding, splits, the loss-limit alert, the walk-forward split (including that
the parameter choice ignores test-period data), caching and network-error messages, the journal
a full scripted session, and the web API (including
that no future dates reach the browser).

## Limitations

trade-sim is a simplified model. It **cannot** model:

- **Market impact:** your orders never move the price, whatever their size.
- **Order book depth and liquidity:** every order fills in full at one price. In reality large
  orders walk the book, and thin markets have wider spreads than a fixed percentage.
- **Variable spreads and slippage:** both are fixed percentages. Real spreads widen around the
  open, around news and in volatile or illiquid markets.
- **Trading halts, circuit breakers and gaps:** an order always fills at the next open, even if a
  stock was halted or opened far from the previous close in a way that would have changed
  execution.
- **Intraday prices:** only daily data is used. There are no limit or stop orders, no intraday
  timing and no fills between the open and the close.
- **Delisting and survivorship bias:** the default tickers are companies that still exist and are
  well known today. Choosing them already uses hindsight.
- **Exact dividend timing and tax:** dividends are credited on the ex-date instead of the later
  pay date. A flat withholding rate is applied, with no treaty rates, reclaims or other taxes.
- **FX rates:** only the conversion spread is charged. Movements in the SGD/USD rate, which
  change your return in SGD, are not modelled, and all values are reported in USD.
- **Corporate actions beyond cash dividends and splits:** spin-offs, mergers, rights issues and
  special distributions are not handled. Cash in lieu of fractional split shares is ignored.
- **Data quality:** Yahoo Finance data can have errors, gaps or late corrections. Prices are
  rebuilt from Yahoo's split-adjusted series, and dividend-adjusted closes are deliberately not
  used, because they are computed from dividends paid later.
- **Behaviour under real money:** a simulator cannot reproduce the emotions of risking real
  savings, and pausing time removes the pressure of live markets.
- **Margin, short selling, options, interest on cash, account fees and minimums.**

Results from this simulator, including the trend bot's, say nothing reliable about future returns.
