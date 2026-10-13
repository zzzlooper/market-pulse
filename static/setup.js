"use strict";
// Setup drawer: the first-run checklist and the settings you can change here.
// Secrets are write-only. The server says whether each one is set (with a few characters at
// most) and never sends one back, except a value it has just generated, shown once below so
// you can copy it to your phone. Secret inputs are always empty and never pre-filled.

const setup = { open: false, items: [], fields: [], generated: null, data: [], tls: {} };
const SETUP_SEEN = "mp-setup-seen-v1";  // per browser: the checklist opened itself once
const SETUP_GROUPS = [
  ["SEC contact", ["sec_user_agent"]],
  ["Phone alerts", ["ntfy_topic", "ntfy_server", "ntfy_token", "telegram_bot_token", "telegram_chat_id"]],
  ["Opening it from your phone", ["access_token", "public_url"]],
  ["Real-time prices", ["finnhub_api_key"]],
  ["Alert tuning", ["rvol_threshold", "min_volume", "insider_min_value", "gap_min_pct", "gap_brief_time", "recap_time"]],
  ["Record and replay", ["record"]],
  ["Keeping your data", ["reactions_keep_days"]],
];
const SETUP_ICON = { ok: "✓", todo: "○", warn: "!", info: "i" };
const GENERATED_NOTE = {
  access_token: "Enter it on your phone when Market Pulse asks. This browser is already signed in.",
  ntfy_topic: "Subscribe to this topic in the ntfy app, then use Send test in the Phone box.",
};

const isLocalPage = () => ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);

async function loadSetup(autoOpen = false) {
  try {
    const [s, f, d, t] = await Promise.all([api("GET", "/api/setup"), api("GET", "/api/settings"),
                                            api("GET", "/api/data"), api("GET", "/api/tls")]);
    Object.assign(setup, { items: s.items, fields: f.fields, data: d.stores, tls: t });
  } catch {
    return;  // signed out or offline: the login prompt or connection pill says so
  }
  updateSetupButton();
  if (setup.open) renderSetup();
  else if (autoOpen && appClock.base == null && setup.items.some((i) => i.state === "todo")) {  // not in a replay
    let seen = false;
    try { seen = localStorage.getItem(SETUP_SEEN) === "1"; } catch { /* private window */ }
    if (!seen) openSetup();
  }
}

function updateSetupButton() {
  const pending = setup.items.filter((i) => i.state === "todo" || i.state === "warn").length;
  const btn = $("#setup-btn");
  btn.textContent = pending ? `Setup · ${pending}` : "Setup";
  btn.classList.toggle("attention", setup.items.some((i) => i.state === "warn"));
}

function openSetup() {
  if (history.state?.drawer !== "setup") history.pushState({ drawer: "setup" }, "", location.pathname + location.search);
  setup.open = true;
  try { localStorage.setItem(SETUP_SEEN, "1"); } catch { /* fine */ }
  $("#setup").hidden = $("#setup-backdrop").hidden = false;
  renderSetup();
}

function closeSetup(fromHistory = false) {
  if (!fromHistory && history.state?.drawer === "setup") {
    history.back();  // popstate closes it: a phone's Back gesture works the same way
    return;
  }
  setup.open = false;
  setup.generated = null;  // a generated value is shown once
  $("#setup").hidden = $("#setup-backdrop").hidden = true;
}

function checklistItem(i) {
  let fix = "";
  if (i.fix?.field) fix = `<button type="button" data-focus="${esc(i.fix.field)}">Set up</button>`;
  else if (i.fix?.command) fix = `<code class="cmd">${esc(i.fix.command)}</code>
    <button type="button" data-copy="${esc(i.fix.command)}">Copy</button>`;
  else if (i.fix?.action === "phone-test") fix = `<button type="button" data-phone-test>Send test</button>`;
  return `<li class="check ${esc(i.state)}"><span class="icon">${SETUP_ICON[i.state] ?? ""}</span>
    <div><b>${esc(i.title)}</b>${i.detail ? `<p>${esc(i.detail)}</p>` : ""}${fix ? `<div class="fix">${fix}</div>` : ""}</div></li>`;
}

