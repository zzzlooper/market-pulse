"use strict";

const state = {
  watchlist: [], keywords: [], quotes: [], movers: {}, unusual: [], news: [],
  alert_rules: [], unusual_alerts: true, alerts: [], halts: [], halt_alerts: "watchlist",
  rvol_threshold: 2, price_source: "yahoo", tab: "gainers", leftTab: "unusual",
  health: { now: 0, sources: [] },
  risk: {},  // symbol -> { level, points, reasons } dilution summary; null = not an SEC filer
};
const lastPrice = new Map();   // symbol -> price, to flash changes
const MAX_NEWS = 500;
const MAX_NOTIFICATIONS = 3;   // per update batch, so a burst doesn't spam the desktop

const $ = (sel) => document.querySelector(sel);

// --- formatting -----------------------------------------------------------------

const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmtPrice = (v) => (v == null ? "–" : v.toFixed(v < 1 ? 4 : 2));
const fmtPct = (v) => (v == null ? "–" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const fmtRvol = (v) => (v == null ? "–" : `${v.toFixed(1)}×`);
const dir = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
const safeUrl = (u) => (/^https?:\/\//i.test(u) ? u : "#");

function fmtVol(v) {
  if (!v) return "–";
  for (const [div, suffix] of [[1e9, "B"], [1e6, "M"], [1e3, "K"]]) {
    if (v >= div) return (v / div).toFixed(1) + suffix;
  }
  return String(v);
}

// The market clock: real time, or the replayed day's time during a replay (from the server,
// running at its speed). Data-source ages ("4m ago") stay on real time.
const appClock = { base: null, at: 0, speed: 1 };
const appNow = () => (appClock.base == null ? Date.now() : appClock.base + (Date.now() - appClock.at) * appClock.speed);

function setClock(c) {
  if (!c) return;
  Object.assign(appClock, c.replay ? { base: c.now * 1000, at: Date.now(), speed: c.speed } : { base: null });
  const banner = $("#replay-banner");
  banner.hidden = !c.replay && !c.recording;
  banner.classList.toggle("recording", !c.replay);
  banner.textContent = c.replay
    ? `REPLAY of ${c.recording}${c.speed !== 1 ? ` at ${c.speed}×` : ""} · no network, no phone alerts`
    : `Recording today's data for replay (${c.recording})`;
}

function fmtTime(iso) {
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === new Date(appNow()).toDateString()
    ? time
    : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

// --- tables -------------------------------------------------------------------------

// Only watchlist rows flash: movers come from a different Yahoo endpoint whose prices can
// differ slightly for the same symbol, which would make shared state flicker.
function quoteRow(r, extra = "", flashable = false) {
  let flash = "";
  if (flashable && r.price != null) {
    const prev = lastPrice.get(r.symbol);
    if (prev != null && r.price !== prev) flash = r.price > prev ? "flash-up" : "flash-down";
    lastPrice.set(r.symbol, r.price);
  }
  const live = r.live
    ? `<span class="live-dot" title="Real-time · last trade ${new Date(r.price_time * 1000).toLocaleTimeString()}"></span>`
    : "";
  const stale = r.stale ? ` class="stale" title="No fresh price from Yahoo; showing the last one received"` : "";
  return `<tr${stale}>
    <td><a class="sym" data-sym="${esc(r.symbol)}" title="${esc(r.name)}">${esc(r.symbol)}</a>${haltBadge(r.symbol)}${riskBadge(r.symbol)}</td>
    <td class="${flash}">${live}${fmtPrice(r.price)}</td>
    <td class="${dir(r.change_pct)}">${fmtPct(r.change_pct)}</td>
    <td>${fmtVol(r.volume)}</td>
    <td class="${r.rvol >= state.rvol_threshold ? "hot" : ""}">${fmtRvol(r.rvol)}</td>${extra}
  </tr>`;
}

function fillTable(tbody, html, cols, emptyText) {
  tbody.innerHTML = html || `<tr class="empty"><td colspan="${cols}">${emptyText}</td></tr>`;
}

function renderWatchlist() {
  $("#price-source").textContent = state.price_source === "finnhub"
    ? "real-time prices · Finnhub" : "prices delayed ~15 min · Yahoo";
  const bySym = new Map(state.quotes.map((q) => [q.symbol, q]));
  const html = state.watchlist.map((sym) => quoteRow(bySym.get(sym) ?? { symbol: sym },
    `${tickerSentimentCell(sym)}<td><button class="x" data-remove="${esc(sym)}" title="Remove">×</button></td>`,
    true)).join("");
  fillTable($("#watchlist"), html, 7, "Add a ticker to start");
}

// --- trading halts --------------------------------------------------------------------

// The feed only gives times; whether a halt is over depends on the clock, so status is
// derived here and the table re-rendered periodically.
function haltStatus(h, now = appNow()) {
  if (!h.resume_trade_at) return "halted";
  return Date.parse(h.resume_trade_at) <= now ? "resumed" : "resuming";
}

const etDate = (d = new Date(appNow())) => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(d);
const fmtET = (iso) => new Date(iso).toLocaleTimeString("en-GB", { timeZone: "America/New_York" });
const isToday = (h) => h.halted_at.slice(0, 10) === etDate();  // halted_at carries the ET offset
const normSym = (s) => s.replace("-", ".");                    // Yahoo BRK-B is BRK.B at Nasdaq

let activeHalts = new Map();  // symbol -> halt, for badges elsewhere

// Only elevated risk gets a badge, so the tables don't fill with "low". Hover for the reasons;
// the ticker drawer has the full breakdown.
function riskBadge(sym) {
  const r = state.risk[sym];
  if (!r || (r.level !== "high" && r.level !== "medium")) return "";
  const tip = `Dilution risk ${r.level} (${r.points} pts)${r.reasons.length ? `: ${r.reasons.join("; ")}` : ""}`;
  return ` <span class="risk-badge ${r.level}" title="${esc(tip)}">DIL</span>`;
}

function haltBadge(sym) {
  const h = activeHalts.get(normSym(sym));
  if (!h) return "";
  const resume = h.resume_trade_at ? ` · resumes ${fmtET(h.resume_trade_at)} ET` : "";
  return ` <span class="halt-badge" title="${esc(`${h.reason_code} · ${h.reason}${resume}`)}">HALTED</span>`;
}

function renderHalts() {
  const now = appNow();
  activeHalts = new Map(state.halts.filter((h) => haltStatus(h, now) !== "resumed").map((h) => [h.symbol, h]));
  const todayActive = state.halts.filter((h) => isToday(h) && haltStatus(h, now) !== "resumed").length;
  $("#halt-count").textContent = todayActive ? `(${todayActive})` : "";
  $("#halt-pill").hidden = !todayActive;
  $("#halt-pill").textContent = `⏸ ${todayActive} halted`;

  const scope = $("#halt-scope").value;
  const order = { halted: 0, resuming: 1, resumed: 2 };
  const rows = state.halts
    .filter((h) => scope === "all" || isToday(h))
    .map((h) => ({ h, status: haltStatus(h, now) }))
    .sort((a, b) => order[a.status] - order[b.status] || b.h.halted_at.localeCompare(a.h.halted_at));
  const html = rows.map(({ h, status }) => {
    const halted = isToday(h) ? fmtET(h.halted_at) : h.halted_at.slice(0, 10);
    const mins = h.resume_trade_at ? Math.ceil((Date.parse(h.resume_trade_at) - now) / 60000) : null;
    const label = status === "resuming" ? `Resumes in ${mins}m` : status === "resumed" ? "Resumed" : "Halted";
    const tip = `${h.name} · ${h.market}${h.pause_price ? ` · pause price ${h.pause_price}` : ""}`;
    return `<tr class="halt-row ${status}">
      <td><a class="sym" data-sym="${esc(h.symbol)}" title="${esc(tip)}">${esc(h.symbol)}</a></td>
      <td>${halted}</td>
      <td class="reason ${esc(h.category)}" title="${esc(`${h.reason_code} · ${h.reason}`)}">${esc(h.reason_code)}</td>
      <td>${h.resume_trade_at ? fmtET(h.resume_trade_at) : "–"}</td>
      <td class="status">${label}</td>
    </tr>`;
  }).join("");
  fillTable($("#halts"), html, 5, scope === "today" ? "No halts today" : "No halts in the feed");
}

// Re-render the halts table, and the quote tables too if the set of halted symbols changed.
function refreshHalts() {
  const before = [...activeHalts.keys()].join();
  renderHalts();
  if ([...activeHalts.keys()].join() !== before) {
    renderWatchlist(); renderUnusual(); renderMovers();
  }
}

function setLeftTab(tab) {
  state.leftTab = tab;
  for (const b of document.querySelectorAll("[data-left-tab]")) b.classList.toggle("active", b.dataset.leftTab === tab);
  $("#tab-unusual").hidden = tab !== "unusual";
  $("#tab-halts").hidden = tab !== "halts";
  $("#tab-reactions").hidden = tab !== "reactions";
  clearInterval(reactionsTimer);
  if (tab === "reactions") {
    loadReactions();
    reactionsTimer = setInterval(loadReactions, 120_000);
  }
}

// --- catalyst reactions ------------------------------------------------------------------

let reactionsTimer = null;
const fmtMove = (v) => (v == null ? `<td class="muted">–</td>`
  : `<td class="${dir(v)}" title="${fmtPct(v)}">${v > 0 ? "+" : ""}${v.toFixed(1)}%</td>`);

function reactionRows(groups, empty) {
  return groups.map((g) => `<tr><td>${esc(g.group)}</td><td><small>${g.n} · ${g.measured}</small></td>
    ${fmtMove(g.median_5m)}${fmtMove(g.median_30m)}${fmtMove(g.median_close)}
    <td>${g.down_share == null ? "–" : `${Math.round(g.down_share * 100)}%`}</td></tr>`).join("")
    || `<tr class="empty"><td colspan="6">${empty}</td></tr>`;
}

async function loadReactions() {
  try {
    const days = $("#reactions-days").value;
    const [r, recap] = await Promise.all([api("GET", `/api/reactions?days=${days}`), api("GET", "/api/recap")]);
    $("#reactions-note").textContent = `${r.tracked} catalysts tracked`;
    $("#reactions-types").innerHTML = reactionRows(r.by_type,
      "Nothing tracked yet: filings, halts and alert headlines are measured as they arrive");
    $("#reactions-risk").innerHTML = reactionRows(r.offerings_by_risk, "No offerings tracked yet");
    $("#reactions-recent").innerHTML = r.recent.map((e) => {
      const ret = e.returns;
      const moves = ["5m", "30m", "close"].map((h) => (ret[h] == null ? "" :
        `<span class="${dir(ret[h])}">${h} ${fmtPct(ret[h])}</span>`)).filter(Boolean).join(" · ");
      return `<li><div class="meta">${fmtTime(new Date(e.at * 1000).toISOString())} · ${esc(e.label)}
          · <a class="sym" data-sym="${esc(e.symbol)}">${esc(e.symbol)}</a></div>
        ${esc(e.title)}${moves ? `<div class="meta">${moves}</div>` : `<div class="meta muted">measuring…</div>`}</li>`;
    }).join("") || `<li class="muted">Nothing yet</li>`;
    $("#recap-preview").textContent = `${recap.title}\n\n${recap.body}`;
  } catch (err) {
    $("#reactions-note").textContent = err.message;
  }
}

// --- sentiment ------------------------------------------------------------------------

const DAY_MS = 24 * 3600 * 1000;
const fmtScore = (v) => `${v > 0 ? "+" : ""}${v.toFixed(2)}`;

function sentimentBadge(s) {
  if (!s || s.label === "neutral") return "";
  const tip = `Sentiment ${fmtScore(s.score)} · ${s.terms.join(", ")}`;
  return `<span class="sentiment ${s.label}" title="${esc(tip)}">${s.label === "bullish" ? "▲" : "▼"} ${s.label}</span>`;
}

// Mean score of the ticker's headlines from the last 24h, so one bearish story among many
// bullish ones still shows up as a dent rather than flipping the label.
function tickerSentimentCell(sym) {
  const since = appNow() - DAY_MS;
  const items = state.news.filter((n) => n.tickers.includes(sym) && Date.parse(n.published) >= since);
  if (!items.length) return `<td class="muted">–</td>`;
  const mean = items.reduce((sum, n) => sum + n.sentiment.score, 0) / items.length;
  const bull = items.filter((n) => n.sentiment.label === "bullish").length;
  const bear = items.filter((n) => n.sentiment.label === "bearish").length;
  const tip = `${items.length} headlines: ${bull} bullish, ${bear} bearish`;
  return `<td class="${mean >= 0.1 ? "up" : mean <= -0.1 ? "down" : "muted"}" title="${tip}">` +
    `${fmtScore(mean)} <small>(${items.length})</small></td>`;
}

function renderUnusual() {
  $("#rvol-note").textContent = `RVOL ≥ ${state.rvol_threshold}×, time-of-day adjusted`;
  fillTable($("#unusual"), state.unusual.map((r) => quoteRow(r)).join(""), 5, "Nothing unusual right now");
}

function renderMovers() {
  for (const b of document.querySelectorAll(".tabs button")) {
    b.classList.toggle("active", b.dataset.tab === state.tab);
  }
  const rows = state.movers[state.tab] ?? [];
  const down = state.health.sources.find((s) => s.name === "movers")?.state === "down";
  fillTable($("#movers"), rows.map((r) => quoteRow(r)).join(""), 5,
    down ? "Yahoo screeners unavailable, retrying…" : "Loading…");
}

// --- news -----------------------------------------------------------------------------

function keywordRegex() {
  if (!state.keywords.length) return null;
  const parts = state.keywords.map((k) => esc(k).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(`(?<!\\w)(${parts.join("|")})(?!\\w)`, "gi");
}

function isAlert(n, kwRe) {
  if (n.filing) return Boolean(n.filing.hot);  // watchlist or a mover when it was filed
  if (n.tickers.some((t) => state.watchlist.includes(t))) return true;
  if (!kwRe) return false;
  kwRe.lastIndex = 0;
  return kwRe.test(esc(`${n.title} ${n.summary}`));
}

function newsFilter() {
  const q = $("#news-search").value.trim().toLowerCase();
  const source = $("#news-source").value;
  const mood = $("#news-sentiment").value;
  const watchOnly = $("#news-watchlist").checked;
  const alertsOnly = $("#news-alerts").checked;
  const kwRe = keywordRegex();
  return (n) => {
    if (source && n.source !== source) return false;
    if (mood === "strong" ? n.sentiment.label === "neutral" : mood && n.sentiment.label !== mood) return false;
    if (watchOnly && !n.tickers.some((t) => state.watchlist.includes(t))) return false;
    if (alertsOnly && !isAlert(n, kwRe)) return false;
    if (q && !n.tickers.some((t) => t.toLowerCase() === q)
        && !n.title.toLowerCase().includes(q) && !n.summary.toLowerCase().includes(q)) return false;
    return true;
  };
}

function renderNews(freshIds = new Set()) {
  const kwRe = keywordRegex();
  const mark = (text) => (kwRe ? esc(text).replace(kwRe, "<mark>$1</mark>") : esc(text));
  const visible = state.news.filter(newsFilter());
  $("#news-count").textContent = `${visible.length} of ${state.news.length}`;
  $("#news").innerHTML = visible.slice(0, 300).map((n) => `
    <li class="news-item ${isAlert(n, kwRe) ? "alert" : ""} ${freshIds.has(n.id) ? "fresh" : ""}">
      <div class="meta">
        <time datetime="${esc(n.published)}">${fmtTime(n.published)}</time>
        <span>${esc(n.source)}</span>
        ${n.filing ? filingBadge(n.filing) : sentimentBadge(n.sentiment)}
        ${n.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}
        ${n.tickers.map((t) => `<a class="sym" data-sym="${esc(t)}">${esc(t)}</a>`).join("")}
        ${n.filing?.hot && n.filing.hot !== "watchlist" ? `<span class="muted">${esc(n.filing.hot)}</span>` : ""}
        ${n.filing ? riskBadge(n.tickers[0]) : ""}
      </div>
      <a class="title" href="${esc(safeUrl(n.url))}" target="_blank" rel="noopener noreferrer">${mark(n.title)}</a>
      ${n.summary ? `<p>${mark(n.summary)}</p>` : ""}
    </li>`).join("");
}

function filingBadge(f) {
  return `<span class="filing ${esc(f.kind)}" title="SEC ${esc(f.form)}">${esc(f.label)}</span>`;
}

function renderSources() {
  const select = $("#news-source");
  const current = select.value;
  const sources = [...new Set(state.news.map((n) => n.source))].sort();
  select.innerHTML = `<option value="">All sources</option>` +
    sources.map((s) => `<option ${s === current ? "selected" : ""}>${esc(s)}</option>`).join("");
}

function renderKeywords() {
  $("#keyword-list").innerHTML = state.keywords.map((k) =>
    `<span class="chip">${esc(k)}<button data-remove-kw="${esc(k)}" title="Remove">×</button></span>`).join("");
}

function addNews(items) {
  const known = new Set(state.news.map((n) => n.id));
  const fresh = items.filter((n) => !known.has(n.id));
  state.news = [...fresh, ...state.news]
    .sort((a, b) => b.published.localeCompare(a.published))
    .slice(0, MAX_NEWS);
  renderSources();
  renderNews(new Set(fresh.map((n) => n.id)));
  renderWatchlist();  // News 24h column
  const alerts = fresh.filter((n) => n.alert);
  if (alerts.length) {
    notify(alerts.map((n) => ({
      title: `${n.tickers.length ? `${n.tickers.join(" ")} · ` : ""}${n.filing ? n.filing.label : n.source}`,
      body: n.title, tag: n.id,
    })));
  }
}

// --- price & volume alerts ------------------------------------------------------------------

const RULE_LABELS = {
  above: (v) => `above ${v}`,
  below: (v) => `below ${v}`,
  move: (v) => `moves ±${v}%`,
  rvol: (v) => `RVOL ≥ ${v}×`,
};

function renderRules() {
  $("#rules").innerHTML = state.alert_rules.map((r) =>
    `<span class="chip">${esc(r.symbol || "All")} ${esc(RULE_LABELS[r.kind]?.(r.value) ?? r.kind)}` +
    `<button data-remove-rule="${esc(r.id)}" title="Remove">×</button></span>`).join("")
    || `<span class="muted">No rules yet</span>`;
  $("#unusual-alerts").checked = state.unusual_alerts;
  $("#halt-alerts").value = state.halt_alerts;
  $("#rule-symbols").innerHTML = state.watchlist.map((s) => `<option value="${esc(s)}">`).join("");
}

function renderPhone() {
  const { targets = [], last_sent: sent, last_error: error } = state.phone ?? {};
  const on = targets.length > 0;
  $("#phone-target").textContent = on
    ? `${targets.join(" + ")}${sent ? ` · last sent ${fmtTime(sent)}` : ""}`
    : "not set up — add ntfy or Telegram in Setup";
  $("#phone-test").disabled = !on;
  for (const box of document.querySelectorAll("[data-phone-cat]")) {
    box.checked = Boolean(state.phone_alerts?.[box.dataset.phoneCat]);
    box.disabled = !on;
  }
  $("#phone-error").hidden = !error;
  $("#phone-error").textContent = error ? `Last delivery failed: ${error}` : "";
}

function renderAlertLog(freshIds = new Set()) {
  $("#alert-log").innerHTML = state.alerts.map((a) => `
    <li class="alert-entry ${esc(a.kind)} ${freshIds.has(a.id) ? "fresh" : ""}">
      <time datetime="${esc(a.time)}">${fmtTime(a.time)}</time>
      <a class="sym" data-sym="${esc(a.symbol)}">${esc(a.symbol)}</a>
      <span>${esc(a.message.replace(`${a.symbol} `, ""))}</span>
    </li>`).join("") || `<li class="muted empty-log">Alerts appear here when a rule fires</li>`;
}

function addAlerts(events) {
  state.alerts = [...events, ...state.alerts].slice(0, 100);
  renderAlertLog(new Set(events.map((a) => a.id)));
  const what = { halt: "trading halted", resume: "resumption", unusual: "unusual volume" };
  notify(events.map((a) => ({ title: `${a.symbol} · ${what[a.kind] ?? "price alert"}`, body: a.message, tag: a.id })), 660);
}

// --- alerts -----------------------------------------------------------------------------

let audio = null;

function beep(freq = 880) {
  if (!audio) return;
  const osc = audio.createOscillator();
  const gain = audio.createGain();
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(0.15, audio.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + 0.25);
  osc.connect(gain).connect(audio.destination);
  osc.start();
  osc.stop(audio.currentTime + 0.25);
}

// messages: [{ title, body, tag }]. News beeps at 880 Hz, price/volume alerts lower,
// so the two are distinguishable without looking.
function notify(messages, freq = 880) {
  beep(freq);
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  for (const m of messages.slice(0, MAX_NOTIFICATIONS)) {
    new Notification(m.title, { body: m.body, tag: m.tag });
  }
  if (messages.length > MAX_NOTIFICATIONS) {
    new Notification("Market Pulse", { body: `+${messages.length - MAX_NOTIFICATIONS} more alerts` });
  }
}

async function enableAlerts() {
  audio ??= new AudioContext();  // must be created from a user gesture
  if ("Notification" in window && Notification.permission === "default") {
    await Notification.requestPermission();
  }
  updateAlertButton();
  beep();
}

function updateAlertButton() {
  const on = audio && (!("Notification" in window) || Notification.permission === "granted");
  $("#notify-btn").textContent = on ? "Alerts on" : "Enable alerts";
}

// --- data-source health -----------------------------------------------------------------
// The server judges each source by the age of its last good update. Panels fed by a source
// that is down get a badge and are dimmed, so old numbers never pass for live ones.

const HEALTH_TEXT = { starting: "starting", ok: "ok", degraded: "partial", down: "down" };
const HEALTH_RANK = { ok: 0, starting: 1, degraded: 2, down: 3 };
let healthSkew = 0;  // browser clock minus server clock, in seconds

function setHealth(data) {
  state.health = data;
  healthSkew = Date.now() / 1000 - data.now;
  renderHealth();
  if (!(state.movers[state.tab] ?? []).length) renderMovers();  // "Loading…" vs "unavailable"
}

function fmtAge(lastOk) {
  if (lastOk == null) return "never";
  const s = Math.max(0, Date.now() / 1000 - healthSkew - lastOk);
  return s < 60 ? `${Math.round(s)}s ago` : s < 3600 ? `${Math.floor(s / 60)}m ago` : `${Math.floor(s / 3600)}h ago`;
}

const worstOf = (sources) =>
  sources.reduce((w, s) => (!w || HEALTH_RANK[s.state] > HEALTH_RANK[w.state] ? s : w), null);

function renderHealth() {
  const sources = state.health?.sources ?? [];
  const worst = worstOf(sources);
  const pill = $("#health");
  pill.hidden = !worst;
  if (!worst) return;
  const bad = sources.filter((s) => s.state === "down" || s.state === "degraded");
  pill.textContent = bad.length > 1 ? `${bad.length} data sources with problems`
    : bad.length === 1 ? `${bad[0].label}: ${HEALTH_TEXT[bad[0].state]}`
    : worst.state === "starting" ? "data starting…" : "data ok";
  pill.className = `pill health-${worst.state}`;
  pill.title = "Data sources: click for details";
  $("#health-list").innerHTML = sources.map((s) => `<div class="health-row">
      <b>${esc(s.label)}</b> <span class="health-${s.state}">${HEALTH_TEXT[s.state]}</span>
      <small class="muted">· last data ${fmtAge(s.last_ok)}</small>
      ${s.reason ? `<div class="small muted">${esc(s.reason)}</div>` : ""}
    </div>`).join("");

  const byName = new Map(sources.map((s) => [s.name, s]));
  for (const badge of document.querySelectorAll(".health-badge")) {
    const src = worstOf(badge.dataset.sources.split(" ").map((n) => byName.get(n)).filter(Boolean));
    const show = src && (src.state === "down" || src.state === "degraded");
    badge.closest(".panel, #tab-halts").classList.toggle("source-down", Boolean(show) && src.state === "down");
    badge.hidden = !show;
    if (!show) continue;
    badge.className = `health-badge health-${src.state}`;
    badge.textContent = src.state === "degraded" ? "partial data"
      : src.last_ok == null ? "no data" : `stale · ${fmtAge(src.last_ok)}`;
    badge.title = `${src.label}: ${src.reason ?? ""}`;
  }
}

// --- clock & market session --------------------------------------------------------------

function tick() {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit",
    second: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(appNow())).map((p) => [p.type, p.value]));
  $("#clock").textContent = `${parts.hour}:${parts.minute}:${parts.second} ET`;

  const mins = Number(parts.hour) * 60 + Number(parts.minute);
  const weekend = parts.weekday === "Sat" || parts.weekday === "Sun";
  const session = weekend ? "Closed"
    : mins >= 570 && mins < 960 ? "Market open"
    : mins >= 240 && mins < 570 ? "Pre-market"
    : mins >= 960 && mins < 1200 ? "After hours"
    : "Closed";
  $("#session").textContent = session;  // holidays aren't accounted for
  renderHealth();  // ages count up between server updates
}

// --- server I/O -------------------------------------------------------------------------

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) showLogin();
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(typeof err.detail === "string" ? err.detail : res.statusText);
  }
  return res.json();
}

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, 3500);
}

