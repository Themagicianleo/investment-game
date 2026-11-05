# trade-sim

An educational trading game for practising **buy, hold and sell decisions** on US stocks and ETFs
by replaying real daily history one trading day at a time.

**Play it in your browser: https://themagicianleo.github.io/investment-game/**

It runs entirely in your browser (hosted on GitHub Pages). Progress, your journal and any session
in progress are stored only on your device. There is also a Python command-line version and a
local server version (see [Python version](#python-version-cli-and-local-server)).

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

## The web app (GitHub Pages)

The site in `web/` is a static TypeScript + Vite app. GitHub Actions builds it and deploys it to
GitHub Pages; there is no server.

| Piece | What it does |
|---|---|
| `web/src/engine/` | The simulation engine, ported from the Python package: replay, fills and costs, dividends and splits, alerts, trend bot, walk-forward, coach, missions, process score and report |
| `web/src/api.ts` | The same JSON API as the Python server (`trade_sim/web.py`), but running in the browser |
| `web/src/ui/app.js`, `web/index.html`, `web/src/styles.css` | The UI. It uses the same Liquid Glass design as the MSFS Buddy EFB (`web/src/glass.css` is copied from it): a floating tab bar, large titles, frosted glass cards, light/dark appearance and a Reduce transparency setting. It can be added to the iPhone/iPad home screen as an app. |
| `web/src/content/*.json` | Lessons, glossary, scenarios, missions and default settings, exported from the Python package by `scripts/export_web_content.py` |
| `web/public/data/market/*.csv` | Daily prices, dividends and splits, refreshed weekly by a workflow |
| `.github/workflows/deploy.yml` | On every push to `main`: runs both test suites, builds, deploys to Pages |
| `.github/workflows/update-market-data.yml` | Weekly (and on demand): downloads data from Yahoo Finance with `scripts/fetch_market_data.py`, commits it if it changed, and redeploys |

**One-time setup on GitHub:**
1. Repository **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. **Actions → Update market data → Run workflow**, to publish the first data set. Until it has
   run, the practice market works but the real-data modes say the data hasn't been published yet.

**Keeping the two engines identical:** `scripts/make_golden.py` runs scripted sessions through the
Python engine and saves the results to `web/tests/golden/`. `npm test` then checks that the
TypeScript engine reproduces every fill, fee, dividend, split, alert, tip, mission and report
figure. `pytest` fails if the Python engine changes without the fixtures being regenerated.
After changing lessons or other content in Python, run `python scripts/export_web_content.py`.

**Working on the site locally** (Node.js 18+):

```bash
cd web
npm install
npm run dev        # http://localhost:5173 with hot reload
npm test           # engine, API and golden tests
npm start          # production build, opened at http://localhost:4173
```

Real-data modes need `web/public/data/market/*.csv`. Run `python scripts/fetch_market_data.py`
once to create them locally. The practice market needs no data.

**In the browser, data stays in the browser.** XP, lesson results, missions, session history and
`journal.csv` live in `localStorage`. The journal can be downloaded from the Journal card, and
Progress → *Reset progress and journal* clears everything. An unfinished session is saved as its
start request plus your actions, and rebuilt by replaying them when you reload the page.

## Python version (CLI and local server)

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

### The learning game (web app and local server)

The hosted site and `trade-sim web --open` (a local server at http://127.0.0.1:8000) show the
same app. It has four tabs:

- **Play:** choose a game mode, then trade day by day (details below).
- **Learn:** 11 short lessons, each about 4–5 minutes and ending in a three-question quiz.
  Pass with 2 of 3 to earn XP.
- **Glossary:** about 40 plain-English definitions. Dotted terms anywhere in the app show the
  definition when you hover or tap them.
- **Progress:** your level, XP, missions earned, lessons passed, and a history of every session
  with its return, benchmark, max drawdown and process score.

#### Game modes

| Mode | Data | What it's for |
|---|---|---|
| **Practice market** | Fictional companies with randomly generated prices (works offline) | Learning the mechanics. Each game is different and includes bull runs, crashes, a bubble, dividends and a split. |
| **Random history** | Real daily data from a random past date | Making decisions without knowing what comes next. |
| **Historical scenarios** | Real data from a fixed period: the dot-com era (2000), the financial crisis (2007), the 2020 pandemic shock, the 2022 rate-hike bear market | Practising discipline in a famous episode. A briefing describes the situation at the time without spoilers; a debrief after the session explains what happened. |

#### What teaches you while you play

- **Order ticket preview.** Before you place an order it estimates shares, total cost, trading
  costs as a %, the rise needed just to break even, the position's weight after the trade, and
  what a 20% fall in that ticker would cost your whole portfolio.
- **Coach.** It flags moments when well-known mistakes tend to happen: buying after a sharp rise,
  selling after a sharp drop, adding to a position past the loss limit, quick round trips,
  small orders eaten by fees, and concentration in a single stock. It also explains events as
  they happen: your first fill versus the open, ex-dividend dates and withholding tax, splits,
  loss-limit alerts and drawdown thresholds. Every tip links to the relevant lesson. The coach
  never says what to buy or sell.
- **Position review.** After a loss-limit alert (or any time), "Review" lets you write down
  whether your thesis still holds instead of trading. It goes in the journal and counts as a
  disciplined response.
- **Process score (0–100).** It rates habits, not profit, across five areas: written reasoning,
  not reacting to big moves, responding to loss limits, position sizing (broad index funds are
  not counted as concentration), and keeping costs down.
- **Missions:** nine goals that reward good process, such as "Plan your exit", "Spread your
  risk", "Face the alert", "Patience" and "Disciplined", each worth XP.
- **Chart.** Optional 50- and 200-day moving-average overlays and markers for your own buys and
  sells, all computed only from data up to today.
- **End-of-session report.** Everything in the CLI report plus max drawdown for you and every
  benchmark, the process-score breakdown, missions completed, XP earned, the scenario debrief and
  up to three suggested lessons based on what happened.

The local server version uses the Python engine, `config.yaml`, the data cache and `journal.csv`
like the CLI, and saves progress in `progress.json` (git-ignored). The browser only receives data up to the session's
current day, which `tests/test_web.py` checks. The page is plain HTML/CSS/JS with no build step
and no CDN.

Options: `--port 8000`, `--host 127.0.0.1`, `--open` (opens your browser), `--no-bot`,
`--refresh`. It listens only on localhost by default. Sessions live in the server's memory, so
restarting the server ends any open session; journal entries and progress are already on disk.
If Yahoo Finance can't be reached, the server still starts, and the practice market works.

**Opening it from your phone:** run `trade-sim web --host 0.0.0.0` and open
`http://<your-computer's-LAN-IP>:8000` on a device on the same Wi-Fi. There is no login, so
only do this on a network you trust.

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
| `data.progress_path` | progress.json | web app XP, lessons and session history |

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
pytest                     # Python engine, CLI, local server, content and golden fixtures
cd web && npm test         # TypeScript engine (golden comparison), browser API, practice market
```

The tests use synthetic data and need no network. They cover fees and FX, the spread and
slippage calculation, whole-share and fractional rounding, no-lookahead data access and next-open
fills, dividend withholding, splits, the loss-limit alert, the walk-forward split (including that
the parameter choice ignores test-period data), caching and network-error messages, the journal
a full scripted session, the web API (including
that no future dates reach the browser), and the education features: glossary links, quiz
grading with answers kept on the server, coach tips, the process score, missions, the practice
market, progress/XP, and a check that no lesson text tells you to buy or sell.

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
- **The practice market is not a model of any real market.** Its regimes, volatility and
  events are made up to exercise the game's features. Use it to learn the mechanics, not to
  draw conclusions about real securities.
- **Lessons, the coach and the process score simplify.** They teach widely discussed concepts.
  They are not personalised financial advice, and a high process score does not mean a strategy
  will make money.

Results from this simulator, including the trend bot's, say nothing reliable about future returns.
