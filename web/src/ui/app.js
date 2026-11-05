// trade-sim UI (shared with the Python server version; here backed by the in-browser LocalApi).

// All market data comes from the local API, which only returns rows up to the session's current day.
import { localApi } from "../main-api";

const $ = (id) => document.getElementById(id);
const fmtUSD = (x) => (x == null ? "—" : (x < 0 ? "-$" : "$") + Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const fmtPct = (x, digits = 2) => (x == null ? "n/a" : (x >= 0 ? "+" : "") + (x * 100).toFixed(digits) + "%");
const pct = (x, digits = 1) => (x == null ? "n/a" : (x * 100).toFixed(digits) + "%");
const cls = (x) => (x == null || Math.abs(x) < 1e-12 ? "" : x > 0 ? "up" : "down");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Inline markup used in lessons and tips: **bold** and [[glossary term]].
function inline(text) {
  return esc(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\[\[(.+?)\]\]/g, (_, t) => `<span class="term" tabindex="0" data-term="${t.toLowerCase()}">${t}</span>`);
}

const ui = {
  edu: null,
  state: null,
  report: null,
  ticker: null,
  side: "buy",
  rangeDays: 252,
  history: null,
  reviewing: null,
  busy: false,
  lastPreviewKey: "",
};

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};

// --- API -------------------------------------------------------------------
// The in-progress session is saved as its creation request plus the actions taken, and
// rebuilt by replaying them after a reload (the engine is deterministic).
const REPLAY_KEY = "trade-sim-replay";
const loadReplay = () => { try { return JSON.parse(store.get(REPLAY_KEY) || "null"); } catch { return null; } };
const saveReplay = (log) => store.set(REPLAY_KEY, JSON.stringify(log));

async function api(path, opts = {}) {
  const method = opts.method || "GET";
  const body = opts.body || {};
  const data = await localApi.handle(method, path, body);
  if (method === "POST") {
    let m;
    if (path === "/api/sessions") {
      saveReplay({ create: { ...body, seed: data.seed, start_date: data.date, xp_start: data.xp_start }, actions: [] });
    } else if ((m = path.match(/^\/api\/sessions\/\w+(\/(orders|review|cancel|hold))$/))) {
      const log = loadReplay();
      if (log) { log.actions.push({ path: m[1], body }); saveReplay(log); }
    } else if (/\/end$/.test(path)) {
      store.del(REPLAY_KEY);
    }
  }
  return data;
}

let toastTimer;
function toast(msg, kind = "error", ms = 7000) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast " + kind;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), ms);
}
$("toast").addEventListener("click", () => ($("toast").hidden = true));

async function run(fn) {
  if (ui.busy) return;
  ui.busy = true;
  document.body.style.cursor = "progress";
  try { await fn(); } catch (e) { toast(e.message); } finally {
    ui.busy = false;
    document.body.style.cursor = "";
  }
}

// --- glossary tooltips -----------------------------------------------------
const tipEl = $("term-tip");
let tipFor = null;
function showTerm(el) {
  const term = el.dataset.term;
  const def = ui.edu && ui.edu.glossary[term];
  if (!def) return;
  tipFor = el;
  tipEl.innerHTML = `<strong>${esc(term)}</strong>${esc(def)}`;
  tipEl.hidden = false;
  const r = el.getBoundingClientRect();
  const w = tipEl.offsetWidth;
  const left = Math.max(8, Math.min(window.scrollX + r.left, window.scrollX + document.documentElement.clientWidth - w - 8));
  tipEl.style.left = left + "px";
  tipEl.style.top = window.scrollY + r.bottom + 6 + "px";
}
function hideTerm() { tipEl.hidden = true; tipFor = null; }
document.addEventListener("mouseover", (e) => { const t = e.target.closest(".term"); if (t) showTerm(t); });
document.addEventListener("mouseout", (e) => { if (e.target.closest(".term")) hideTerm(); });
document.addEventListener("focusin", (e) => { const t = e.target.closest(".term"); if (t) showTerm(t); });
document.addEventListener("focusout", (e) => { if (e.target.closest(".term")) hideTerm(); });
document.addEventListener("click", (e) => {
  const t = e.target.closest(".term");
  if (t) { e.preventDefault(); tipFor === t && !tipEl.hidden ? hideTerm() : showTerm(t); } else hideTerm();
});

// --- routing ---------------------------------------------------------------
const VIEWS = ["view-start", "view-game", "view-report", "view-learn", "view-lesson", "view-glossary", "view-progress", "view-settings"];
const TAB_FOR = { play: "play", learn: "learn", progress: "progress", glossary: "more", settings: "more" };

/** Large title and subtitle, as in the EFB. */
function setTitle(title, subtitle = "") {
  $("page-title").textContent = title;
  $("page-subtitle").textContent = subtitle;
  document.title = title === "Play" ? "Trade Sim" : `${title} · Trade Sim`;
}

function show(view) {
  for (const v of VIEWS) $(v).hidden = v !== view;
  const inGame = view === "view-game";
  $("date-capsule").hidden = !inGame;
  $("end-btn").hidden = !inGame;
  $("wrap").classList.toggle("wide", inGame);
  hideTerm();
  closeMore();
  $("toast").hidden = true;
}