let riskRender = null;

function handle({ type, data }) {
  switch (type) {
    case "snapshot":
      setClock(data.clock);
      Object.assign(state, data);
      renderHalts();  // first: other tables show HALTED badges
      renderKeywords(); renderWatchlist(); renderUnusual(); renderMovers(); renderSources(); renderNews();
      renderRules(); renderAlertLog(); renderPhone(); setHealth(data.health);
      window.dispatchEvent(new Event("mp-snapshot"));
      break;
    case "phone": state.phone = data; renderPhone(); window.dispatchEvent(new Event("mp-status")); break;
    case "health": setHealth(data); window.dispatchEvent(new Event("mp-status")); break;
    case "halts":
      state.halts = data;
      refreshHalts();
      break;
    case "config":
      Object.assign(state, data);
      renderKeywords(); renderWatchlist(); renderNews(); renderRules(); renderPhone();
      break;
    case "alerts": addAlerts(data); break;
    case "quotes": state.quotes = data; renderWatchlist(); break;
    case "movers": state.movers = data; renderMovers(); break;
    case "unusual": state.unusual = data; renderUnusual(); break;
    case "news": addNews(data); break;
    case "risk":
      Object.assign(state.risk, data);
      // Profiles arrive one symbol at a time after startup; redraw once per frame at most.
      riskRender ??= requestAnimationFrame(() => {
        riskRender = null;
        renderWatchlist(); renderMovers(); renderUnusual(); renderNews();
      });
      break;
  }
  if (["quotes", "config", "news", "halts", "alerts"].includes(type)) refreshDetail(type);
}

