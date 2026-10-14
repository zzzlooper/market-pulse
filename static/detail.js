"use strict";
// Ticker detail drawer: intraday chart with news/halt markers, key stats, and the ticker's
// news, halts and alerts. Profile and bars come from /api/ticker; everything else is read
// from the live `state` in app.js, so the drawer updates as new data streams in.

const detail = {
  symbol: null, range: "1d", data: null, error: null, timer: null, seq: 0, hover: null, hoverY: null, geom: null,
  dilution: null, dilutionError: null,  // from /api/dilution, loaded once per open (cached 6h server-side)
};
const DETAIL_REFRESH_MS = 30_000;

const etFormat = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  weekday: "short", month: "short", day: "numeric",
});

// "13:29" for today, "Oct 8 13:29" otherwise, both ET.
function fmtETShort(iso) {
  const d = new Date(iso);
  const p = Object.fromEntries(etFormat.formatToParts(d).map((x) => [x.type, x.value]));
  return etDate(d) === etDate() ? `${p.hour}:${p.minute}` : `${p.month} ${p.day} ${p.hour}:${p.minute}`;
}

function fmtBig(v, prefix = "") {
  if (v == null) return "–";
  for (const [div, s] of [[1e12, "T"], [1e9, "B"], [1e6, "M"], [1e3, "K"]]) {
    if (Math.abs(v) >= div) return `${prefix}${(v / div).toFixed(2)}${s}`;
  }
  return `${prefix}${v}`;
}

// --- open / close / load ---------------------------------------------------------------

// The drawer is a history entry, so a phone's Back gesture closes it instead of leaving the
// dashboard. A deep link (a tapped phone alert) puts the dashboard underneath it first.
function pushDrawer(name, hash) {
  const base = location.pathname + location.search;
  if (history.state?.drawer === name) {
    history.replaceState({ drawer: name }, "", base + hash);
  } else {
    if (location.hash.startsWith("#ticker=") && !history.state) history.replaceState(null, "", base);
    history.pushState({ drawer: name }, "", base + hash);
  }
}

async function openDetail(symbol) {
  pushDrawer("detail", `#ticker=${encodeURIComponent(symbol)}`);
  if (symbol !== detail.symbol) {
    Object.assign(detail, { symbol, data: null, error: null, hover: null, hoverY: null, dilution: null, dilutionError: null });
    renderDetail();
    loadDilution(symbol);
  }
  $("#detail").hidden = $("#detail-backdrop").hidden = false;
  clearInterval(detail.timer);
  detail.timer = setInterval(loadDetail, DETAIL_REFRESH_MS);
  await loadDetail();
}

function closeDetail(fromHistory = false) {
  if (!fromHistory && history.state?.drawer === "detail") {
    history.back();  // popstate closes it, keeping history and drawer in step
    return;
  }
  clearInterval(detail.timer);
  detail.symbol = null;
  detail.seq++;  // drop any response still in flight
  $("#detail").hidden = $("#detail-backdrop").hidden = true;
}

async function loadDetail() {
  const seq = ++detail.seq;
  const { symbol, range } = detail;
  try {
    const data = await api("GET", `/api/ticker/${encodeURIComponent(symbol)}?range=${range}`);
    if (seq !== detail.seq) return;  // superseded by a newer request or closed
    const firstLoad = !detail.data;
    Object.assign(detail, { data, error: null });
    prepareBars(data.chart.bars);
    firstLoad ? renderDetail() : (updateDetailPrice(), updateDetailStats(), drawDetailChart());
  } catch (err) {
    if (seq !== detail.seq) return;
    detail.error = err.message;
    if (!detail.data) renderDetail();
  }
}

async function loadDilution(symbol) {
  try {
    const data = await api("GET", `/api/dilution/${encodeURIComponent(symbol)}`);
    if (symbol !== detail.symbol) return;
    Object.assign(detail, { dilution: data, dilutionError: null });
  } catch (err) {
    if (symbol !== detail.symbol) return;
    detail.dilutionError = err.message;
  }
  renderDilution();
}

// Precompute ET labels once per load; the chart redraws on every mouse move.
function prepareBars(bars) {
  for (const b of bars) {
    const p = Object.fromEntries(etFormat.formatToParts(new Date(b.t * 1000)).map((x) => [x.type, x.value]));
    const mins = Number(p.hour) * 60 + Number(p.minute);
    b.regular = mins >= 570 && mins < 960;
    b.time = `${p.hour}:${p.minute}`;
    b.day = `${p.weekday} ${p.month} ${p.day}`;
  }
}

// --- rendering --------------------------------------------------------------------------