function routeName() {
  return (location.hash.replace(/^#\/?/, "") || "play").split("/");
}

function selectTab(page) {
  const tab = TAB_FOR[page] || "play";
  for (const b of document.querySelectorAll(".tabbar button")) {
    b.setAttribute("aria-selected", String((b.dataset.route || "more") === tab));
  }
  for (const a of document.querySelectorAll(".menu-row[data-route]")) {
    if (a.dataset.route === page) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
}

function route() {
  const parts = routeName();
  const page = parts[0];
  selectTab(page);
  if (page === "learn" && parts[1]) return renderLesson(parts[1]);
  if (page === "learn") return renderLearn();
  if (page === "glossary") return renderGlossary();
  if (page === "progress") return renderProgress();
  if (page === "settings") return renderSettings();
  // play
  if (ui.state && !ui.state.ended) {
    setTitle("Play", gameSubtitle(ui.state));
    show("view-game");
    if (!ui.history || ui.history.as_of !== ui.state.date) run(loadHistory);
    requestAnimationFrame(() => { drawPrice(); drawEquity(ui.state.equity_curve); });
  }
  else if (ui.report) { setTitle("Session report", reportSubtitle(ui.report)); show("view-report"); }
  else { renderStart(); setTitle("Play", "Practise buy, hold and sell decisions on real market history"); show("view-start"); }
}

function gameSubtitle(st) {
  const mode = st.mode === "practice" ? "Practice market (fictional prices)"
    : st.mode === "scenario" ? st.scenario.title : "Random history";
  return `${mode} · started ${st.start_date}`;
}

function reportSubtitle(rep) {
  return `${rep.start} to ${rep.end} · ${rep.trading_days} trading days` + (rep.mode === "practice" ? " · practice market" : "");
}

// --- More menu ---------------------------------------------------------------
function closeMore() {
  $("more-menu").hidden = true;
  $("more-btn").setAttribute("aria-expanded", "false");
  selectTab(routeName()[0]);
}
$("more-btn").addEventListener("click", () => {
  const open = $("more-menu").hidden;
  $("more-menu").hidden = !open;
  $("more-btn").setAttribute("aria-expanded", String(open));
  if (open) for (const b of document.querySelectorAll(".tabbar button")) b.setAttribute("aria-selected", String(b.id === "more-btn"));
  else selectTab(routeName()[0]);
});
$("more-backdrop").addEventListener("click", closeMore);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("more-menu").hidden) closeMore(); });
for (const b of document.querySelectorAll(".tabbar button[data-route]")) {
  b.addEventListener("click", () => {
    const target = "#/" + b.dataset.route;
    if (location.hash === target || (!location.hash && b.dataset.route === "play")) { route(); window.scrollTo({ top: 0, behavior: "smooth" }); }
    else location.hash = target;
  });
}
for (const a of document.querySelectorAll(".menu-row")) a.addEventListener("click", () => { $("more-menu").hidden = true; });

// --- settings: appearance and reduce transparency (same options as the EFB) ---------
const SETTINGS_KEY = "trade-sim-settings";
function loadSettings() {
  try { return { appearance: "system", reduceTransparency: false, ...JSON.parse(store.get(SETTINGS_KEY) || "{}") }; }
  catch { return { appearance: "system", reduceTransparency: false }; }
}
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
function applySettings() {
  const st = loadSettings();
  const theme = st.appearance === "system" ? (darkQuery.matches ? "dark" : "light") : st.appearance;
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]').setAttribute("content", theme === "dark" ? "#000000" : "#f2f2f7");
  document.body.classList.toggle("solid", !!st.reduceTransparency);
  for (const b of document.querySelectorAll("#appearance-seg button")) {
    b.setAttribute("aria-pressed", String(b.dataset.appearance === st.appearance));
  }
  $("reduce-transparency").checked = !!st.reduceTransparency;
  if (ui.state) { drawPrice(); drawEquity(ui.state.equity_curve); }
}
function saveSettings(patch) {
  store.set(SETTINGS_KEY, JSON.stringify({ ...loadSettings(), ...patch }));
  applySettings();
}
darkQuery.addEventListener("change", applySettings);
$("appearance-seg").addEventListener("click", (e) => {
  const b = e.target.closest("[data-appearance]");
  if (b) saveSettings({ appearance: b.dataset.appearance });
});
$("reduce-transparency").addEventListener("change", (e) => saveSettings({ reduceTransparency: e.target.checked }));

let manifestInfo = null;
async function renderSettings() {
  setTitle("Settings", "Display, your data and about");
  show("view-settings");
  applySettings();
  if (!manifestInfo) {
    try {
      const res = await fetch(`${import.meta.env.BASE_URL}data/market/manifest.json`, { cache: "no-cache" });
      manifestInfo = res.ok ? await res.json() : { missing: true };
    } catch { manifestInfo = { missing: true }; }
  }
  $("about-data").textContent = manifestInfo.missing ? "Not published yet (the practice market works without it)"
    : `Updated ${manifestInfo.updated} · ${Object.keys(manifestInfo.tickers).length} tickers`;
}
window.addEventListener("hashchange", () => { route(); window.scrollTo(0, 0); });

function lessonTitle(id) { const l = ui.edu.lessons.find((x) => x.id === id); return l ? l.title : id; }
function lessonLink(id, label) { return `<a class="more" href="#/learn/${esc(id)}">${esc(label || "Learn: " + lessonTitle(id))} →</a>`; }

function renderLevel(progress) {
  if (!progress) return;
  ui.edu.progress = progress;
  const lv = progress.level;
  $("level-pill").innerHTML = `<span class="lv">Lv ${lv.level}</span><span class="clock-caption lv-name">${esc(lv.name)}</span><span class="xp-num">${progress.xp} XP</span>`;
}

// --- start -----------------------------------------------------------------
function renderStart() {
  $("scenario-list").innerHTML = ui.edu.scenarios.map((s) => `
    <article class="section scenario">
      <div class="meta"><span class="pill accent">${esc(s.period)}</span><span class="pill ${s.difficulty === "Hard" ? "danger" : "warn"}">${esc(s.difficulty)}</span></div>
      <h3>${esc(s.title)}</h3>
      <p class="muted small">${esc(s.briefing.split(". ").slice(0, 2).join(". "))}.</p>
      <div class="small muted">${esc(s.tickers.join(" · "))}</div>
      <button class="btn btn-small" data-start="scenario" data-scenario="${esc(s.id)}" type="button">Play scenario</button>
    </article>`).join("");
  const passed = ui.edu.progress.lessons_passed;
  $("newbie-callout").hidden = passed >= 3;
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-start]");
  if (!b) return;
  const body = { mode: b.dataset.start };
  if (body.mode === "random") body.seed = $("seed").value.trim();
  if (body.mode === "scenario") body.scenario = b.dataset.scenario;
  run(async () => {
    $("start-status").textContent = body.mode === "practice" ? "Generating a practice market…"
      : "Loading market data… the first run downloads from Yahoo Finance and can take a minute.";
    try {
      const state = await api("/api/sessions", { method: "POST", body });
      ui.report = null;
      await enterGame(state);
    } catch (err) {
      if (err.status === 503 && body.mode !== "practice") {
        throw new Error(err.message + "\n\nThe practice market works without any downloads. Try that instead.");
      }
      throw err;
    } finally { $("start-status").textContent = ""; }
  });
});