let socket = null;

function connect(delay = 1000) {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  socket = ws;
  const setConn = (on) => {
    $("#conn").textContent = on ? "live" : "offline";
    $("#conn").className = `pill ${on ? "on" : "off"}`;
  };
  ws.onopen = () => { setConn(true); delay = 1000; };
  ws.onmessage = (ev) => handle(JSON.parse(ev.data));
  ws.onclose = (ev) => {
    setConn(false);
    if (ev.code === 4401) { showLogin(); return; }  // reconnects after sign-in
    if (ev.code === 4403) { toast("The server refused this page: open it from the Market Pulse address"); return; }
    setTimeout(() => connect(Math.min(delay * 2, 30000)), delay);
  };
}

// --- access token -------------------------------------------------------------------------

function showLogin() {
  if (!$("#login").open) $("#login").showModal();
}

$("#login").addEventListener("cancel", (ev) => ev.preventDefault());  // nothing works without it
$("#login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  const res = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: form.token.value }),
  }).catch(() => null);
  if (!res?.ok) {
    $("#login-error").textContent = res?.status === 401 ? "Wrong access token" : "Server unreachable, try again";
    $("#login-error").hidden = false;
    return;
  }
  form.reset();
  $("#login-error").hidden = true;
  $("#login").close();
  if (!socket || socket.readyState === WebSocket.CLOSED) connect();
});