function renderDetail() {
  const sym = detail.symbol;
  const p = detail.data?.profile ?? {};
  const meta = [p.exchange, p.sector, p.industry].filter(Boolean).map(esc).join(" · ");
  $("#detail").innerHTML = `
    <header class="detail-head">
      <div>
        <h2>${esc(sym)} <small>${esc(p.name ?? "")}</small></h2>
        <div class="muted">${meta}</div>
      </div>
      <div class="detail-actions">
        <button data-detail-action="watch" id="detail-watch"></button>
        <button data-detail-action="news" title="Show this ticker in the news feed">Filter news</button>
        <button data-detail-action="close" class="x" title="Close (Esc)">×</button>
      </div>
    </header>
    ${detail.error && !detail.data ? `<p class="detail-msg">${esc(detail.error)}</p>`
      : !detail.data ? `<p class="detail-msg muted">Loading ${esc(sym)}…</p>` : `
    <div id="detail-price" class="detail-price"></div>
    <nav class="tabs detail-range">
      <button data-range="1d">1 day (1m, incl. pre/post)</button>
      <button data-range="5d">5 days (5m)</button>
    </nav>
    <div class="chart-wrap">
      <canvas id="detail-chart"></canvas>
      <div id="chart-tip" hidden></div>
      <div id="chart-alert" class="chart-alert" hidden></div>
    </div>
    <div class="chart-legend muted">
      <span><i class="dot bullish"></i>bullish news</span><span><i class="dot bearish"></i>bearish news</span>
      <span><i class="dot neutral"></i>other news</span><span><i class="band"></i>halted</span>
      <span><i class="band ext"></i>pre/post-market</span>
      <span class="chart-hint">Click or tap the chart to set an alert at that price</span>
    </div>
    <div id="detail-stats" class="stats"></div>
    <section id="detail-dilution" class="dilution"></section>
    <div class="detail-cols">
      <section><h3>News <small>times ET</small></h3><ul id="detail-news" class="detail-list"></ul></section>
      <section>
        <h3>Halts</h3><ul id="detail-halts" class="detail-list"></ul>
        <h3>Alerts</h3><div id="detail-alerts"></div>
      </section>
    </div>
    ${p.summary ? `<details class="about"><summary>About ${esc(p.name || sym)}</summary>
      <p>${esc(p.summary)}</p>${p.website ? `<a href="${esc(safeUrl(p.website))}" target="_blank" rel="noopener noreferrer">${esc(p.website)}</a>` : ""}
    </details>` : ""}`}`;
  updateDetailWatch();
  if (!detail.data) return;
  for (const b of document.querySelectorAll(".detail-range button")) {
    b.classList.toggle("active", b.dataset.range === detail.range);
  }
  updateDetailPrice();
  updateDetailStats();
  renderDilution();
  updateDetailLists();
  drawDetailChart();
}

// --- dilution risk ------------------------------------------------------------------------

// "2026-06-30" -> "Jun 30, 2026". Dates from SEC have no time, so format them as UTC.
const fmtDay = (iso) => (iso ? new Date(`${iso}T00:00:00Z`).toLocaleDateString([], {
  month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }) : "–");

// Warrants as the company's own filings state them (best effort: read from their first pages).
function warrantStat(w, stat) {
  if (!w || !w.checked) return stat("Warrants", "–", "", "No offering in the last 12 months to read them from");
  const out = w.outstanding;
  if (out) {
    const price = out.exercise_price != null ? ` @ $${out.exercise_price.toFixed(2)}` : "";
    return stat("Warrants", `${fmtBig(out.shares)}${price}`, w.offerings_with_warrants ? "hot" : "",
      `Outstanding per the ${out.form} of ${fmtDay(out.as_of)}` +
      (w.latest_sold ? `; latest sold: ${w.latest_sold.text}` : ""));
  }
  if (w.offerings_with_warrants) {
    return stat("Warrants", `sold ×${w.offerings_with_warrants}`, "hot", w.latest_sold.text);
  }
  return stat("Warrants", "none found", "", `Not mentioned on the first pages of ${w.checked} recent filing(s)`);
}