// --- game rendering ----------------------------------------------------------
function render(state) {
  ui.state = state;
  if (!ui.ticker || !state.rules.tickers.includes(ui.ticker)) ui.ticker = state.rules.tickers[0];

  $("date-label").textContent = new Date(state.date + "T12:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  $("day-label").textContent = `Day ${state.day}`;
  if (!$("view-game").hidden) setTitle("Play", gameSubtitle(state));
  $("mode-chip").textContent = state.mode === "practice" ? "Practice market (fictional)"
    : state.mode === "scenario" ? `Scenario: ${state.scenario.title}` : "Random history";
  const banner = $("mode-banner");
  if (state.mode === "practice") {
    banner.className = "banner banner-warn";
    banner.innerHTML = "<strong>Practice market:</strong> these companies and prices are made up and randomly generated. " +
      "They behave like real markets (trends, crashes, dividends, splits) but say nothing about any real security.";
    banner.hidden = false;
  } else if (state.mode === "scenario") {
    banner.className = "banner banner-info";
    banner.innerHTML = `<strong>${esc(state.scenario.title)} · briefing:</strong> ${esc(state.scenario.briefing)}`;
    banner.hidden = false;
  } else banner.hidden = true;

  // tickers
  const alerts = new Set(state.positions.filter((p) => p.alert).map((p) => p.ticker));
  $("ticker-tabs").innerHTML = state.market.map((m) => `
    <button class="tick ${m.ticker === ui.ticker ? "on" : ""}" data-ticker="${esc(m.ticker)}" ${m.description ? `title="${esc(m.description)}"` : ""}>
      <div class="sym">${esc(m.ticker)} ${alerts.has(m.ticker) ? '<span class="badge">LOSS LIMIT</span>' : ""}</div>
      <div class="px">${fmtUSD(m.close)}</div>
      <div class="chg"><span class="${cls(m.change_1d)}">${fmtPct(m.change_1d)}</span>
        <span class="muted">· 20d</span> <span class="${cls(m.change_20d)}">${fmtPct(m.change_20d, 1)}</span></div>
    </button>`).join("");

  const sel = $("order-ticker");
  if ([...sel.options].map((o) => o.value).join() !== state.rules.tickers.join()) {
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
  $("dd").innerHTML = `<span class="${state.drawdown > 0.0001 ? "down" : ""}">${pct(-state.drawdown)}</span> / ${pct(-state.max_drawdown)}`;
  for (const b of document.querySelectorAll("[data-hold]")) b.disabled = !state.has_next;

  renderPositions(state);
  renderCoach(state);
  renderProcess(state.process, "process-score", "process");

  $("missions").innerHTML = state.missions.map((m) => `
    <li class="${m.done ? "done" : ""}"><span class="ck">${m.done ? "✓" : ""}</span>
      <div><div class="mt"><strong>${esc(m.title)}</strong></div><div class="small muted">${esc(m.desc)}</div></div>
      <span class="xp">+${m.xp} XP</span></li>`).join("");

  // pending
  $("pending-wrap").hidden = !state.pending.length;
  $("pending-list").innerHTML = state.pending.map((o) => `
    <li><strong class="${o.side === "buy" ? "up" : "down"}">${o.side.toUpperCase()}</strong> ${esc(o.ticker)}
      ${o.amount === "all" ? "all shares" : fmtUSD(o.amount)}
      <div class="small muted">${esc(o.reason)}</div></li>`).join("");

  // costs
  const c = state.costs, r = state.rules;
  $("costs").innerHTML = `
    <dt><span class="term" tabindex="0" data-term="commission">Flat fees</span></dt><dd>${fmtUSD(c.fees)}</dd>
    <dt><span class="term" tabindex="0" data-term="fx spread">FX spread</span></dt><dd>${fmtUSD(c.fx)}</dd>
    <dt><span class="term" tabindex="0" data-term="spread">Spread</span> + <span class="term" tabindex="0" data-term="slippage">slippage</span></dt><dd>${fmtUSD(c.spread_slippage)}</dd>
    <dt><span class="term" tabindex="0" data-term="withholding tax">Dividend tax withheld</span></dt><dd>${fmtUSD(c.dividend_tax)}</dd>
    <dt>Net dividends received</dt><dd>${fmtUSD(c.dividends_net)}</dd>`;
  $("rules-text").textContent =
    `Fills: next open ± ${((r.half_spread + r.slippage) * 100).toFixed(2)}% (half spread + slippage), ` +
    `$${r.fee.toFixed(2)} per order. FX ${(r.fx_spread * 100).toFixed(2)}% (${r.fx_mode === "per_buy" ? "on every buy" : r.fx_mode === "deposit" ? "on the initial deposit" : "off"}). ` +
    `Dividend withholding ${(r.withholding * 100).toFixed(0)}%. ${r.fractional ? "Fractional shares on." : "Whole shares only (rounded down)."}`;

  $("log").innerHTML = state.log.slice().reverse().map((e) =>
    `<li class="${esc(e.type)}"><span class="d">${esc(e.date)}</span>${esc(e.text)}</li>`).join("");

  $("journal").innerHTML = state.journal.length ? state.journal.slice().reverse().map((e) => {
    let head;
    if (e.status === "filled") head = `<span class="${e.action === "buy" ? "up" : "down"}">${e.action.toUpperCase()}</span> ${+e.shares} ${esc(e.ticker)} @ ${fmtUSD(+e.fill_price)} <span class="muted small">fee ${fmtUSD(+e.fee_usd)}</span>`;
    else if (e.status === "review") head = `REVIEW ${esc(e.ticker)} <span class="muted small">${esc(e.note)}</span>`;
    else head = `<span class="down">NOT FILLED</span> ${esc(e.action)} ${esc(e.ticker)} <span class="muted small">${esc(e.note)}</span>`;
    return `<div class="entry"><div class="head">${esc(e.fill_date)} · ${head}</div>
      <div class="q">${e.status === "review" ? "Note" : "Reason"}: ${esc(e.reason)}</div>
      ${e.status === "review" ? "" : `<div class="q">Would prove me wrong: ${esc(e.prove_wrong)}</div>`}</div>`;
  }).join("") : '<div class="muted">No trades yet. Each trade you place is recorded here with your reasoning.</div>';

  drawEquity(state.equity_curve);
}

function renderPositions(state) {
  const lim = (state.rules.loss_limit * 100).toFixed(0);
  $("positions").innerHTML = state.positions.length ? state.positions.map((p) => `
    <div class="pos ${p.alert ? "alert" : ""}">
      <div class="line"><strong>${esc(p.ticker)}</strong><strong>${fmtUSD(p.value)}</strong></div>
      <div class="line small muted"><span>${+p.shares.toFixed(6)} sh · avg ${fmtUSD(p.avg_cost)} · ${p.held_days}d held</span>
        <span class="${cls(p.pl)}">${fmtPct(p.pl)}</span></div>
      <div class="line small"><span class="muted">${pct(p.weight, 0)} of portfolio</span>
        <button class="btn btn-small" data-review="${esc(p.ticker)}" type="button">Review</button></div>
      <div class="weight"><i class="${p.weight > 0.4 ? "heavy" : ""}" style="width:${Math.min(100, p.weight * 100).toFixed(1)}%"></i></div>
      ${p.alert ? `<div class="small down">${lim}% loss limit reached. Nothing is sold automatically.</div>` : ""}
      ${ui.reviewing === p.ticker ? `<form class="review" data-review-form="${esc(p.ticker)}">
        <textarea rows="3" placeholder="Does your original reason still hold? Has your 'prove me wrong' condition happened?"></textarea>
        <div class="btn-row end"><button type="button" class="btn btn-small" data-review-cancel>Cancel</button>
        <button class="btn btn-primary btn-small">Save review</button></div></form>` : ""}
    </div>`).join("") : '<div class="muted">No positions. You are all in cash, which is also a position.</div>';
}

function tipHtml(t) {
  return `<div class="tip ${esc(t.kind)}"><div class="t">${esc(t.title)}${t.date ? `<span class="d">${esc(t.date)}</span>` : ""}</div>
    <div>${inline(t.text)}</div>${t.lesson ? lessonLink(t.lesson) : ""}</div>`;
}

function renderCoach(state) {
  $("coach").innerHTML = state.coach.length ? state.coach.slice().reverse().map(tipHtml).join("")
    : '<div class="muted">The coach will explain events as they happen.</div>';
}

function renderProcess(proc, badgeId, listId) {
  $(badgeId).textContent = proc.score == null ? "–" : proc.score;
  $(listId).innerHTML = proc.components.map((c) => {
    const frac = c.score == null ? null : c.score / c.max;
    const k = frac == null ? "" : frac >= 0.8 ? "" : frac >= 0.5 ? "mid" : "low";
    return `<div class="bar-row"><div class="top"><span>${esc(c.name)}</span>
      <span class="muted">${c.score == null ? "n/a" : `${+c.score.toFixed(1)}/${c.max}`}</span></div>
      <div class="bar"><i class="${k}" style="width:${frac == null ? 0 : (frac * 100).toFixed(0)}%"></i></div>
      <div class="small muted">${esc(c.detail)} ${lessonLink(c.lesson, "Why it matters")}</div></div>`;
  }).join("") + `<div class="small muted">${esc(proc.note)}</div>`;
}

// --- order ticket ------------------------------------------------------------
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
  $("order-btn").textContent = `Queue ${ui.side} order`;
  $("order-btn").disabled = !s.has_next;
  $("order-hint").textContent = isSell
    ? (pos ? `You hold ${+pos.shares.toFixed(6)} ${t} (≈ ${fmtUSD(pos.value)}).` : `You hold no ${t}.`)
    : `Available ${fmtUSD(s.available_cash)}. The amount includes the $${s.rules.fee.toFixed(2)} fee.`;
  schedulePreview();
}

let previewTimer;
function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(loadPreview, 250);
}

