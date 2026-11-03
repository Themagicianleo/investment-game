"use strict";

// trade-sim web front end. All market data comes from the local API, which only
// ever returns rows up to the session's current day.

const $ = (id) => document.getElementById(id);
const fmtUSD = (x) => (x < 0 ? "-$" : "$") + Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtPct = (x, digits = 2) => (x == null ? "n/a" : (x >= 0 ? "+" : "") + (x * 100).toFixed(digits) + "%");
const cls = (x) => (x == null || x === 0 ? "" : x > 0 ? "up" : "down");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const ui = {
  state: null,
  ticker: null,
  side: "buy",
  rangeDays: 252,
  history: null,
  busy: false,
};

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};

// --- API -------------------------------------------------------------------
async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    const err = new Error((data && data.error) || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

let toastTimer;
function showError(msg) {
  const el = $("error");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 7000);
}
$("error").addEventListener("click", () => ($("error").hidden = true));

async function run(fn) {
  if (ui.busy) return;
  ui.busy = true;
  document.body.style.cursor = "progress";
  try { await fn(); } catch (e) { showError(e.message); } finally {
    ui.busy = false;
    document.body.style.cursor = "";
  }
}

// --- views -----------------------------------------------------------------
function show(view) {
  for (const v of ["start-view", "game-view", "report-view"]) $(v).hidden = v !== view;
  const inGame = view === "game-view";
  $("end-btn").hidden = !inGame;
  $("date-pill").hidden = view === "start-view";
}

// --- rendering -------------------------------------------------------------
function render(state) {
  ui.state = state;
  if (!ui.ticker || !state.rules.tickers.includes(ui.ticker)) ui.ticker = state.rules.tickers[0];

  $("date-label").textContent = state.date;
  $("day-label").textContent = `day ${state.day}`;

  // tickers
  const alerts = new Set(state.positions.filter((p) => p.alert).map((p) => p.ticker));
  $("ticker-tabs").innerHTML = state.market.map((m) => `
    <button class="tick ${m.ticker === ui.ticker ? "on" : ""}" data-ticker="${esc(m.ticker)}">
      <div class="sym">${esc(m.ticker)} ${alerts.has(m.ticker) ? '<span class="badge">LOSS LIMIT</span>' : ""}</div>
      <div class="px">${fmtUSD(m.close)}</div>
      <div class="chg"><span class="${cls(m.change_1d)}">${fmtPct(m.change_1d)}</span>
        <span class="muted">· 20d</span> <span class="${cls(m.change_20d)}">${fmtPct(m.change_20d, 1)}</span></div>
    </button>`).join("");

  // order ticker select
  const sel = $("order-ticker");
  if (sel.options.length !== state.rules.tickers.length) {
    sel.innerHTML = state.rules.tickers.map((t) => `<option>${esc(t)}</option>`).join("");
  }
  sel.value = ui.ticker;
  updateOrderForm();

  // account
  $("equity").textContent = fmtUSD(state.equity);
  $("return").innerHTML = `<span class="${cls(state.return)}">${fmtPct(state.return)}</span> <span class="muted small">since ${esc(state.start_date)}</span>`;
  $("cash").textContent = fmtUSD(state.cash);
  $("avail").textContent = fmtUSD(state.available_cash);
  $("holdings").textContent = fmtUSD(state.holdings);
  for (const b of document.querySelectorAll("[data-hold]")) b.disabled = !state.has_next;

  // positions
  $("positions").innerHTML = state.positions.length ? state.positions.map((p) => `
    <div class="pos ${p.alert ? "alert" : ""}">
      <div class="line"><strong>${esc(p.ticker)}</strong><strong>${fmtUSD(p.value)}</strong></div>
      <div class="line small muted"><span>${+p.shares.toFixed(6)} sh · avg ${fmtUSD(p.avg_cost)} · close ${fmtUSD(p.close)}</span>
        <span class="${cls(p.pl)}">${fmtPct(p.pl)}</span></div>
      ${p.alert ? `<div class="small down">${(state.rules.loss_limit * 100).toFixed(0)}% loss limit reached. Nothing is sold automatically.</div>` : ""}
    </div>`).join("") : '<div class="muted">No positions. You are all in cash.</div>';

  // pending
  $("pending-wrap").hidden = !state.pending.length;
  $("pending-list").innerHTML = state.pending.map((o) => `
    <li><strong class="${o.side === "buy" ? "up" : "down"}">${o.side.toUpperCase()}</strong> ${esc(o.ticker)}
      ${o.amount === "all" ? "all shares" : fmtUSD(o.amount)}
      <div class="small muted">${esc(o.reason)}</div></li>`).join("");

  // costs
  const c = state.costs, r = state.rules;
  $("costs").innerHTML = `
    <dt>Flat fees</dt><dd>${fmtUSD(c.fees)}</dd>
    <dt>FX spread</dt><dd>${fmtUSD(c.fx)}</dd>
    <dt>Spread + slippage</dt><dd>${fmtUSD(c.spread_slippage)}</dd>
    <dt>Dividend tax withheld</dt><dd>${fmtUSD(c.dividend_tax)}</dd>
    <dt>Net dividends received</dt><dd>${fmtUSD(c.dividends_net)}</dd>`;
  $("rules-text").textContent =
    `Fills: next open ± ${((r.half_spread + r.slippage) * 100).toFixed(2)}% (half spread + slippage), ` +
    `$${r.fee.toFixed(2)} per order. FX ${(r.fx_spread * 100).toFixed(2)}% (${r.fx_mode === "per_buy" ? "on every buy" : r.fx_mode === "deposit" ? "on the initial deposit" : "off"}). ` +
    `Dividend withholding ${(r.withholding * 100).toFixed(0)}%. ${r.fractional ? "Fractional shares on." : "Whole shares only (rounded down)."}`;

  // log
  $("log").innerHTML = state.log.slice().reverse().map((e) =>
    `<li class="${esc(e.type)}"><span class="d">${esc(e.date)}</span>${esc(e.text)}</li>`).join("");

  // journal
  $("journal").innerHTML = state.journal.length ? state.journal.slice().reverse().map((e) => `
    <div class="entry">
      <div class="head">${esc(e.fill_date)} · ${e.status === "filled"
        ? `<span class="${e.action === "buy" ? "up" : "down"}">${e.action.toUpperCase()}</span> ${+e.shares} ${esc(e.ticker)} @ ${fmtUSD(+e.fill_price)} <span class="muted small">fee ${fmtUSD(+e.fee_usd)}</span>`
        : `<span class="down">NOT FILLED</span> ${esc(e.action)} ${esc(e.ticker)} <span class="muted small">${esc(e.note)}</span>`}</div>
      <div class="q">Reason: ${esc(e.reason)}</div>
      <div class="q">Would prove me wrong: ${esc(e.prove_wrong)}</div>
    </div>`).join("") : '<div class="muted">No trades yet. Each trade you place is recorded here with your reasoning.</div>';

  drawEquity(state.equity_curve);
}

