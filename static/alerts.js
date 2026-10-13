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
  const what = { halt: "trading halted", resume: "resumption", unusual: "unusual volume",
                 warrant: "warrants in the money", offering_below: "below offering price",
                 offering_above: "back above offering price", rotation: "float rotated" };
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