async function loadPreview() {
  const s = ui.state;
  if (!s || s.ended) return;
  const all = ui.side === "sell" && $("sell-all").checked;
  const amount = all ? "all" : $("order-amount").value.trim();
  const key = [s.id, s.day, ui.side, $("order-ticker").value, amount].join("|");
  if (key === ui.lastPreviewKey) return;
  ui.lastPreviewKey = key;
  const box = $("preview");
  if (!amount) { box.hidden = true; return; }
  try {
    const p = await api(`/api/sessions/${s.id}/preview`, { method: "POST", body: { side: ui.side, ticker: $("order-ticker").value, amount } });
    if (key !== ui.lastPreviewKey) return;
    const rows = [
      ["Estimated shares", `${+p.shares.toFixed(6)}`],
      ["Est. fill price", fmtUSD(p.est_fill)],
      [ui.side === "buy" ? "Est. total cost" : "Est. proceeds", fmtUSD(Math.abs(ui.side === "buy" ? p.gross + p.fee + p.fx : p.gross - p.fee))],
      ["Trading costs", `${fmtUSD(p.costs)} (${pct(p.cost_pct, 2)})`],
    ];
    if (p.break_even_move != null) rows.push(["Rise needed to break even", pct(p.break_even_move, 2)]);
    rows.push(["Position weight after", pct(p.weight_after, 0)]);
    if (p.value_after > 0) rows.push([`If ${p.ticker} fell 20%`, `−${fmtUSD(p.loss_if_down_20)} (${pct(p.loss_if_down_20_pct_portfolio)} of all)`]);
    rows.push(["Cash left", fmtUSD(p.cash_after)]);
    box.innerHTML = `<div class="grid">${rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</div>
      <div class="note small muted">${esc(p.note)}</div>
      ${p.tips.length ? `<div style="margin-top:8px">${p.tips.map(tipHtml).join("")}</div>` : ""}`;
    box.hidden = false;
  } catch (e) {
    box.innerHTML = `<span class="small muted">${esc(e.message)}</span>`;
    box.hidden = false;
  }
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
  if (!canvas.clientWidth) return;
  const { ctx, w, h: H } = setupCanvas(canvas);
  chartGeom = null;
  if (!h || !h.dates.length || !ui.state) return;
  const n = ui.rangeDays ? Math.min(ui.rangeDays, h.dates.length) : h.dates.length;
  const dates = h.dates.slice(-n), ys = h.adj_close.slice(-n);
  const show50 = $("show-ma50").checked, show200 = $("show-ma200").checked;
  const ma50 = h.ma50.slice(-n), ma200 = h.ma200.slice(-n);
  const pos = ui.state.positions.find((p) => p.ticker === h.ticker);
  const vals = ys.concat(show50 ? ma50.filter((v) => v != null) : [], show200 ? ma200.filter((v) => v != null) : []);
  let min = Math.min(...vals), max = Math.max(...vals);
  if (pos) { min = Math.min(min, pos.avg_cost); max = Math.max(max, pos.avg_cost); }
  const pad = (max - min) * 0.08 || 1;
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
  ctx.textAlign = "left"; ctx.fillText(dates[0], L, H - 6);
  ctx.textAlign = "right"; ctx.fillText(dates[n - 1], w - R, H - 6);
  if (n > 40 && w > 420) { ctx.textAlign = "center"; ctx.fillText(dates[Math.floor(n / 2)], x(Math.floor(n / 2)), H - 6); }
  ctx.textAlign = "left";

  const path = (arr) => {
    ctx.beginPath();
    let started = false;
    arr.forEach((v, i) => {
      if (v == null) { started = false; return; }
      if (!started) { ctx.moveTo(x(i), y(v)); started = true; } else ctx.lineTo(x(i), y(v));
    });
  };
  path(ys);
  ctx.lineTo(x(n - 1), H - B); ctx.lineTo(x(0), H - B); ctx.closePath();
  ctx.fillStyle = cssVar("--chart-fill"); ctx.fill();
  path(ys); ctx.strokeStyle = cssVar("--chart-line"); ctx.lineWidth = 1.75; ctx.lineJoin = "round"; ctx.stroke();
  if (show50) { path(ma50); ctx.strokeStyle = cssVar("--ma50"); ctx.lineWidth = 1.5; ctx.stroke(); }
  if (show200) { path(ma200); ctx.strokeStyle = cssVar("--ma200"); ctx.lineWidth = 1.5; ctx.stroke(); }

  if (pos) {
    ctx.setLineDash([5, 4]); ctx.strokeStyle = cssVar("--cost-line"); ctx.lineWidth = 1.25;
    ctx.beginPath(); ctx.moveTo(L, y(pos.avg_cost)); ctx.lineTo(w - R, y(pos.avg_cost)); ctx.stroke();
    ctx.setLineDash([]);
  }
  // your fills
  const idx = new Map(dates.map((d, i) => [d, i]));
  for (const m of h.markers) {
    const i = idx.get(m.date);
    if (i == null) continue;
    const px = x(i), py = y(ys[i]), buy = m.side === "buy";
    ctx.fillStyle = buy ? cssVar("--up") : cssVar("--down");
    ctx.beginPath();
    if (buy) { ctx.moveTo(px, py + 5); ctx.lineTo(px - 6, py + 15); ctx.lineTo(px + 6, py + 15); }
    else { ctx.moveTo(px, py - 5); ctx.lineTo(px - 6, py - 15); ctx.lineTo(px + 6, py - 15); }
    ctx.closePath(); ctx.fill();
  }
  ctx.beginPath(); ctx.arc(x(n - 1), y(ys[n - 1]), 3.5, 0, Math.PI * 2);
  ctx.fillStyle = cssVar("--chart-line"); ctx.fill();

  chartGeom = { x, y, n, dates, ys, closes: h.close.slice(-n), ma50, ma200, L, R, w };
}