function updateOrderForm() {
  const s = ui.state;
  if (!s) return;
  const isSell = ui.side === "sell";
  for (const b of document.querySelectorAll("#side-seg button")) b.classList.toggle("on", b.dataset.side === ui.side);
  $("sell-all-wrap").hidden = !isSell;
  if (!isSell) $("sell-all").checked = false;
  $("order-amount").disabled = isSell && $("sell-all").checked;
  const t = $("order-ticker").value;
  const pos = s.positions.find((p) => p.ticker === t);
  const price = (s.market.find((m) => m.ticker === t) || {}).close;
  $("order-btn").textContent = `Queue ${ui.side} order`;
  $("order-btn").disabled = !s.has_next;
  $("order-hint").textContent = isSell
    ? (pos ? `You hold ${+pos.shares.toFixed(6)} ${t} (≈ ${fmtUSD(pos.value)} at today's close).` : `You hold no ${t}.`)
    : `Available: ${fmtUSD(s.available_cash)}. The amount includes the $${s.rules.fee.toFixed(2)} fee. Today's close ${fmtUSD(price)}. The fill will be at tomorrow's open, which you can't see yet.`;
}

// --- charts ----------------------------------------------------------------
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function setupCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

function niceTicks(min, max, n = 4) {
  const span = max - min || 1;
  const step0 = span / n;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0) || step0;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(v);
  return ticks;
}

let chartGeom = null;

