"use strict";
// The header: data-source health and the market session clock.

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

