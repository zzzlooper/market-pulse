"use strict";
// Price & volume alert rules, the alert log, the Phone box, desktop notifications and sound.

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
  $("#rotation-alerts").checked = state.rotation_alerts;
  $("#breakout-alerts").checked = state.breakout_alerts;
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
      ${state.snoozed?.[a.symbol] === etDate() ? "" : `<button class="snooze" data-snooze="${esc(a.symbol)}"
        title="No alerts about ${esc(a.symbol)} for the rest of today">snooze</button>`}
    </li>`).join("") || `<li class="muted empty-log">Alerts appear here when a rule fires</li>`;
}

// --- noise control: each kind's counts and track record, muting it, snoozed stocks ----------------

let kindsLoading = null;

async function loadAlertKinds() {
  if (!$("#alert-kinds-box").open || kindsLoading) return;
  kindsLoading = api("GET", "/api/alert-kinds");
  try {
    renderAlertKinds(await kindsLoading);
  } catch (err) {
    $("#alert-kinds").innerHTML = `<p class="muted">${esc(err.message)}</p>`;
  } finally {
    kindsLoading = null;
  }
}

function recordText(r) {
  if (!r || r.measured === 0) return `<span class="muted">–</span>`;
  const close = r.median_close == null ? "–" : `<span class="${dir(r.median_close)}">${fmtPct(r.median_close)}</span>`;
  return `${close} <small class="muted">n=${r.measured}</small>`;
}

function renderAlertKinds({ kinds, snoozed }) {
  let group = null;
  const rows = kinds.map((k) => {
    const head = k.group !== group ? `<tr class="group"><th colspan="6">${esc((group = k.group))}</th></tr>` : "";
    const phoned = k.phoned_today ? ` <small class="muted" title="Sent to the phone today">(${k.phoned_today} 📱)</small>` : "";
    return `${head}<tr${k.browser || k.phone ? "" : ` class="muted"`}><td>${esc(k.label)}</td>
      <td>${k.today}${phoned}</td><td>${k.week}</td><td title="Median move to the close after it fired, last 30 days">${recordText(k.record)}</td>
      <td><input type="checkbox" data-mute-kind="${esc(k.kind)}" data-where="browser" ${k.browser ? "checked" : ""}
        title="Show it here"></td>
      <td><input type="checkbox" data-mute-kind="${esc(k.kind)}" data-where="phone" ${k.phone ? "checked" : ""}
        title="Send it to the phone (if its phone category is on)"></td></tr>`;
  }).join("");
  const today = etDate();
  const asleep = Object.entries(snoozed).filter(([, day]) => day === today).map(([s]) => s);
  $("#alert-kinds").innerHTML = `
    <table class="alert-kinds"><thead><tr><th>Kind</th><th>Today</th><th>7 days</th><th>Close</th>
      <th title="Show here">Show</th><th title="Send to the phone">Phone</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="muted fine">Muted alerts are still measured, so their track record shows what you'd be missing.
      ${asleep.length ? "" : "Snooze a stock from any alert below to silence it for the rest of the day."}</p>
    ${asleep.length ? `<div class="keywords">Snoozed today: ${asleep.map((s) => `<span class="chip">${esc(s)}
      <button data-unsnooze="${esc(s)}" title="Alerts about ${esc(s)} again">×</button></span>`).join("")}</div>` : ""}`;
}

$("#alert-kinds-box").addEventListener("toggle", loadAlertKinds);
$("#alert-kinds").addEventListener("change", async (ev) => {
  const box = ev.target.closest("[data-mute-kind]");
  if (!box) return;
  try {
    await api("PUT", `/api/alert-kinds/${encodeURIComponent(box.dataset.muteKind)}`, { [box.dataset.where]: box.checked });
    await loadAlertKinds();
  } catch (err) {
    box.checked = !box.checked;
    toast(err.message);
  }
});
document.addEventListener("click", async (ev) => {
  const snooze = ev.target.closest("[data-snooze]");
  const wake = ev.target.closest("[data-unsnooze]");
  if (!snooze && !wake) return;
  try {
    if (snooze) {
      await api("POST", "/api/snooze", { symbol: snooze.dataset.snooze });
      toast(`${snooze.dataset.snooze}: no alerts for the rest of today`);
    } else {
      await api("DELETE", `/api/snooze/${encodeURIComponent(wake.dataset.unsnooze)}`);
    }
    await loadAlertKinds();
  } catch (err) {
    toast(err.message);
  }
});

function addAlerts(events) {
  state.alerts = [...events, ...state.alerts].slice(0, 100);
  renderAlertLog(new Set(events.map((a) => a.id)));
  const what = { halt: "trading halted", resume: "resumption", unusual: "unusual volume",
                 warrant: "warrants in the money", offering_below: "below offering price",
                 offering_above: "back above offering price", rotation: "float rotated",
                 pmh: "broke pre-market high", hod: "new high of day", resuming: "resumes in a minute",
                 resumed: "resumed", luld_up: "near limit-up band", luld_down: "near limit-down band",
                 sympathy: "sympathy move" };
  const fresh = events.filter((a) => a.kind !== "filing");  // filings notify from the news feed (news.js)
  if (fresh.length) {
    notify(fresh.map((a) => ({ title: `${a.symbol} · ${what[a.kind] ?? "price alert"}`, body: a.message, tag: a.id })), 660);
  }
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

