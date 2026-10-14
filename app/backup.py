"""Backing up your setup to a file, and restoring it here or on another computer.

A backup holds what you've set up: the watchlist, news keywords, alert rules, alert switches,
phone categories, muted alert kinds, and the Setup values you've changed from the defaults
(signal thresholds, recap time…). Never a secret: no access token, ntfy topic or token,
Telegram ids, Finnhub key or SEC contact email. Nor this computer's address (public_url),
nor the day's snoozes. Restoring shows every change first, checks every value as the dashboard
would, and ignores anything else in the file. (Settings set by environment variables are all
secrets, so a restore never meets one.)
"""
from __future__ import annotations

import math
from dataclasses import fields

from .alerts import KINDS as RULE_KINDS
from .alerts import new_rule
from .config import DEFAULT_PHONE_ALERTS, SYMBOL_RE, Settings
from .noise import KINDS as ALERT_KINDS
from .setup import EDITABLE, VERSION, SettingsError, validate

FORMAT = 1
STATE_KEYS = ("watchlist", "keywords", "alert_rules", "unusual_alerts", "rotation_alerts", "breakout_alerts",
              "halt_alerts", "phone_alerts", "alert_mutes")
NOT_EXPORTED = {"public_url"}  # this computer's address: wrong anywhere else
MAX_SYMBOLS, MAX_KEYWORDS, MAX_RULES = 200, 200, 100


class BackupError(ValueError):
    """A message safe to show: it never repeats a value from the file."""


def exportable(name: str) -> bool:
    spec = EDITABLE[name]
    return spec.kind not in ("secret", "email") and name not in NOT_EXPORTED


def export(state, settings: Settings, day: str) -> dict:
    defaults = Settings()
    return {
        "market_pulse_backup": FORMAT,
        "app": f"market-pulse/{VERSION}",
        "exported": day,
        "state": {key: getattr(state, key) for key in STATE_KEYS},
        "settings": {name: getattr(settings, name) for name in EDITABLE
                     if exportable(name) and getattr(settings, name) != getattr(defaults, name)},
    }


def _symbols(value, what: str, limit: int) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise BackupError(f"{what}: a list of at most {limit} tickers")
    out = []
    for item in value:
        symbol = item.strip().upper() if isinstance(item, str) else ""
        if not SYMBOL_RE.match(symbol):
            raise BackupError(f"{what}: one entry isn't a ticker")
        if symbol not in out:
            out.append(symbol)
    return out


def _keywords(value) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_KEYWORDS:
        raise BackupError(f"keywords: a list of at most {MAX_KEYWORDS}")
    out = []
    for item in value:
        keyword = " ".join(item.split()) if isinstance(item, str) else ""
        if not 1 <= len(keyword) <= 50:
            raise BackupError("keywords: each 1-50 characters")
        if keyword not in out:
            out.append(keyword)
    return out


def _rules(value, watchlist: list[str]) -> list[dict]:
    if not isinstance(value, list) or len(value) > MAX_RULES:
        raise BackupError(f"alert rules: a list of at most {MAX_RULES}")
    out = []
    for rule in value:
        if not isinstance(rule, dict) or rule.get("kind") not in RULE_KINDS:
            raise BackupError("alert rules: one has an unknown type")
        number = rule.get("value")
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number <= 0:
            raise BackupError("alert rules: each value a positive number")
        symbol = rule.get("symbol") or ""
        if symbol and (not isinstance(symbol, str) or symbol.upper() not in watchlist):
            raise BackupError("alert rules: one is for a ticker that isn't on the watchlist")
        out.append(new_rule(symbol.upper(), rule["kind"], float(number)))  # fresh ids
    return out


def _flag(value, what: str) -> bool:
    if not isinstance(value, bool):
        raise BackupError(f"{what}: on or off")
    return value


def read(data) -> tuple[dict, dict, list[str]]:
    """(state, settings, notes) from a backup file's contents, every value checked. Raises
    BackupError for anything malformed; notes say what was left out."""
    if not isinstance(data, dict) or data.get("market_pulse_backup") != FORMAT:
        raise BackupError("Not a Market Pulse backup file")
    notes: list[str] = []
    raw_state = data.get("state") if isinstance(data.get("state"), dict) else {}
    state: dict = {}
    if "watchlist" in raw_state:
        state["watchlist"] = _symbols(raw_state["watchlist"], "watchlist", MAX_SYMBOLS)
    if "keywords" in raw_state:
        state["keywords"] = _keywords(raw_state["keywords"])
    if "alert_rules" in raw_state:
        state["alert_rules"] = _rules(raw_state["alert_rules"], state.get("watchlist", []))
    for key in ("unusual_alerts", "rotation_alerts", "breakout_alerts"):
        if key in raw_state:
            state[key] = _flag(raw_state[key], key)
    if "halt_alerts" in raw_state:
        if raw_state["halt_alerts"] not in ("watchlist", "all", "off"):
            raise BackupError("halt alerts: watchlist, all or off")
        state["halt_alerts"] = raw_state["halt_alerts"]
    if "phone_alerts" in raw_state:
        cats = raw_state["phone_alerts"]
        if not isinstance(cats, dict) or not all(isinstance(v, bool) for v in cats.values()):
            raise BackupError("phone categories: on or off each")
        state["phone_alerts"] = {k: v for k, v in cats.items() if k in DEFAULT_PHONE_ALERTS}
    if "alert_mutes" in raw_state:
        mutes = raw_state["alert_mutes"]
        if not isinstance(mutes, dict):
            raise BackupError("muted alerts: not in the expected form")
        state["alert_mutes"] = {
            kind: {where: True for where in ("browser", "phone") if isinstance(m, dict) and m.get(where) is True}
            for kind, m in mutes.items() if kind in ALERT_KINDS}
        state["alert_mutes"] = {k: v for k, v in state["alert_mutes"].items() if v}
    if ignored := sorted(set(raw_state) - set(STATE_KEYS)):
        notes.append(f"Left out: {len(ignored)} other entr{'y' if len(ignored) == 1 else 'ies'} of the file")

    raw_settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    settings: dict = {}
    known = {f.name for f in fields(Settings)}
    for name, value in raw_settings.items():
        if name in EDITABLE and not exportable(name):
            notes.append(f"Left out {EDITABLE[name].label}: secrets and this computer's address are never restored")
        elif name in EDITABLE and name in known:
            try:
                settings[name] = validate(name, value)
            except SettingsError as exc:
                raise BackupError(str(exc)) from None
        else:
            notes.append("Left out a setting this version doesn't have")
    return state, settings, notes


def changes(state_now, settings_now: Settings, state: dict, settings: dict) -> list[dict]:
    """What restoring would change: [{"what", "now", "then"}], in words a person can check."""
    out = []

    def add(what, now, then):
        if now != then:
            out.append({"what": what, "now": now, "then": then})

    def rules_text(rules):
        return sorted(f"{r['symbol'] or 'All'} {r['kind']} {r['value']:g}" for r in rules)
    for key, then in state.items():
        now = getattr(state_now, key)
        if key == "alert_rules":
            add("Alert rules", rules_text(now), rules_text(then))
        else:
            add(key.replace("_", " ").capitalize(), now, then)
    for name, then in settings.items():
        add(EDITABLE[name].label, getattr(settings_now, name), then)
    return out