function renderDilution() {
  const el = $("#detail-dilution");
  if (!el) return;
  const d = detail.dilution;
  const head = (extra = "") => `<h3>Dilution risk ${extra}</h3>`;
  if (detail.dilutionError) {
    el.innerHTML = head() + `<p class="muted">${esc(detail.dilutionError)}</p>`;
    return;
  }
  if (!d) {
    el.innerHTML = head() + `<p class="muted">Reading SEC filings…</p>`;
    return;
  }
  const c = d.cash, s = d.shares, h = d.history;
  const money = (v) => (c.currency === "USD" ? fmtBig(v, "$") : `${fmtBig(v)} ${esc(c.currency)}`);
  const stat = (label, value, cls = "", tip = "") =>
    `<div title="${esc(tip)}"><span>${label}</span><b class="${cls}">${value}</b></div>`;
  let left = "–";
  if (c?.months_left != null) left = c.months_left <= 0 ? "out?" : `~${c.months_left.toFixed(1)} mo`;
  else if (c?.monthly_burn === 0) left = "no burn";
  const pct = s?.change_pct;
  el.innerHTML = head(`<span class="risk ${esc(d.level)}">${esc(d.level)}</span>
      <small><a href="${esc(safeUrl(d.sec_url))}" target="_blank" rel="noopener noreferrer">SEC filings ↗</a></small>`) + `
    <ul class="risk-reasons">${d.reasons.map((r) => `
      <li><b class="pts ${r.points > 0 ? "bad" : r.points < 0 ? "good" : ""}">${r.points > 0 ? "+" : ""}${r.points}</b>
        ${esc(r.text)}</li>`).join("") || `<li class="muted">No warning signs in the filings</li>`}</ul>
    <div class="stats">
      ${stat("Cash", c ? money(c.cash) : "–", "", c ? `As of ${fmtDay(c.as_of)}` : "No XBRL cash figure")}
      ${stat("Monthly burn", c?.monthly_burn != null ? money(c.monthly_burn) : "–", "",
        c?.burn_months ? `Operating cash flow over ${c.burn_months} months` : "")}
      ${stat("Cash left", left, c?.months_left != null && c.months_left < 6 ? "down" : "",
        "Cash at the last report minus the burn since, at the reported rate")}
      ${stat("Shares out", s ? fmtBig(s.shares) + (s.before_split ? "*" : "") : "–", "",
        s ? `As of ${fmtDay(s.as_of)}` + (s.before_split
          ? `, before the ${fmtDay(s.before_split)} shareholder-rights change (likely a reverse split); not split-adjusted` : "") : "")}
      ${stat("Shares 1y", pct != null ? `${pct > 0 ? "+" : ""}${pct.toFixed(0)}%` : "–", pct >= 20 ? "down" : "",
        s?.year_ago ? `${fmtBig(s.year_ago)} a year earlier` : "")}
      ${stat("Shelf", h.shelf ? `${h.shelf.effective ? "effective" : "filed"}` : "none",
        h.shelf ? "hot" : "", h.shelf ? `${h.shelf.form} filed ${fmtDay(h.shelf.filed)}, usable to ~${fmtDay(h.shelf.expires)}` : "")}
      ${stat("Offerings 12m", String(h.offerings_12m.length), h.offerings_12m.length ? "hot" : "",
        h.offerings_12m.map(fmtDay).join(", "))}
      ${warrantStat(d.warrants, stat)}
      ${d.reverse_split ? stat("Reverse split", `1:${d.reverse_split.ratio}`, "hot",
        `1-for-${d.reverse_split.ratio}, ${splitWhen(d.reverse_split)}, per the ${d.reverse_split.form} of ${fmtDay(d.reverse_split.filed)}`) : ""}
    </div>
    <div id="detail-levels"></div>
    ${h.events.length ? `<details class="dilution-events"><summary>Dilution-related filings (${h.events.length})</summary>
      <ul class="detail-list">${h.events.map((e) => `
        <li><div class="meta">${fmtDay(e.filed)} · ${esc(e.form)}</div>
          <a href="${esc(safeUrl(e.url))}" target="_blank" rel="noopener noreferrer">${esc(e.label)}</a></li>`).join("")}
      </ul></details>` : ""}
    <p class="muted fine">From SEC filings and XBRL financials, which lag by up to a quarter. Filings since
      ${fmtDay(h.covered_since)}.</p>`;
  updateDetailLevels();
  drawDetailChart();  // the warrant and offering lines
}

// The price levels from the filings, highest first, with the current price between them: warrant
// exercise prices and the prices recent offerings sold at. Redrawn as prices stream in.
function updateDetailLevels() {
  const el = $("#detail-levels");
  if (!el) return;
  const w = detail.dilution?.warrants;
  const levels = [...(w?.levels ?? []), ...(w?.offering_levels ?? [])].sort((a, b) => b.price - a.price);
  if (!levels.length) {
    el.innerHTML = "";
    return;
  }
  const price = detailPrice();
  const item = (lv) => {
    const offering = lv.kind === "offering";
    const away = price != null ? (lv.price / price - 1) * 100 : null;
    const size = Math.abs(away ?? 0);
    const where = away == null ? ""
      : !offering && away <= 0 ? `<b class="hot">in the money</b>`
      : `<span class="muted">${size.toFixed(size < 10 ? 1 : 0)}% ${away > 0 ? "above" : "below"} the price</span>`;
    const what = offering ? `offering price (${lv.form} of ${fmtDay(lv.filed)})` : warrantLevelText(lv, false);
    return `<li${offering ? ` class="offering"` : ""}><b>$${fmtPrice(lv.price)}</b> ${esc(what)} ${where}</li>`;
  };
  const above = levels.filter((lv) => price == null || lv.price > price);
  const below = levels.filter((lv) => price != null && lv.price <= price);
  const now = price != null ? `<li class="now">now $${fmtPrice(price)}</li>` : "";
  el.innerHTML = `<p class="muted fine">Price levels from the filings · watchlist stocks and movers alert when they cross
    a warrant price going up, or an offering price either way</p>
    <ul class="price-levels">${above.map(item).join("")}${now}${below.map(item).join("")}</ul>`;
}

function detailPrice() {
  return detailQuote()?.price ?? detail.data?.chart.bars.at(-1)?.c ?? null;
}

