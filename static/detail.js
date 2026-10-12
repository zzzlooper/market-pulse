"use strict";
// Ticker detail drawer: intraday chart with news/halt markers, key stats, and the ticker's
// news, halts and alerts. Profile and bars come from /api/ticker; everything else is read
// from the live `state` in app.js, so the drawer updates as new data streams in.

const detail = { symbol: null, range: "1d", data: null, error: null, timer: null, seq: 0, hover: null, geom: null };
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

async function openDetail(symbol) {
  if (symbol !== detail.symbol) {
    Object.assign(detail, { symbol, data: null, error: null, hover: null });
    renderDetail();
  }
  $("#detail").hidden = $("#detail-backdrop").hidden = false;
  clearInterval(detail.timer);
  detail.timer = setInterval(loadDetail, DETAIL_REFRESH_MS);
  await loadDetail();
}

function closeDetail() {
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
    </div>
    <div class="chart-legend muted">
      <span><i class="dot bullish"></i>bullish news</span><span><i class="dot bearish"></i>bearish news</span>
      <span><i class="dot neutral"></i>other news</span><span><i class="band"></i>halted</span>
      <span><i class="band ext"></i>pre/post-market</span>
    </div>
    <div id="detail-stats" class="stats"></div>
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
  updateDetailLists();
  drawDetailChart();
}

function detailQuote() {
  // Prefer the streamed watchlist quote (real-time with Finnhub), then the server's snapshot.
  return state.quotes.find((q) => q.symbol === detail.symbol && q.price != null) ?? detail.data?.quote ?? null;
}

function updateDetailPrice() {
  const el = $("#detail-price");
  if (!el) return;
  const q = detailQuote();
  const bars = detail.data.chart.bars;
  const price = q?.price ?? bars.at(-1)?.c;
  const prev = detail.data.profile.prev_close ?? q?.prev_close;
  const pct = q?.change_pct ?? (price != null && prev ? (price / prev - 1) * 100 : null);
  const live = q?.live ? `<span class="live-dot" title="Real-time (Finnhub)"></span>` : "";
  const delayed = q?.live ? "" : `<small class="muted">delayed ~15 min</small>`;
  el.innerHTML = `${live}<strong>${fmtPrice(price)}</strong>
    <span class="${dir(pct)}">${fmtPct(pct)}</span>${haltBadge(detail.symbol)} ${delayed}`;
}

function updateDetailWatch() {
  const btn = $("#detail-watch");
  if (btn) btn.textContent = state.watchlist.includes(detail.symbol) ? "★ On watchlist" : "☆ Add to watchlist";
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
    const days = Math.ceil((d - Date.now()) / 86_400_000);
    earnings = `${d.toLocaleDateString([], { month: "short", day: "numeric" })} <small class="muted">(${days <= 0 ? "today" : `in ${days}d`})</small>`;
  }
  const stat = (label, value, cls = "") => `<div><span>${label}</span><b class="${cls}">${value}</b></div>`;
  el.innerHTML = [
    stat("Open", fmtPrice(p.open)),
    stat("Prev close", fmtPrice(p.prev_close)),
    stat("Gap", fmtPct(p.gap_pct), dir(p.gap_pct)),
    stat("Day range", range(p.day_low, p.day_high)),
    stat("52-wk range", range(p.week52_low, p.week52_high)),
    stat("Volume", fmtVol(volume)),
    stat("Avg volume", fmtVol(p.avg_volume)),
    stat("RVOL", fmtRvol(q?.rvol), q?.rvol >= state.rvol_threshold ? "hot" : ""),
    stat("Market cap", fmtBig(p.market_cap, "$")),
    stat("Float", fmtBig(p.float_shares)),
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
    <li><div class="meta"><time>${fmtETShort(n.published)}</time> ${esc(n.source)} ${sentimentBadge(n.sentiment)}</div>
      <a href="${esc(safeUrl(n.url))}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a></li>`).join("")
    || `<li class="muted">No headlines for ${esc(sym)} since the app started</li>`;

  $("#detail-halts").innerHTML = detailHalts().map((h) => {
    const status = haltStatus(h);
    const resume = h.resume_trade_at ? ` → ${fmtET(h.resume_trade_at)}` : " → no resumption time yet";
    return `<li class="halt-row ${status}"><b class="reason ${esc(h.category)}">${esc(h.reason_code)}</b>
      ${isToday(h) ? fmtET(h.halted_at) : h.halted_at.slice(0, 10)}${resume}
      <span class="muted">· ${esc(h.reason)}</span> <span class="status">${status}</span></li>`;
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
  const span = hi - lo || hi * 0.01 || 1;
  lo -= span * 0.06; hi += span * 0.06;
  const y = (p) => pad.t + ((hi - p) / (hi - lo)) * priceH;
  const volTop = pad.t + priceH + 6;
  const maxVol = Math.max(...bars.map((b) => b.v), 1);
  const barW = Math.max(step * 0.7, 1);
  detail.geom = { pad, plotW, n, step };

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
  if (i !== detail.hover) {
    detail.hover = i;
    drawDetailChart();
  }
  showChartTip(i, mx);
});

$("#detail").addEventListener("mouseout", (ev) => {
  if (ev.target.id !== "detail-chart") return;
  detail.hover = null;
  $("#chart-tip").hidden = true;
  drawDetailChart();
});

$("#detail").addEventListener("click", async (ev) => {
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

$("#detail-backdrop").addEventListener("click", closeDetail);
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && detail.symbol) closeDetail();
});
window.addEventListener("resize", () => drawDetailChart());

// Deep link: /#ticker=AAPL opens the drawer on load.
const tickerHash = location.hash.match(/^#ticker=([A-Za-z0-9.\-^=]{1,12})$/);
if (tickerHash) openDetail(tickerHash[1].toUpperCase());

// Called by app.js when streamed data changes.
function refreshDetail(what) {
  if (!detail.symbol || !detail.data) return;
  if (what === "quotes") { updateDetailPrice(); updateDetailStats(); return; }
  if (what === "config") updateDetailWatch();
  updateDetailLists();
  drawDetailChart();  // news/halt markers
}
