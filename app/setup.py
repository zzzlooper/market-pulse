"""Settings you can change from the dashboard, and the first-run checklist.

Layers, lowest to highest: defaults < config.toml < dashboard (data/settings.json) < environment
variables. A setting that comes from the environment is shown as locked: change it where it's set.

Privacy: secrets (tokens, the ntfy topic, Telegram ids, your SEC contact address) are
write-only. The API reports whether each one is set and where from, with at most a few
characters to tell values apart; it never returns a stored secret. The one exception is a
value the server has just generated for you (an access token, an ntfy topic), returned once so
you can copy it to your phone. data/settings.json is written readable by you only, and no
setting's value is logged.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import urlparse

from .config import CONFIG_PATH, RECAP_TIME, SECRET_ENV, Settings, load_settings

VERSION = "0.7"
EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net")


@dataclass(frozen=True)
class Field:
    label: str
    kind: str            # secret, email, url, number, integer, time
    live: bool = True    # applies at once; False needs a restart
    hint: str = ""
    low: float = 0
    high: float = 1e12


EDITABLE: dict[str, Field] = {
    "sec_user_agent": Field("Your contact email for SEC", "email",
                            hint="SEC asks automated clients for one. Sent only to sec.gov."),
    "ntfy_topic": Field("ntfy topic", "secret", hint="Anyone who knows it can read your alerts: use Generate."),
    "ntfy_server": Field("ntfy server", "url", hint="https://ntfy.sh unless you run your own."),
    "ntfy_token": Field("ntfy access token", "secret", hint="Only for protected topics or your own server."),
    "telegram_bot_token": Field("Telegram bot token", "secret", hint="From @BotFather."),
    "telegram_chat_id": Field("Telegram chat id", "secret", hint="See docs/setup.md: Phone alerts."),
    "access_token": Field("Access token", "secret",
                          hint="Needed before opening the dashboard from your phone. Use Generate."),
    "public_url": Field("Address your phone uses", "url", hint="e.g. http://192.168.1.20:8000, so tapped alerts open the ticker."),
    "finnhub_api_key": Field("Finnhub API key", "secret", live=False, hint="Optional: real-time watchlist prices."),
    "rvol_threshold": Field("Unusual volume at RVOL", "number", low=1, high=50),
    "min_volume": Field("…and at least shares traded", "integer", high=1e10),
    "insider_min_value": Field("Smallest insider trade shown ($)", "number", high=1e10),
    "recap_time": Field("Daily recap at (ET)", "time"),
    "phone_cap_per_kind": Field("Phone: most alerts of one kind a day (0 = no cap)", "integer", high=1000,
                                hint="Past it they still show here. Mute kinds in the Alerts panel."),
    "gap_min_pct": Field("Pre-market gaps from (%)", "number", low=0.5, high=100),
    "gap_brief_time": Field("Pre-market brief at (ET)", "time"),
    "reactions_keep_days": Field("Keep reaction history (days, 0 = forever)", "integer", high=3650),
    "breakout_pullback_pct": Field("New high of day counts after a pullback of (%)", "number", low=1, high=50,
                                   hint="Smaller: more breakout alerts on runners. Default 5."),
    "day2_min_pct": Field("Day 2 after a gain of (%)", "number", low=5, high=1000, hint="Default 30."),
    "rotation_multiple": Field("Float rotation at volume / float of", "number", low=0.1, high=50, hint="Default 1."),
    "luld_near_pct": Field("Near a LULD band within (% of the band)", "number", low=5, high=100,
                           hint="25 = a quarter of the band's width. Default 25."),
    "record_min": Field("Quote an alert's track record after (measured)", "integer", low=3, high=1000,
                        hint="Fewer is noisier. Default 10."),
    "sympathy_lead_pct": Field("A stock leads its industry when up (%)", "number", low=5, high=1000, hint="Default 30."),
    "sympathy_min_pct": Field("Moving in sympathy when up (%)", "number", low=1, high=500, hint="Default 5."),
    "record": Field("Record the day's data for replay", "bool", live=False,
                    hint="Saved in data/recordings, readable by you only; see docs/setup.md: Record and replay."),
}
GENERATED = {"access_token": lambda: secrets.token_urlsafe(24),
             "ntfy_topic": lambda: "mp-" + secrets.token_urlsafe(12)}

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
CHECKS = {
    "ntfy_topic": (re.compile(r"^[A-Za-z0-9_-]{12,64}$"), "12–64 letters, digits, - or _ (Generate makes one)"),
    "ntfy_token": (re.compile(r"^\S{8,200}$"), "no spaces"),
    "telegram_bot_token": (re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,}$"), "the form 123456:ABC… from @BotFather"),
    "telegram_chat_id": (re.compile(r"^-?\d{3,20}$"), "a number"),
    "access_token": (re.compile(r"^[\x21-\x7e]{16,200}$"), "at least 16 characters, no spaces"),
    "finnhub_api_key": (re.compile(r"^[A-Za-z0-9]{10,100}$"), "letters and digits"),
}


class SettingsError(ValueError):
    pass


def user_agent(email: str) -> str:
    return f"market-pulse/{VERSION} {email}"


def contact_email(agent: str) -> str | None:
    """The email in an "app/version email" User-Agent, unless it's a placeholder."""
    email = agent.rsplit(" ", 1)[-1]
    if not EMAIL.match(email) or email.lower().endswith(EXAMPLE_DOMAINS):
        return None
    return email