function drawPrice() {
  const h = ui.history, canvas = $("price-chart");
  const { ctx, w, h: H } = setupCanvas(canvas);
  chartGeom = null;
  if (!h || !h.dates.length) return;
  const n = ui.rangeDays ? Math.min(ui.rangeDays, h.dates.length) : h.dates.length;
  const dates = h.dates.slice(-n), ys = h.adj_close.slice(-n);
  const pos = ui.state.positions.find((p) => p.ticker === h.ticker);
  let min = Math.min(...ys), max = Math.max(...ys);
  if (pos) { min = Math.min(min, pos.avg_cost); max = Math.max(max, pos.avg_cost); }
  const pad = (max - min) * 0.06 || 1;
  min -= pad; max += pad;

  const L = 8, R = 58, T = 10, B = 22;
  const x = (i) => L + (n === 1 ? 0 : (i / (n - 1)) * (w - L - R));
  const y = (v) => T + (1 - (v - min) / (max - min)) * (H - T - B);

  ctx.font = "11px system-ui, sans-serif";
  ctx.fillStyle = cssVar("--muted");
  ctx.strokeStyle = cssVar("--chart-grid");
  ctx.lineWidth = 1;
  for (const t of niceTicks(min, max)) {
    ctx.beginPath(); ctx.moveTo(L, y(t)); ctx.lineTo(w - R, y(t)); ctx.stroke();
    ctx.fillText(t >= 1000 ? t.toFixed(0) : t.toFixed(t < 10 ? 2 : 1), w - R + 6, y(t) + 4);
  }
  // date labels: first, middle, last
  ctx.textAlign = "left"; ctx.fillText(dates[0], L, H - 6);
  ctx.textAlign = "right"; ctx.fillText(dates[n - 1], w - R, H - 6);
  if (n > 40) { ctx.textAlign = "center"; ctx.fillText(dates[Math.floor(n / 2)], x(Math.floor(n / 2)), H - 6); }
  ctx.textAlign = "left";

  // area + line
  ctx.beginPath();
  ys.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
  ctx.lineTo(x(n - 1), H - B); ctx.lineTo(x(0), H - B); ctx.closePath();
  ctx.fillStyle = cssVar("--chart-fill"); ctx.fill();
  ctx.beginPath();
  ys.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
  ctx.strokeStyle = cssVar("--chart-line"); ctx.lineWidth = 1.75; ctx.lineJoin = "round"; ctx.stroke();

  if (pos) {
    ctx.setLineDash([5, 4]); ctx.strokeStyle = cssVar("--cost-line"); ctx.lineWidth = 1.25;
    ctx.beginPath(); ctx.moveTo(L, y(pos.avg_cost)); ctx.lineTo(w - R, y(pos.avg_cost)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = cssVar("--cost-line");
    ctx.fillText("avg cost", L + 4, y(pos.avg_cost) - 4);
  }
  // today marker
  ctx.beginPath(); ctx.arc(x(n - 1), y(ys[n - 1]), 3.5, 0, Math.PI * 2);
  ctx.fillStyle = cssVar("--chart-line"); ctx.fill();

  chartGeom = { x, y, n, dates, ys, closes: h.close.slice(-n), L, R, w };
}

function chartHover(ev) {
  const tip = $("chart-tip");
  if (!chartGeom) { tip.hidden = true; return; }
  const rect = ev.target.getBoundingClientRect();
  const mx = ev.clientX - rect.left;
  const g = chartGeom;
  const i = Math.max(0, Math.min(g.n - 1, Math.round(((mx - g.L) / (g.w - g.L - g.R)) * (g.n - 1))));
  tip.innerHTML = `<strong>${esc(g.dates[i])}</strong><br>close ${fmtUSD(g.closes[i])}` +
    (Math.abs(g.closes[i] - g.ys[i]) > 1e-6 ? `<br><span class="muted">split-adj. ${fmtUSD(g.ys[i])}</span>` : "");
  tip.hidden = false;
  const left = Math.min(Math.max(g.x(i) + 10, 0), rect.width - tip.offsetWidth - 4);
  tip.style.left = left + "px";
  tip.style.top = Math.max(0, g.y(g.ys[i]) - 46) + "px";
}

function drawEquity(curve) {
  const { ctx, w, h } = setupCanvas($("equity-chart"));
  if (!curve || curve.length < 2) {
    ctx.fillStyle = cssVar("--muted"); ctx.font = "11px system-ui, sans-serif";
    ctx.fillText("Your account value will be charted here.", 0, h / 2 + 4);
    return;
  }
  const vals = curve.map((p) => p[1]);
  const start = ui.state.starting_cash;
  const min = Math.min(...vals, start), max = Math.max(...vals, start);
  const x = (i) => (i / (vals.length - 1)) * (w - 2) + 1;
  const y = (v) => 3 + (1 - (v - min) / (max - min || 1)) * (h - 6);
  ctx.setLineDash([3, 3]); ctx.strokeStyle = cssVar("--chart-grid"); ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, y(start)); ctx.lineTo(w, y(start)); ctx.stroke(); ctx.setLineDash([]);
  ctx.beginPath(); vals.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
  ctx.strokeStyle = vals[vals.length - 1] >= start ? cssVar("--up") : cssVar("--down");
  ctx.lineWidth = 1.5; ctx.stroke();
}