function chartHover(ev) {
  const tip = $("chart-tip");
  if (!chartGeom) { tip.hidden = true; return; }
  const rect = ev.target.getBoundingClientRect();
  const g = chartGeom;
  const i = Math.max(0, Math.min(g.n - 1, Math.round(((ev.clientX - rect.left - g.L) / (g.w - g.L - g.R)) * (g.n - 1))));
  let html = `<strong>${esc(g.dates[i])}</strong><br>close ${fmtUSD(g.closes[i])}`;
  if (Math.abs(g.closes[i] - g.ys[i]) > 1e-6) html += `<br><span class="muted">split-adj. ${fmtUSD(g.ys[i])}</span>`;
  if ($("show-ma50").checked && g.ma50[i] != null) html += `<br>MA50 ${fmtUSD(g.ma50[i])}`;
  if ($("show-ma200").checked && g.ma200[i] != null) html += `<br>MA200 ${fmtUSD(g.ma200[i])}`;
  tip.innerHTML = html;
  tip.hidden = false;
  tip.style.left = Math.min(Math.max(g.x(i) + 10, 0), rect.width - tip.offsetWidth - 4) + "px";
  tip.style.top = Math.max(0, g.y(g.ys[i]) - 50) + "px";
}

function drawEquity(curve) {
  const canvas = $("equity-chart");
  if (!canvas.clientWidth) return;
  const { ctx, w, h } = setupCanvas(canvas);
  if (!curve || curve.length < 2) {
    ctx.fillStyle = "rgba(255,255,255,.75)"; ctx.font = "12px -apple-system, system-ui, sans-serif";
    ctx.fillText("Your account value will be charted here.", 0, h / 2 + 4);
    return;
  }
  const vals = curve.map((p) => p[1]);
  const start = ui.state.starting_cash;
  const min = Math.min(...vals, start), max = Math.max(...vals, start);
  const x = (i) => (i / (vals.length - 1)) * (w - 2) + 1;
  const y = (v) => 3 + (1 - (v - min) / (max - min || 1)) * (h - 6);
  ctx.setLineDash([3, 4]); ctx.strokeStyle = "rgba(255,255,255,.45)"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, y(start)); ctx.lineTo(w, y(start)); ctx.stroke(); ctx.setLineDash([]);
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "rgba(255,255,255,.35)"); grad.addColorStop(1, "rgba(255,255,255,0)");
  ctx.beginPath(); vals.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
  ctx.lineTo(x(vals.length - 1), h); ctx.lineTo(x(0), h); ctx.closePath(); ctx.fillStyle = grad; ctx.fill();
  ctx.beginPath(); vals.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
  ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.stroke();
}

async function loadHistory() {
  const s = ui.state;
  if (!s) return;
  const h = await api(`/api/sessions/${s.id}/history/${encodeURIComponent(ui.ticker)}`);
  ui.history = h;
  const m = s.market.find((x) => x.ticker === ui.ticker);
  $("chart-title").textContent = `${ui.ticker}  ${fmtUSD(m.close)}`;
  $("chart-sub").innerHTML = (m.description ? `${esc(m.description)} · ` : "") +
    `Today: open ${fmtUSD(m.open)} · high ${fmtUSD(m.high)} · low ${fmtUSD(m.low)} · ` +
    `<span class="${cls(m.change_1d)}">${fmtPct(m.change_1d)}</span>`;
  drawPrice();
}