function detailQuote() {
  // Prefer the streamed watchlist quote (real-time with Finnhub), then the server's snapshot.
  return state.quotes.find((q) => q.symbol === detail.symbol && q.price != null) ?? detail.data?.quote ?? null;
}

function updateDetailPrice() {
  const el = $("#detail-price");
  if (!el) return;
  const q = detailQuote();
  const price = detailPrice();
  const prev = detail.data.profile.prev_close ?? q?.prev_close;
  const pct = q?.change_pct ?? (price != null && prev ? (price / prev - 1) * 100 : null);
  const live = q?.live ? `<span class="live-dot" title="Real-time (Finnhub)"></span>` : "";
  const delayed = q?.live ? "" : `<small class="muted">delayed ~15 min</small>`;
  el.innerHTML = `${live}<strong>${fmtPrice(price)}</strong>
    <span class="${dir(pct)}">${fmtPct(pct)}</span>${haltBadge(detail.symbol)}${warrantBadge(detail.symbol, price)}${ssrBadge(detail.symbol)}${splitBadge(detail.symbol)}${day2Badge(detail.symbol)}${luldBadge(detail.symbol)} ${delayed}`;
}

function updateDetailWatch() {
  const btn = $("#detail-watch");
  if (btn) btn.textContent = state.watchlist.includes(detail.symbol) ? "★ On watchlist" : "☆ Add to watchlist";
}

function luldStat(l) {
  if (!l) return `<div title="Estimated from the prices of the last 5 minutes, in regular hours"><span>LULD bands</span><b>–</b></div>`;
  return `<div title="${esc(luldText(l))}. An estimate from the prices this app sees."><span>LULD bands <small>est.</small></span>` +
    `<b class="${l.near ? "hot" : ""}">${fmtPrice(l.lower)}–${fmtPrice(l.upper)}</b></div>`;
}

function floatStat(float, volume) {
  if (!float) return "–";
  const r = volume ? volume / float : null;
  return fmtBig(float) + (r >= 0.1 ? ` <small class="${r >= 1 ? "up" : "muted"}">(${r.toFixed(1)}× today)</small>` : "");
}

function updateDetailStats() {
  const el = $("#detail-stats");
  if (!el) return;
  const p = detail.data.profile;
  const q = detailQuote();
  const volume = q?.volume ?? detail.data.chart.bars.filter((b) => b.regular).reduce((s, b) => s + b.v, 0);
  const range = (lo, hi) => (lo != null && hi != null ? `${fmtPrice(lo)} – ${fmtPrice(hi)}` : "–");
  let earnings = "–";
  if (p.next_earnings) {
    const d = new Date(p.next_earnings * 1000);
    const days = Math.ceil((d - appNow()) / 86_400_000);
    earnings = `${d.toLocaleDateString([], { month: "short", day: "numeric" })} <small class="muted">(${days <= 0 ? "today" : `in ${days}d`})</small>`;
  }
  const stat = (label, value, cls = "") => `<div><span>${label}</span><b class="${cls}">${value}</b></div>`;
  el.innerHTML = [
    stat("Open", fmtPrice(p.open)),
    stat("Prev close", fmtPrice(p.prev_close)),
    stat("Gap", fmtPct(p.gap_pct), dir(p.gap_pct)),
    stat("PM high", fmtPrice(state.pre_highs?.[detail.symbol]),
      q?.price != null && q.price >= state.pre_highs?.[detail.symbol] ? "up" : ""),
    luldStat(state.luld?.[detail.symbol]),
    stat("Day range", range(p.day_low, p.day_high)),
    stat("52-wk range", range(p.week52_low, p.week52_high)),
    stat("Volume", fmtVol(volume)),
    stat("Avg volume", fmtVol(p.avg_volume)),
    stat("RVOL", fmtRvol(q?.rvol), q?.rvol >= state.rvol_threshold ? "hot" : ""),
    stat("Market cap", fmtBig(p.market_cap, "$")),
    stat("Float", floatStat(p.float_shares ?? state.floats[detail.symbol], volume)),
    stat("Short % float", p.short_pct_float != null ? `${(p.short_pct_float * 100).toFixed(1)}%` : "–",
      p.short_pct_float >= 0.2 ? "hot" : ""),
    stat("Days to cover", p.short_ratio != null ? p.short_ratio.toFixed(1) : "–"),
    stat("P/E", p.pe != null ? p.pe.toFixed(1) : "–"),
    stat("Beta", p.beta != null ? p.beta.toFixed(2) : "–"),
    stat("Next earnings", earnings),
  ].join("");
}

function detailNews() {
  return state.news.filter((n) => n.tickers.includes(detail.symbol));
}

function detailHalts() {
  return state.halts.filter((h) => h.symbol === normSym(detail.symbol));
}

