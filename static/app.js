"use strict";
// Wiring: server messages, the live connection, sign-in, page events and startup.

let riskRender = null;

function handle({ type, data }) {
  switch (type) {
    case "snapshot":
      setClock(data.clock);
      Object.assign(state, data);
      renderHalts();  // first: other tables show HALTED badges
      renderKeywords(); renderWatchlist(); renderUnusual(); renderMovers(); renderSources(); renderNews();
      renderRules(); renderAlertLog(); renderPhone(); setHealth(data.health);
      renderGaps();  // after setClock: whether it's scanning depends on the (possibly replayed) time
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
    case "ssr": state.ssr = data; renderWatchlist(); renderMovers(); renderUnusual(); renderGaps(); refreshDetail("quotes"); break;
    case "floats": state.floats = data; renderWatchlist(); renderMovers(); renderUnusual(); refreshDetail("quotes"); break;
    case "news": addNews(data); break;
    case "gaps": state.gaps = data; renderGaps(); break;
    case "earnings": state.earnings = data; renderWatchlist(); renderMovers(); renderUnusual(); renderGaps(); break;
    case "risk":
      Object.assign(state.risk, data);
      // Profiles arrive one symbol at a time after startup; redraw once per frame at most.
      riskRender ??= requestAnimationFrame(() => {
        riskRender = null;
        renderWatchlist(); renderMovers(); renderUnusual(); renderNews(); renderGaps();
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

$("#rotation-alerts").addEventListener("change", async (ev) => {
  try {
    await api("PUT", "/api/alert-rules/rotation", { enabled: ev.target.checked });
  } catch (err) {
    ev.target.checked = !ev.target.checked;
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
if (location.hash === "#gaps") setLeftTab("gaps");
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
