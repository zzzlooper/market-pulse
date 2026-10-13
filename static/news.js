"use strict";
// The news feed: filters, keyword highlighting, filing badges, new items.

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