function updateDetailLists() {
  if (!$("#detail-news")) return;
  const sym = detail.symbol;

  // Times in ET throughout the drawer, to line up with the chart axis and halt times.
  $("#detail-news").innerHTML = detailNews().slice(0, 40).map((n) => `
    <li><div class="meta"><time>${fmtETShort(n.published)}</time> ${esc(n.source)} ${n.filing ? filingBadge(n.filing) : sentimentBadge(n.sentiment)}</div>
      <a href="${esc(safeUrl(n.url))}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a></li>`).join("")
    || `<li class="muted">No headlines for ${esc(sym)} since the app started</li>`;

  $("#detail-halts").innerHTML = detailHalts().map((h) => {
    const status = haltStatus(h);
    const resume = h.resume_trade_at ? ` → ${fmtET(h.resume_trade_at)}` : " → no resumption time yet";
    const resumed = status === "resumed" ? resumedText(h) : null;
    const label = status === "resuming" ? `resumes in ${countdown(h.resume_trade_at)}`
      : resumed ? resumed.html : status;
    return `<li class="halt-row ${status}"><b class="reason ${esc(h.category)}">${esc(h.reason_code)}</b>
      ${isToday(h) ? fmtET(h.halted_at) : h.halted_at.slice(0, 10)}${resume}
      <span class="muted">· ${esc(h.reason)}</span> <span class="status" title="${esc(resumed?.tip ?? "")}">${label}</span>
      ${resumed?.tip ? `<div class="meta">${esc(resumed.tip)}</div>` : ""}</li>`;
  }).join("") || `<li class="muted">No halts in the feed</li>`;

  const watched = state.watchlist.includes(sym);
  const rules = state.alert_rules.filter((r) => r.symbol === sym);
  const general = state.alert_rules.filter((r) => !r.symbol);
  const fired = state.alerts.filter((a) => a.symbol === sym || a.symbol === normSym(sym)).slice(0, 10);
  $("#detail-alerts").innerHTML = `
    <div class="keywords">${rules.map((r) => `<span class="chip">${esc(RULE_LABELS[r.kind](r.value))}
      <button data-remove-rule="${esc(r.id)}" title="Remove">×</button></span>`).join("")
      || `<span class="muted">No rules for ${esc(sym)}</span>`}</div>
    ${watched ? `
      <form id="detail-rule" class="rule-form">
        <select name="kind">
          <option value="above">Price above</option><option value="below">Price below</option>
          <option value="move">Moves ±%</option><option value="rvol">RVOL ≥</option>
        </select>
        <input name="value" type="number" step="any" min="0" required placeholder="Value">
        <button>Add</button>
      </form>
      ${general.length ? `<p class="muted small">Also covered by: ${general.map((r) => esc(RULE_LABELS[r.kind](r.value))).join(", ")} (all watchlist)</p>` : ""}`
      : `<p class="muted small">Add ${esc(sym)} to your watchlist to set alerts for it.</p>`}
    <ul class="detail-list">${fired.map((a) => `<li><time>${fmtETShort(a.time)}</time> ${esc(a.message)}</li>`).join("")}</ul>`;
}

// --- chart -------------------------------------------------------------------------------

// Index of the bar covering epoch second t, or -1 if t is outside the chart. News can be
// newer than the last (delayed) bar; up to an hour past it still pins to the last bar.
function barIndexAt(bars, t) {
  if (!bars.length || t < bars[0].t || t > bars.at(-1).t + 3600) return -1;
  let lo = 0, hi = bars.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (bars[mid].t <= t) lo = mid; else hi = mid - 1;
  }
  return lo;
}

function chartMarkers(bars) {
  const news = detailNews().map((n) => ({ i: barIndexAt(bars, Date.parse(n.published) / 1000), n }))
    .filter((m) => m.i >= 0);
  const halts = detailHalts().map((h) => {
    const start = barIndexAt(bars, Date.parse(h.halted_at) / 1000);
    if (start < 0) return null;
    const resume = h.resume_trade_at ? barIndexAt(bars, Date.parse(h.resume_trade_at) / 1000) : -1;
    return { start, end: resume >= 0 ? resume : bars.length - 1, h };
  }).filter(Boolean);
  return { news, halts };
}