// --- report ------------------------------------------------------------------
function renderReport(rep) {
  ui.report = rep;
  const u = rep.user;
  setTitle("Session report", reportSubtitle(rep));
  const stat = (l, v, c = "", tile = "") => `<div class="stat-tile ${tile}"><div class="stat-label">${l}</div><div class="stat-value ${c}">${v}</div></div>`;
  $("report-user").innerHTML =
    stat("Final value", fmtUSD(u.final_value)) +
    stat("Your return", fmtPct(u.return), "", "accent") +
    stat("Max drawdown", pct(-u.max_drawdown), u.max_drawdown > 0 ? "down" : "") +
    stat("Process score", rep.process.score == null ? "n/a" : `${rep.process.score}/100`) +
    stat("Orders filled", u.orders) +
    stat("Flat fees", fmtUSD(u.fees)) +
    stat("FX spread", fmtUSD(u.fx)) +
    stat("Spread + slippage", fmtUSD(u.spread_slippage)) +
    stat("Dividend tax", fmtUSD(u.dividend_tax)) +
    stat("Total fees + taxes", fmtUSD(u.total_fees_and_taxes));
  const lv = rep.xp.level;
  $("report-xp").innerHTML = `<strong>+${rep.xp.gained_now} XP this session · Level ${lv.level} ${esc(lv.name)} (${lv.xp} XP)</strong>`;

  $("report-scenario-card").hidden = !rep.scenario;
  if (rep.scenario) {
    $("report-scenario-title").textContent = rep.scenario.title;
    $("report-scenario").textContent = rep.scenario.debrief;
  }
  renderProcess(rep.process, "report-process-score", "report-process");

  const head = "<tr><th></th><th>Return</th><th>Max drawdown</th><th>Final value</th><th>Trades</th><th>Costs</th></tr>";
  const row = (b, extra = "") => `<tr class="${extra}"><td>${esc(b.name)}</td><td class="${cls(b.return)}">${fmtPct(b.return)}</td>
    <td>${pct(-(b.max_drawdown || 0))}</td><td>${fmtUSD(b.final_value)}</td><td>${b.trades}</td><td>${fmtUSD(b.costs)}</td></tr>`;
  const you = { name: "You", return: u.return, max_drawdown: u.max_drawdown, final_value: u.final_value, trades: u.orders,
    costs: u.fees + u.fx + u.spread_slippage };
  $("report-bench").innerHTML = head + row(you, "you") + (rep.trend_bot ? row(rep.trend_bot) : "") + (rep.benchmark ? row(rep.benchmark) : "");
  $("report-bot-rule").innerHTML = rep.trend_bot ? `Trend bot: ${esc(rep.trend_bot_rule)} It is a benchmark that follows past trends, not advice. ${lessonLink("trend-following")}` : "";
  $("report-tickers").innerHTML = head + rep.tickers.map((b) => row(b)).join("");

  const wf = rep.walk_forward;
  $("report-wf-card").hidden = !wf && !rep.walk_forward_skipped;
  $("report-wf").innerHTML = wf ? `
    <table class="tbl">
      <tr><th></th><th>In-sample<br><span class="small">${esc(wf.train.start)} – ${esc(wf.train.end)}</span></th>
          <th>Out-of-sample<br><span class="small">${esc(wf.test.start)} – ${esc(wf.test.end)}</span></th></tr>
      <tr><td>Chosen on in-sample: ${esc(wf.chosen_rule)}</td><td class="${cls(wf.chosen_in_sample)}">${fmtPct(wf.chosen_in_sample)}</td>
          <td class="${cls(wf.chosen_out_of_sample)}">${fmtPct(wf.chosen_out_of_sample)}</td></tr>
      <tr><td>Default ${esc(wf.default_rule)}</td><td class="${cls(wf.default_in_sample)}">${fmtPct(wf.default_in_sample)}</td>
          <td class="${cls(wf.default_out_of_sample)}">${fmtPct(wf.default_out_of_sample)}</td></tr>
    </table>
    <p class="small">${esc(wf.caveat)} ${lessonLink("backtesting")}</p>`
    : `<p class="muted">Skipped: ${esc(rep.walk_forward_skipped)}</p>`;

  $("report-review").innerHTML = rep.review.length ? rep.review.map((f) =>
    `<div class="finding"><strong>${esc(f.title)}</strong> <span class="muted">× ${f.count}</span><div class="small">${esc(f.detail)}</div></div>`).join("")
    : '<p class="muted">No common patterns were flagged in this session\'s journal.</p>';
  $("report-missions").innerHTML = rep.missions_completed.length ? `<div class="badges">${rep.missions_completed.map((m) =>
    `<div class="badge-card earned"><div class="t">★ ${esc(m.title)}</div><div class="small muted">${esc(m.desc)}</div></div>`).join("")}</div>`
    : '<p class="muted">None this time. See the Missions panel during play for ideas.</p>';
  $("report-lessons").innerHTML = rep.recommended_lessons.map(lessonCard).join("") || '<p class="muted">You have covered the lessons related to this session.</p>';
  $("report-notices").innerHTML = rep.notices.map((n) => `<li>${esc(n)}</li>`).join("");
}

// --- learn -------------------------------------------------------------------
const LEVEL_ORDER = ["Basics", "Risk", "Mechanics", "Psychology", "Strategies"];

function lessonCard(id) {
  const l = ui.edu.lessons.find((x) => x.id === id);
  if (!l) return "";
  const p = ui.edu.progress.lessons[id];
  return `<a class="lesson-card" href="#/learn/${esc(id)}">
    <span class="t">${esc(l.title)}</span><span class="s">${esc(l.summary)}</span>
    <span class="m"><span>${esc(l.level)} · ${l.minutes} min</span>
      ${p && p.passed ? '<span class="passed">✓ passed</span>' : p ? `<span>best ${p.best}/${p.total}</span>` : "<span>not started</span>"}</span></a>`;
}

