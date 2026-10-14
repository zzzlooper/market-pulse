"use strict";
// Table rows and the badges they carry (HALTED, DIL, ER, sentiment): watchlist, movers,
// unusual volume.

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
    <td><a class="sym" data-sym="${esc(r.symbol)}" title="${esc(r.name)}">${esc(r.symbol)}</a>${haltBadge(r.symbol)}${riskBadge(r.symbol)}${warrantBadge(r.symbol, r.price)}${ssrBadge(r.symbol)}${rotationBadge(r)}${splitBadge(r.symbol)}${day2Badge(r.symbol)}${symBadge(r.symbol)}${luldBadge(r.symbol)}${erBadge(r.symbol)}</td>
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


// ER: the stock reports earnings within 5 days (highlighted on the day). Only the watchlist's
// dates are looked up. Times are shown only when Yahoo's is clearly before the open or after
// the close; its other times are often placeholders.
const ER_DAYS = 5;

function erBadge(sym) {
  const e = state.earnings?.[sym];
  if (!e) return "";
  const today = etDate();
  const days = Math.round((Date.parse(`${e.date}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / DAY_MS);
  if (days < 0 || days > ER_DAYS) return "";
  const day = days === 0 ? "today" : days === 1 ? "tomorrow"
    : new Date(`${e.date}T12:00:00Z`).toLocaleDateString([], { weekday: "long", timeZone: "UTC" });
  const tip = `Earnings ${e.when ? `${e.when} ` : ""}${day}${e.when ? "" : ", time not confirmed"}${e.estimate ? " (estimated date)" : ""}`;
  return ` <span class="er-badge${days === 0 ? " today" : ""}" title="${esc(tip)}">ER${days === 0 ? " today" : ""}</span>`;
}

// Only elevated risk gets a badge, so the tables don't fill with "low". Hover for the reasons;
// the ticker drawer has the full breakdown.
function riskBadge(sym) {
  const r = state.risk[sym];
  if (!r || (r.level !== "high" && r.level !== "medium")) return "";
  const tip = `Dilution risk ${r.level} (${r.points} pts)${r.reasons.length ? `: ${r.reasons.join("; ")}` : ""}`;
  return ` <span class="risk-badge ${r.level}" title="${esc(tip)}">DIL</span>`;
}

// WAR: the price is at or above the exercise price of warrants from the stock's recent filings,
// so holders can exercise them at a profit and sell the new shares. Pre-funded warrants are left
// out (their price is next to nothing). The ticker drawer lists every level and its distance.
function warrantLevelText(lv, withPrice = true) {
  const what = lv.kind === "outstanding" ? "outstanding warrants (average)"
    : lv.kind === "common" ? "warrants" : `${lv.kind} warrants`;
  return `${what}${withPrice ? ` at $${fmtPrice(lv.price)}` : ""} for ${fmtBig(lv.shares)} shares (${lv.form} of ${fmtDay(lv.filed)})`;
}

function warrantBadge(sym, price) {
  const levels = (state.risk[sym]?.warrant_levels ?? []).filter((lv) => price != null && price >= lv.price);
  if (!levels.length) return "";
  const tip = `Warrants in the money: ${levels.map((lv) => warrantLevelText(lv)).join("; ")}. ` +
    "They can be exercised at a profit now, and the new shares sold.";
  return ` <span class="war-badge" title="${esc(tip)}">WAR</span>`;
}

// SSR: the short sale restriction (Rule 201) is in effect: triggered today by a 10% drop from
// the last close (so it holds tomorrow too), or carried over from the last session's drop.
function ssrBadge(sym) {
  const s = state.ssr?.[sym];
  if (!s) return "";
  const tip = s.today
    ? "Short sale restriction: triggered today, when the stock traded 10% below the previous close. " +
      "Shorts can't sell at or below the bid for the rest of today and all of the next trading day."
    : `Short sale restriction all day today, carried over from the 10% drop on ${fmtDay(s.since)}. ` +
      "Shorts can't sell at or below the bid.";
  return ` <span class="ssr-badge" title="${esc(tip)}">SSR</span>`;
}

// RS: a reverse split announced in an 8-K, taking effect within 30 days or done in the last 5.
function splitWhen(rs) {
  if (!rs.effective) return `announced ${fmtDay(rs.filed)}, date not found in the filing`;
  const days = Math.round((Date.parse(`${rs.effective}T00:00:00Z`) - Date.parse(`${etDate()}T00:00:00Z`)) / DAY_MS);
  const rel = days === 0 ? "today" : days === 1 ? "tomorrow" : days === -1 ? "yesterday"
    : days > 0 ? `in ${days} days` : `${-days} days ago`;
  return `effective ${fmtDay(rs.effective)} (${rel})`;
}

function splitBadge(sym) {
  const rs = state.risk[sym]?.reverse_split;
  if (!rs) return "";
  const done = rs.effective && rs.effective < etDate();
  const tip = `1-for-${rs.ratio} reverse split, ${splitWhen(rs)}, per the ${rs.form} of ${fmtDay(rs.filed)}. ` +
    `Every ${rs.ratio} shares become one, so the float is ${rs.ratio}× smaller: freshly split stocks often run, ` +
    "and offerings often follow.";
  return ` <span class="rs-badge${done ? " done" : ""}" title="${esc(tip)}">RS 1:${rs.ratio}</span>`;
}

// LULD: near an estimated limit-up/limit-down band, where a volatility pause comes (app/luld.py).
function luldText(l) {
  const pct = Math.round(((l.upper - l.ref) / l.ref) * 100);
  return `Estimated LULD bands ${fmtPrice(l.lower)}–${fmtPrice(l.upper)} around ${fmtPrice(l.ref)} ` +
    `(average of the last 5 minutes; Tier ${l.tier}, ±${pct}%)`;
}

function luldBadge(sym) {
  const l = state.luld?.[sym];
  if (!l?.near) return "";
  const side = l.near === "up" ? "limit-up" : "limit-down";
  const tip = `${l.away_pct.toFixed(1)}% from the ${side} band. ${luldText(l)}. At a band the stock pauses ` +
    "for 5 minutes if it can't get back inside within 15 seconds. An estimate from the prices this app sees.";
  return ` <span class="luld-badge ${l.near}" title="${esc(tip)}">LULD ${l.near === "up" ? "↑" : "↓"}${l.away_pct.toFixed(1)}%</span>`;
}

// SYM: up with its industry's leader today (the leader up sympathy_lead_pct, this one sympathy_min_pct).
function symBadge(sym) {
  const s = state.sympathy?.[sym];
  if (!s) return "";
  const tip = `Up ${s.change_pct.toFixed(1)}% in sympathy with ${s.leader} ${s.leader_pct >= 0 ? "+" : ""}` +
    `${s.leader_pct.toFixed(0)}% (${s.industry}). The ticker panel lists the industry's movers.`;
  return ` <span class="sym-badge" title="${esc(tip)}">SYM</span>`;
}

// D2: up 30% or more in the last session; today is its second day.
function day2Badge(sym) {
  const d = state.day2?.[sym];
  if (!d) return "";
  const tip = `Day 2: up ${d.change_pct.toFixed(0)}% in the last session (${fmtDay(d.date)}). ` +
    "Second-day continuation is a classic setup, and so is the fade.";
  return ` <span class="d2-badge" title="${esc(tip)}">D2</span>`;
}

// ROT: today's volume has reached the stock's whole public float (Yahoo's figure).
function floatRotation(row) {
  const float = state.floats?.[row.symbol];
  if (!float || !row.volume || (row.session && row.session !== etDate())) return null;  // before the open: yesterday's volume
  return row.volume / float;
}

function rotationBadge(row) {
  const r = floatRotation(row);
  if (r == null || r < (state.thresholds?.rotation_multiple ?? 1)) return "";
  const tip = `Volume today ${fmtVol(row.volume)} is ${r.toFixed(1)}× the float of ${fmtBig(state.floats[row.symbol])} shares: ` +
    "every share available to trade has changed hands at least once.";
  return ` <span class="rot-badge" title="${esc(tip)}">ROT ${r.toFixed(1)}×</span>`;
}

function haltBadge(sym) {
  const h = activeHalts.get(normSym(sym));
  if (!h) return "";
  const resume = h.resume_trade_at ? ` · resumes ${fmtET(h.resume_trade_at)} ET` : "";
  const clock = h.resume_trade_at && Date.parse(h.resume_trade_at) > appNow() ? ` ${countdown(h.resume_trade_at)}` : "";
  return ` <span class="halt-badge" title="${esc(`${h.reason_code} · ${h.reason}${resume}`)}">HALTED${clock}</span>`;
}

// --- sentiment ------------------------------------------------------------------------


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
  for (const b of document.querySelectorAll("[data-tab]")) {  // the movers tabs only, not the left panel's
    b.classList.toggle("active", b.dataset.tab === state.tab);
  }
  const rows = state.movers[state.tab] ?? [];
  const down = state.health.sources.find((s) => s.name === "movers")?.state === "down";
  fillTable($("#movers"), rows.map((r) => quoteRow(r)).join(""), 5,
    down ? "Yahoo screeners unavailable, retrying…" : "Loading…");
}