function drawDetailChart() {
  const canvas = $("#detail-chart");
  // Mouse events still reach the canvas while the drawer closes, after symbol is cleared.
  if (!canvas || !detail.data || !detail.symbol) return;
  const bars = detail.data.chart.bars;
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth, H = canvas.clientHeight;
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const css = getComputedStyle(document.documentElement);
  const color = (name) => css.getPropertyValue(name).trim();
  ctx.font = "11px system-ui, sans-serif";

  if (!bars.length) {
    ctx.fillStyle = color("--muted");
    ctx.fillText("No intraday data for this range", 12, H / 2);
    detail.geom = null;
    return;
  }

  const pad = { l: 6, r: 58, t: 16, b: 20 };
  const innerH = H - pad.t - pad.b;
  const volH = innerH * 0.2;
  const priceH = innerH - volH - 6;
  const n = bars.length;
  const plotW = W - pad.l - pad.r;
  const step = plotW / Math.max(n - 1, 1);
  const x = (i) => pad.l + (n === 1 ? plotW / 2 : i * step);

  const prev = detail.data.profile.prev_close;
  const base = detail.range === "1d" && prev ? prev : bars[0].o ?? bars[0].c;
  let lo = Math.min(...bars.map((b) => b.l ?? b.c));
  let hi = Math.max(...bars.map((b) => b.h ?? b.c));
  if (base) { lo = Math.min(lo, base); hi = Math.max(hi, base); }
  // Levels just outside the price range widen it so they show; far-away ones don't squash the chart.
  const levels = chartLevels();
  const near = hi - lo || hi * 0.05;
  for (const lv of levels) {
    if (lv.price >= lo - near * 0.5 && lv.price <= hi + near * 0.5) { lo = Math.min(lo, lv.price); hi = Math.max(hi, lv.price); }
  }
  const span = hi - lo || hi * 0.01 || 1;
  lo -= span * 0.06; hi += span * 0.06;
  const y = (p) => pad.t + ((hi - p) / (hi - lo)) * priceH;
  const volTop = pad.t + priceH + 6;
  const maxVol = Math.max(...bars.map((b) => b.v), 1);
  const barW = Math.max(step * 0.7, 1);
  detail.geom = { pad, plotW, n, step, lo, hi, priceH };

  // pre/post-market shading
  ctx.fillStyle = "#ffffff08";
  for (let i = 0; i < n; i++) {
    if (!bars[i].regular) ctx.fillRect(x(i) - step / 2, pad.t, step, innerH);
  }

  const { news, halts } = chartMarkers(bars);
  ctx.fillStyle = "#f8514933";
  for (const m of halts) ctx.fillRect(x(m.start), pad.t, Math.max(x(m.end) - x(m.start), 3), innerH);

  // price grid + labels
  ctx.strokeStyle = color("--border");
  ctx.fillStyle = color("--muted");
  ctx.lineWidth = 1;
  for (let k = 0; k <= 4; k++) {
    const p = lo + ((hi - lo) * k) / 4;
    const yy = Math.round(y(p)) + 0.5;
    ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(pad.l + plotW, yy); ctx.stroke();
    ctx.fillText(fmtPrice(p), pad.l + plotW + 6, yy + 4);
  }

  // reference line: previous close (1d) or first bar (5d)
  ctx.setLineDash([4, 4]);
  ctx.strokeStyle = color("--muted");
  ctx.beginPath(); ctx.moveTo(pad.l, y(base)); ctx.lineTo(pad.l + plotW, y(base)); ctx.stroke();
  ctx.setLineDash([]);

  // volume
  for (let i = 0; i < n; i++) {
    const b = bars[i];
    ctx.fillStyle = b.c >= (b.o ?? b.c) ? "#3fb95066" : "#f8514966";
    const h = (b.v / maxVol) * volH;
    ctx.fillRect(x(i) - barW / 2, volTop + volH - h, barW, h);
  }

  // price line + fill
  const last = bars.at(-1).c;
  const lineColor = last >= base ? color("--up") : color("--down");
  ctx.beginPath();
  bars.forEach((b, i) => (i ? ctx.lineTo(x(i), y(b.c)) : ctx.moveTo(x(i), y(b.c))));
  ctx.strokeStyle = lineColor;
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.lineTo(x(n - 1), pad.t + priceH);
  ctx.lineTo(x(0), pad.t + priceH);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, pad.t, 0, pad.t + priceH);
  grad.addColorStop(0, `${lineColor}40`);
  grad.addColorStop(1, `${lineColor}00`);
  ctx.fillStyle = grad;
  ctx.fill();

  // levels: your alert rules and the ones from the filings, labelled at the left edge
  for (const lv of levels) {
    if (lv.price < lo || lv.price > hi) continue;
    const yy = Math.round(y(lv.price)) + 0.5;
    ctx.strokeStyle = lv.color;
    ctx.setLineDash(lv.dash);
    ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(pad.l + plotW, yy); ctx.stroke();
    ctx.setLineDash([]);
    const text = `${lv.label} ${fmtPrice(lv.price)}`;
    ctx.fillStyle = color("--bg");
    ctx.fillRect(pad.l + 2, yy - 13, ctx.measureText(text).width + 6, 12);
    ctx.fillStyle = lv.color;
    ctx.fillText(text, pad.l + 5, yy - 3);
  }

  // news markers along the top
  const dotColor = { bullish: color("--up"), bearish: color("--down"), neutral: color("--muted") };
  for (const m of news) {
    ctx.fillStyle = dotColor[m.n.sentiment.label];
    ctx.beginPath(); ctx.arc(x(m.i), pad.t - 7, 3.5, 0, Math.PI * 2); ctx.fill();
  }

  // time axis: ~6 labels, plus day boundaries on 5d
  ctx.fillStyle = color("--muted");
  const every = Math.max(1, Math.round(n / 6));
  for (let i = 0; i < n; i += every) {
    const label = detail.range === "1d" ? bars[i].time : bars[i].day;
    ctx.fillText(label, Math.min(x(i), pad.l + plotW - 40), H - 5);
  }

  // hover: the price under the pointer, where a click would set an alert
  if (detail.hoverY != null && detail.hoverY >= pad.t && detail.hoverY <= pad.t + priceH) {
    const p = hi - ((detail.hoverY - pad.t) / priceH) * (hi - lo);
    ctx.strokeStyle = color("--muted");
    ctx.setLineDash([2, 3]);
    ctx.beginPath(); ctx.moveTo(pad.l, detail.hoverY); ctx.lineTo(pad.l + plotW, detail.hoverY); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = color("--border");
    ctx.fillRect(pad.l + plotW + 2, detail.hoverY - 8, pad.r - 2, 16);
    ctx.fillStyle = color("--text");
    ctx.fillText(fmtPrice(p), pad.l + plotW + 6, detail.hoverY + 4);
  }

  // hover crosshair
  if (detail.hover != null && detail.hover < n) {
    const i = detail.hover;
    ctx.strokeStyle = color("--text");
    ctx.globalAlpha = 0.4;
    ctx.beginPath(); ctx.moveTo(x(i), pad.t); ctx.lineTo(x(i), volTop + volH); ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillStyle = lineColor;
    ctx.beginPath(); ctx.arc(x(i), y(bars[i].c), 3, 0, Math.PI * 2); ctx.fill();
  }
}

