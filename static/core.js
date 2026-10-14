"use strict";
// Shared by every other script: state, formatting, the market clock, server calls.
// Load order (index.html): core, tables, tabs, news, alerts, header, app, detail, setup.

const state = {
  watchlist: [], keywords: [], quotes: [], movers: {}, unusual: [], news: [],
  alert_rules: [], unusual_alerts: true, rotation_alerts: true, breakout_alerts: true, alerts: [], halts: [],
  halt_alerts: "watchlist",
  rvol_threshold: 2, price_source: "yahoo", tab: "gainers", leftTab: "unusual",
  health: { now: 0, sources: [] },
  risk: {},  // symbol -> { level, points, reasons } dilution summary; null = not an SEC filer
  ssr: {},     // symbol -> { since, today }: short sale restriction in effect today
  floats: {},  // symbol -> shares in the public float (Yahoo)
  pre_highs: {},  // symbol -> today's pre-market high, for every stock the gap scanner covers
  halt_info: {},  // halt id -> how trading resumed: first trade, the price before the halt, the move
  day2: {},       // symbol -> { date, change_pct }: a big gainer in the last session
};
const lastPrice = new Map();   // symbol -> price, to flash changes
const MAX_NEWS = 500;
const MAX_NOTIFICATIONS = 3;   // per update batch, so a burst doesn't spam the desktop

const $ = (sel) => document.querySelector(sel);

// --- formatting -----------------------------------------------------------------

const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmtPrice = (v) => (v == null ? "–" : v.toFixed(v < 1 ? 4 : 2));
const fmtPct = (v) => (v == null ? "–" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const fmtRvol = (v) => (v == null ? "–" : `${v.toFixed(1)}×`);
const dir = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
const safeUrl = (u) => (/^https?:\/\//i.test(u) ? u : "#");

function fmtVol(v) {
  if (!v) return "–";
  for (const [div, suffix] of [[1e9, "B"], [1e6, "M"], [1e3, "K"]]) {
    if (v >= div) return (v / div).toFixed(1) + suffix;
  }
  return String(v);
}

// The market clock: real time, or the replayed day's time during a replay (from the server,
// running at its speed). Data-source ages ("4m ago") stay on real time.
const appClock = { base: null, at: 0, speed: 1 };
const appNow = () => (appClock.base == null ? Date.now() : appClock.base + (Date.now() - appClock.at) * appClock.speed);

function setClock(c) {
  if (!c) return;
  Object.assign(appClock, c.replay ? { base: c.now * 1000, at: Date.now(), speed: c.speed } : { base: null });
  tick();  // show the (possibly replayed) time now, not at the next second
  const banner = $("#replay-banner");
  banner.hidden = !c.replay && !c.recording;
  banner.classList.toggle("recording", !c.replay);
  banner.textContent = c.replay
    ? `REPLAY of ${c.recording}${c.speed !== 1 ? ` at ${c.speed}×` : ""} · no network, no phone alerts`
    : `Recording today's data for replay (${c.recording})`;
}

function fmtTime(iso) {
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === new Date(appNow()).toDateString()
    ? time
    : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}


const etDate = (d = new Date(appNow())) => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(d);
const fmtET = (iso) => new Date(iso).toLocaleTimeString("en-GB", { timeZone: "America/New_York" });

const DAY_MS = 24 * 3600 * 1000;
const fmtScore = (v) => `${v > 0 ? "+" : ""}${v.toFixed(2)}`;

// --- server I/O -------------------------------------------------------------------------

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) showLogin();
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(typeof err.detail === "string" ? err.detail : res.statusText);
  }
  return res.json();
}

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, 3500);
}

