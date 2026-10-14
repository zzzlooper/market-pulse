"use strict";
// The left panel's tabs: unusual volume (rendered in tables.js), halts, pre-market gaps,
// catalyst reactions.

// --- trading halts --------------------------------------------------------------------

// The feed only gives times; whether a halt is over depends on the clock, so status is
// derived here and the table re-rendered periodically.
function haltStatus(h, now = appNow()) {
  if (!h.resume_trade_at) return "halted";
  return Date.parse(h.resume_trade_at) <= now ? "resumed" : "resuming";
}

const isToday = (h) => h.halted_at.slice(0, 10) === etDate();  // halted_at carries the ET offset
const normSym = (s) => s.replace("-", ".");                    // Yahoo BRK-B is BRK.B at Nasdaq

let activeHalts = new Map();  // symbol -> halt, for badges elsewhere

// Live countdowns to a resumption: any element with data-until (epoch ms) is updated every
// second; when one runs out, the halt tables and badges are redrawn (halted -> resumed).
function fmtCountdown(ms) {
  if (ms <= 0) return "now";
  const s = Math.ceil(ms / 1000);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}:${String(sec).padStart(2, "0")}`;
}

function countdown(iso) {
  const until = Date.parse(iso);
  return `<span class="countdown" data-until="${until}">${fmtCountdown(until - appNow())}</span>`;
}

function tickCountdowns() {
  const now = appNow();
  let ended = false;
  for (const el of document.querySelectorAll("[data-until]")) {
    const left = Number(el.dataset.until) - now;
    el.textContent = fmtCountdown(left);
    if (left <= 0) {
      el.removeAttribute("data-until");
      ended = true;
    }
  }
  if (ended) refreshHalts();
}

// How a halt resumed, once the first minute's trading is known (app/halts.py resumption()).
function resumedText(h) {
  const info = state.halt_info?.[h.id];
  if (!info) return { html: "Resumed", tip: "" };
  const pct = info.change_pct;
  const tip = `First trade ${fmtPrice(info.open)} at ${fmtET(info.resumed_at)}` +
    (info.halt_price != null ? `, last before the halt ${fmtPrice(info.halt_price)}` : "") +
    (info.low != null ? `; first minute ${fmtPrice(info.low)}–${fmtPrice(info.high)}` : "");
  return { html: `Resumed${pct != null ? ` <span class="${dir(pct)}">${fmtPct(pct)}</span>` : ""}`, tip };
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
    const resumed = status === "resumed" ? resumedText(h) : null;
    const label = status === "resuming" ? `in ${countdown(h.resume_trade_at)}` : resumed ? resumed.html : "Halted";
    const tip = `${h.name} · ${h.market}${h.pause_price ? ` · pause price ${h.pause_price}` : ""}`;
    return `<tr class="halt-row ${status}">
      <td><a class="sym" data-sym="${esc(h.symbol)}" title="${esc(tip)}">${esc(h.symbol)}</a></td>
      <td>${halted}</td>
      <td class="reason ${esc(h.category)}" title="${esc(`${h.reason_code} · ${h.reason}`)}">${esc(h.reason_code)}</td>
      <td>${h.resume_trade_at ? fmtET(h.resume_trade_at) : "–"}</td>
      <td class="status" title="${esc(resumed?.tip ?? "")}">${label}</td>
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
  $("#tab-gaps").hidden = tab !== "gaps";
  if (tab === "gaps") renderGaps();
  clearInterval(reactionsTimer);
  if (tab === "reactions") {
    loadReactions();
    reactionsTimer = setInterval(loadReactions, 120_000);
  }
}

// --- pre-market gaps ---------------------------------------------------------------------

function etMinutes() {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(appNow())).map((x) => [x.type, x.value]));
  return { mins: Number(p.hour) * 60 + Number(p.minute), weekend: p.weekday === "Sat" || p.weekday === "Sun" };
}

function renderGaps() {
  const { mins, weekend } = etMinutes();
  const scanning = !weekend && mins >= 240 && mins < 600;
  const rows = state.gaps ?? [];
  const atOpen = rows.some((g) => g.at_open);
  $("#gaps-note").textContent = scanning
    ? `Gaps ≥ ${state.gap_min_pct}% ${atOpen ? "at the open" : "pre-market"} · watchlist, fresh catalysts, movers`
    : "The scan runs 4:00–10:00 ET on weekdays";
  const html = rows.map((g) => {
    const why = g.catalyst
      ? `<span class="clamp" title="${esc(`${fmtTime(g.catalyst.at)} · ${g.catalyst.text}`)}">${
        g.catalyst.filing ? `<b>${esc(g.catalyst.text)}</b>` : esc(g.catalyst.text)}</span>`
      : `<span class="muted">${esc(g.why)}</span>`;
    return `<tr><td><a class="sym" data-sym="${esc(g.symbol)}">${esc(g.symbol)}</a>${haltBadge(g.symbol)}${riskBadge(g.symbol)}${warrantBadge(g.symbol, g.price)}${ssrBadge(g.symbol)}${splitBadge(g.symbol)}${day2Badge(g.symbol)}${erBadge(g.symbol)}</td>
      <td class="${dir(g.gap_pct)}">${fmtPct(g.gap_pct)}</td><td>${fmtPrice(g.price)}</td>
      <td>${g.at_open ? `<small class="muted">open</small>` : g.pre_minutes}</td><td class="why">${why}</td></tr>`;
  }).join("");
  fillTable($("#gaps"), html, 5, scanning ? `No gaps of ${state.gap_min_pct}% or more yet` : "No gaps scanned today");
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
    $("#reactions-csv").href = `/api/reactions.csv?days=${days}`;
    const [r, recap] = await Promise.all([api("GET", `/api/reactions?days=${days}`), api("GET", "/api/recap")]);
    $("#reactions-note").textContent = `${r.catalysts} catalysts, ${r.tracked - r.catalysts} alerts tracked`;
    $("#reactions-types").innerHTML = reactionRows(r.by_type,
      "Nothing tracked yet: filings, halts and alert headlines are measured as they arrive");
    $("#reactions-alerts").innerHTML = reactionRows(r.alerts,
      "No alerts yet: breakouts, level crossings, float rotations, resumptions and your rules are measured as they fire");
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