// Horizontal lines on the chart: your price rules for this stock, the warrant and offering prices
// from its filings, and today's pre-market high (1-day view).
function chartLevels() {
  const sym = detail.symbol;
  const css = getComputedStyle(document.documentElement);
  const hot = css.getPropertyValue("--hot").trim();
  const muted = css.getPropertyValue("--muted").trim();
  const out = state.alert_rules.filter((r) => r.symbol === sym && (r.kind === "above" || r.kind === "below"))
    .map((r) => ({ price: r.value, label: `alert ${r.kind === "above" ? "↑" : "↓"}`, color: hot, dash: [6, 3] }));
  const w = detail.dilution?.warrants;
  const risk = state.risk[sym];
  for (const lv of w?.levels ?? risk?.warrant_levels ?? []) {
    out.push({ price: lv.price, label: lv.kind === "outstanding" ? "warrants (avg)" : `${lv.kind === "common" ? "" : `${lv.kind} `}warrants`,
      color: "#f0883e", dash: [] });
  }
  for (const lv of w?.offering_levels ?? risk?.offering_levels ?? []) {
    out.push({ price: lv.price, label: `offering ${lv.form}`, color: "#58a6ff", dash: [] });
  }
  const pmh = state.pre_highs?.[sym];
  if (pmh && detail.range === "1d") out.push({ price: pmh, label: "pre-market high", color: muted, dash: [2, 2] });
  return out;
}

// A click on the chart offers an alert at that price: above it if the stock is below, and the
// other way round. A stock not on the watchlist is added first (rules cover the watchlist).
function offerChartAlert(offsetX, offsetY) {
  const g = detail.geom;
  const box = $("#chart-alert");
  if (!g || offsetY < g.pad.t || offsetY > g.pad.t + g.priceH) {
    box.hidden = true;
    return;
  }
  const raw = g.hi - ((offsetY - g.pad.t) / g.priceH) * (g.hi - g.lo);
  const price = Number(raw.toFixed(raw < 1 ? 4 : 2));
  const now = detailPrice();
  const kind = now != null && price < now ? "below" : "above";
  const sym = detail.symbol;
  const watched = state.watchlist.includes(sym);
  Object.assign(box.dataset, { kind, price: String(price) });
  box.innerHTML = `<span>Alert when ${esc(sym)} goes ${kind} <b>$${fmtPrice(price)}</b></span>
    <button data-chart-alert="set">${watched ? "Set alert" : "Add to watchlist &amp; set alert"}</button>
    <button data-chart-alert="cancel" class="x" title="Cancel">×</button>`;
  box.hidden = false;
  const wrap = box.parentElement.clientWidth;
  box.style.left = `${Math.min(Math.max(offsetX - box.offsetWidth / 2, 0), wrap - box.offsetWidth)}px`;
  const above = offsetY - box.offsetHeight - 10;  // above the point if it fits, else below: never over it
  box.style.top = `${above >= 0 ? above : offsetY + 12}px`;
}

async function setChartAlert() {
  const box = $("#chart-alert");
  const sym = detail.symbol;
  const { kind } = box.dataset;
  const price = Number(box.dataset.price);
  box.hidden = true;
  if (!state.watchlist.includes(sym)) await api("POST", "/api/watchlist", { symbol: sym });
  await api("POST", "/api/alert-rules", { symbol: sym, kind, value: price });
  toast(`Alert set: ${sym} ${kind} $${fmtPrice(price)}`);
}