function renderLearn() {
  const prog = ui.edu.progress;
  setTitle("Learn", `${prog.lessons_passed} of ${prog.lessons_total} lessons passed · 4–5 minutes each · quiz at the end (+50 XP)`);
  $("lesson-groups").innerHTML = LEVEL_ORDER.map((lvl) => {
    const ls = ui.edu.lessons.filter((l) => l.level === lvl);
    return ls.length ? `<div class="group-title">${lvl}</div><div class="lesson-grid">${ls.map((l) => lessonCard(l.id)).join("")}</div>` : "";
  }).join("");
  show("view-learn");
}

function renderLesson(id) {
  const l = ui.edu.lessons.find((x) => x.id === id);
  if (!l) { location.hash = "#/learn"; return; }
  const block = (b) => {
    if (b.p) return `<p>${inline(b.p)}</p>`;
    if (b.h) return `<h3>${inline(b.h)}</h3>`;
    if (b.ul) return `<ul>${b.ul.map((i) => `<li>${inline(i)}</li>`).join("")}</ul>`;
    if (b.example) return `<div class="box example"><div class="k">Example</div>${inline(b.example)}</div>`;
    if (b.try) return `<div class="box try"><div class="k">Try it in the game</div>${inline(b.try)}</div>`;
    return "";
  };
  const order = ui.edu.lessons.map((x) => x.id);
  const next = order[order.indexOf(id) + 1];
  setTitle(l.title, `${l.level} · ${l.minutes} min read · lesson ${order.indexOf(id) + 1} of ${order.length}`);
  $("lesson").innerHTML = `
    <p class="lede">${esc(l.summary)}</p>
    ${l.body.map(block).join("")}
    <form class="quiz" id="quiz-form">
      <h2>Check your understanding</h2>
      ${l.quiz.map((q, qi) => `<div class="q" data-q="${qi}"><div class="qt">${qi + 1}. ${inline(q.q)}</div>
        ${q.options.map((o, oi) => `<label class="opt"><input type="radio" name="q${qi}" value="${oi}"> <span>${inline(o)}</span></label>`).join("")}
        <div class="explain" hidden></div></div>`).join("")}
      <div class="quiz-result" id="quiz-result"></div>
      <div class="btn-row">
        <button class="btn btn-primary" id="quiz-submit">Check answers</button>
        ${next ? `<a class="btn" href="#/learn/${esc(next)}">Next lesson ›</a>` : ""}
        <a class="btn" href="#/play">Practise in the game</a>
      </div>
    </form>`;
  $("quiz-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const answers = l.quiz.map((_, qi) => {
      const c = document.querySelector(`input[name=q${qi}]:checked`);
      return c ? +c.value : null;
    });
    if (answers.includes(null)) { toast("Answer every question first.", "error", 3000); return; }
    run(async () => {
      const r = await api(`/api/lessons/${id}/quiz`, { method: "POST", body: { answers } });
      r.results.forEach((res, qi) => {
        const qEl = document.querySelector(`.q[data-q="${qi}"]`);
        qEl.querySelectorAll(".opt").forEach((opt, oi) => {
          opt.classList.toggle("right", oi === res.answer);
          opt.classList.toggle("wrong", oi === answers[qi] && !res.correct);
        });
        const ex = qEl.querySelector(".explain");
        ex.innerHTML = `${res.correct ? "✓ Correct." : "✗ Not quite."} ${inline(res.explain)}`;
        ex.hidden = false;
      });
      $("quiz-result").textContent = `${r.score}/${r.total}: ${r.passed ? "passed" : "not passed yet. Read the explanations and try again."}`;
      renderLevel(r.progress);
      if (r.xp_gained) toast(`Lesson passed: +${r.xp_gained} XP`, "success", 3500);
    });
  });
  show("view-lesson");
}

// --- glossary & progress ---------------------------------------------------------
function renderGlossary() {
  const q = $("glossary-search").value.trim().toLowerCase();
  const terms = Object.keys(ui.edu.glossary).sort().filter((t) => !q || t.includes(q) || ui.edu.glossary[t].toLowerCase().includes(q));
  setTitle("Glossary", `${Object.keys(ui.edu.glossary).length} terms in plain English`);
  $("glossary").innerHTML = terms.map((t) => `<div id="term-${esc(t.replace(/\s+/g, "-"))}"><dt>${esc(t)}</dt><dd>${esc(ui.edu.glossary[t])}</dd></div>`).join("")
    || '<p class="muted">No matching terms.</p>';
  show("view-glossary");
}
$("glossary-search").addEventListener("input", renderGlossary);

async function renderProgress() {
  setTitle("Progress", "Your level, missions, lessons and session history");
  show("view-progress");
  try { renderLevel(await api("/api/progress")); } catch { /* use cached */ }
  const p = ui.edu.progress, lv = p.level;
  const span = lv.next_level_at ? lv.next_level_at - lv.level_start : 1;
  const into = lv.next_level_at ? (p.xp - lv.level_start) / span : 1;
  $("progress-level").innerHTML = `<div class="label">Level ${lv.level}</div>
    <div class="level-name">${esc(lv.name)}</div>
    <div class="xpbar"><i style="width:${(into * 100).toFixed(0)}%"></i></div>
    <div class="muted small">${p.xp} XP${lv.next_level_at ? ` · ${lv.next_level_at - p.xp} XP to the next level` : " · top level reached"}.
      Earn XP by passing lessons (+50), completing missions and finishing sessions (+25).</div>`;
  const tile = (l, v) => `<div class="stat-tile"><div class="stat-label">${l}</div><div class="stat-value">${v}</div></div>`;
  $("progress-stats").innerHTML = tile("Lessons passed", `${p.lessons_passed}<small> / ${p.lessons_total}</small>`) +
    tile("Missions earned", `${Object.keys(p.missions).length}<small> / ${ui.edu.missions.length}</small>`) +
    tile("Sessions played", p.sessions.length) +
    tile("Best process score", p.sessions.reduce((m, s) => (s.process_score != null && s.process_score > m ? s.process_score : m), 0) || "–");
  $("progress-missions").innerHTML = ui.edu.missions.map((m) => {
    const n = p.missions[m.id] || 0;
    return `<div class="badge-card ${n ? "earned" : "locked"}"><div class="t">${n ? "★" : "☆"} ${esc(m.title)}</div>
      <div class="small muted">${esc(m.desc)}</div><div class="small">${n ? `earned ×${n}` : `+${m.xp} XP`}</div></div>`;
  }).join("");
  $("progress-lessons").innerHTML = ui.edu.lessons.map((l) => {
    const r = p.lessons[l.id];
    return `<li><a href="#/learn/${esc(l.id)}">${esc(l.title)}</a> <span class="small ${r && r.passed ? "up" : "muted"}">
      ${r && r.passed ? "✓ passed" : r ? `best ${r.best}/${r.total}` : "not started"}</span></li>`;
  }).join("");
  $("progress-sessions").innerHTML = p.sessions.length ? `<div class="table-scroll"><table class="tbl"><tr><th>Played</th><th>Mode</th><th>Period</th>
      <th>Return</th><th>Benchmark</th><th>Max DD</th><th>Process</th></tr>${p.sessions.slice().reverse().map((s) => `
      <tr><td>${esc(s.played)}</td><td>${esc(s.scenario || s.mode)}</td><td>${esc(s.start)} → ${esc(s.end)}</td>
      <td class="${cls(s.return)}">${fmtPct(s.return)}</td><td>${s.benchmark_return == null ? "–" : fmtPct(s.benchmark_return)}</td>
      <td>${pct(-s.max_drawdown)}</td><td>${s.process_score == null ? "–" : s.process_score}</td></tr>`).join("")}</table></div>`
    : '<p class="muted">No finished sessions yet.</p>';
}

