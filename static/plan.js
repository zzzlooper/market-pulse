"use strict";
// The morning game plan (app/plan.py): today's gappers, catalyst first, each with what the app
// knows about it in one card. Refreshed every minute while open.

const plan = { open: false, data: null, timer: null };

function openPlan(push = true) {
  if (push && history.state?.drawer !== "plan") history.pushState({ drawer: "plan" }, "", location.pathname + location.search + "#plan");
  plan.open = true;
  $("#plan").hidden = $("#plan-backdrop").hidden = false;
  renderPlan();
  loadPlan();
  clearInterval(plan.timer);
  plan.timer = setInterval(loadPlan, 60_000);
}

function closePlan(fromHistory = false) {
  if (!fromHistory && history.state?.drawer === "plan") {
    history.back();  // popstate closes it, as a phone's Back gesture does
    return;
  }
  plan.open = false;
  clearInterval(plan.timer);
  $("#plan").hidden = $("#plan-backdrop").hidden = true;
}

async function loadPlan() {
  try {
    plan.data = await api("GET", "/api/plan");
  } catch (err) {
    plan.data = { error: err.message };
  }
  if (plan.open) renderPlan();
}

function planLevel(lv) {
  const away = lv.away_pct == null ? "" : ` <small class="${lv.away_pct > 0 ? "muted" : "hot"}">${
    lv.away_pct > 0 ? `${lv.away_pct.toFixed(1)}% above` : lv.away_pct < 0 ? `${(-lv.away_pct).toFixed(1)}% below` : "at the price"}</small>`;
  return `<li class="${esc(lv.kind)}"><b>$${fmtPrice(lv.price)}</b> ${esc(lv.label)}${away}</li>`;
}

function planPeers(p) {
  if (!p || (!p.leader && !p.followers.length)) return "";
  const move = (m) => `<a class="sym" data-sym="${esc(m.symbol)}">${esc(m.symbol)}</a> <span class="${dir(m.change_pct)}">${fmtPct(m.change_pct)}</span>`;
  const text = p.leader ? `moving with ${move(p.leader)}` : `leads ${p.followers.map(move).join(", ")}`;
  return `<p class="peers">${esc(p.industry)}: ${text}</p>`;
}

const fmtRecord = (v) => (v == null ? "–" : `<span class="${dir(v)}">${fmtPct(v)}</span>`);

function planCard(e) {
  const when = e.at_open ? "at the open" : `${e.pre_minutes} min traded pre-market`;
  const cat = e.catalyst
    ? `<p class="catalyst">${e.catalyst.filing ? "<b>Filing:</b> " : ""}${esc(e.catalyst.text)}
        <small class="muted">${fmtTime(e.catalyst.at)}</small></p>`
    : `<p class="catalyst muted">No headline or filing since yesterday's close (${esc(e.why)})</p>`;
  const facts = [e.float_shares ? `float ${fmtBig(e.float_shares)}` : null,
                 e.market_cap ? `cap ${fmtBig(e.market_cap, "$")}` : null].filter(Boolean).join(" · ");
  return `<article class="plan-card">
    <div class="plan-head"><a class="sym" data-sym="${esc(e.symbol)}">${esc(e.symbol)}</a>
      <span class="${dir(e.gap_pct)}">${e.gap_pct > 0 ? "+" : ""}${e.gap_pct.toFixed(1)}%</span>
      <b>${fmtPrice(e.price)}</b> <small class="muted">${esc(when)}${facts ? ` · ${esc(facts)}` : ""}</small></div>
    ${cat}
    ${e.flags.length ? `<div class="flags">${e.flags.map((f) => `<span class="flag ${esc(f.tone)}" title="${esc(f.why)}">${esc(f.text)}</span>`).join("")}</div>` : ""}
    ${e.levels.length ? `<ul class="levels">${e.levels.map(planLevel).join("")}</ul>` : ""}
    ${planPeers(e.peers)}
    ${(e.records ?? []).map((g) => `<p class="record"><b>${esc(g.group)}</b>: 10:00 ${fmtRecord(g.median_30m)},
      close ${fmtRecord(g.median_close)}, ${Math.round((g.down_share ?? 0) * 100)}% faded
      <small class="muted">(${g.measured} in 30 days)</small></p>`).join("")}
  </article>`;
}

function renderPlan() {
  const d = plan.data;
  const head = `<header class="detail-head"><h2>Game plan</h2>
    <div class="detail-actions"><button data-plan-close class="x" title="Close (Esc)">×</button></div></header>`;
  if (!d) {
    $("#plan").innerHTML = `${head}<p class="muted">Loading…</p>`;
    return;
  }
  if (d.error) {
    $("#plan").innerHTML = `${head}<p class="muted">${esc(d.error)}</p>`;
    return;
  }
  const note = `${d.entries.length} gapper${d.entries.length === 1 ? "" : "s"} of ${d.gap_min_pct}% or more · with a
    catalyst first, then by gap · ${d.scanning ? "updating every minute" : "this morning's list (the scan runs 4:00–10:00 ET)"}`;
  $("#plan").innerHTML = `${head}<p class="muted fine">${note}</p>
    ${d.entries.map(planCard).join("") || `<p class="muted">No gaps today yet.</p>`}
    <p class="muted fine">Levels are this app's reading of SEC filings and Yahoo's pre-market trades; flags explain
      themselves on hover. A starting point for your own checks, not advice.</p>`;
}

$("#plan-btn").addEventListener("click", () => (plan.open ? closePlan() : openPlan()));
$("#plan-backdrop").addEventListener("click", () => closePlan());
$("#plan").addEventListener("click", (ev) => {
  if (ev.target.closest("[data-plan-close]")) {
    closePlan();
    return;
  }
  if (ev.target.closest("[data-sym]")) {  // the page's ticker click opens the panel over the plan's
    plan.open = false;                       // history entry, so Back comes back to the plan
    clearInterval(plan.timer);
    $("#plan").hidden = $("#plan-backdrop").hidden = true;
  }
});
window.addEventListener("popstate", () => {
  if (plan.open && history.state?.drawer !== "plan") closePlan(true);
  else if (!plan.open && history.state?.drawer === "plan") openPlan(false);
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && plan.open) closePlan();
});
if (location.hash === "#plan") openPlan(false);