function fieldRow(f) {
  const id = `set-${f.name}`;
  const from = f.source === "default" ? "" : ` <small class="muted">· from ${esc(f.source)}</small>`;
  const restart = f.restart ? ` <small class="muted">· applies after a restart</small>` : "";
  const status = f.secret ? (f.set ? `set <code>${esc(f.value)}</code>` : "not set") : "";
  if (f.locked) {
    return `<div class="set-row"><label>${esc(f.label)}${from}</label>
      <div class="set-locked">${f.secret ? status : esc(f.value)} · set by the environment variable
        <code>${esc(f.name.toUpperCase())}</code>; change it there</div></div>`;
  }
  if (f.kind === "bool") {
    return `<div class="set-row"><label><input name="${esc(f.name)}" type="checkbox"${f.value ? " checked" : ""}>
      ${esc(f.label)}${from}${restart}</label><small class="muted">${esc(f.hint)}</small></div>`;
  }
  const type = f.kind === "secret" ? "password" : f.kind === "email" ? "email"
    : ["number", "integer"].includes(f.kind) ? "number" : "text";
  const value = f.secret ? "" : ` value="${esc(f.value ?? "")}"`;
  const placeholder = f.secret && f.set ? "type a new value to replace it" : f.kind === "email" ? "you@yourmail.com" : "";
  return `<div class="set-row">
    <label for="${id}">${esc(f.label)}${from}${restart}</label>
    <div class="set-input">
      <input id="${id}" name="${esc(f.name)}" type="${type}"${value} placeholder="${esc(placeholder)}"
        autocomplete="off" spellcheck="false"${type === "number" ? ' step="any"' : ""}>
      ${f.generate ? `<button type="button" data-generate="${esc(f.name)}">Generate</button>` : ""}
      ${f.source === "dashboard" ? `<button type="button" data-clear="${esc(f.name)}" title="Remove the value set here">Clear</button>` : ""}
    </div>
    <small class="muted">${status ? `${status} · ` : ""}${esc(f.hint)}</small>
  </div>`;
}

// Installing Market Pulse's own CA certificate on a phone: once, then HTTPS just works.
function httpsBox() {
  const t = setup.tls;
  if (!t?.exists) return "";
  const port = location.port || (location.protocol === "https:" ? "443" : "80");
  const urls = t.addresses.filter((a) => !a.includes(":")).map((a) => `https://${a}:${port}`);
  return `<div class="https-box">
    <b>Secure phone access</b>${t.https ? " <small class=\"up\">· this connection is encrypted</small>" : ""}
    <p>Certificate fingerprint (SHA-256), to compare on the phone:<br><code class="fp">${esc(t.fingerprint)}</code></p>
    <p class="muted">Covers ${esc(urls.join(", ") || "this computer only")}; renews before ${esc(t.server_expires)}.
      It can only vouch for private addresses, never for a public website.</p>
    <details><summary>Install it on your phone (once)</summary>
      <ol>
        <li>On the phone, open <code>${esc(urls[0] ?? "https://<this computer's address>:" + port)}/market-pulse-ca.crt</code>.
          The first time, the browser warns that the connection isn't trusted yet: continue anyway, just for this.</li>
        <li><b>iPhone:</b> Settings › General › VPN & Device Management › install the "Market Pulse local CA" profile,
          then Settings › General › About › Certificate Trust Settings › turn on full trust for it.</li>
        <li><b>Android:</b> Settings › Security › Encryption & credentials › Install a certificate › CA certificate,
          and pick the downloaded file.</li>
        <li>Check the fingerprint the phone shows against the one above, then reopen the dashboard: no more warnings.</li>
      </ol>
      <a class="button-link" href="/market-pulse-ca.crt" download>Download the certificate here</a>
    </details>
  </div>`;
}

function fmtBytes(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  return n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// What's kept on this computer, and in this browser. No contents, no values: sizes, counts, dates.
function yourData() {
  const browserNote = "Remembers only that Setup has opened once" +
    (setup.fields.find((f) => f.name === "access_token")?.set ? ", and keeps you signed in (a cookie only the server can read)" : "");
  return `<h3 class="sub">Your data <small>kept on this computer; nothing is sent anywhere</small></h3>
    <ul class="data-list">${setup.data.map((d) => `
      <li><div><b>${esc(d.label)}</b>${d.path ? ` <code>${esc(d.path)}</code>` : ""}
        ${d.size != null ? `<small class="muted">· ${fmtBytes(d.size)}</small>` : ""}
        <p>${esc(d.detail)}</p><p class="muted">${esc(d.note)}</p></div>
        ${d.deletable ? `<button type="button" class="danger" data-delete-data="${esc(d.id)}">Delete</button>` : ""}</li>`).join("")}
      <li><div><b>This browser</b><p>${esc(browserNote)}</p></div>
        <button type="button" data-forget-browser>Forget</button></li>
    </ul>`;
}

function renderSetup() {
  const byName = Object.fromEntries(setup.fields.map((f) => [f.name, f]));
  const g = setup.generated;
  const plainHttp = location.protocol === "http:" && !isLocalPage();
  $("#setup").innerHTML = `
    <header class="detail-head">
      <h2>Setup</h2>
      <div class="detail-actions"><button data-setup-close class="x" title="Close (Esc)">×</button></div>
    </header>
    <ul class="checklist">${setup.items.map(checklistItem).join("")}</ul>
    ${g ? `<div class="generated">
      <b>New ${esc(byName[g.name]?.label ?? g.name)}</b> — shown once, copy it now:
      <div class="set-input"><code id="generated-value">${esc(g.value)}</code>
        <button type="button" data-copy-generated>Copy</button></div>
      <small>${esc(GENERATED_NOTE[g.name] ?? "")}</small></div>` : ""}
    <h3 class="sub">Settings</h3>
    ${plainHttp ? `<p class="warn-box">This page is plain HTTP: anything you type here crosses your network
      unencrypted. Prefer setting secrets on the computer running Market Pulse.</p>` : ""}
    <p class="muted fine">Saved in data/settings.json, readable by you only. Secrets are never shown again
      after saving; type a new value to replace one. config.toml and environment variables still work.</p>
    ${httpsBox()}
    ${yourData()}
    ${SETUP_GROUPS.map(([title, names]) => `
      <form class="set-group" data-group>
        <h3 class="sub">${esc(title)}</h3>
        ${names.filter((n) => byName[n]).map((n) => fieldRow(byName[n])).join("")}
        <button>Save</button>
      </form>`).join("")}`;
}

async function saveSettings(values) {
  const result = await api("PUT", "/api/settings", { values });
  setup.fields = result.fields;
  toast(result.restart_required.length ? "Saved. It takes effect when Market Pulse restarts." : "Saved");
  await loadSetup();
}

$("#setup-btn").addEventListener("click", () => (setup.open ? closeSetup() : openSetup()));
$("#setup-backdrop").addEventListener("click", () => closeSetup());
window.addEventListener("popstate", () => {
  if (setup.open && history.state?.drawer !== "setup") closeSetup(true);
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && setup.open) closeSetup();
});