def validate(name: str, value: object) -> object:
    """The value to store for name, or SettingsError with a message safe to show (it never
    repeats the value)."""
    spec = EDITABLE.get(name)
    if spec is None:
        raise SettingsError(f"{name} can't be changed from the dashboard")
    if spec.kind == "bool":
        if isinstance(value, bool):
            return value
        if value in ("true", "false"):
            return value == "true"
        raise SettingsError(f"{spec.label}: on or off")
    if spec.kind in ("number", "integer"):
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise SettingsError(f"{spec.label}: enter a number") from None
        if not spec.low <= number <= spec.high:
            raise SettingsError(f"{spec.label}: between {spec.low:g} and {spec.high:g}")
        return int(number) if spec.kind == "integer" else number
    if not isinstance(value, str):
        raise SettingsError(f"{spec.label}: expected text")
    value = value.strip()
    if spec.kind == "email":
        if not EMAIL.match(value):
            raise SettingsError(f"{spec.label}: enter an email address")
        if value.lower().endswith(EXAMPLE_DOMAINS):
            raise SettingsError(f"{spec.label}: use a real address; SEC blocks placeholders")
        return user_agent(value)
    if spec.kind == "url":
        url = urlparse(value)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise SettingsError(f"{spec.label}: a full address starting with http:// or https://")
        return value.rstrip("/")
    if spec.kind == "time":
        if not RECAP_TIME.match(value):
            raise SettingsError(f'{spec.label}: "HH:MM", 24-hour')
        return value
    pattern, expected = CHECKS[name]
    if not pattern.match(value):
        raise SettingsError(f"{spec.label}: expected {expected}")
    return value


def masked(name: str, value: str) -> str:
    """Enough to tell values apart, never enough to use one."""
    if not value:
        return ""
    if name == "sec_user_agent":
        email = contact_email(value)
        if not email:
            return "placeholder"
        local, domain = email.split("@")
        return f"{local[0]}…@…{domain[domain.rfind('.'):]}"
    return f"…{value[-3:]}" if len(value) >= 12 else "set"


