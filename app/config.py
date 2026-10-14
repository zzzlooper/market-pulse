"""Settings (from config.toml) and the user's runtime state (watchlist, keywords)."""
from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# MARKET_PULSE_CONFIG points elsewhere, e.g. for tests that must not read your real settings.
CONFIG_PATH = Path(os.environ.get("MARKET_PULSE_CONFIG") or ROOT / "config.toml")
# MARKET_PULSE_DATA moves the data folder (watchlist state, reaction history), e.g. for tests.
STATE_PATH = Path(os.environ.get("MARKET_PULSE_DATA") or ROOT / "data") / "state.json"
TLS_DIR = STATE_PATH.parent / "tls"  # HTTPS certificates (app/tls.py), also used during a replay

# 8-K filings come through app/filings.py, which tags their items; this feed is used only
# when sec_filings is off.
SEC_8K_FEED = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&count=40&output=atom"
DEFAULT_FEEDS = {
    "GlobeNewswire": "https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire%20-%20News%20about%20Public%20Companies",
    "PR Newswire": "https://www.prnewswire.com/rss/news-releases-list.rss",
}
DEFAULT_WATCHLIST = ["AAPL", "NVDA", "TSLA", "AMD", "SPY"]
DEFAULT_KEYWORDS = ["FDA", "acquisition", "merger", "offering", "guidance", "buyback"]


@dataclass
class Settings:
    # SEC asks automated clients to identify themselves: "app-name contact@email".
    sec_user_agent: str = "market-pulse/0.1 admin@example.com"
    # Real-time watchlist prices; empty = delayed Yahoo prices only.
    finnhub_api_key: str = ""
    finnhub_quotes_interval: float = 300.0  # REST snapshot refresh; trades stream in between
    # Phone alerts (all optional). ntfy: pick a hard-to-guess topic, since anyone who knows it
    # can read it on the public server. Telegram: a bot token from @BotFather + your chat id.
    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_token: str = ""          # only for protected topics / self-hosted servers
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_api: str = "https://api.telegram.org"
    public_url: str = ""          # e.g. http://192.168.1.20:8000, so a tapped alert opens the ticker
    # Required to use the app when set. Leave empty only while it listens on 127.0.0.1.
    access_token: str = ""
    source_alert_minutes: float = 5.0  # phone alert when a data source is down this long; 0 = never
    quotes_interval: float = 15.0
    movers_interval: float = 60.0
    news_interval: float = 30.0
    halts_interval: float = 20.0
    rvol_threshold: float = 2.0
    min_volume: int = 300_000
    news_history: int = 500
    feeds: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FEEDS))
    ticker_news: bool = True  # also poll Yahoo's per-ticker RSS for each watchlist symbol
    # SEC filings: offerings (dilution), insider buys/sells and 13D stakes. See app/filings.py.
    sec_filings: bool = True
    filings_interval: float = 30.0
    insider_min_value: float = 50_000  # smallest insider trade shown; sales only for the watchlist
    # Dilution risk badges: profiles are kept fresh for the watchlist and these movers lists.
    risk_movers: list[str] = field(default_factory=lambda: ["gainers", "small_caps"])
    risk_max_symbols: int = 60
    recap_time: str = "16:30"  # ET, weekdays: end-of-day recap to the phone (Phone box: Daily recap)
    # Pre-market gap scanner (app/gaps.py): 04:00–10:00 ET on weekdays, brief to the phone.
    gap_min_pct: float = 3.0
    gap_min_minutes: int = 5          # minutes with pre-market trades before a gap counts
    gap_max_symbols: int = 80
    gap_brief_time: str = "09:15"     # ET, weekdays (Phone box: Pre-market brief)
    # Record what the data sources answer, to replay later (app/replay.py). Newest record_keep kept.
    record: bool = False
    record_keep: int = 10
    reactions_keep_days: int = 365  # older reaction history is deleted daily; 0 keeps it all
    # Signal thresholds (Setup → Signal thresholds); docs/signals.md says what each signal does.
    breakout_pullback_pct: float = 5.0   # a new high of day counts after a pullback at least this deep
    day2_min_pct: float = 30.0           # the last session's gain that makes today day 2
    rotation_multiple: float = 1.0       # volume / float that counts as having traded the float
    luld_near_pct: float = 25.0          # this share of the band's width from a LULD band counts as near
    record_min: int = 10                 # alerts measured to the close before a track record is quoted
    sympathy_lead_pct: float = 30.0      # a stock up this much today leads its industry
    sympathy_min_pct: float = 5.0        # a same-industry stock up this much moves in sympathy
    phone_cap_per_kind: int = 0          # most alerts of one kind sent to the phone a day; 0 = no cap