$("#setup").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const values = {};
  for (const input of ev.target.querySelectorAll("input[name]")) {
    const f = setup.fields.find((x) => x.name === input.name);
    if (!f || f.locked) continue;
    if (f.kind === "bool") {
      if (input.checked !== Boolean(f.value)) values[f.name] = input.checked;
      continue;
    }
    const v = input.value.trim();
    if (f.secret) {
      if (v) values[f.name] = v;  // empty = keep the current secret
    } else if (v !== String(f.value ?? "")) {
      values[f.name] = v === "" ? null : (["number", "integer"].includes(f.kind) ? Number(v) : v);
    }
  }
  if (!Object.keys(values).length) return toast("Nothing changed");
  try {
    await saveSettings(values);
  } catch (err) {
    toast(err.message);
  }
});

$("#setup").addEventListener("click", async (ev) => {
  const el = ev.target.closest("button");
  if (!el) return;
  try {
    if (el.dataset.setupClose !== undefined) {
      closeSetup();
    } else if (el.dataset.focus) {
      const input = $(`#set-${el.dataset.focus}`);
      input?.scrollIntoView({ block: "center" });
      input?.focus();
    } else if (el.dataset.generate) {
      const r = await api("POST", "/api/settings/generate", { name: el.dataset.generate });
      setup.generated = { name: r.name, value: r.value };
      setup.fields = r.fields;
      await loadSetup();
      $("#setup").scrollTop = 0;
    } else if (el.dataset.clear) {
      await saveSettings({ [el.dataset.clear]: null });
    } else if (el.dataset.copy !== undefined || el.dataset.copyGenerated !== undefined) {
      const text = el.dataset.copy ?? setup.generated?.value ?? "";
      try {
        await navigator.clipboard.writeText(text);
        toast("Copied");
      } catch {  // no clipboard on plain HTTP from another device: select it instead
        const code = el.previousElementSibling;
        getSelection().selectAllChildren(code);
        toast("Selected: copy it with Ctrl+C");
      }
    } else if (el.dataset.deleteData) {
      const store = setup.data.find((d) => d.id === el.dataset.deleteData);
      if (!store || !confirm(store.confirm)) return;
      setup.data = (await api("POST", "/api/data/delete", { store: store.id })).stores;
      toast(`Deleted: ${store.label.toLowerCase()}`);
      await loadSetup();
      if (store.id === "reactions" && state.leftTab === "reactions") loadReactions();
    } else if (el.dataset.forgetBrowser !== undefined) {
      if (!confirm("Forget this browser? It clears what Market Pulse stored here and signs it out if an access token is set.")) return;
      try { localStorage.removeItem(SETUP_SEEN); } catch { /* nothing stored */ }
      await fetch("/api/logout", { method: "POST" });
      toast("This browser is forgotten");
      location.reload();  // signed out: the sign-in prompt shows if a token is required
    } else if (el.dataset.phoneTest !== undefined) {
      state.phone = await api("POST", "/api/phone/test");
      renderPhone();
      toast(state.phone.last_error ? "Test not delivered everywhere — see the error under Phone" : "Test sent — check your phone");
      await loadSetup();
    }
  } catch (err) {
    toast(err.message);
  }
});

// app.js announces these: a new connection, and phone or data-source changes.
window.addEventListener("mp-snapshot", () => loadSetup(true));
window.addEventListener("mp-status", () => loadSetup());