async function loadHistory() {
  const s = ui.state;
  if (!s) return;
  const h = await api(`/api/sessions/${s.id}/history/${encodeURIComponent(ui.ticker)}`);
  ui.history = h;
  const m = s.market.find((x) => x.ticker === ui.ticker);
  $("chart-title").textContent = `${ui.ticker}  ${fmtUSD(m.close)}`;
  $("chart-sub").innerHTML = `Today (${esc(s.date)}): open ${fmtUSD(m.open)} · high ${fmtUSD(m.high)} · low ${fmtUSD(m.low)} · ` +
    `<span class="${cls(m.change_1d)}">${fmtPct(m.change_1d)}</span>`;
  drawPrice();
}

// --- report ----------------------------------------------------------------
function renderReport(rep) {
  const u = rep.user;
  $("report-period").textContent = `${rep.start} to ${rep.end} · ${rep.trading_days} trading days`;
  const stat = (l, v, c = "") => `<div class="stat"><div class="l">${l}</div><div class="v ${c}">${v}</div></div>`;
  $("report-user").innerHTML =
    stat("Final value", fmtUSD(u.final_value)) +
    stat("Your return", fmtPct(u.return), cls(u.return)) +
    stat("Orders filled", u.orders) +
    stat("Flat fees", fmtUSD(u.fees)) +
    stat("FX spread", fmtUSD(u.fx)) +
    stat("Spread + slippage", fmtUSD(u.spread_slippage)) +
    stat("Dividend tax", fmtUSD(u.dividend_tax)) +
    stat("Total fees + taxes", fmtUSD(u.total_fees_and_taxes));

  const head = "<tr><th></th><th>Return</th><th>Final value</th><th>Trades</th><th>Costs</th></tr>";
  const row = (b, extra = "") => `<tr class="${extra}"><td>${esc(b.name)}</td><td class="${cls(b.return)}">${fmtPct(b.return)}</td>
    <td>${fmtUSD(b.final_value)}</td><td>${b.trades}</td><td>${fmtUSD(b.costs)}</td></tr>`;
  const you = { name: "You", return: u.return, final_value: u.final_value, trades: u.orders,
    costs: u.fees + u.fx + u.spread_slippage };
  $("report-bench").innerHTML = head + row(you, "you") + (rep.trend_bot ? row(rep.trend_bot) : "") + (rep.benchmark ? row(rep.benchmark) : "");
  $("report-bot-rule").textContent = rep.trend_bot ? `Trend bot: ${rep.trend_bot_rule} It is a benchmark that follows past trends, not advice.` : "";
  $("report-tickers").innerHTML = head + rep.tickers.map((b) => row(b)).join("");

  const wf = rep.walk_forward;
  $("report-wf-card").hidden = !wf && !rep.walk_forward_skipped;
  $("report-wf").innerHTML = wf ? `
    <table class="tbl">
      <tr><th></th><th>In-sample<br><span class="small">${esc(wf.train.start)} – ${esc(wf.train.end)} (${wf.train.days} d)</span></th>
          <th>Out-of-sample<br><span class="small">${esc(wf.test.start)} – ${esc(wf.test.end)} (${wf.test.days} d)</span></th></tr>
      <tr><td>Chosen on in-sample: ${esc(wf.chosen_rule)}</td><td class="${cls(wf.chosen_in_sample)}">${fmtPct(wf.chosen_in_sample)}</td>
          <td class="${cls(wf.chosen_out_of_sample)}">${fmtPct(wf.chosen_out_of_sample)}</td></tr>
      <tr><td>Default ${esc(wf.default_rule)}</td><td class="${cls(wf.default_in_sample)}">${fmtPct(wf.default_in_sample)}</td>
          <td class="${cls(wf.default_out_of_sample)}">${fmtPct(wf.default_out_of_sample)}</td></tr>
    </table>
    <p class="small">The moving-average lengths were chosen using only the first part of the period. ${esc(wf.caveat)}</p>`
    : `<p class="muted">Skipped: ${esc(rep.walk_forward_skipped)}</p>`;

  $("report-review").innerHTML = rep.review.length ? rep.review.map((f) =>
    `<div class="finding"><strong>${esc(f.title)}</strong> <span class="muted">× ${f.count}</span><div class="small">${esc(f.detail)}</div></div>`).join("")
    : '<p class="muted">No common patterns were flagged in this session\'s journal.</p>';
  $("report-notices").innerHTML = rep.notices.map((n) => `<li>${esc(n)}</li>`).join("");
}