// --- events -------------------------------------------------------------------------------

document.addEventListener("click", async (ev) => {
  const el = ev.target.closest(
    "[data-sym], [data-remove], [data-remove-kw], [data-remove-rule], [data-tab], [data-left-tab]");
  if (!el) return;
  try {
    if (el.dataset.sym) {
      openDetail(el.dataset.sym);
    } else if (el.dataset.remove) {
      await api("DELETE", `/api/watchlist/${encodeURIComponent(el.dataset.remove)}`);
    } else if (el.dataset.removeKw) {
      await api("DELETE", `/api/keywords/${encodeURIComponent(el.dataset.removeKw)}`);
    } else if (el.dataset.removeRule) {
      await api("DELETE", `/api/alert-rules/${encodeURIComponent(el.dataset.removeRule)}`);
    } else if (el.dataset.tab) {
      state.tab = el.dataset.tab;
      renderMovers();
    } else if (el.dataset.leftTab) {
      setLeftTab(el.dataset.leftTab);
    }
  } catch (err) {
    toast(err.message);
  }
});

$("#add-symbol").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const input = ev.target.symbol;
  try {
    await api("POST", "/api/watchlist", { symbol: input.value });
    input.value = "";
  } catch (err) {
    toast(err.message);
  }
});

$("#add-keyword").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const input = ev.target.keyword;
  try {
    await api("POST", "/api/keywords", { keyword: input.value });
    input.value = "";
  } catch (err) {
    toast(err.message);
  }
});