# Settings that may come from same-named upper-case env vars (which win), so secrets and
# your contact address can stay out of config.toml.
SECRET_ENV = ("sec_user_agent", "finnhub_api_key", "ntfy_topic", "ntfy_token", "telegram_bot_token", "telegram_chat_id",
              "access_token")


RECAP_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SYMBOL_RE = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]{0,11}$")  # a ticker as Yahoo writes it (BRK-B, ^GSPC, ES=F)


def load_settings(path: Path = CONFIG_PATH, dashboard: dict | None = None) -> Settings:
    """config.toml, then values saved from the dashboard (app/setup.py), then the environment."""
    raw = tomllib.loads(path.read_text()) if path.exists() else {}
    unknown = set(raw) - {f.name for f in fields(Settings)}
    if unknown:
        raise ValueError(f"Unknown settings in {path.name}: {', '.join(sorted(unknown))}")
    raw.update(dashboard or {})
    for name in SECRET_ENV:
        if value := os.environ.get(name.upper()):
            raw[name] = value
    if "telegram_chat_id" in raw:
        raw["telegram_chat_id"] = str(raw["telegram_chat_id"])  # TOML may give an int
    settings = Settings(**raw)
    for name, url in settings.feeds.items():
        if not (isinstance(url, str) and re.match(r"^https?://\S+$", url)):
            # Usually a top-level setting written after [feeds], which TOML files under it. The
            # value isn't repeated: it may be a token.
            raise ValueError(f"feeds.{name} in {path.name} isn't a web address. Settings written after "
                             "[feeds] belong to it: put top-level settings before any [section]")
    for name in ("recap_time", "gap_brief_time"):
        if not RECAP_TIME.match(getattr(settings, name)):
            raise ValueError(f'{name} must be "HH:MM" (24-hour, ET), got {getattr(settings, name)!r}')
    return settings


# Which alert categories go to the phone. Unusual volume is off: it can fire dozens of times a day.
# "sources" is a data source going down (and coming back), so silence isn't mistaken for a calm market.
DEFAULT_PHONE_ALERTS = {"price": True, "halts": True, "news": True, "filings": True, "unusual": False,
                        "breakouts": False, "sources": True, "recap": True, "gaps": True}

DEFAULT_ALERT_RULES = [
    {"id": "default-move", "symbol": "", "kind": "move", "value": 5.0},
    {"id": "default-rvol", "symbol": "", "kind": "rvol", "value": 3.0},
]


class UserState:
    """Watchlist, keywords and alert rules, editable from the UI and persisted to JSON."""

    def __init__(self, path: Path = STATE_PATH):
        self.path = path
        data = json.loads(path.read_text()) if path.exists() else {}
        self.watchlist: list[str] = data.get("watchlist", list(DEFAULT_WATCHLIST))
        self.keywords: list[str] = data.get("keywords", list(DEFAULT_KEYWORDS))
        self.alert_rules: list[dict] = data.get("alert_rules", [dict(r) for r in DEFAULT_ALERT_RULES])
        self.unusual_alerts: bool = data.get("unusual_alerts", True)
        self.rotation_alerts: bool = data.get("rotation_alerts", True)
        self.breakout_alerts: bool = data.get("breakout_alerts", True)
        self.alert_mutes: dict[str, dict[str, bool]] = data.get("alert_mutes", {})  # kind -> {"browser", "phone"}
        self.snoozed: dict[str, str] = data.get("snoozed", {})  # symbol -> the day it's snoozed for (ET)
        self.halt_alerts: str = data.get("halt_alerts", "watchlist")  # "watchlist" | "all" | "off"
        self.phone_alerts: dict[str, bool] = {**DEFAULT_PHONE_ALERTS, **data.get("phone_alerts", {})}

    def to_dict(self) -> dict:
        return {
            "watchlist": self.watchlist,
            "keywords": self.keywords,
            "alert_rules": self.alert_rules,
            "unusual_alerts": self.unusual_alerts,
            "rotation_alerts": self.rotation_alerts,
            "breakout_alerts": self.breakout_alerts,
            "alert_mutes": self.alert_mutes,
            "snoozed": self.snoozed,
            "halt_alerts": self.halt_alerts,
            "phone_alerts": self.phone_alerts,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        # Readable by you only, whatever the umask: it's your watchlist and alert rules.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(self.to_dict(), indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