class DashboardSettings:
    """Reads and writes data/settings.json, and reports where each setting comes from."""

    def __init__(self, path: Path, config_path: Path = CONFIG_PATH):
        self.path = path
        self.config_path = config_path

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return {}
        return {k: v for k, v in data.items() if k in EDITABLE}

    def save(self, values: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        # Created readable by you only, before anything is written to it.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(values, f, indent=2)
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def source(self, name: str, dashboard: dict) -> str:
        if name in SECRET_ENV and os.environ.get(name.upper()):
            return "environment"
        if name in dashboard:
            return "dashboard"
        try:
            import tomllib
            if name in tomllib.loads(self.config_path.read_text()):
                return "config.toml"
        except (FileNotFoundError, ValueError):
            pass
        return "default"

    def describe(self, settings: Settings) -> list[dict]:
        """Each editable setting for the dashboard: plain values as they are, secrets masked."""
        dashboard = self.load()
        out = []
        for name, spec in EDITABLE.items():
            value = getattr(settings, name)
            secret = spec.kind in ("secret", "email")
            source = self.source(name, dashboard)
            if name == "sec_user_agent":
                is_set = contact_email(value) is not None
            else:
                is_set = bool(value)
            out.append({
                "name": name, "label": spec.label, "kind": spec.kind, "hint": spec.hint,
                "secret": secret, "set": is_set, "source": source,
                "locked": source == "environment", "restart": not spec.live,
                "value": masked(name, value) if secret else value,
                "generate": name in GENERATED,
            })
        return out

    def effective(self, dashboard: dict) -> Settings:
        """Settings as they'd load from config.toml + these dashboard values + the environment."""
        return load_settings(self.config_path, dashboard)


def apply(live: Settings, fresh: Settings, names: list[str]) -> list[str]:
    """Copy the changed settings onto the running Settings. Returns those needing a restart."""
    restart = []
    names_ok = {f.name for f in fields(Settings)}
    for name in names:
        if name in names_ok:
            setattr(live, name, getattr(fresh, name))
            if not EDITABLE[name].live:
                restart.append(name)
    return restart


SERVICE_COMMAND = "sh scripts/install-service.sh --enable"
HTTPS_COMMAND = "MP_HOST=0.0.0.0 MP_HTTPS=1 sh scripts/install-service.sh --enable"


def checklist(settings: Settings, *, channels: list[str], phone_error: str | None, health: list[dict],
              watchlist: list[str], default_watchlist: list[str], recap_on: bool, local_client: bool,
              local_server: bool, https: bool, under_systemd: bool, tls: dict | None = None) -> list[dict]:
    """First-run checklist. state: todo (needed), warn (something's wrong), ok, or info (optional)."""
    items = []

    def add(id_, state, title, detail, fix=None):
        items.append({"id": id_, "state": state, "title": title, "detail": detail, "fix": fix})

    if contact_email(settings.sec_user_agent):
        add("sec", "ok", "SEC contact set", "News, filings and dilution data identify you to SEC only.")
    else:
        add("sec", "todo", "Add your contact email for SEC",
            "SEC asks automated clients for one and may block requests without it. "
            "It's sent only to sec.gov.", {"field": "sec_user_agent"})

    exposed = not (local_client and local_server)
    if exposed and not settings.access_token:
        add("access", "warn", "Anyone on this network can change your settings",
            "Market Pulse is reachable from other devices without an access token. Generate one.",
            {"field": "access_token"})
    elif settings.access_token:
        add("access", "ok", "Access token set", "Every device signs in once with it.")
    else:
        add("access", "info", "Open it from your phone (optional)",
            "Generate an access token first, then start it with MP_HOST=0.0.0.0 (see README).",
            {"field": "access_token"})

    # Encryption: only worth a line once the dashboard is (or is meant to be) used from elsewhere.
    tls = tls or {}
    if https:
        detail = f"Encrypted with Market Pulse's own certificate; it renews before {tls.get('server_expires', '?')}."
        if tls.get("renew_soon"):
            add("https", "warn", "HTTPS certificate due for renewal",
                "Restart Market Pulse to renew it (the service does this on every start).",
                {"command": "systemctl --user restart market-pulse"})
        else:
            add("https", "ok", "Connection encrypted (HTTPS)", detail)
    elif exposed:
        add("https", "warn", "This connection isn't encrypted",
            "Anyone who can watch this network could read the access token and the dashboard. Fine on your "
            "own home Wi-Fi; never on public or shared Wi-Fi. To encrypt it, start Market Pulse with HTTPS:",
            {"command": HTTPS_COMMAND})
    elif settings.access_token:
        add("https", "info", "Encrypt phone access (optional)",
            "Serve HTTPS with Market Pulse's own certificate, then install that certificate on your phone once:",
            {"command": HTTPS_COMMAND})

    if not channels:
        add("phone", "todo", "Set up phone alerts",
            "Alerts and the daily recap only reach this browser until ntfy or Telegram is set up.",
            {"field": "ntfy_topic"})
    elif phone_error:
        add("phone", "warn", "Phone alerts are failing", "The last delivery failed: see Phone below, then Send test.",
            {"action": "phone-test"})
    else:
        detail = f"Sending to {' and '.join(channels)}."
        if not recap_on:
            detail += " The daily recap is switched off in the Phone box."
        add("phone", "ok", "Phone alerts set up", detail, {"action": "phone-test"})

    if under_systemd:
        add("service", "ok", "Running as a background service", "Alerts keep coming after you close the terminal.")
    else:
        add("service", "todo", "Keep it running in the background",
            "Started from a terminal, it stops (with its alerts) when the terminal closes. "
            "In the project folder run:", {"command": SERVICE_COMMAND})

    down = [s["label"] for s in health if s["state"] == "down"]
    if down:
        add("data", "warn", "Some data isn't arriving", ", ".join(down) + ": see the data status in the header.")
    else:
        add("data", "ok", "All data sources delivering", "")

    if sorted(watchlist) == sorted(default_watchlist):
        add("watchlist", "info", "Make the watchlist yours", "It still has the example tickers.")
    if not settings.finnhub_api_key:
        add("finnhub", "info", "Real-time prices (optional)",
            "Without a free Finnhub key, prices are ~15 minutes delayed.", {"field": "finnhub_api_key"})
    return items