// --- game actions ----------------------------------------------------------------
async function enterGame(state) {
  ui.reviewing = null;
  ui.lastPreviewKey = "";
  location.hash = "#/play";
  show("view-game");
  render(state);
  await loadHistory();
}

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
$("show-ma50").addEventListener("change", drawPrice);
$("show-ma200").addEventListener("change", drawPrice);

$("side-seg").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  ui.side = b.dataset.side;
  updateOrderForm();
});
$("sell-all").addEventListener("change", updateOrderForm);
$("order-amount").addEventListener("input", schedulePreview);
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
    $("preview").hidden = true;
    render(state);
    toast("Order queued. It fills at the next trading day's open. Press 'Next day' to continue.", "success", 3500);
  });
});

$("cancel-btn").addEventListener("click", () => run(async () => {
  render(await api(`/api/sessions/${ui.state.id}/cancel`, { method: "POST" }));
}));

$("positions").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-review]");
  if (b) { ui.reviewing = ui.reviewing === b.dataset.review ? null : b.dataset.review; renderPositions(ui.state); return; }
  if (ev.target.closest("[data-review-cancel]")) { ui.reviewing = null; renderPositions(ui.state); }
});
$("positions").addEventListener("submit", (ev) => {
  const form = ev.target.closest("[data-review-form]");
  if (!form) return;
  ev.preventDefault();
  run(async () => {
    const state = await api(`/api/sessions/${ui.state.id}/review`, {
      method: "POST", body: { ticker: form.dataset.reviewForm, note: form.querySelector("textarea").value },
    });
    ui.reviewing = null;
    render(state);
    renderLevel(await api("/api/progress"));
    toast("Review saved to your journal.", "success", 3000);
  });
});

for (const b of document.querySelectorAll("[data-hold]")) {
  b.addEventListener("click", () => run(async () => {
    const state = await api(`/api/sessions/${ui.state.id}/hold`, { method: "POST", body: { days: +b.dataset.hold } });
    ui.lastPreviewKey = "";
    render(state);
    await loadHistory();
    if (state.new_missions.length) renderLevel(await api("/api/progress"));
    const alert = state.new_tips.find((t) => t.kind === "alert");
    if (alert) toast(`${alert.title}. See the Coach panel.`, "error");
    else if (state.new_missions.length) toast(`★ Mission complete: ${state.new_missions.map((m) => m.title).join(", ")}`, "success", 4000);
    else if (state.stopped && !state.has_next) toast(state.stopped);
  }));
}

$("end-btn").addEventListener("click", () => {
  if (!confirm("End this session and see the report? Pending orders will be cancelled.")) return;
  run(async () => {
    const { report } = await api(`/api/sessions/${ui.state.id}/end`, { method: "POST" });
    ui.state = null;
    renderReport(report);
    renderLevel((await api("/api/progress")));
    show("view-report");
    toast(`Session complete: +${report.xp.gained_now} XP`, "success", 3500);
    window.scrollTo(0, 0);
  });
});

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
for (const id of ["journal-download", "settings-journal"]) {
  $(id).addEventListener("click", () => run(async () => {
    download("journal.csv", (await api("/api/journal")).csv);
  }));
}
$("progress-reset").addEventListener("click", () => {
  if (!confirm("Reset all XP, lesson results, missions, session history and your saved journal in this browser?")) return;
  run(async () => { renderLevel(await api("/api/progress/reset", { method: "POST" })); toast("Progress and journal reset.", "success", 3000); });
});

$("new-btn").addEventListener("click", () => { ui.report = null; ui.history = null; renderStart(); show("view-start"); window.scrollTo(0, 0); });

$("price-chart").addEventListener("mousemove", chartHover);
$("price-chart").addEventListener("mouseleave", () => ($("chart-tip").hidden = true));

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (ui.state) { drawPrice(); drawEquity(ui.state.equity_curve); } }, 120);
});

document.addEventListener("keydown", (ev) => {
  if (ev.key !== "n" || ev.metaKey || ev.ctrlKey || ev.target.closest("input, textarea, select") || $("view-game").hidden) return;
  document.querySelector('[data-hold="1"]').click();
});

// --- boot --------------------------------------------------------------------
applySettings();
(async function boot() {
  try {
    ui.edu = await api("/api/education");
  } catch (e) {
    toast("Could not start the simulator: " + e.message);
    return;
  }
  renderLevel(ui.edu.progress);
  try {
    const info = await api("/api/info");
    $("start-tickers").textContent = info.tickers.join(", ");
  } catch { /* optional */ }
  const saved = loadReplay();
  if (saved) {
    try {
      $("start-status").textContent = "Restoring your session…";
      const state = await localApi.replay(saved);
      ui.state = state;
      if (!location.hash || location.hash.startsWith("#/play")) { await enterGame(state); return; }
      render(state);
    } catch (e) {
      store.del(REPLAY_KEY);
      toast("Your previous session could not be restored: " + e.message);
    } finally { $("start-status").textContent = ""; }
  }
  route();
})();