function showChartTip(i, mouseX) {
  const tip = $("#chart-tip");
  const bars = detail.data.chart.bars;
  const b = bars[i];
  const base = detail.range === "1d" && detail.data.profile.prev_close ? detail.data.profile.prev_close : bars[0].c;
  const pct = base ? (b.c / base - 1) * 100 : null;
  const { news, halts } = chartMarkers(bars);
  const near = news.filter((m) => Math.abs(m.i - i) <= 1).slice(0, 3);
  const halted = halts.filter((m) => i >= m.start && i <= m.end);
  tip.innerHTML = `
    <b>${esc(b.day)} ${esc(b.time)} ET</b>${b.regular ? "" : ` <span class="muted">${detail.range === "1d" ? "extended hours" : ""}</span>`}
    <div>O ${fmtPrice(b.o)} H ${fmtPrice(b.h)} L ${fmtPrice(b.l)} C ${fmtPrice(b.c)}
      <span class="${dir(pct)}">${fmtPct(pct)}</span></div>
    <div class="muted">Vol ${fmtVol(b.v)}</div>
    ${halted.map((m) => `<div class="down">Halted · ${esc(m.h.reason_code)} ${esc(m.h.reason)}</div>`).join("")}
    ${near.map((m) => `<div class="tip-news">${sentimentBadge(m.n.sentiment)} ${esc(m.n.title)}</div>`).join("")}`;
  tip.hidden = false;
  const wrap = tip.parentElement.clientWidth;
  tip.style.left = `${Math.min(Math.max(mouseX + 12, 0), wrap - tip.offsetWidth - 4)}px`;
}

// --- events ------------------------------------------------------------------------------

$("#detail").addEventListener("mousemove", (ev) => {
  if (ev.target.id !== "detail-chart" || !detail.geom || !detail.symbol) return;
  const { pad, n, step } = detail.geom;
  const mx = ev.offsetX;
  const i = Math.min(n - 1, Math.max(0, Math.round((mx - pad.l) / (step || 1))));
  if (i !== detail.hover || ev.offsetY !== detail.hoverY) {
    detail.hover = i;
    detail.hoverY = ev.offsetY;
    drawDetailChart();
  }
  showChartTip(i, mx);
});

$("#detail").addEventListener("mouseout", (ev) => {
  if (ev.target.id !== "detail-chart") return;
  detail.hover = detail.hoverY = null;
  $("#chart-tip").hidden = true;
  drawDetailChart();
});

$("#detail").addEventListener("click", async (ev) => {
  if (ev.target.id === "detail-chart" && detail.symbol) {
    $("#chart-tip").hidden = true;
    offerChartAlert(ev.offsetX, ev.offsetY);
    return;
  }
  const action = ev.target.closest("[data-chart-alert]");
  if (action) {
    if (action.dataset.chartAlert === "cancel") $("#chart-alert").hidden = true;
    else await setChartAlert().catch((err) => toast(err.message));
    return;
  }
  const el = ev.target.closest("[data-detail-action], [data-range]");
  if (!el) return;
  const sym = detail.symbol;
  try {
    if (el.dataset.range) {
      detail.range = el.dataset.range;
      detail.hover = null;
      await loadDetail();
      for (const b of document.querySelectorAll(".detail-range button")) {
        b.classList.toggle("active", b.dataset.range === detail.range);
      }
    } else if (el.dataset.detailAction === "close") {
      closeDetail();
    } else if (el.dataset.detailAction === "news") {
      $("#news-search").value = sym;
      renderNews();
      closeDetail();
    } else if (el.dataset.detailAction === "watch") {
      if (state.watchlist.includes(sym)) await api("DELETE", `/api/watchlist/${encodeURIComponent(sym)}`);
      else await api("POST", "/api/watchlist", { symbol: sym });
    }
  } catch (err) {
    toast(err.message);
  }
});

$("#detail").addEventListener("submit", async (ev) => {
  if (ev.target.id !== "detail-rule") return;
  ev.preventDefault();
  const form = ev.target;
  try {
    await api("POST", "/api/alert-rules", { symbol: detail.symbol, kind: form.kind.value, value: Number(form.value.value) });
  } catch (err) {
    toast(err.message);
  }
});

$("#detail-backdrop").addEventListener("click", () => closeDetail());
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && detail.symbol) closeDetail();
});
window.addEventListener("popstate", () => {
  if (detail.symbol && history.state?.drawer !== "detail") closeDetail(true);
});
window.addEventListener("resize", () => drawDetailChart());

// Deep link: /#ticker=AAPL opens the drawer on load.
const tickerHash = location.hash.match(/^#ticker=([A-Za-z0-9.\-^=]{1,12})$/);
if (tickerHash) openDetail(tickerHash[1].toUpperCase());

// Called by app.js when streamed data changes.
function refreshDetail(what) {
  if (!detail.symbol || !detail.data) return;
  if (what === "quotes") { updateDetailPrice(); updateDetailStats(); updateDetailLevels(); return; }
  if (what === "config") updateDetailWatch();
  updateDetailLists();
  drawDetailChart();  // news/halt markers
}