$("#add-rule").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  try {
    await api("POST", "/api/alert-rules", {
      symbol: form.symbol.value, kind: form.kind.value, value: Number(form.value.value),
    });
    form.value.value = "";
  } catch (err) {
    toast(err.message);
  }
});

$("#unusual-alerts").addEventListener("change", async (ev) => {
  try {
    await api("PUT", "/api/alert-rules/unusual", { enabled: ev.target.checked });
  } catch (err) {
    ev.target.checked = !ev.target.checked;
    toast(err.message);
  }
});

$("#halt-alerts").addEventListener("change", async (ev) => {
  try {
    await api("PUT", "/api/alert-rules/halts", { mode: ev.target.value });
  } catch (err) {
    ev.target.value = state.halt_alerts;
    toast(err.message);
  }
});

for (const box of document.querySelectorAll("[data-phone-cat]")) {
  box.addEventListener("change", async () => {
    try {
      await api("PUT", "/api/phone", { categories: { [box.dataset.phoneCat]: box.checked } });
    } catch (err) {
      box.checked = !box.checked;
      toast(err.message);
    }
  });
}

$("#phone-test").addEventListener("click", async (ev) => {
  ev.target.disabled = true;
  try {
    state.phone = await api("POST", "/api/phone/test");
    renderPhone();
    toast(state.phone.last_error ? "Test not delivered everywhere — see the error under Phone" : "Test sent — check your phone");
  } catch (err) {
    toast(err.message);
  } finally {
    ev.target.disabled = !(state.phone?.targets?.length);
  }
});