// --- actions ---------------------------------------------------------------
async function enterGame(state) {
  store.set("trade-sim-session", state.id);
  show("game-view");
  render(state);
  await loadHistory();
}

$("start-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  run(async () => {
    $("start-btn").disabled = true;
    $("start-status").textContent = "Loading market data… the first run downloads from Yahoo Finance and can take a minute.";
    try {
      const state = await api("/api/sessions", { method: "POST", body: { seed: $("seed").value.trim() } });
      await enterGame(state);
    } finally {
      $("start-btn").disabled = false;
      $("start-status").textContent = "";
    }
  });
});

$("ticker-tabs").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-ticker]");
  if (!b) return;
  ui.ticker = b.dataset.ticker;
  run(async () => { render(ui.state); await loadHistory(); });
});

$("range-seg").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  ui.rangeDays = +b.dataset.days;
  for (const x of $("range-seg").children) x.classList.toggle("on", x === b);
  drawPrice();
});

$("side-seg").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  ui.side = b.dataset.side;
  updateOrderForm();
});
$("sell-all").addEventListener("change", updateOrderForm);
$("order-ticker").addEventListener("change", () => {
  ui.ticker = $("order-ticker").value;
  run(async () => { render(ui.state); await loadHistory(); });
});

$("order-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  run(async () => {
    const all = ui.side === "sell" && $("sell-all").checked;
    const state = await api(`/api/sessions/${ui.state.id}/orders`, {
      method: "POST",
      body: {
        side: ui.side, ticker: $("order-ticker").value,
        amount: all ? "all" : $("order-amount").value,
        reason: $("order-reason").value, prove_wrong: $("order-wrong").value,
      },
    });
    $("order-amount").value = ""; $("order-reason").value = ""; $("order-wrong").value = ""; $("sell-all").checked = false;
    render(state);
  });
});

$("cancel-btn").addEventListener("click", () => run(async () => {
  render(await api(`/api/sessions/${ui.state.id}/cancel`, { method: "POST" }));
}));

for (const b of document.querySelectorAll("[data-hold]")) {
  b.addEventListener("click", () => run(async () => {
    const state = await api(`/api/sessions/${ui.state.id}/hold`, { method: "POST", body: { days: +b.dataset.hold } });
    render(state);
    await loadHistory();
    const alert = state.new_events.find((e) => e.type === "alert");
    if (alert) showError(alert.text);
    else if (state.stopped && !state.has_next) showError(state.stopped);
  }));
}

$("end-btn").addEventListener("click", () => {
  if (!confirm("End this session and see the report? Pending orders will be cancelled.")) return;
  run(async () => {
    const { report } = await api(`/api/sessions/${ui.state.id}/end`, { method: "POST" });
    store.del("trade-sim-session");
    renderReport(report);
    show("report-view");
    window.scrollTo(0, 0);
  });
});

$("new-btn").addEventListener("click", () => { show("start-view"); ui.state = null; ui.history = null; });

$("price-chart").addEventListener("mousemove", chartHover);
$("price-chart").addEventListener("mouseleave", () => ($("chart-tip").hidden = true));

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (ui.state) { drawPrice(); drawEquity(ui.state.equity_curve); } }, 120);
});

// keyboard: "n" = next day (when not typing)
document.addEventListener("keydown", (ev) => {
  if (ev.key !== "n" || ev.target.closest("input, textarea, select") || $("game-view").hidden) return;
  document.querySelector('[data-hold="1"]').click();
});

// --- boot --------------------------------------------------------------------
(async function boot() {
  try {
    const info = await api("/api/info");
    $("start-cash").textContent = fmtUSD(info.starting_cash);
  } catch { /* server may be starting */ }
  const sid = store.get("trade-sim-session");
  if (sid) {
    try {
      const state = await api(`/api/sessions/${sid}`);
      if (!state.ended) { await enterGame(state); return; }
    } catch { store.del("trade-sim-session"); }
  }
  show("start-view");
})();
