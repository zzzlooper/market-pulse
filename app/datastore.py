"""What Market Pulse keeps on this computer, for Setup's "Your data" section, and deleting it.

Paths are shown relative to the project folder, never in full (a full path contains your user
name). Settings saved from the dashboard are listed by name only; no value, secret or not, is
ever returned. Nothing here leaves the machine.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import ROOT
from .market import ET
from .setup import EDITABLE

DELETABLE = ("reactions", "recordings", "dashboard_settings")


def shown_path(path: Path) -> str:
    """'data/reactions.db', or just the name under a data folder elsewhere (a replay's)."""
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return f"(data folder)/{path.name}"


def _size(path: Path) -> int:
    if path.is_dir():
        return sum(p.stat().st_size for p in path.iterdir() if p.is_file())
    return path.stat().st_size if path.exists() else 0


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _day(epoch: float | None) -> str | None:
    return datetime.fromtimestamp(epoch, ET).date().isoformat() if epoch else None


def state_item(path: Path, watchlist: list, keywords: list, rules: list) -> dict:
    return {
        "id": "state", "label": "Watchlist, keywords and alert rules", "path": shown_path(path),
        "size": _size(path), "deletable": False,
        "detail": f"{_count(len(watchlist), 'ticker')}, {_count(len(keywords), 'keyword')}, "
                  f"{_count(len(rules), 'alert rule')}",
        "note": "Change them on the dashboard.",
    }


def settings_item(path: Path) -> dict:
    try:
        saved = [name for name in json.loads(path.read_text()) if name in EDITABLE]
    except (FileNotFoundError, ValueError):
        saved = []
    secret = {n for n in saved if EDITABLE[n].kind in ("secret", "email")}
    names = [f"{EDITABLE[n].label}{' (secret)' if n in secret else ''}" for n in saved]
    confirm = ("Delete the settings saved in Setup? Values from config.toml and environment variables "
               "stay. Secrets saved here (tokens, the ntfy topic, your SEC email) are deleted.")
    if "access_token" in saved:
        confirm += (" That includes the access token: other devices are signed out, and the dashboard "
                    "then only answers on this computer.")
    return {
        "id": "dashboard_settings", "label": "Settings saved in Setup", "path": shown_path(path),
        "size": _size(path), "deletable": bool(saved), "confirm": confirm,
        "detail": f"{len(saved)} saved: {', '.join(names)}" if saved else "Nothing saved here",
        "note": "Values are never shown; secrets are listed by name only.",
    }


def reactions_item(path: Path, stats: dict, keep_days: int) -> dict:
    span = f", {_day(stats['oldest'])} to {_day(stats['newest'])}" if stats["count"] else ""
    keep = f"kept {keep_days} days" if keep_days else "kept until you delete it"
    return {
        "id": "reactions", "label": "Catalyst reaction history", "path": shown_path(path),
        "size": _size(path), "deletable": stats["count"] > 0,
        "detail": f"{_count(stats['count'], 'event')}{span}; {keep}",
        "note": ("Tickers, catalysts, prices and public headlines, and the alerts that fired for you, "
                 "your own price rules among them (which stocks you watch, at what levels)."),
        "confirm": "Delete the whole reaction history? The Reactions tab and recaps start from scratch.",
    }


def recordings_item(folder: Path, keep: int, active: Path | None) -> dict:
    files = sorted(folder.glob("*.jsonl.gz")) if folder.is_dir() else []
    others = [f for f in files if f != active]
    span = f", {files[0].name[:10]} to {files[-1].name[:10]}" if files else ""
    detail = f"{_count(len(files), 'recording')}{span}; newest {keep} kept"
    if active:
        detail += "; recording now"
    return {
        "id": "recordings", "label": "Recordings for replay", "path": shown_path(folder),
        "size": sum(f.stat().st_size for f in files), "deletable": bool(others), "detail": detail,
        "note": "What the data sources answered; insiders' names and addresses are cut out when recorded.",
        "confirm": "Delete all recordings?" + (" The one being written now is kept." if active else ""),
    }


def tls_item(folder: Path, status: dict) -> dict:
    if not status.get("exists"):
        detail = "Not set up (start with --https to create them)"
    else:
        detail = (f"Market Pulse's own CA and server certificate, renews before {status['server_expires']}; "
                  f"CA fingerprint {status['fingerprint'][:23]}…")
    return {
        "id": "tls", "label": "HTTPS certificates", "path": shown_path(folder),
        "size": _size(folder) if folder.is_dir() else 0, "deletable": False, "detail": detail,
        "note": "The private keys are readable by you only and never shown or sent; only the CA certificate is public.",
    }


def log_item() -> dict:
    return {
        "id": "log", "label": "Log", "path": None, "size": None, "deletable": False,
        "detail": "As a background service: the system journal (journalctl --user -u market-pulse)",
        "note": "Records events and setting names, never their values or any secret.",
    }


def delete_recordings(folder: Path, active: Path | None) -> int:
    deleted = 0
    for f in sorted(folder.glob("*.jsonl.gz")) if folder.is_dir() else []:
        if f != active:
            f.unlink()
            deleted += 1
    return deleted