$("#health").addEventListener("click", (ev) => {
  ev.stopPropagation();
  const list = $("#health-list");
  list.hidden = !list.hidden;
  $("#health").setAttribute("aria-expanded", String(!list.hidden));
});
document.addEventListener("click", (ev) => {
  if (!$("#health-list").hidden && !ev.target.closest("#health-list")) {
    $("#health-list").hidden = true;
    $("#health").setAttribute("aria-expanded", "false");
  }
});

$("#halt-pill").addEventListener("click", () => {
  $("#halt-scope").value = "today";
  setLeftTab("halts");
  renderHalts();
});
$("#halt-scope").addEventListener("input", () => renderHalts());
setInterval(refreshHalts, 10_000);  // "resumes in Nm" countdowns and halted -> resumed
if (location.hash === "#halts") setLeftTab("halts");
if (location.hash === "#reactions") setLeftTab("reactions");
$("#reactions-days").addEventListener("input", loadReactions);
$("#recap-send").addEventListener("click", async (ev) => {
  ev.target.disabled = true;
  try {
    state.phone = await api("POST", "/api/recap/send");
    renderPhone();
    toast(state.phone.last_error ? "Recap not delivered everywhere — see the error under Phone" : "Recap sent");
  } catch (err) {
    toast(err.message);
  } finally {
    ev.target.disabled = false;
  }
});

for (const id of ["#news-search", "#news-source", "#news-sentiment", "#news-watchlist", "#news-alerts"]) {
  $(id).addEventListener("input", () => renderNews());
}
$("#notify-btn").addEventListener("click", enableAlerts);

tick();
setInterval(tick, 1000);
connect();
